"""Tests for the artifact registry (provenance, review queue, drift hook-up).

The registry's job is not storage, it is *answerability*: given a number in a
report, can you say which file, which config, which library versions and which
day produced it? These tests assert the manifest actually contains that, that
the review queue holds only flagged rows, and that saving is deterministic --
which is the claim that justifies not persisting a pickled model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import pytest
from tests.conftest import TINY_CLAIMS

from claimsight.config import Settings
from claimsight.monitor import MONITORED_FEATURES
from claimsight.registry import (
    FINAL_VIEW,
    MANIFEST_FILE,
    REVIEW_COLUMNS,
    RunRecord,
    compare_runs,
    drift_against_run,
    latest_run,
    list_runs,
    load_run,
    save_run,
)
from claimsight.scoring import SCORED_VIEW, ScoringConfig


@pytest.fixture
def final_con(scored: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Extend the scored fixture to the full view contract, forest disabled.

    Mirrors what the CLI does when ``--no-forest`` is passed: downstream code
    always reads ``claim_scores_final``, so skipping the forest must not change
    the shape of the output.
    """
    scored.execute(
        f"""
        CREATE OR REPLACE VIEW {FINAL_VIEW} AS
        SELECT *,
               NULL::DOUBLE AS iforest_score,
               NULL::DOUBLE AS iforest_percentile,
               false        AS iforest_flag,
               false        AS flagged_both
        FROM {SCORED_VIEW}_flagged
        """
    )
    return scored


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Point the registry at a scratch directory so tests never touch artifacts/."""
    return Settings(artifacts_dir=tmp_path / "artifacts")


def _save(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    *,
    run_id: str,
    config: ScoringConfig | None = None,
    source: Path = TINY_CLAIMS,
) -> RunRecord:
    return save_run(
        con,
        config or ScoringConfig(),
        source=source,
        settings=settings,
        run_id=run_id,
        elapsed_seconds=1.5,
    )


class TestSaveRun:
    def test_writes_manifest_and_review_queue(
        self, final_con, settings: Settings, tmp_path: Path
    ) -> None:
        record = _save(final_con, settings, run_id="20260101T000000Z")
        assert record.path == tmp_path / "artifacts" / "runs" / "20260101T000000Z"
        assert (record.path / MANIFEST_FILE).is_file()
        assert record.outliers_path.is_file()
        assert record.run_id == "20260101T000000Z"

    def test_manifest_records_provenance(self, final_con, settings: Settings) -> None:
        record = _save(final_con, settings, run_id="20260101T000000Z")
        manifest = record.manifest
        assert manifest["source"]["name"] == TINY_CLAIMS.name
        assert manifest["source"]["sha256"] == hashlib.sha256(TINY_CLAIMS.read_bytes()).hexdigest()
        assert manifest["source"]["bytes"] == TINY_CLAIMS.stat().st_size
        assert manifest["schema_version"] == 1
        assert manifest["created_at"].endswith("+00:00")
        # Versions that could silently change a result.
        for key in ("claimsight", "python", "duckdb", "numpy", "scikit-learn"):
            assert manifest["environment"][key]

    def test_manifest_records_the_config_actually_used(self, final_con, settings: Settings) -> None:
        cfg = ScoringConfig(z_threshold=4.0, confidence_floor=0.3)
        record = _save(final_con, settings, run_id="20260101T000000Z", config=cfg)
        assert record.manifest["config"]["z_threshold"] == 4.0
        assert record.manifest["config"]["confidence_floor"] == 0.3
        assert record.manifest["config"]["min_support"] == 8
        # Sequence fields must survive JSON as lists, not repr'd objects.
        assert list(record.manifest["config"]["iforest_features"]) == list(cfg.iforest_features)

    def test_manifest_metrics_match_the_relation(self, final_con, settings: Settings) -> None:
        record = _save(final_con, settings, run_id="20260101T000000Z")
        expected_rows = final_con.sql(f"SELECT count(*) FROM {FINAL_VIEW}").fetchone()[0]
        expected_flags = final_con.sql(
            f"SELECT count(*) FROM {FINAL_VIEW} WHERE flagged_peer"
        ).fetchone()[0]
        assert record.manifest["metrics"]["rows_scored"] == expected_rows
        assert record.manifest["metrics"]["flagged_peer"] == expected_flags
        assert record.rows_flagged == expected_flags
        assert record.manifest["review_queue"]["rows"] == expected_flags
        assert record.manifest["elapsed_seconds"] == 1.5

    def test_manifest_carries_a_distribution_profile_for_drift(
        self, final_con, settings: Settings
    ) -> None:
        """Without this, next month's drift check has nothing to compare to."""
        record = _save(final_con, settings, run_id="20260101T000000Z")
        profile = record.manifest["distribution"]
        assert profile["rows"] == record.manifest["metrics"]["rows_scored"]
        assert profile["bins"] == 10
        assert set(profile["features"]) <= set(MONITORED_FEATURES)
        assert profile["features"], "expected at least one profiled feature"
        for spec in profile["features"].values():
            assert "edges" in spec and "expected" in spec
            if spec["edges"]:
                assert len(spec["expected"]) == len(spec["edges"]) + 1

    def test_documents_that_no_model_is_pickled(self, final_con, settings: Settings) -> None:
        record = _save(final_con, settings, run_id="20260101T000000Z")
        assert record.manifest["reproducibility"]["deterministic"] is True
        assert "random_state" in record.manifest["reproducibility"]["note"]
        assert not list(record.path.glob("*.pkl"))
        assert not list(record.path.glob("*.joblib"))

    def test_review_queue_holds_only_flagged_rows_worst_first(
        self, final_con, settings: Settings
    ) -> None:
        record = _save(final_con, settings, run_id="20260101T000000Z")
        queue = (
            duckdb.connect().sql(f"SELECT * FROM read_parquet('{record.outliers_path}')").fetchdf()
        )
        assert len(queue) == record.rows_flagged
        assert queue["flagged_peer"].all()
        excess = list(queue["relative_excess"])
        assert excess == sorted(excess, reverse=True)
        # Columns a human reviewer needs, and no absent ones silently dropped.
        for column in ("provider_id", "provider_name", "drg", "peer_level", "flag_reason"):
            assert column in queue.columns

    def test_manifest_is_valid_json_on_disk(self, final_con, settings: Settings) -> None:
        record = _save(final_con, settings, run_id="20260101T000000Z")
        on_disk = json.loads((record.path / MANIFEST_FILE).read_text())
        assert on_disk["run_id"] == record.run_id
        assert on_disk["metrics"] == record.manifest["metrics"]

    def test_omits_forest_columns_it_cannot_write(self, final_con, settings: Settings) -> None:
        """iforest_* exist here as NULLs, so they are written -- but the manifest
        must always state which review columns were dropped, never imply more."""
        record = _save(final_con, settings, run_id="20260101T000000Z")
        written = set(record.manifest["review_queue"]["columns"])
        assert written <= set(REVIEW_COLUMNS)
        assert "iforest_flag" in written
        assert set(record.manifest["review_queue"]["omitted_columns"]).isdisjoint(written)


class TestListAndLoad:
    def test_empty_registry_returns_nothing(self, settings: Settings) -> None:
        assert list_runs(settings) == []
        assert latest_run(settings) is None

    def test_lists_newest_first(self, final_con, settings: Settings) -> None:
        _save(final_con, settings, run_id="20260101T000000Z")
        _save(final_con, settings, run_id="20260301T000000Z")
        _save(final_con, settings, run_id="20260201T000000Z")
        ids = [r.run_id for r in list_runs(settings)]
        assert ids == ["20260301T000000Z", "20260201T000000Z", "20260101T000000Z"]
        assert latest_run(settings).run_id == "20260301T000000Z"

    def test_load_by_id_round_trips_the_manifest(self, final_con, settings: Settings) -> None:
        saved = _save(final_con, settings, run_id="20260101T000000Z")
        loaded = load_run("20260101T000000Z", settings)
        assert loaded.manifest["metrics"] == saved.manifest["metrics"]
        assert loaded.created_at == saved.created_at

    def test_unknown_run_id_lists_what_exists(self, final_con, settings: Settings) -> None:
        _save(final_con, settings, run_id="20260101T000000Z")
        with pytest.raises(KeyError, match="20260101T000000Z"):
            load_run("20991231T235959Z", settings)

    def test_skips_a_corrupt_manifest(self, final_con, settings: Settings) -> None:
        """A half-written file must not take the whole registry down with it."""
        good = _save(final_con, settings, run_id="20260101T000000Z")
        broken = good.path.parent / "20260202T000000Z"
        broken.mkdir(parents=True)
        (broken / MANIFEST_FILE).write_text("{not json")
        assert [r.run_id for r in list_runs(settings)] == ["20260101T000000Z"]

    def test_ignores_directories_without_a_manifest(self, final_con, settings: Settings) -> None:
        record = _save(final_con, settings, run_id="20260101T000000Z")
        (record.path.parent / "scratch").mkdir()
        assert [r.run_id for r in list_runs(settings)] == ["20260101T000000Z"]


class TestCompareRuns:
    def test_detects_identical_input_and_config(self, final_con, settings: Settings) -> None:
        first = _save(final_con, settings, run_id="20260101T000000Z")
        second = _save(final_con, settings, run_id="20260201T000000Z")
        diff = compare_runs(first, second)
        assert diff["same_input"] is True
        assert diff["config_changed"] is False
        assert diff["baseline_run"] == "20260101T000000Z"
        assert diff["current_run"] == "20260201T000000Z"

    def test_detects_a_config_change(self, final_con, settings: Settings) -> None:
        first = _save(final_con, settings, run_id="20260101T000000Z")
        second = _save(
            final_con,
            settings,
            run_id="20260201T000000Z",
            config=ScoringConfig(z_threshold=5.0),
        )
        assert compare_runs(first, second)["config_changed"] is True

    def test_detects_a_different_input_file(self, final_con, settings: Settings, tmp_path) -> None:
        other = tmp_path / "other_claims.csv"
        other.write_bytes(TINY_CLAIMS.read_bytes() + b"\n")
        first = _save(final_con, settings, run_id="20260101T000000Z")
        second = _save(final_con, settings, run_id="20260201T000000Z", source=other)
        assert compare_runs(first, second)["same_input"] is False

    def test_reports_deltas_for_numeric_metrics(self, final_con, settings: Settings) -> None:
        first = _save(final_con, settings, run_id="20260101T000000Z")
        second = _save(
            final_con, settings, run_id="20260201T000000Z", config=ScoringConfig(z_threshold=9.0)
        )
        changes = compare_runs(first, second)["metric_changes"]
        assert "flagged_peer" in changes
        entry = changes["flagged_peer"]
        assert entry["delta"] == pytest.approx(entry["current"] - entry["baseline"])
        # Non-numeric entries (flag_reasons) are skipped rather than crashing.
        assert "flag_reasons" not in changes


class TestDeterminism:
    def test_saving_the_same_input_twice_reproduces_the_manifest(
        self, final_con, settings: Settings
    ) -> None:
        """The claim that justifies storing no model artifact."""
        first = _save(final_con, settings, run_id="20260101T000000Z")
        second = _save(final_con, settings, run_id="20260101T000001Z")
        for key in ("metrics", "distribution", "peer_levels", "source", "config"):
            assert first.manifest[key] == second.manifest[key], key

    def test_review_queue_bytes_are_stable(self, final_con, settings: Settings) -> None:
        first = _save(final_con, settings, run_id="20260101T000000Z")
        second = _save(final_con, settings, run_id="20260101T000001Z")
        one = duckdb.connect().sql(f"SELECT * FROM '{first.outliers_path}'").fetchall()
        two = duckdb.connect().sql(f"SELECT * FROM '{second.outliers_path}'").fetchall()
        assert one == two


class TestDriftAgainstRun:
    def test_self_comparison_reports_no_drift(self, final_con, settings: Settings) -> None:
        """PSI of a run against itself is exactly zero, or the metric lies."""
        _save(final_con, settings, run_id="20260101T000000Z")
        report = drift_against_run("20260101T000000Z", final_con, settings=settings)
        assert report.verdict == "no meaningful shift"
        assert report.max_psi == pytest.approx(0.0, abs=1e-9)
        assert report.baseline_rows == report.current_rows

    def test_accepts_a_record_as_well_as_an_id(self, final_con, settings: Settings) -> None:
        record = _save(final_con, settings, run_id="20260101T000000Z")
        by_record = drift_against_run(record, final_con)
        by_id = drift_against_run("20260101T000000Z", final_con, settings=settings)
        assert by_record.max_psi == by_id.max_psi

    def test_rejects_a_manifest_without_a_profile(self, final_con, settings: Settings) -> None:
        """Silently reporting 'no drift' for a comparison that never ran is the
        failure mode worth an exception."""
        record = _save(final_con, settings, run_id="20260101T000000Z")
        manifest = json.loads((record.path / MANIFEST_FILE).read_text())
        del manifest["distribution"]
        (record.path / MANIFEST_FILE).write_text(json.dumps(manifest))
        with pytest.raises(ValueError, match="no distribution profile"):
            drift_against_run("20260101T000000Z", final_con, settings=settings)

    def test_unknown_baseline_raises_keyerror(self, final_con, settings: Settings) -> None:
        _save(final_con, settings, run_id="20260101T000000Z")
        with pytest.raises(KeyError):
            drift_against_run("19990101T000000Z", final_con, settings=settings)

    def test_detects_a_distribution_that_moved(self, final_con, settings: Settings) -> None:
        """Charge every row 10x and the monitor must notice."""
        _save(final_con, settings, run_id="20260101T000000Z")
        final_con.execute(
            f"""
            CREATE OR REPLACE VIEW drifted AS
            SELECT * REPLACE (avg_covered_charges * 10 AS avg_covered_charges,
                              markup_ratio * 10 AS markup_ratio)
            FROM {FINAL_VIEW}
            """
        )
        report = drift_against_run("20260101T000000Z", final_con, view="drifted", settings=settings)
        by_name = {f.feature: f for f in report.features}
        assert by_name["markup_ratio"].psi > 0.25
        assert report.verdict == "significant shift"
