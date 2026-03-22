"""Tests for the ingest layer and its data-quality reporting."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from tests.conftest import (
    CORRUPT_PROVIDER,
    OUTLIER_PROVIDER,
    TINY_CLAIMS,
)

from claimsight import schema
from claimsight.ingest import IngestReport, load_claims, write_parquet


class TestLoadClaims:
    def test_reads_every_row_and_reports_the_reject(self, con) -> None:
        """18 rows in, 1 unusable (``N/A`` in a required money column)."""
        con, report = load_claims(TINY_CLAIMS, con)
        assert report.rows_read == 18
        assert report.rows_usable == 17
        assert report.rows_rejected == 1
        assert report.rejection_rate == pytest.approx(1 / 18)

    def test_rejected_row_is_identifiable_not_silently_dropped(self, con) -> None:
        """The reject stays inspectable in the ``_raw`` view."""
        con, _ = load_claims(TINY_CLAIMS, con)
        raw = con.sql(
            f"SELECT avg_medicare_payments FROM claims_raw WHERE provider_id = '{CORRUPT_PROVIDER}'"
        ).fetchone()
        assert raw[0] is None
        usable = con.sql(
            f"SELECT count(*) FROM claims WHERE provider_id = '{CORRUPT_PROVIDER}'"
        ).fetchone()
        assert usable[0] == 0

    def test_money_is_numeric_not_varchar(self, con) -> None:
        """Trap #1: the whole point of the explicit schema."""
        con, _ = load_claims(TINY_CLAIMS, con)
        val = con.sql(
            f"SELECT avg_covered_charges FROM claims WHERE provider_id = '{OUTLIER_PROVIDER}'"
        ).fetchone()[0]
        assert isinstance(val, float)
        assert val == pytest.approx(285000.00)

    def test_discharges_with_thousands_separator_parses(self, con) -> None:
        """Trap #2: the ``"1,134"`` row that breaks sampling-based inference."""
        con, _ = load_claims(TINY_CLAIMS, con)
        val = con.sql(
            "SELECT total_discharges FROM claims WHERE provider_id = '010036'"
        ).fetchone()[0]
        assert val == 1134

    def test_provider_id_keeps_leading_zeros(self, con) -> None:
        """Trap #3: identifiers must survive as text."""
        con, _ = load_claims(TINY_CLAIMS, con)
        ids = {r[0] for r in con.sql("SELECT provider_id FROM claims").fetchall()}
        assert "010033" in ids
        assert "10033" not in ids

    def test_zip_code_keeps_leading_zeros(self, con) -> None:
        con, _ = load_claims(TINY_CLAIMS, con)
        zips = {r[0] for r in con.sql("SELECT zip_code FROM claims").fetchall()}
        assert "02115" in zips

    def test_markup_ratio_is_derived(self, con) -> None:
        con, _ = load_claims(TINY_CLAIMS, con)
        val = con.sql(
            f"SELECT markup_ratio FROM claims WHERE provider_id = '{OUTLIER_PROVIDER}'"
        ).fetchone()[0]
        assert val == pytest.approx(285000.00 / 9500.00)

    def test_cardinality_counts(self, con) -> None:
        con, report = load_claims(TINY_CLAIMS, con)
        assert report.distinct_drgs == 3
        assert report.distinct_providers == 17
        assert report.null_counts["avg_medicare_payments"] == 1


class TestHeaderValidation:
    def test_wrong_file_raises_a_useful_error(self, tmp_path: Path) -> None:
        bogus = tmp_path / "not_claims.csv"
        bogus.write_text("foo,bar,baz\n1,2,3\n")
        with pytest.raises(ValueError, match="not a CMS inpatient provider charge file"):
            load_claims(bogus, duckdb.connect())

    def test_missing_file_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_claims(tmp_path / "nope.csv", duckdb.connect())

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.csv"
        empty.write_text("")
        with pytest.raises(ValueError, match="empty"):
            load_claims(empty, duckdb.connect())


class TestParquetRoundTrip:
    def test_parquet_preserves_resolved_types(self, con, tmp_path: Path) -> None:
        """Parquet is written so later stages cannot re-introduce VARCHAR money."""
        con, _ = load_claims(TINY_CLAIMS, con)
        out = write_parquet(con, tmp_path / "nested" / "claims.parquet")
        assert out.exists()

        fresh = duckdb.connect()
        kind = fresh.sql(f"SELECT typeof(avg_covered_charges) FROM '{out}' LIMIT 1").fetchone()[0]
        assert kind == "DOUBLE"
        n = fresh.sql(f"SELECT count(*) FROM '{out}'").fetchone()[0]
        assert n == 17

    def test_creates_parent_directories(self, con, tmp_path: Path) -> None:
        con, _ = load_claims(TINY_CLAIMS, con)
        out = write_parquet(con, tmp_path / "a" / "b" / "c.parquet")
        assert out.parent.is_dir()


class TestIngestReport:
    def test_rejection_rate_zero_safe(self) -> None:
        report = IngestReport(source="x", rows_read=0, rows_usable=0, rows_rejected=0)
        assert report.rejection_rate == 0.0

    def test_as_dict_is_json_shaped(self, con) -> None:
        import json

        con, report = load_claims(TINY_CLAIMS, con)
        dumped = json.dumps(report.as_dict())
        assert "rows_rejected" in dumped
        assert set(report.as_dict()["null_counts"]) == set(schema.CANONICAL_NAMES)
