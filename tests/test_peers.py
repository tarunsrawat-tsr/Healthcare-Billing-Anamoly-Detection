"""Tests for hierarchical peer-group construction.

The core property under test: on the real file 43% of DRG x HRR groups are
singletons, so a scoring pipeline that does not fall back is silently broken.
These tests prove the fallback engages, engages at the right level, and records
which level it used.
"""

from __future__ import annotations

import duckdb
import pytest
from tests.conftest import DRG_A, DRG_C_SINGLETON, TINY_CLAIMS

from claimsight.ingest import load_claims
from claimsight.peers import (
    DEFAULT_LEVELS,
    DEFAULT_MIN_SUPPORT,
    build_peer_stats,
    peer_level_summary,
)


def _levels_for(con: duckdb.DuckDBPyConnection, drg: str) -> set[tuple[str, int]]:
    rows = con.sql(
        f"SELECT peer_level, peer_n FROM claims_with_peers WHERE drg = '{drg}'"
    ).fetchall()
    return {(r[0], int(r[1])) for r in rows}


class TestHierarchySelection:
    def test_well_populated_group_uses_the_finest_level(self, with_peers) -> None:
        """DRG A has 8 rows in one HRR, exactly meeting default min_support."""
        assert DEFAULT_MIN_SUPPORT == 8
        assert _levels_for(with_peers, DRG_A) == {("drg_hrr", 8)}

    def test_singleton_falls_all_the_way_back_to_national(self, with_peers) -> None:
        """DRG C has one row nationwide: no level has support, so the guaranteed
        coarsest fallback is used -- and peer_n=1 makes that visible downstream."""
        assert _levels_for(with_peers, DRG_C_SINGLETON) == {("drg_national", 1)}

    def test_raising_min_support_forces_a_coarser_level(self, ingested) -> None:
        """With support=10, DRG A's 8-row HRR group is too thin at every level
        except the ELSE branch, which lands on the coarsest rung."""
        build_peer_stats(ingested, min_support=10)
        levels = {lvl for lvl, _ in _levels_for(ingested, DRG_A)}
        assert levels == {"drg_national"}

    def test_every_row_gets_peer_statistics(self, with_peers) -> None:
        """No row may come out with a null peer median."""
        row = with_peers.sql(
            """SELECT count(*) AS n,
                      sum(CASE WHEN peer_median IS NULL THEN 1 ELSE 0 END) AS nulls
               FROM claims_with_peers"""
        ).fetchone()
        assert row[0] == 17
        assert row[1] == 0

    def test_peer_level_is_recorded_on_every_row(self, with_peers) -> None:
        """Uncertainty is part of the output, not hidden inside it."""
        nulls = with_peers.sql(
            "SELECT count(*) FROM claims_with_peers WHERE peer_level IS NULL"
        ).fetchone()[0]
        assert nulls == 0


class TestGuardRails:
    def test_min_support_below_two_is_rejected(self, ingested) -> None:
        """A group of one has no dispersion; scoring it would be meaningless."""
        with pytest.raises(ValueError, match="min_support must be >= 2"):
            build_peer_stats(ingested, min_support=1)

    def test_empty_hierarchy_is_rejected(self, ingested) -> None:
        with pytest.raises(ValueError, match="at least one peer level"):
            build_peer_stats(ingested, levels=())

    def test_requires_a_claims_view(self, con) -> None:
        with pytest.raises(duckdb.Error):
            build_peer_stats(con)


class TestSummary:
    def test_summary_accounts_for_every_row(self, with_peers) -> None:
        summary = peer_level_summary(with_peers)
        assert sum(r["rows_scored"] for r in summary) == 17
        assert sum(r["pct_of_rows"] for r in summary) == pytest.approx(100.0, abs=0.1)

    def test_summary_reports_group_thinness(self, with_peers) -> None:
        summary = {r["peer_level"]: r for r in peer_level_summary(with_peers)}
        assert summary["drg_hrr"]["smallest_group"] == 8
        assert summary["drg_national"]["smallest_group"] == 1


class TestCustomLevels:
    def test_a_two_level_hierarchy_works(self, ingested) -> None:
        """The hierarchy is data, not hard-coded: callers can redefine it."""
        levels = (DEFAULT_LEVELS[0], DEFAULT_LEVELS[2])  # hrr then national
        build_peer_stats(ingested, levels=levels)
        used = {r["peer_level"] for r in peer_level_summary(ingested)}
        assert used <= {"drg_hrr", "drg_national"}


class TestAgainstTheCommittedSample:
    """The sample is what CI actually runs on, so prove it behaves too."""

    SAMPLE = (
        TINY_CLAIMS.parent.parent.parent
        / "data"
        / "sample"
        / "cms_inpatient_fy2016_sample.csv"
    )

    @pytest.mark.skipif(not SAMPLE.exists(), reason="committed sample not present")
    def test_sample_exercises_all_fallback_levels(self) -> None:
        """The sample was built stratified specifically so that every rung of the
        hierarchy is exercised. If this fails, the sample has drifted."""
        con = duckdb.connect()
        load_claims(self.SAMPLE, con)
        build_peer_stats(con)
        used = {r["peer_level"] for r in peer_level_summary(con)}
        assert {"drg_hrr", "drg_state", "drg_national"} <= used
