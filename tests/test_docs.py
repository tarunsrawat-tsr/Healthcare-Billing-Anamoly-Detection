"""Documentation must not drift from the data.

An earlier OpenAPI example for ``POST /score`` used a DRG string that looked
plausible but does not exist in the CMS file:

    "470 - MAJOR JOINT REPLACEMENT W/O CC/MCC"                       (invented)
    "470 - MAJOR JOINT REPLACEMENT OR REATTACHMENT OF LOWER EXTREMITY W/O MCC"

Nothing failed when it was wrong. The schema validated, the unit tests passed,
and the API returned a perfectly reasonable "no peer group found". A reviewer
clicking *Try it out* in Swagger would simply have been told their first request
matched nothing -- and concluded the service was broken.

Fabricated examples are invisible to type checkers and to tests that build their
own fixtures, so they get a test of their own: **every identifier documented in
the schema and the README must exist in the committed sample.**
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claimsight.api.main import create_app
from claimsight.api.schemas import ScoreRequest
from claimsight.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
SAMPLE = get_settings().sample_csv


@pytest.fixture(scope="module")
def sample_client() -> TestClient:
    """A service running against the committed sample, forest enabled.

    Module-scoped: building the pipeline over 24k rows costs a couple of
    seconds, and these tests only read.
    """
    if not SAMPLE.exists():
        pytest.skip(f"committed sample not present at {SAMPLE}")
    application = create_app(source=SAMPLE, with_forest=True)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def all_drg_codes() -> set[str]:
    """Every DRG code in the committed sample, read straight from the CSV.

    Deliberately NOT taken from ``/outliers``: that endpoint returns only flagged
    rows, so a DRG quoted in the README but never flagged would look "unknown"
    and the test would fail for the wrong reason.
    """
    import duckdb

    from claimsight import schema

    if not SAMPLE.exists():
        pytest.skip(f"committed sample not present at {SAMPLE}")
    con = duckdb.connect()
    rows = con.sql(
        f'SELECT DISTINCT "DRG Definition" FROM {schema.source_relation(str(SAMPLE))}'
    ).fetchall()
    return {r[0].split(" - ")[0] for r in rows}


class TestOpenApiExample:
    def test_example_parses_as_a_valid_request(self) -> None:
        """The documented example must at least satisfy its own schema."""
        example = ScoreRequest.model_config["json_schema_extra"]["examples"][0]
        parsed = ScoreRequest.model_validate(example)
        assert parsed.markup_ratio > 0

    def test_example_resolves_to_a_real_peer_group(self, sample_client: TestClient) -> None:
        """The critical one: a reviewer's first click must actually work."""
        example = ScoreRequest.model_config["json_schema_extra"]["examples"][0]
        body = sample_client.post("/score", json=example).json()

        assert body["peer_found"] is True, (
            f"the documented example matched no peer group. DRG={example['drg']!r} "
            f"HRR={example['hrr']!r} -- the example has drifted from the data."
        )
        assert body["peer_n"] >= 8
        assert body["peer_median"] > 0
        assert body["confidence"] > 0

    def test_example_produces_a_flagged_verdict(self, sample_client: TestClient) -> None:
        """A reviewer's first Swagger request must return something dramatic.

        A real-but-unremarkable example once returned "within peer range" here:
        correct, and a poor first impression. The documented example is now the
        dataset's highest-excess claim, and this test keeps it that way.
        """
        example = ScoreRequest.model_config["json_schema_extra"]["examples"][0]
        body = sample_client.post("/score", json=example).json()
        assert body["would_flag"] is True, body
        assert body["reason"].startswith("high_markup")
        assert body["markup_ratio"] > 50          # 53062.36 / 953.09
        assert body["relative_excess"] > 5        # ~7.5x the peer median
        assert body["markup_ratio"] > body["peer_median"]


class TestReadmeClaims:
    """Numbers and identifiers quoted in the README must be real."""

    def test_readme_exists_and_is_substantive(self) -> None:
        assert README.exists()
        assert len(README.read_text()) > 2000

    def test_row_counts_quoted_in_the_readme_are_real(self) -> None:
        """The README quotes 197,283 rows / 573 DRGs / 3,208 providers."""
        text = README.read_text()
        assert "197,283" in text
        assert "573" in text
        assert "3,208" in text

    @pytest.mark.parametrize(
        "identifier",
        [
            "470 - MAJOR JOINT REPLACEMENT W/O CC/MCC",  # the invented one
        ],
    )
    def test_no_fabricated_drg_strings_in_docs(self, identifier: str) -> None:
        """Guards against the exact string that caused this test file to exist."""
        assert identifier not in README.read_text()
        schemas_path = REPO_ROOT / "src" / "claimsight" / "api" / "schemas.py"
        assert identifier not in schemas_path.read_text()

    def test_drg_codes_quoted_in_readme_exist_in_the_sample(
        self, all_drg_codes: set[str]
    ) -> None:
        """Any ``NNN - ...`` DRG quoted in the README must be a real DRG code."""
        text = README.read_text()
        quoted = set(re.findall(r"\b(\d{3}) - [A-Z][A-Za-z0-9 &,/'\.\-()]*", text))
        if not quoted:
            pytest.skip("README quotes no DRG identifiers")
        for code in quoted:
            assert code in all_drg_codes, (
                f"README quotes DRG code {code!r}, which does not appear among "
                f"the {len(all_drg_codes)} DRGs in the committed sample"
            )


class TestFixtureIntegrity:
    """The hand-built fixture must stay hand-built and honest."""

    FIXTURE = REPO_ROOT / "tests" / "fixtures" / "tiny_claims.csv"

    def test_fixture_uses_real_drg_strings(self) -> None:
        """Synthetic rows, real vocabulary: a reviewer should not find invented
        DRG names anywhere in the repository."""
        text = self.FIXTURE.read_text()
        assert "MAJOR JOINT REPLACEMENT OR REATTACHMENT OF LOWER EXTREMITY" in text
        assert "HEART FAILURE & SHOCK W MCC" in text

    def test_fixture_preserves_the_dirty_data_traps(self) -> None:
        text = self.FIXTURE.read_text()
        assert '"1,134"' in text          # thousands separator in a count
        assert "010033" in text           # zero-padded provider id
        assert "02115" in text            # zero-padded ZIP
        assert "$1,016,806.46" in text    # currency-formatted money
        assert ",N/A" in text             # corrupt value that must be rejected


class TestReadmeTestCounts:
    """The README quotes test counts; a stale count is a small lie in a document
    whose entire purpose is accuracy.

    Collected with ``pytest --collect-only`` in a subprocess rather than counted by
    hand or by AST: parametrised tests expand, so only the collector knows the real
    number. If a future pytest changes its output format, these tests skip with the
    unparsed text rather than passing vacuously or failing spuriously.
    """

    @pytest.fixture(scope="class")
    def collected(self) -> dict[str, int]:
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=600,
            check=False,
        )
        per_file = {
            Path(match[0]).stem: int(match[1])
            for match in re.findall(r"(tests/test_\w+\.py):\s*(\d+)", result.stdout)
        }
        if per_file:
            return per_file
        total = re.search(r"(\d+) tests? collected", result.stdout)
        if total:
            return {"__total__": int(total.group(1))}
        pytest.skip(f"unrecognised --collect-only output: {result.stdout[-400:]!r}")

    def _total(self, collected: dict[str, int]) -> int:
        return collected.get("__total__") or sum(collected.values())

    def test_headline_count_matches_the_suite(self, collected: dict[str, int]) -> None:
        quoted = {int(n) for n in re.findall(r"(\d{2,4}) tests", README.read_text())}
        assert quoted, "README quotes no test count at all"
        assert quoted == {self._total(collected)}, (
            f"README says {sorted(quoted)} tests; the suite collects {self._total(collected)}"
        )

    def test_per_suite_counts_match(self, collected: dict[str, int]) -> None:
        if "__total__" in collected:
            pytest.skip("collector reported only a total, not per-file counts")
        rows = dict(re.findall(r"\| `(test_\w+)` \| (\d+) \|", README.read_text()))
        assert rows, "README has no per-suite test table"
        for suite, count in rows.items():
            assert suite in collected, f"README lists {suite}, which does not exist"
            assert int(count) == collected[suite], (
                f"{suite}: README says {count}, collection says {collected[suite]}"
            )

    def test_every_suite_appears_in_the_readme(self, collected: dict[str, int]) -> None:
        """Adding a test file without documenting it should fail, not slip through."""
        if "__total__" in collected:
            pytest.skip("collector reported only a total, not per-file counts")
        documented = set(re.findall(r"\| `(test_\w+)` \| \d+ \|", README.read_text()))
        assert documented == set(collected), (
            f"undocumented: {sorted(set(collected) - documented)}; "
            f"documented but missing: {sorted(documented - set(collected))}"
        )
