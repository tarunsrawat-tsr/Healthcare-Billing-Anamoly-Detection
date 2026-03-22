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

    def test_example_produces_an_interesting_verdict(self, sample_client: TestClient) -> None:
        """A demo example that flags nothing is a poor demo."""
        example = ScoreRequest.model_config["json_schema_extra"]["examples"][0]
        body = sample_client.post("/score", json=example).json()
        assert body["markup_ratio"] > body["peer_median"]
        assert body["relative_excess"] > 1.0


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
