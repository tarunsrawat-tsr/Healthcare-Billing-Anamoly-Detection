"""CLI tests.

The CLI is the interface a reviewer actually touches first (``make report``), so
it gets the same coverage as the library. Tests run against the tiny fixture with
the Isolation Forest disabled to keep them fast; assertions check that real
numbers reach stdout rather than just that the command exited 0.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import OUTLIER_PROVIDER, TINY_CLAIMS
from typer.testing import CliRunner

from claimsight.cli import app

runner = CliRunner()


class TestVersion:
    def test_prints_a_version(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "claimsight" in result.stdout


class TestIngest:
    def test_reports_data_quality(self) -> None:
        result = runner.invoke(app, ["ingest", str(TINY_CLAIMS)])
        assert result.exit_code == 0
        assert '"rows_read": 18' in result.stdout
        assert '"rows_usable": 17' in result.stdout
        assert '"rows_rejected": 1' in result.stdout

    def test_warns_about_rejected_rows(self) -> None:
        result = runner.invoke(app, ["ingest", str(TINY_CLAIMS)])
        assert "rejected" in result.stdout

    def test_writes_parquet_when_asked(self, tmp_path: Path) -> None:
        out = tmp_path / "claims.parquet"
        result = runner.invoke(app, ["ingest", str(TINY_CLAIMS), "--parquet", str(out)])
        assert result.exit_code == 0
        assert out.exists()

    def test_missing_file_fails_clearly(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["ingest", str(tmp_path / "nope.csv")])
        assert result.exit_code != 0


class TestPeers:
    def test_prints_hierarchy_usage(self) -> None:
        result = runner.invoke(app, ["peers", str(TINY_CLAIMS)])
        assert result.exit_code == 0
        assert "drg_hrr" in result.stdout
        assert "drg_national" in result.stdout

    def test_min_support_is_honoured(self) -> None:
        """Raising support past the fixture's group size forces the fallback."""
        result = runner.invoke(app, ["peers", str(TINY_CLAIMS), "--min-support", "10"])
        assert result.exit_code == 0
        assert "drg_hrr" not in result.stdout


class TestReport:
    def test_prints_summary_and_top_claims(self) -> None:
        result = runner.invoke(
            app, ["report", str(TINY_CLAIMS), "--no-forest", "--top", "5"]
        )
        assert result.exit_code == 0
        assert "rows scored" in result.stdout
        assert OUTLIER_PROVIDER in result.stdout or "OUTLIER MEMORIAL" in result.stdout

    def test_writes_json_diagnostics(self, tmp_path: Path) -> None:
        import json

        out = tmp_path / "nested" / "report.json"
        result = runner.invoke(
            app,
            ["report", str(TINY_CLAIMS), "--no-forest", "--json", str(out)],
        )
        assert result.exit_code == 0
        assert out.exists()
        payload = json.loads(out.read_text())
        assert "ingest" in payload
        assert payload["ingest"]["rows_read"] == 18

    def test_thresholds_are_configurable(self, tmp_path: Path) -> None:
        """A threshold so high that nothing can flag must still run cleanly.

        Asserted against the JSON diagnostics rather than the rendered table:
        parsing a rich-formatted console table is brittle and would pass for the
        wrong reasons.
        """
        import json

        out = tmp_path / "strict.json"
        result = runner.invoke(
            app,
            [
                "report", str(TINY_CLAIMS), "--no-forest",
                "--z-threshold", "1000", "--json", str(out),
            ],
        )
        assert result.exit_code == 0
        assert json.loads(out.read_text())["summary"]["flagged_peer"] == 0


class TestFairness:
    def test_runs_and_reports_volume_bands(self) -> None:
        # fairness always skips the forest: it reports flag rates, not scores.
        result = runner.invoke(app, ["fairness", str(TINY_CLAIMS)])
        assert result.exit_code == 0
        assert "volume_band" in result.stdout or "low_volume" in result.stdout
