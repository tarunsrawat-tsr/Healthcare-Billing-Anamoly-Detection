"""Tests for drift monitoring (PSI).

The most important test in this file is
:meth:`TestPsi.test_zero_against_its_own_profile`. A drift metric that reports
movement when nothing moved is worse than no metric at all -- it trains people
to ignore the alarm. That test failed on the first implementation, because
``total_discharges`` is a small integer: several of its deciles land on the same
value, the duplicate edges collapse, and the surviving bins are no longer
equal-sized. The fix was to measure the baseline's bin proportions instead of
assuming they are uniform.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pytest

from claimsight.monitor import (
    DEFAULT_BINS,
    MODERATE_DRIFT,
    MONITORED_FEATURES,
    NO_DRIFT,
    DriftReport,
    compute_drift,
    distribution_profile,
    interpret_psi,
    population_stability_index,
)

BASELINE_N = 500
SEED = 20160917


def _make_relation(
    con: duckdb.DuckDBPyConnection,
    *,
    charge_multiplier: float = 1.0,
    n: int = BASELINE_N,
    seed: int = SEED,
    view: str = "claim_scores_final",
) -> duckdb.DuckDBPyConnection:
    """Build a stand-in scored relation.

    Only three of the monitored columns are created on purpose: the rest must be
    reported as absent rather than crashing the profiler.
    ``markup_ratio`` is continuous (deciles are distinct); ``total_discharges``
    is a small integer (deciles collide), which is the interesting case.
    """
    rng = np.random.default_rng(seed)
    markup = rng.lognormal(mean=1.5, sigma=0.6, size=n) * charge_multiplier
    # Heavy ties: 8 distinct values across 10 deciles, so edges must collapse.
    # Exaggerated relative to the real file, but not invented -- on FY2016 data
    # total_discharges is a low-count integer (median ~21, long left tail of 1s
    # and 2s) and its deciles do collide there too.
    discharges = rng.integers(5, 13, size=n).astype(int)
    flagged = markup > np.quantile(markup, 0.95)

    con.execute(
        f"""
        CREATE OR REPLACE TABLE {view} (
            markup_ratio DOUBLE,
            total_discharges INTEGER,
            flagged_peer BOOLEAN
        )
        """
    )
    con.executemany(
        f"INSERT INTO {view} VALUES (?, ?, ?)",
        [(float(m), int(d), bool(f)) for m, d, f in zip(markup, discharges, flagged, strict=True)],
    )
    return con


@pytest.fixture
def baseline_con() -> duckdb.DuckDBPyConnection:
    return _make_relation(duckdb.connect())


@pytest.fixture
def baseline_profile(baseline_con: duckdb.DuckDBPyConnection) -> dict:
    return distribution_profile(baseline_con)


class TestDistributionProfile:
    def test_profiles_only_columns_that_exist(self, baseline_profile: dict) -> None:
        assert set(baseline_profile["features"]) == {"markup_ratio", "total_discharges"}
        assert "avg_covered_charges" in MONITORED_FEATURES  # monitored, but absent here
        assert "avg_covered_charges" not in baseline_profile["features"]

    def test_records_bin_edges_and_proportions(self, baseline_profile: dict) -> None:
        spec = baseline_profile["features"]["markup_ratio"]
        assert len(spec["edges"]) == DEFAULT_BINS - 1
        assert len(spec["expected"]) == DEFAULT_BINS
        assert spec["edges"] == sorted(spec["edges"])
        assert sum(spec["expected"]) == pytest.approx(1.0)

    def test_tied_column_collapses_to_fewer_bins(self, baseline_profile: dict) -> None:
        """The condition that broke the uniform-expectation assumption."""
        spec = baseline_profile["features"]["total_discharges"]
        assert len(spec["edges"]) < DEFAULT_BINS - 1
        # Collapsed bins are unequal, so the measured proportions are not 1/k.
        assert max(spec["expected"]) > 1.0 / len(spec["expected"]) + 1e-9

    def test_records_headline_counts(self, baseline_con, baseline_profile: dict) -> None:
        assert baseline_profile["rows"] == BASELINE_N
        assert baseline_profile["flag_rate_pct"] == pytest.approx(5.0, abs=0.01)
        assert baseline_profile["view"] == "claim_scores_final"

    def test_refuses_to_bin_a_handful_of_rows(self, con) -> None:
        """Fewer rows than bins means quantiles are noise, not a baseline."""
        _make_relation(con, n=4)
        profile = distribution_profile(con)
        assert profile["features"]["markup_ratio"]["edges"] == []
        assert profile["features"]["markup_ratio"]["median"] is not None

    def test_null_rate_is_measured(self, con) -> None:
        _make_relation(con)
        con.execute("UPDATE claim_scores_final SET markup_ratio = NULL WHERE rowid <= 50")
        profile = distribution_profile(con)
        assert profile["features"]["markup_ratio"]["null_rate"] == pytest.approx(0.1, abs=0.01)


class TestPsi:
    def test_zero_against_its_own_profile(self, baseline_con, baseline_profile: dict) -> None:
        """Regression: PSI(x, x) must be 0 for every column, tied or not."""
        values = np.asarray(
            [
                row[0]
                for row in baseline_con.sql(
                    "SELECT markup_ratio FROM claim_scores_final"
                ).fetchall()
            ]
        )
        spec = baseline_profile["features"]["markup_ratio"]
        psi = population_stability_index(spec["edges"], values, expected=spec["expected"])
        assert psi == pytest.approx(0.0, abs=1e-9)

    def test_zero_against_own_profile_for_tied_column(
        self, baseline_con, baseline_profile: dict
    ) -> None:
        """The tied-integer case that exposed the uniform-expectation bug."""
        values = np.asarray(
            [
                row[0]
                for row in baseline_con.sql(
                    "SELECT total_discharges FROM claim_scores_final"
                ).fetchall()
            ],
            dtype=float,
        )
        spec = baseline_profile["features"]["total_discharges"]
        measured = population_stability_index(spec["edges"], values, expected=spec["expected"])
        assumed_uniform = population_stability_index(spec["edges"], values)
        assert measured == pytest.approx(0.0, abs=1e-9)
        assert assumed_uniform > 0.01  # and this is why expected is measured

    def test_detects_a_real_shift(self, baseline_profile: dict) -> None:
        shifted = np.asarray(baseline_profile["features"]["markup_ratio"]["edges"]) * 3
        psi = population_stability_index(
            baseline_profile["features"]["markup_ratio"]["edges"],
            shifted + 1e6,
            expected=baseline_profile["features"]["markup_ratio"]["expected"],
        )
        assert psi > MODERATE_DRIFT

    def test_same_distribution_new_sample_is_quiet(self, baseline_profile: dict) -> None:
        con = _make_relation(duckdb.connect(), seed=SEED + 1)
        values = np.asarray(
            [row[0] for row in con.sql("SELECT markup_ratio FROM claim_scores_final").fetchall()]
        )
        spec = baseline_profile["features"]["markup_ratio"]
        psi = population_stability_index(spec["edges"], values, expected=spec["expected"])
        assert psi < NO_DRIFT

    def test_stays_finite_when_a_bin_empties(self, baseline_profile: dict) -> None:
        """A vacated bin should score high, not produce inf or nan."""
        spec = baseline_profile["features"]["markup_ratio"]
        psi = population_stability_index(spec["edges"], np.array([-1e9]), expected=spec["expected"])
        assert np.isfinite(psi)
        assert psi > MODERATE_DRIFT

    def test_empty_current_is_finite(self, baseline_profile: dict) -> None:
        spec = baseline_profile["features"]["markup_ratio"]
        psi = population_stability_index(spec["edges"], np.array([]), expected=spec["expected"])
        assert np.isfinite(psi)
        assert psi > 0.0

    def test_requires_edges(self) -> None:
        with pytest.raises(ValueError, match="no bin edges"):
            population_stability_index([], np.array([1.0, 2.0]))

    def test_rejects_mismatched_expected_length(self, baseline_profile: dict) -> None:
        spec = baseline_profile["features"]["markup_ratio"]
        with pytest.raises(ValueError, match="baseline proportions"):
            population_stability_index(spec["edges"], np.array([1.0]), expected=[0.5, 0.5])


class TestInterpretation:
    @pytest.mark.parametrize(
        ("psi", "expected"),
        [
            (0.0, "no meaningful shift"),
            (0.099, "no meaningful shift"),
            (0.10, "moderate shift"),
            (0.249, "moderate shift"),
            (0.25, "significant shift"),
            (9.0, "significant shift"),
        ],
    )
    def test_bands_are_the_conventional_ones(self, psi: float, expected: str) -> None:
        assert interpret_psi(psi) == expected


class TestComputeDrift:
    def test_no_drift_against_itself(self, baseline_con, baseline_profile: dict) -> None:
        report = compute_drift(baseline_profile, baseline_con)
        assert report.verdict == "no meaningful shift"
        assert report.max_psi == pytest.approx(0.0, abs=1e-9)
        assert {f.feature for f in report.features} == {"markup_ratio", "total_discharges"}
        assert report.current_rows == BASELINE_N

    def test_significant_drift_on_a_shifted_file(self, baseline_profile: dict) -> None:
        con = _make_relation(duckdb.connect(), charge_multiplier=4.0, seed=SEED + 7)
        report = compute_drift(baseline_profile, con)
        assert report.verdict == "significant shift"
        assert report.worst_feature == "markup_ratio"
        by_name = {f.feature: f for f in report.features}
        assert by_name["markup_ratio"].psi > MODERATE_DRIFT
        # The untouched column must stay quiet, or the metric is not localized.
        assert by_name["total_discharges"].psi < NO_DRIFT

    def test_reports_median_movement_and_null_rates(
        self, baseline_con, baseline_profile: dict
    ) -> None:
        report = compute_drift(baseline_profile, baseline_con)
        entry = next(f for f in report.features if f.feature == "markup_ratio")
        assert entry.current_median == pytest.approx(entry.baseline_median)
        assert entry.baseline_null_rate == 0.0
        assert entry.current_null_rate == 0.0
        assert entry.interpretation == "no meaningful shift"

    def test_skips_features_the_baseline_never_profiled(self, baseline_con) -> None:
        """An old manifest must not silently report 'no drift' for new columns."""
        report = compute_drift({"features": {}}, baseline_con)
        assert report.features == ()
        assert report.worst_feature is None

    def test_skips_baseline_rows_too_small_to_bin(self, con) -> None:
        _make_relation(con, n=4)
        profile = distribution_profile(con)
        report = compute_drift(profile, con)
        assert report.features == ()

    def test_flag_rate_delta_in_percentage_points(self, baseline_profile: dict) -> None:
        con = _make_relation(duckdb.connect(), seed=SEED + 3)
        report = compute_drift(baseline_profile, con)
        assert report.flag_rate_delta == pytest.approx(
            report.current_flag_rate_pct - report.baseline_flag_rate_pct, abs=1e-9
        )

    def test_serialises_to_json_safe_dict(self, baseline_con, baseline_profile: dict) -> None:
        import json

        payload = json.dumps(compute_drift(baseline_profile, baseline_con).as_dict())
        assert "verdict" in payload
        assert "features" in payload


class TestDriftReportShape:
    def test_empty_report_is_quiet_not_crashing(self) -> None:
        report = DriftReport(
            features=(),
            baseline_flag_rate_pct=None,
            current_flag_rate_pct=None,
            baseline_rows=None,
            current_rows=0,
        )
        assert report.max_psi == 0.0
        assert report.worst_feature is None
        assert report.verdict == "no meaningful shift"
        assert report.flag_rate_delta is None
        assert report.as_dict()["features"] == []
