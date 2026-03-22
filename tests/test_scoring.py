"""Tests for outlier scoring.

Two things are pinned here beyond ordinary correctness:

* the **pure-Python twins** (``robust_z``, ``confidence``) must agree with the
  SQL that actually runs at scale -- otherwise the API layer and the batch
  pipeline would disagree about the same claim;
* **degenerate dispersion must be reported, not papered over** -- a peer group
  with zero MAD cannot produce a meaningful z-score, and pretending otherwise
  is how silent false positives happen.
"""

from __future__ import annotations

import pytest
from tests.conftest import (
    CORRUPT_PROVIDER,
    DRG_B_UNIFORM,
    DRG_C_SINGLETON,
    OUTLIER_PROVIDER,
)

from claimsight.scoring import (
    CLAIM_KEY,
    MAD_SCALE,
    ScoringConfig,
    add_isolation_forest,
    confidence,
    robust_z,
    score_claims,
    summarise,
)

VIEW = "claim_scores_flagged"


def _row(con, provider_id: str) -> dict:
    cols = [
        "robust_z",
        "peer_median",
        "peer_mad",
        "peer_n",
        "confidence",
        "flagged_peer",
        "flag_reason",
        "dispersion_degenerate",
        "markup_ratio",
        "peer_level",
    ]
    row = con.sql(
        f"SELECT {', '.join(cols)} FROM {VIEW} WHERE provider_id = '{provider_id}'"
    ).fetchone()
    assert row is not None, f"no scored row for {provider_id}"
    return dict(zip(cols, row, strict=True))


class TestOutlierDetection:
    def test_the_planted_outlier_is_flagged(self, scored) -> None:
        row = _row(scored, OUTLIER_PROVIDER)
        assert row["flagged_peer"] is True
        assert row["flag_reason"] == "high_markup"
        assert row["robust_z"] > ScoringConfig().z_threshold

    def test_peer_statistics_match_hand_computed_values(self, scored) -> None:
        """Group A markups are [4.2, 4.5, 4.5, 5.0, 5.5, 5.8, 6.0, 30.0].

        median = (5.0 + 5.5) / 2 = 5.25
        MAD    = median(|x - 5.25|) = 0.75

        Pinning these catches a regression in the two-pass median-of-deviations
        computation, which is easy to get subtly wrong.
        """
        row = _row(scored, OUTLIER_PROVIDER)
        assert row["peer_median"] == pytest.approx(5.25)
        assert row["peer_mad"] == pytest.approx(0.75)
        assert row["markup_ratio"] == pytest.approx(30.0)

    def test_z_is_scaled_by_the_mad_consistency_factor(self, scored) -> None:
        row = _row(scored, OUTLIER_PROVIDER)
        expected = (30.0 - 5.25) / (MAD_SCALE * 0.75)
        assert row["robust_z"] == pytest.approx(expected, rel=1e-6)

    def test_normal_rows_in_the_same_group_are_not_flagged(self, scored) -> None:
        for provider in ("010033", "010034", "010035", "010037", "010039"):
            row = _row(scored, provider)
            assert row["flagged_peer"] is False, provider
            assert row["flag_reason"] == "within_peer_range"
            assert abs(row["robust_z"]) < ScoringConfig().z_threshold


class TestDegenerateDispersion:
    def test_uniform_peer_group_is_marked_degenerate(self, scored) -> None:
        """DRG B bills an identical 5.0x markup everywhere: MAD = 0 and IQR = 0,
        so no dispersion exists to measure against."""
        row = _row(scored, "010041")
        assert row["dispersion_degenerate"] is True
        assert row["robust_z"] is None
        assert row["flagged_peer"] is False
        assert row["flag_reason"] == "degenerate_peers"

    def test_every_uniform_row_is_treated_the_same_way(self, scored) -> None:
        rows = scored.sql(
            f"""SELECT count(*) AS n,
                       sum(CASE WHEN dispersion_degenerate THEN 1 ELSE 0 END) AS degen,
                       sum(CASE WHEN flagged_peer THEN 1 ELSE 0 END) AS flagged
                FROM {VIEW} WHERE drg = '{DRG_B_UNIFORM}'"""
        ).fetchone()
        assert rows[0] == 8
        assert rows[1] == 8
        assert rows[2] == 0

    def test_singleton_group_is_degenerate_rather_than_anomalous(self, scored) -> None:
        """A lone row has MAD 0 and IQR 0 -- it must not be flagged as an outlier
        simply for having no peers."""
        row = scored.sql(
            f"""SELECT dispersion_degenerate, flagged_peer, flag_reason, peer_n
                FROM {VIEW} WHERE drg = '{DRG_C_SINGLETON}'"""
        ).fetchone()
        assert row[0] is True
        assert row[1] is False
        assert row[3] == 1


class TestDataQuality:
    def test_unusable_row_never_reaches_scoring(self, scored) -> None:
        count = scored.sql(
            f"SELECT count(*) FROM {VIEW} WHERE provider_id = '{CORRUPT_PROVIDER}'"
        ).fetchone()[0]
        assert count == 0

    def test_natural_key_is_unique_in_the_output(self, scored) -> None:
        row = scored.sql(
            f"""SELECT count(*) AS n,
                       count(DISTINCT ({CLAIM_KEY[0]}, {CLAIM_KEY[1]})) AS keys
                FROM {VIEW}"""
        ).fetchone()
        assert row[0] == row[1] == 17


class TestConfidence:
    def test_confidence_is_bounded_and_positive(self, scored) -> None:
        row = scored.sql(
            f"SELECT min(confidence) AS lo, max(confidence) AS hi FROM {VIEW}"
        ).fetchone()
        assert 0.0 < row[0] <= row[1] <= 1.0

    def test_confidence_rises_with_peer_support(self, scored) -> None:
        """More peers and more discharges must mean more confidence."""
        assert confidence(300, 500) > confidence(8, 500) > confidence(8, 20)

    def test_python_twin_matches_sql(self, scored) -> None:
        """The API layer uses the Python twin; the batch pipeline uses SQL."""
        row = _row(scored, OUTLIER_PROVIDER)
        discharges = scored.sql(
            f"SELECT total_discharges FROM {VIEW} WHERE provider_id = '{OUTLIER_PROVIDER}'"
        ).fetchone()[0]
        assert row["confidence"] == pytest.approx(
            confidence(int(row["peer_n"]), int(discharges)), rel=1e-9
        )

    def test_low_confidence_rows_are_not_flagged(self, scored) -> None:
        cfg = ScoringConfig(confidence_floor=0.99)
        score_claims(scored, cfg)
        flagged = scored.sql(
            f"SELECT count(*) FROM {VIEW} WHERE flagged_peer"
        ).fetchone()[0]
        assert flagged == 0
        reasons = {
            r[0]
            for r in scored.sql(
                f"SELECT DISTINCT flag_reason FROM {VIEW}"
            ).fetchall()
        }
        assert "low_confidence" in reasons


class TestPythonTwins:
    def test_robust_z_matches_the_definition(self) -> None:
        assert robust_z(30.0, 5.25, 0.75) == pytest.approx((30.0 - 5.25) / (MAD_SCALE * 0.75))

    def test_robust_z_returns_none_on_zero_dispersion(self) -> None:
        assert robust_z(30.0, 5.0, 0.0) is None

    def test_robust_z_is_signed(self) -> None:
        assert robust_z(4.2, 5.25, 0.75) < 0


class TestIsolationForest:
    def test_runs_and_is_deterministic(self, scored) -> None:
        """Same seed must give the same flags, or CI cannot assert stability."""
        cfg = ScoringConfig(random_state=42)
        first = add_isolation_forest(scored, cfg)
        flags_a = scored.sql(
            "SELECT provider_id, iforest_flag FROM claim_scores_final ORDER BY provider_id"
        ).fetchall()

        score_claims(scored, cfg)
        second = add_isolation_forest(scored, cfg)
        flags_b = scored.sql(
            "SELECT provider_id, iforest_flag FROM claim_scores_final ORDER BY provider_id"
        ).fetchall()

        assert flags_a == flags_b
        assert first["scored_rows"] == second["scored_rows"] == 17
        assert first["dropped_incomplete"] == 0

    def test_agreement_column_exists_and_is_conservative(self, scored) -> None:
        """``flagged_both`` requires agreement, so it is a subset of each method."""
        add_isolation_forest(scored, ScoringConfig())
        row = scored.sql(
            """SELECT sum(CASE WHEN flagged_both THEN 1 ELSE 0 END) AS both,
                      sum(CASE WHEN flagged_peer THEN 1 ELSE 0 END) AS peer,
                      sum(CASE WHEN iforest_flag THEN 1 ELSE 0 END) AS forest
               FROM claim_scores_final"""
        ).fetchone()
        assert row[0] <= row[1]
        assert row[0] <= row[2]


class TestSummarise:
    def test_summary_shape_and_arithmetic(self, scored) -> None:
        add_isolation_forest(scored, ScoringConfig())
        summary = summarise(scored)

        assert summary["rows_scored"] == 17
        assert summary["flagged_peer"] == 1  # only the planted outlier
        assert summary["distinct_providers_flagged_peer"] == 1
        # The forest's contamination rate on 17 rows flags nothing, so the
        # intersection is empty -- which is exactly why the two counts are
        # reported separately rather than as one ambiguous "flagged" number.
        assert summary["distinct_providers_flagged_both"] == 0
        assert summary["degenerate_dispersion_rows"] == 9  # 8 uniform + 1 singleton
        # Jaccard must be a valid overlap ratio.
        assert 0.0 <= summary["method_agreement_jaccard"] <= 1.0

    def test_percentages_are_consistent_with_counts(self, scored) -> None:
        add_isolation_forest(scored, ScoringConfig())
        s = summarise(scored)
        assert s["flagged_peer_pct"] == pytest.approx(
            100.0 * s["flagged_peer"] / s["rows_scored"], abs=1e-3
        )


class TestPracticalSignificanceGuards:
    """Regression tests for the artifact that the naive scorer produced.

    Ranking by raw robust z on the FY2016 file returned eight Washington DC
    hospitals at z = 134..192. DC has a single HRR and few hospitals, so its
    peer groups are small and homogeneous: MAD approaches zero, and dividing by
    it inflates z without bound. Those rows were statistically extreme and
    practically unremarkable. Two guards were added, and both are asserted here
    so they cannot silently become dead code.
    """

    def test_relative_excess_is_computed(self, scored) -> None:
        row = _row_with(scored, OUTLIER_PROVIDER, "relative_excess")
        # 30.0 markup against a 5.25 peer median.
        assert row == pytest.approx(30.0 / 5.25)

    def test_thin_dispersion_blocks_flagging(self, scored) -> None:
        """Group A has MAD/median = 0.75/5.25 = 0.143; a 0.2 floor rejects it."""
        cfg = ScoringConfig(min_relative_dispersion=0.2)
        score_claims(scored, cfg)
        row = _row(scored, OUTLIER_PROVIDER)
        assert row["flagged_peer"] is False
        assert row["flag_reason"] == "thin_dispersion"

    def test_thin_dispersion_is_false_under_default_config(self, scored) -> None:
        row = _row_with(scored, OUTLIER_PROVIDER, "dispersion_thin")
        assert row is False

    def test_immaterial_excess_blocks_flagging(self, scored) -> None:
        """A 3.5-sigma deviation worth only 5.7x peers is rejected by a 10x floor."""
        cfg = ScoringConfig(min_relative_excess=10.0)
        score_claims(scored, cfg)
        row = _row(scored, OUTLIER_PROVIDER)
        assert row["flagged_peer"] is False
        assert row["flag_reason"] == "immaterial_excess"

    def test_default_config_still_flags_the_real_outlier(self, scored) -> None:
        """Guards must not be so tight that they suppress genuine anomalies."""
        row = _row(scored, OUTLIER_PROVIDER)
        assert row["flagged_peer"] is True
        assert row["flag_reason"] == "high_markup"

    def test_every_reason_branch_is_reachable(self, scored) -> None:
        """Each config below must produce its own reason string, proving no
        branch in the CASE chain is shadowed by an earlier one."""
        observed: dict[str, str] = {}
        for label, cfg in {
            "degenerate_peers": ScoringConfig(),
            "thin_dispersion": ScoringConfig(min_relative_dispersion=0.2),
            "low_confidence": ScoringConfig(confidence_floor=0.99),
            "immaterial_excess": ScoringConfig(min_relative_excess=10.0),
            "high_markup": ScoringConfig(),
            "within_peer_range": ScoringConfig(),
        }.items():
            score_claims(scored, cfg)
            reasons = {
                r[0]
                for r in scored.sql(f"SELECT DISTINCT flag_reason FROM {VIEW}").fetchall()
            }
            observed[label] = label in reasons
        assert all(observed.values()), observed


def _row_with(con, provider_id: str, column: str):
    return con.sql(
        f"SELECT {column} FROM {VIEW} WHERE provider_id = '{provider_id}'"
    ).fetchone()[0]


class TestSummariseContract:
    def test_summary_reports_guard_activity(self, scored) -> None:
        add_isolation_forest(scored, ScoringConfig())
        summary = summarise(scored)
        assert "thin_dispersion_rows" in summary
        assert "max_relative_excess" in summary
        assert isinstance(summary["flag_reasons"], list)
        reasons = {r["reason"] for r in summary["flag_reasons"]}
        assert "degenerate_peers" in reasons
        assert "high_markup" in reasons

    def test_summary_percentages_sum_to_100(self, scored) -> None:
        add_isolation_forest(scored, ScoringConfig())
        summary = summarise(scored)
        total = sum(r["pct"] for r in summary["flag_reasons"])
        assert total == pytest.approx(100.0, abs=0.05)
        assert sum(r["rows"] for r in summary["flag_reasons"]) == summary["rows_scored"]


class TestDispersionFallbackChain:
    """Regression tests for a divergence between the SQL and its Python twin.

    The batch path falls back MAD -> IQR -> NULL. An earlier ``robust_z()``
    returned ``None`` whenever MAD was zero, ignoring the IQR entirely, so for a
    peer group where MAD is zero but the IQR is not, ``POST /score`` and the
    batch report disagreed about the same claim. These pin the shared chain.
    """

    # [1, 5, 5, 5, 5, 5, 9, 9]: median 5, five zero deviations -> MAD 0,
    # but p25=5 / p75=6 so the IQR is 1 and a scale does exist.
    def test_mad_zero_with_nonzero_iqr_uses_the_iqr(self) -> None:
        from claimsight.scoring import IQR_SCALE

        z = robust_z(9.0, 5.0, 0.0, p25=5.0, p75=6.0)
        assert z is not None
        assert z == pytest.approx((9.0 - 5.0) / ((6.0 - 5.0) / IQR_SCALE))

    def test_mad_preferred_over_iqr_when_both_exist(self) -> None:
        from claimsight.scoring import MAD_SCALE

        assert robust_z(9.0, 5.0, 2.0, p25=4.0, p75=7.0) == pytest.approx(
            (9.0 - 5.0) / (MAD_SCALE * 2.0)
        )

    def test_no_dispersion_at_all_returns_none(self) -> None:
        assert robust_z(9.0, 5.0, 0.0, p25=5.0, p75=5.0) is None
        assert robust_z(9.0, 5.0, 0.0) is None

    def test_twin_signature_stays_backwards_compatible(self) -> None:
        """Existing three-argument callers must keep working unchanged."""
        assert robust_z(30.0, 5.25, 0.75) == pytest.approx(
            (30.0 - 5.25) / (MAD_SCALE * 0.75)
        )


class TestCursorVisibility:
    """Regression tests for a bug the API hit in production-shaped conditions.

    ``con.register(df)`` creates a *connection-local* virtual table. A view built
    on it resolves on ``con`` but fails on ``con.cursor()`` with
    "Catalog Error: iforest_scores does not exist", because cursors share the
    database catalog and not Python-side replacement scans. The service takes a
    cursor per request, so every scored endpoint returned 500 while the test
    suite -- which reused one connection -- stayed green.

    These tests cross the cursor boundary on purpose.
    """

    def test_final_view_is_readable_across_a_cursor(self, scored) -> None:
        add_isolation_forest(scored, ScoringConfig())
        cursor = scored.cursor()
        try:
            assert cursor.sql("SELECT count(*) FROM claim_scores_final").fetchone()[0] == 17
            summary = summarise(cursor)
            assert summary["rows_scored"] == 17
            assert summary["flagged_iforest"] >= 0
        finally:
            cursor.close()

    def test_scores_are_a_real_table_not_a_registered_scan(self, scored) -> None:
        """The fix: materialise, so the catalog -- not a Python object -- owns it."""
        add_isolation_forest(scored, ScoringConfig())
        kinds = dict(
            scored.sql(
                "SELECT table_name, table_type FROM information_schema.tables"
            ).fetchall()
        )
        assert kinds.get("iforest_scores") == "BASE TABLE"

    def test_querying_the_final_view_does_not_need_the_dataframe(self, scored) -> None:
        """A fresh cursor must work even though the pandas frame is long gone."""
        add_isolation_forest(scored, ScoringConfig())
        row = scored.cursor().sql(
            """SELECT count(*) AS n,
                      sum(CASE WHEN iforest_score IS NOT NULL THEN 1 ELSE 0 END) AS scored
               FROM claim_scores_final"""
        ).fetchone()
        assert row[0] == 17
        assert row[1] == 17
