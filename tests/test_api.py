"""API contract tests.

These run against the hand-built tiny fixture with the Isolation Forest disabled,
so the whole suite stays fast and hermetic -- no network, no 24k-row sample, no
GPU. What they pin is the *contract*: status codes, validation boundaries,
response shapes, and above all that ``POST /score`` agrees with the batch
pipeline about the same claim.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from tests.conftest import DRG_A, DRG_C_SINGLETON, OUTLIER_PROVIDER, TINY_CLAIMS

from claimsight.api.main import RESPONSE_PRECISION, create_app
from claimsight.scoring import ScoringConfig, confidence, robust_z


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Built once per module: the pipeline is the expensive part."""
    application = create_app(source=TINY_CLAIMS, with_forest=False)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def forest_client() -> TestClient:
    """A client with the Isolation Forest ENABLED.

    The forest is what registers extra state in DuckDB, and an earlier version
    of that code left it as a connection-local DataFrame scan -- invisible to the
    per-request ``cursor()``, so every scored endpoint 500'd over HTTP while the
    ``with_forest=False`` suite stayed green. This fixture exists to keep that
    gap closed.
    """
    application = create_app(source=TINY_CLAIMS, with_forest=True)
    with TestClient(application) as test_client:
        yield test_client


class TestWithIsolationForestEnabled:
    """Every scored endpoint must work over HTTP with the forest on."""

    @pytest.mark.parametrize("path", ["/summary", "/outliers", "/peer-levels"])
    def test_scored_endpoints_do_not_500(self, forest_client: TestClient, path: str) -> None:
        response = forest_client.get(path)
        assert response.status_code == 200, response.text

    def test_summary_includes_forest_columns(self, forest_client: TestClient) -> None:
        body = forest_client.get("/summary").json()
        assert body["flagged_iforest"] >= 0
        assert body["method_agreement_jaccard"] >= 0.0

    def test_outliers_still_finds_the_planted_anomaly(self, forest_client: TestClient) -> None:
        body = forest_client.get("/outliers").json()
        assert [r["provider_id"] for r in body] == [OUTLIER_PROVIDER]

    def test_provider_scorecard_works(self, forest_client: TestClient) -> None:
        response = forest_client.get(f"/providers/{OUTLIER_PROVIDER}")
        assert response.status_code == 200
        assert response.json()["flagged_claims"] == 1


class TestServiceEndpoints:
    def test_health_reports_the_loaded_dataset(self, client: TestClient) -> None:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["rows_loaded"] == 17  # 18 rows minus the corrupt one
        assert "tiny_claims.csv" in body["source_file"]
        assert body["pipeline_seconds"] >= 0

    def test_openapi_is_generated(self, client: TestClient) -> None:
        spec = client.get("/openapi.json")
        assert spec.status_code == 200
        paths = spec.json()["paths"]
        for expected in ("/health", "/summary", "/outliers", "/score", "/peer-levels"):
            assert expected in paths


class TestSummary:
    def test_summary_matches_the_fixture(self, client: TestClient) -> None:
        body = client.get("/summary").json()
        assert body["rows_scored"] == 17
        assert body["flagged_peer"] == 1
        # 8 uniform-markup rows + 1 singleton DRG.
        assert body["degenerate_dispersion_rows"] == 9
        reasons = {r["reason"] for r in body["flag_reasons"]}
        assert "high_markup" in reasons
        assert "degenerate_peers" in reasons


class TestPeerLevels:
    def test_reports_hierarchy_usage(self, client: TestClient) -> None:
        body = client.get("/peer-levels").json()
        assert sum(r["rows_scored"] for r in body) == 17
        assert {r["peer_level"] for r in body} == {"drg_hrr", "drg_national"}


class TestOutliers:
    def test_returns_the_planted_outlier_first(self, client: TestClient) -> None:
        body = client.get("/outliers").json()
        assert len(body) == 1
        assert body[0]["provider_id"] == OUTLIER_PROVIDER
        assert body[0]["flag_reason"] == "high_markup"
        assert body[0]["relative_excess"] == pytest.approx(30.0 / 5.25)

    def test_state_filter(self, client: TestClient) -> None:
        assert client.get("/outliers", params={"state": "TS"}).json()
        assert client.get("/outliers", params={"state": "ZZ"}).json() == []

    def test_limit_is_bounded(self, client: TestClient) -> None:
        assert client.get("/outliers", params={"limit": 0}).status_code == 422
        assert client.get("/outliers", params={"limit": 501}).status_code == 422


class TestProviderScorecard:
    def test_returns_profile_and_claims(self, client: TestClient) -> None:
        body = client.get(f"/providers/{OUTLIER_PROVIDER}").json()
        assert body["provider_id"] == OUTLIER_PROVIDER
        assert body["flagged_claims"] == 1
        assert len(body["claims"]) == 1
        assert body["state"] == "TS"

    def test_unknown_provider_is_404(self, client: TestClient) -> None:
        response = client.get("/providers/999999")
        assert response.status_code == 404
        assert "unknown provider_id" in response.json()["detail"]

    def test_rejected_row_is_not_served(self, client: TestClient) -> None:
        """The corrupt row was rejected at ingest, so it must not be queryable."""
        assert client.get("/providers/010050").status_code == 404


class TestScoreEndpoint:
    PAYLOAD: ClassVar[dict[str, Any]] = {
        "drg": DRG_A,
        "hrr": "TS - Testville",
        "state": "TS",
        "total_discharges": 27,
        "avg_covered_charges": 285000.00,
        "avg_medicare_payments": 9500.00,
    }

    def test_agrees_with_the_batch_pipeline(self, client: TestClient) -> None:
        """The central API guarantee: same claim, same verdict, two code paths."""
        body = client.post("/score", json=self.PAYLOAD).json()
        assert body["peer_found"] is True
        assert body["peer_level"] == "drg_hrr"
        assert body["peer_n"] == 8
        assert body["peer_median"] == pytest.approx(5.25)
        assert body["peer_mad"] == pytest.approx(0.75)
        # The API rounds floats to RESPONSE_PRECISION for presentation; the
        # batch path keeps full precision. Rounding happens after every guard,
        # so it cannot change the verdict -- hence abs= rather than an exact match.
        tol = 10 ** -RESPONSE_PRECISION
        assert body["relative_excess"] == pytest.approx(30.0 / 5.25, abs=tol)
        assert body["robust_z"] == pytest.approx(robust_z(30.0, 5.25, 0.75), abs=tol)
        assert body["confidence"] == pytest.approx(
            confidence(8, 27, ScoringConfig()), abs=tol
        )
        assert body["would_flag"] is True
        assert body["reason"].startswith("high_markup")

    def test_a_normal_claim_is_not_flagged(self, client: TestClient) -> None:
        payload = {**self.PAYLOAD, "avg_covered_charges": 50000.00,
                   "avg_medicare_payments": 10000.00}
        body = client.post("/score", json=payload).json()
        assert body["markup_ratio"] == pytest.approx(5.0)
        assert body["would_flag"] is False
        assert body["reason"].startswith("within_peer_range")

    def test_thin_dispersion_guard_is_enforced(self, client: TestClient) -> None:
        """A statistically extreme but practically trivial claim must not flag."""
        payload = {**self.PAYLOAD, "avg_covered_charges": 51000.00,
                   "avg_medicare_payments": 10000.00}
        body = client.post("/score", json=payload).json()
        assert body["markup_ratio"] == pytest.approx(5.1)
        assert body["would_flag"] is False

    def test_singleton_drg_has_no_usable_peer_group(self, client: TestClient) -> None:
        """DRG C has one row: dispersion is zero, so it must not be flagged."""
        payload = {
            "drg": DRG_C_SINGLETON,
            "hrr": "ZZ - Zedville",
            "state": "ZZ",
            "total_discharges": 11,
            "avg_covered_charges": 5_000_000.00,
            "avg_medicare_payments": 150_139.69,
        }
        body = client.post("/score", json=payload).json()
        assert body["peer_found"] is True
        assert body["peer_n"] == 1
        assert body["would_flag"] is False
        assert body["reason"].startswith("degenerate_peers")

    def test_unknown_drg_reports_no_peers(self, client: TestClient) -> None:
        payload = {**self.PAYLOAD, "drg": "999 - NOT A REAL DRG"}
        body = client.post("/score", json=payload).json()
        assert body["peer_found"] is False
        assert body["would_flag"] is False
        assert "cannot be compared" in body["reason"]

    @pytest.mark.parametrize(
        "override",
        [
            {"total_discharges": 0},          # not > 0
            {"total_discharges": -5},
            {"avg_covered_charges": 0},       # not > 0
            {"avg_medicare_payments": -1.0},
            {"state": "TSX"},                 # max_length 2
            {"state": ""},
            {"drg": ""},                      # min_length 1
        ],
    )
    def test_validation_rejects_invalid_claims(self, client: TestClient, override: dict) -> None:
        assert client.post("/score", json={**self.PAYLOAD, **override}).status_code == 422

    def test_unknown_fields_are_rejected(self, client: TestClient) -> None:
        """``extra='forbid'``: a typo'd field must fail loudly, not be ignored."""
        payload = {**self.PAYLOAD, "avg_totel_payments": 100.0}
        assert client.post("/score", json=payload).status_code == 422

    def test_state_is_normalised_to_uppercase(self, client: TestClient) -> None:
        body = client.post("/score", json={**self.PAYLOAD, "state": "ts"}).json()
        assert body["peer_found"] is True
