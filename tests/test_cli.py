"""CLI tests.

The CLI is the interface a reviewer actually touches first (``make report``), so
it gets the same coverage as the library. Tests run against the tiny fixture with
the Isolation Forest disabled to keep them fast; assertions check that real
numbers reach stdout rather than just that the command exited 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest
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
        result = runner.invoke(app, ["report", str(TINY_CLAIMS), "--no-forest", "--top", "5"])
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
                "report",
                str(TINY_CLAIMS),
                "--no-forest",
                "--z-threshold",
                "1000",
                "--json",
                str(out),
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


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI's artifact registry at a scratch directory."""
    directory = tmp_path / "artifacts"
    monkeypatch.setenv("CLAIMSIGHT_ARTIFACTS_DIR", str(directory))
    return directory


def _save_baseline(registry: Path) -> str:
    """Save one run against the tiny fixture and return its run id."""
    result = runner.invoke(app, ["report", str(TINY_CLAIMS), "--no-forest", "--save"])
    assert result.exit_code == 0, result.stdout
    run_ids = [p.name for p in (registry / "runs").iterdir() if p.is_dir()]
    assert len(run_ids) == 1
    return run_ids[0]


class TestSaveAndRuns:
    def test_report_save_persists_a_run(self, registry: Path) -> None:
        run_id = _save_baseline(registry)
        run_dir = registry / "runs" / run_id
        assert (run_dir / "manifest.json").is_file()
        assert (run_dir / "outliers.parquet").is_file()

    def test_save_prints_the_reproduction_command(self, registry: Path) -> None:
        """The point of saving is being able to compare later; say so."""
        result = runner.invoke(app, ["report", str(TINY_CLAIMS), "--no-forest", "--save"])
        assert "saved run" in result.stdout
        assert "claimsight drift" in result.stdout

    def test_report_without_save_writes_nothing(self, registry: Path) -> None:
        result = runner.invoke(app, ["report", str(TINY_CLAIMS), "--no-forest"])
        assert result.exit_code == 0
        assert not (registry / "runs").exists()

    def test_runs_advises_when_the_registry_is_empty(self, registry: Path) -> None:
        result = runner.invoke(app, ["runs"])
        assert result.exit_code == 0
        assert "no saved runs yet" in result.stdout

    def test_runs_lists_saved_runs(self, registry: Path) -> None:
        run_id = _save_baseline(registry)
        result = runner.invoke(app, ["runs"])
        assert result.exit_code == 0
        assert "Saved runs (1)" in result.stdout
        # Rich truncates narrow columns, so assert the stable prefix.
        assert run_id[:8] in result.stdout

    def test_runs_shows_one_manifest_in_detail(self, registry: Path) -> None:
        run_id = _save_baseline(registry)
        result = runner.invoke(app, ["runs", run_id])
        assert result.exit_code == 0
        assert "source sha256" in result.stdout
        assert "duckdb" in result.stdout

    def test_runs_json_dumps_the_manifest(self, registry: Path) -> None:
        run_id = _save_baseline(registry)
        result = runner.invoke(app, ["runs", run_id, "--json"])
        assert result.exit_code == 0
        assert '"distribution"' in result.stdout

    def test_runs_unknown_id_fails_loudly(self, registry: Path) -> None:
        _save_baseline(registry)
        result = runner.invoke(app, ["runs", "19990101T000000Z"])
        assert result.exit_code != 0


class TestDriftCommand:
    def test_no_baseline_exits_with_a_distinct_code(self, registry: Path) -> None:
        """Exit 2 (nothing to compare) vs 1 (drift found) vs 0 (stable)."""
        result = runner.invoke(app, ["drift", str(TINY_CLAIMS)])
        assert result.exit_code == 2
        assert "no baseline run saved" in result.stdout

    def test_same_file_against_its_own_baseline_is_stable(self, registry: Path) -> None:
        _save_baseline(registry)
        result = runner.invoke(app, ["drift", str(TINY_CLAIMS)])
        assert result.exit_code == 0
        assert "distributions stable" in result.stdout
        assert "Population Stability Index" in result.stdout

    def test_baseline_can_be_named_explicitly(self, registry: Path) -> None:
        run_id = _save_baseline(registry)
        result = runner.invoke(app, ["drift", str(TINY_CLAIMS), "--baseline", run_id])
        assert result.exit_code == 0
        assert run_id in result.stdout

    def test_writes_a_machine_readable_report(self, registry: Path, tmp_path: Path) -> None:
        import json

        _save_baseline(registry)
        out = tmp_path / "drift.json"
        result = runner.invoke(app, ["drift", str(TINY_CLAIMS), "--json", str(out)])
        assert result.exit_code == 0
        payload = json.loads(out.read_text())
        assert payload["verdict"] == "no meaningful shift"
        assert payload["max_psi"] == pytest.approx(0.0, abs=1e-9)
        assert {f["feature"] for f in payload["features"]}

    def test_significant_drift_exits_nonzero(self, registry: Path, monkeypatch) -> None:
        """A drift gate is only a gate if it can fail the job.

        The report is stubbed rather than manufactured from a shifted CSV: PSI
        itself is covered exhaustively in test_monitor.py, and what needs testing
        here is the CLI's exit-code and message contract.
        """
        from claimsight import cli
        from claimsight.monitor import DriftReport, FeatureDrift

        _save_baseline(registry)
        stub = DriftReport(
            features=(
                FeatureDrift(
                    feature="markup_ratio",
                    psi=0.83,
                    baseline_median=5.0,
                    current_median=12.0,
                    baseline_null_rate=0.0,
                    current_null_rate=0.0,
                    rows=17,
                ),
            ),
            baseline_flag_rate_pct=3.5,
            current_flag_rate_pct=9.0,
            baseline_rows=17,
            current_rows=17,
        )
        monkeypatch.setattr(cli, "drift_against_run", lambda *a, **k: stub)
        result = runner.invoke(app, ["drift", str(TINY_CLAIMS)])
        assert result.exit_code == 1
        assert "significant drift" in result.stdout
        assert "markup_ratio" in result.stdout
