"""Tests for the explicit schema and its two parser implementations.

These tests encode the three dirty-data traps documented in
``claimsight.schema``. They are the reason the ingest layer can be trusted: each
trap becomes an assertion that fails if anyone reintroduces type inference.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from claimsight import schema
from claimsight.schema import ParseError


class TestMoneyParser:
    """Trap #1: money columns are formatted currency strings."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("$1,016,806.46", 1016806.46),
            ("$296,937.00", 296937.00),
            ("$0.00", 0.0),
            ("$3,104,374.28", 3104374.28),  # the true file maximum
            ("98136.7", 98136.7),  # no '$' prefix occurs in some vintages
            ("  $5,460.64  ", 5460.64),  # surrounding whitespace
            ("-$12.50", -12.5),  # credits/adjustments
        ],
    )
    def test_parses_currency(self, raw: str, expected: float) -> None:
        assert schema.parse_money(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_blank_is_none(self, raw: str | None) -> None:
        assert schema.parse_money(raw) is None

    @pytest.mark.parametrize("raw", ["N/A", "abc", "$$5", "1,23", "$1,2345"])
    def test_malformed_raises_rather_than_hiding(self, raw: str) -> None:
        """Present-but-corrupt must raise; a silent None would mask bad data."""
        with pytest.raises(ParseError):
            schema.parse_money(raw)

    def test_lexicographic_trap_is_documented_and_avoided(self) -> None:
        """The concrete failure: string sort puts '98136.7' above '$99,997.15'.

        '$' is 0x24 and '9' is 0x39, so every '$'-prefixed value sorts below a
        bare digit. Sorting money as text therefore never surfaces the real
        maximum. Parsing to float fixes the ordering.
        """
        values = ["$3,104,374.28", "98136.7", "$99,997.15"]
        as_strings = sorted(values, reverse=True)
        assert as_strings[0] == "98136.7"  # WRONG answer from a string sort

        as_numbers = sorted(schema.parse_money(v) for v in values)
        assert as_numbers[-1] == pytest.approx(3104374.28)  # correct


class TestIntegerParser:
    """Trap #2: comma-separated counts break sampling-based inference."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1,134", 1134),  # row 111,180 of FY2016 - the row that kills inference
            ("13", 13),
            ("2,048", 2048),
            ("0", 0),
        ],
    )
    def test_parses_counts_with_separators(self, raw: str, expected: int) -> None:
        assert schema.parse_integer(raw) == expected

    @pytest.mark.parametrize("raw", ["", None])
    def test_blank_is_none(self, raw: str | None) -> None:
        assert schema.parse_integer(raw) is None

    @pytest.mark.parametrize("raw", ["1.5", "abc", "12,34"])
    def test_malformed_raises(self, raw: str) -> None:
        with pytest.raises(ParseError):
            schema.parse_integer(raw)


class TestIdentifierParser:
    """Trap #3: zero-padded identifiers must never be cast to int."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("010033", "010033"),  # a real Provider Id from the FY2016 file
            ("02115", "02115"),  # a Massachusetts ZIP - leading zero is meaningful
            (" 100007 ", "100007"),
        ],
    )
    def test_preserves_leading_zeros(self, raw: str, expected: str) -> None:
        assert schema.parse_identifier(raw) == expected

    def test_int_cast_would_corrupt_the_id(self) -> None:
        assert str(int("010033")) != "010033"
        assert str(int("010033")) == "10033"


class TestProjection:
    """The SQL projection must cover every column and force VARCHAR input."""

    def test_every_canonical_column_is_projected(self) -> None:
        sql = schema.projection_sql()
        for name in schema.CANONICAL_NAMES:
            assert f"AS {name}" in sql

    def test_source_relation_disables_inference(self) -> None:
        """No type guessing: explicit columns, every one VARCHAR."""
        sql = schema.source_relation("/tmp/x.csv")
        assert "auto_detect=false" in sql
        assert "columns={" in sql
        assert sql.count("'VARCHAR'") == len(schema.COLUMNS)
        assert "header=true" in sql

    def test_source_relation_escapes_quotes_in_path(self) -> None:
        sql = schema.source_relation("/tmp/o'brien/data.csv")
        assert "o''brien" in sql

    def test_money_and_integer_use_try_cast_not_cast(self) -> None:
        """TRY_CAST yields NULL on bad input so we can *count* rejects."""
        sql = schema.projection_sql()
        assert "TRY_CAST" in sql
        assert sql.count("TRY_CAST") >= 4  # 3 money columns + discharges


class TestRowParser:
    """End-to-end parse of a real row from the FY2016 file."""

    REAL_ROW: ClassVar[dict[str, Any]] = {
        "DRG Definition": "001 - HEART TRANSPLANT OR IMPLANT OF HEART ASSIST SYSTEM W MCC",
        "Provider Id": "010033",
        "Provider Name": "UNIVERSITY OF ALABAMA HOSPITAL",
        "Provider Street Address": "619 SOUTH 19TH STREET",
        "Provider City": "BIRMINGHAM",
        "Provider State": "AL",
        "Provider Zip Code": "35233",
        "Hospital Referral Region (HRR) Description": "AL - Birmingham",
        "Total Discharges": "13",
        "Average Covered Charges": "$1,016,806.46",
        "Average Total Payments": "$296,937.00",
        "Average Medicare Payments": "$150,139.69",
    }

    def test_parses_a_real_cms_row(self) -> None:
        parsed = schema.parse_row(self.REAL_ROW)
        assert parsed["provider_id"] == "010033"
        assert parsed["total_discharges"] == 13
        assert parsed["avg_covered_charges"] == pytest.approx(1016806.46)
        assert parsed["avg_total_payments"] == pytest.approx(296937.00)
        assert parsed["avg_medicare_payments"] == pytest.approx(150139.69)
        assert parsed["drg"].startswith("001 - HEART TRANSPLANT")

    def test_markup_ratio_is_well_defined(self) -> None:
        parsed = schema.parse_row(self.REAL_ROW)
        charges = parsed["avg_covered_charges"]
        medicare = parsed["avg_medicare_payments"]
        assert isinstance(charges, float) and isinstance(medicare, float)
        assert charges / medicare == pytest.approx(6.7725, abs=1e-3)


@pytest.fixture
def con():
    import duckdb

    return duckdb.connect()


def _column(canonical: str) -> schema.Column:
    return next(c for c in schema.COLUMNS if c.canonical == canonical)


class TestPythonAndSqlParsersAgree:
    """The two parser implementations must not drift apart.

    Ingest runs the DuckDB SQL (fast, vectorised); the API layer and unit tests
    run the Python functions. If they disagree, the pipeline parses the same
    file differently depending on which path touched it -- a bug that is nearly
    impossible to spot downstream. These tests pin them together by running the
    *same* ``Column.expression()`` object against tricky inputs.
    """

    TRICKY_MONEY: ClassVar[list[str]] = [
        "$1,016,806.46",
        "$0.00",
        "98136.7",
        "$3,104,374.28",
        "-$12.50",
        "$-12.50",
        "  $5,460.64  ",
        "",
    ]
    TRICKY_INT: ClassVar[list[str]] = ["1,134", "13", "2,048", "0", "  7  ", ""]

    def test_money_agrees(self, con) -> None:
        expr = _column("avg_covered_charges").expression('"v"')
        for raw in self.TRICKY_MONEY:
            sql_val = con.execute(f'SELECT {expr} FROM (SELECT ? AS v)', [raw]).fetchone()[0]
            py_val = schema.parse_money(raw if raw.strip() else None)
            assert (sql_val is None) == (py_val is None), f"nullability differs for {raw!r}"
            if sql_val is not None:
                assert sql_val == pytest.approx(py_val), f"value differs for {raw!r}"

    def test_integer_agrees(self, con) -> None:
        expr = _column("total_discharges").expression('"v"')
        for raw in self.TRICKY_INT:
            sql_val = con.execute(f'SELECT {expr} FROM (SELECT ? AS v)', [raw]).fetchone()[0]
            py_val = schema.parse_integer(raw if raw.strip() else None)
            assert sql_val == py_val, f"disagreement on {raw!r}: sql={sql_val} py={py_val}"

    def test_identifier_keeps_padding_in_sql(self, con) -> None:
        """SQL must not silently numeric-cast Provider Id either."""
        expr = _column("provider_id").expression('"v"')
        val = con.execute(f"SELECT {expr} FROM (SELECT '010033' AS v)").fetchone()[0]
        assert val == schema.parse_identifier("010033") == "010033"

    def test_projection_still_emits_aliases(self) -> None:
        """The refactor must not have broken the projection used by ingest."""
        for column in schema.COLUMNS:
            assert column.sql.endswith(f" AS {column.canonical}")
