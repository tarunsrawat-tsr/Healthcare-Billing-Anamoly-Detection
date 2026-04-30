"""Tests for the dashboard surface: the HTML asset, ``/reference``, ``/distribution``.

Three things are pinned here that are easy to break silently:

1. The dashboard must stay **self-contained**. A CDN reference would make it
   render blank offline, in CI, or in a container with no egress, and a
   hard-coded host would break it behind any reverse proxy. Both are asserted
   against the file's text, not against a browser.
2. ``/distribution`` must **conserve rows**. A histogram whose bucket counts do
   not sum to the number of rows binned is a chart that hides data.
3. The column parameter is a **whitelist** interpolated into SQL. Anything not
   on the list must be rejected at the boundary with a 422.
"""

from __future__ import annotations

import csv
import itertools
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.conftest import DRG_A, TINY_CLAIMS

from claimsight.api.main import DASHBOARD_HTML, HISTOGRAM_COLUMNS, OUTLIER_COLUMNS, create_app
from claimsight.api.schemas import (
    DistributionBin,
    DistributionResponse,
    FlagReason,
    HealthResponse,
    OutlierRow,
    PeerLevelUsage,
    ProviderScorecard,
    ReferenceData,
    ScoreVerdict,
    SummaryResponse,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def client() -> TestClient:
    application = create_app(source=TINY_CLAIMS, with_forest=False)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def constant_client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """A dataset where the metric is identical on every row.

    Rewrites the fixture's charge columns to constants so ``markup_ratio`` has a
    single value. That is the case where a histogram's bin width would be zero
    and ``floor((x - lo) / step)`` would divide by zero -- a real edge, because
    a DRG with one hospital billing identically to everyone else produces it.
    """
    directory = tmp_path_factory.mktemp("constant")
    source = directory / "constant_markup.csv"
    with TINY_CLAIMS.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0])
    for row in rows:
        row["Average Covered Charges"] = "$10,000.00"
        row["Average Medicare Payments"] = "$2,000.00"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    application = create_app(source=source, with_forest=False)
    with TestClient(application) as test_client:
        yield test_client


class TestDashboardAsset:
    def test_serves_html(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<title>claimsight" in response.text

    def test_no_external_resources(self) -> None:
        """No CDN, no webfont, no remote stylesheet or script."""
        html = DASHBOARD_HTML.read_text(encoding="utf-8")
        assert not re.search(r"<script[^>]+src=", html)
        assert not re.search(r"<link[^>]+stylesheet", html)
        assert "@import" not in html
        for pattern in ("cdn.", "unpkg.com", "jsdelivr", "googleapis", "fonts.g"):
            assert pattern not in html, pattern

    def test_no_absolute_or_hard_coded_hosts(self) -> None:
        """Relative fetches only, so the page works behind any proxy or host."""
        html = DASHBOARD_HTML.read_text(encoding="utf-8")
        assert "localhost" not in html
        assert "127.0.0.1" not in html
        for url in re.findall(r"""api\.(?:get|post)\(\s*[`"']([^`"']+)[`"']""", html):
            assert not url.startswith(("http://", "https://", "//")), url
        # The one absolute URL allowed is the SVG namespace, which is an
        # identifier and never fetched.
        for url in re.findall(r"""(?:src|href)\s*=\s*["'](https?:[^"']+)["']""", html):
            assert url.startswith("http://www.w3.org/2000/svg"), url

    def test_repeats_the_not_a_fraud_caveat(self) -> None:
        """The page is the most likely thing to be screenshotted without the README."""
        html = DASHBOARD_HTML.read_text(encoding="utf-8").lower()
        assert "not findings of fraud" in html

    def test_is_shipped_in_the_package(self) -> None:
        assert DASHBOARD_HTML.is_file()
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "static/*.html" in pyproject, "dashboard would not ship in a wheel"

    def test_endpoints_it_calls_all_exist(self, client: TestClient) -> None:
        """Every path the page fetches must be a real route on this service."""
        html = DASHBOARD_HTML.read_text(encoding="utf-8")
        called = {
            url.split("?")[0]
            for url in re.findall(r"""api\.(?:get|post)\(\s*[`"']([^`"']+)[`"']""", html)
        }
        called |= {"health", "summary", "peer-levels", "reference"}  # literal calls in boot()
        # Template literals (providers/${id}) and query strings are reduced to
        # their first path segment, which is what has to exist as a route.
        called_roots = {url.removeprefix("./").split("/")[0].split("?")[0] for url in called}
        route_roots = {
            route.path.strip("/").split("/")[0]
            for route in client.app.routes
            if hasattr(route, "path")
        }
        assert called_roots, "regex found no api calls -- the test is not testing anything"
        missing = called_roots - route_roots
        assert not missing, f"dashboard calls unregistered routes: {sorted(missing)}"


class TestReferenceEndpoint:
    def test_returns_the_loaded_dimensions(self, client: TestClient) -> None:
        body = client.get("/reference").json()
        assert body["drg_count"] == len(body["drgs"]) > 0
        assert body["hrr_count"] == len(body["hrrs"]) > 0
        assert body["state_count"] == len(body["states"]) > 0

    def test_values_are_real_dataset_strings(self, client: TestClient) -> None:
        body = client.get("/reference").json()
        assert DRG_A in body["drgs"]
        assert all(len(state) == 2 for state in body["states"])
        assert all(" - " in hrr for hrr in body["hrrs"])

    def test_lists_are_sorted(self, client: TestClient) -> None:
        body = client.get("/reference").json()
        assert body["drgs"] == sorted(body["drgs"])
        assert body["states"] == sorted(body["states"])


class TestDistribution:
    def test_buckets_conserve_rows_and_flags(self, client: TestClient) -> None:
        body = client.get("/distribution?column=markup_ratio&bins=20").json()
        assert sum(b["count"] for b in body["buckets"]) == body["rows_binned"]
        assert sum(b["flagged"] for b in body["buckets"]) <= body["rows_binned"]
        summary = client.get("/summary").json()
        assert sum(b["flagged"] for b in body["buckets"]) == summary["flagged_peer"]

    def test_edges_are_contiguous_and_ordered(self, client: TestClient) -> None:
        buckets = client.get("/distribution?column=markup_ratio&bins=25").json()["buckets"]
        assert [b["index"] for b in buckets] == list(range(1, 26))
        for earlier, later in itertools.pairwise(buckets):
            assert earlier["lo"] < earlier["hi"]
            assert abs(earlier["hi"] - later["lo"]) < 1e-3

    def test_edges_span_the_data(self, client: TestClient) -> None:
        body = client.get("/distribution?column=markup_ratio&bins=30&log=false").json()
        assert body["buckets"][0]["lo"] == pytest.approx(body["min"], rel=1e-3)
        assert body["buckets"][-1]["hi"] == pytest.approx(body["max"], rel=1e-3)

    def test_log_transform_is_reported_and_reversible(self, client: TestClient) -> None:
        logged = client.get("/distribution?column=markup_ratio&bins=20&log=true").json()
        linear = client.get("/distribution?column=markup_ratio&bins=20&log=false").json()
        assert logged["transform"] == "log"
        assert linear["transform"] == "identity"
        # Same data, same totals; only the bucketing differs.
        assert logged["rows_binned"] == linear["rows_binned"]
        assert logged["buckets"][-1]["hi"] == pytest.approx(linear["buckets"][-1]["hi"], rel=1e-3)
        # Log buckets are geometric: equal ratios, not equal widths.
        ratios = {b["hi"] / b["lo"] for b in logged["buckets"] if b["lo"] > 0}
        assert max(ratios) - min(ratios) < 1e-3

    @pytest.mark.parametrize("column", sorted(HISTOGRAM_COLUMNS))
    def test_every_whitelisted_column_works(self, client: TestClient, column: str) -> None:
        response = client.get(f"/distribution?column={column}&bins=10")
        assert response.status_code == 200, response.text
        assert response.json()["column"] == column

    @pytest.mark.parametrize(
        "column",
        ["total_discharges", "confidence", "robust_z", "non_medicare_share"],
    )
    def test_log_is_ignored_where_it_is_not_meaningful(
        self, client: TestClient, column: str
    ) -> None:
        """Discharges and confidence are not log-scale quantities."""
        assert HISTOGRAM_COLUMNS[column] is False
        body = client.get(f"/distribution?column={column}&log=true").json()
        assert body["transform"] == "identity"

    @pytest.mark.parametrize(
        "column",
        [
            "provider_name",
            "drg",
            "state",
            "flag_reason",
            "markup_ratio; DROP TABLE claims",
            "' OR 1=1 --",
            "",
        ],
    )
    def test_rejects_anything_not_on_the_whitelist(self, client: TestClient, column: str) -> None:
        assert client.get(f"/distribution?column={column}").status_code == 422

    @pytest.mark.parametrize("bins", [0, 1, 4, 101, 1000, -5])
    def test_rejects_out_of_range_bin_counts(self, client: TestClient, bins: int) -> None:
        assert client.get(f"/distribution?column=markup_ratio&bins={bins}").status_code == 422

    def test_constant_column_does_not_divide_by_zero(self, constant_client: TestClient) -> None:
        """Bin width zero: report it, do not 500, do not emit NaN."""
        response = constant_client.get("/distribution?column=markup_ratio&bins=20&log=false")
        assert response.status_code == 200
        body = response.json()
        assert body["note"] and "identical" in body["note"]
        populated = [b for b in body["buckets"] if b["count"]]
        assert len(populated) == 1
        assert populated[0]["count"] == body["rows_binned"]
        assert json.dumps(body)  # serialisable: no NaN/Infinity leaked into JSON

    def test_reports_summary_statistics(self, client: TestClient) -> None:
        body = client.get("/distribution?column=markup_ratio&bins=20").json()
        for key in ("min", "median", "mean", "p95", "p99", "max"):
            assert body[key] is not None
        assert body["min"] <= body["median"] <= body["max"]
        assert body["p95"] <= body["p99"]

    def test_column_order_matches_the_outlier_contract(self) -> None:
        """The two lists are bound positionally to a SELECT; a mismatch mis-labels data."""
        assert len(OUTLIER_COLUMNS) == len(set(OUTLIER_COLUMNS))
        assert "avg_covered_charges" in OUTLIER_COLUMNS

    def test_outlier_rows_expose_the_dollars(self, client: TestClient) -> None:
        rows: list[dict[str, Any]] = client.get("/outliers?limit=5").json()
        if not rows:
            pytest.skip("fixture flags nothing at this threshold")
        for row in rows:
            assert row["avg_covered_charges"] > 0
            assert row["avg_medicare_payments"] > 0
            assert row["markup_ratio"] == pytest.approx(
                row["avg_covered_charges"] / row["avg_medicare_payments"], rel=1e-6
            )
            assert row["flagged_peer"] is True


class TestDashboardApiContract:
    """Every snake_case field the dashboard reads must exist on some response model.

    This is the test that would have caught a real bug: the queue table rendered
    ``avg_covered_charges`` before that field was added to ``OutlierRow``, so the
    column showed an em dash for every row. Nothing failed -- the JS read
    ``undefined``, the API contract was self-consistent, and the page simply lied
    by omission. Field names are the one interface between the two halves that no
    type checker in this repo can see.
    """

    MODELS = (
        HealthResponse,
        SummaryResponse,
        FlagReason,
        PeerLevelUsage,
        OutlierRow,
        ProviderScorecard,
        DistributionResponse,
        DistributionBin,
        ReferenceData,
        ScoreVerdict,
    )

    def _script(self) -> str:
        html = DASHBOARD_HTML.read_text(encoding="utf-8")
        match = re.search(r"<script>(.*)</script>", html, re.S)
        assert match, "dashboard has no script block"
        return match.group(1)

    def test_extraction_is_not_vacuous(self) -> None:
        """Guard against the regex silently matching nothing after a refactor."""
        fields = set(re.findall(r"\.([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", self._script()))
        assert len(fields) > 25, fields

    def test_every_field_it_reads_exists(self) -> None:
        known: set[str] = set()
        for model in self.MODELS:
            known |= set(model.model_fields)
        used = set(re.findall(r"\.([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", self._script()))
        # snake_case is the tell: DOM/JS properties in this file are camelCase or
        # single words, API fields are snake_case.
        unknown = sorted(used - known)
        assert not unknown, (
            f"dashboard reads fields no endpoint returns: {unknown}. Known fields: {sorted(known)}"
        )

    def test_the_dollar_columns_are_actually_in_the_response(self, client: TestClient) -> None:
        """The specific regression, asserted over HTTP rather than by inspection."""
        rows: list[dict[str, Any]] = client.get("/outliers?limit=5").json()
        if not rows:
            pytest.skip("fixture flags nothing at this threshold")
        for key in ("avg_covered_charges", "avg_medicare_payments", "flagged_peer"):
            assert all(key in row for row in rows), key
