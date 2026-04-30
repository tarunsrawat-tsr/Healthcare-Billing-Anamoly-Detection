"""Explicit schema and parsing rules for the CMS Medicare Inpatient Provider Charge File.

Why this module exists
----------------------
Automatic CSV type inference is *unsafe* on this file. Profiling the real FY2016
release (197,283 rows, 34 MB) surfaced three distinct failure modes, each of
which is silent or catastrophic in a different way:

1. **Money columns arrive as formatted strings.** ``Average Covered Charges``,
   ``Average Total Payments`` and ``Average Medicare Payments`` look like
   ``"$1,016,806.46"``. Inference gives up and types them ``VARCHAR``, so
   arithmetic fails outright and ``ORDER BY`` silently becomes lexicographic.
   In this file ``'98136.7'`` sorts *above* ``'$99,997.15'`` because ``'$'``
   (0x24) precedes ``'9'`` (0x39) -- so a string sort reports the largest
   charge as ~$99,997 when the true maximum is **$3,104,374**, off by 31x.

2. **Sampling-based inference breaks mid-file.** ``Total Discharges`` is
   inferred as ``BIGINT`` from the first 20,480 sampled rows, which all happen
   to be under 1,000 and therefore comma-free. Row 111,180 contains ``"1,134"``
   and the read either hard-fails or, with error tolerance enabled, silently
   drops rows. A pipeline that works on a sample and dies in production.

3. **Zero-padded identifiers lose their padding.** ``Provider Id`` (``010033``)
   and ``Provider Zip Code`` (``02115``) are identifiers that happen to look
   numeric. Cast to integers they lose leading zeros, and joins against any
   other CMS vintage break quietly rather than loudly.

The rule this module enforces: **read every column as VARCHAR, then parse
through tested converters.** Never let a reader guess.

Two parsers are provided and kept in agreement by ``tests/test_schema.py``:

* pure-Python functions (used by the API layer and unit tests);
* equivalent DuckDB SQL expressions (used for the 197k-row ingest, so parsing
  happens inside the vectorised engine instead of a Python row loop).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

# --------------------------------------------------------------------------- #
# Column model
# --------------------------------------------------------------------------- #


class Kind(str, Enum):
    """How a raw CSV column should be interpreted."""

    TEXT = "text"  # free text, no parsing beyond trimming
    IDENTIFIER = "identifier"  # opaque id: keep leading zeros, never cast to int
    INTEGER = "integer"  # a count; may contain thousands separators
    MONEY = "money"  # currency: strip '$' and ',', parse as float


@dataclass(frozen=True)
class Column:
    """One column of the source file, mapped to its canonical output name."""

    raw: str
    canonical: str
    kind: Kind

    def expression(self, col_ref: str | None = None) -> str:
        """DuckDB expression that parses this column.

        Args:
            col_ref: SQL reference to read from. Defaults to the quoted raw
                column name. Tests pass a placeholder so the *same* expression
                object can be exercised against arbitrary values, which is what
                keeps the SQL and Python parsers provably in agreement.

        Assumes the source was read with ``all_varchar=true`` so every column is
        VARCHAR and no inference can interfere.
        """
        col = col_ref if col_ref is not None else f'"{self.raw}"'
        if self.kind in (Kind.TEXT, Kind.IDENTIFIER):
            # Identifiers are trimmed but never cast: casting Provider Id or
            # Zip Code to a number silently destroys leading zeros.
            return f"trim({col})"
        if self.kind is Kind.INTEGER:
            # Strip thousands separators, tolerate blanks, never guess a type.
            return f"TRY_CAST(replace(trim({col}), ',', '') AS BIGINT)"
        if self.kind is Kind.MONEY:
            return f"TRY_CAST(replace(replace(trim({col}), '$', ''), ',', '') AS DOUBLE)"
        raise ValueError(f"unhandled kind: {self.kind}")  # pragma: no cover

    @property
    def sql(self) -> str:
        """DuckDB projection fragment: ``<expr> AS <canonical>``."""
        return f"{self.expression()} AS {self.canonical}"


#: Canonical schema, in source order. ``raw`` must match the CMS header exactly.
COLUMNS: Final[tuple[Column, ...]] = (
    Column("DRG Definition", "drg", Kind.TEXT),
    Column("Provider Id", "provider_id", Kind.IDENTIFIER),
    Column("Provider Name", "provider_name", Kind.TEXT),
    Column("Provider Street Address", "street_address", Kind.TEXT),
    Column("Provider City", "city", Kind.TEXT),
    Column("Provider State", "state", Kind.TEXT),
    Column("Provider Zip Code", "zip_code", Kind.IDENTIFIER),
    Column("Hospital Referral Region (HRR) Description", "hrr", Kind.TEXT),
    Column("Total Discharges", "total_discharges", Kind.INTEGER),
    Column("Average Covered Charges", "avg_covered_charges", Kind.MONEY),
    Column("Average Total Payments", "avg_total_payments", Kind.MONEY),
    Column("Average Medicare Payments", "avg_medicare_payments", Kind.MONEY),
)

CANONICAL_NAMES: Final[tuple[str, ...]] = tuple(c.canonical for c in COLUMNS)
RAW_NAMES: Final[tuple[str, ...]] = tuple(c.raw for c in COLUMNS)

#: Columns that must be non-null for a row to be usable in scoring.
REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "drg",
        "provider_id",
        "hrr",
        "total_discharges",
        "avg_covered_charges",
        "avg_medicare_payments",
    }
)


def projection_sql() -> str:
    """Full ``SELECT`` projection turning raw CMS columns into the canonical schema."""
    return ",\n  ".join(c.sql for c in COLUMNS)


def source_relation(path: str) -> str:
    """``read_csv`` call that declares the schema explicitly, column by column.

    ``auto_detect=false`` plus an explicit ``columns`` map removes type
    inference from the pipeline *entirely*. DuckDB will not guess a single
    type, will not sample the first 20k rows to decide what
    ``Total Discharges`` is, and will fail loudly if the header does not match
    -- which is how traps #2 and #3 in this module's docstring are neutralised.

    Every column is declared VARCHAR; parsing then happens in the projection
    from :func:`projection_sql`, through converters that have tests.
    """
    escaped = str(path).replace("'", "''")
    colspec = ", ".join(f"'{c.raw}': 'VARCHAR'" for c in COLUMNS)
    return f"read_csv('{escaped}', header=true, auto_detect=false, columns={{{colspec}}})"


# --------------------------------------------------------------------------- #
# Pure-Python parsers
# --------------------------------------------------------------------------- #

#: A number with optional thousands separators and an optional sign. Applied
#: *after* currency symbols are stripped, so the sign may sit on either side of
#: the symbol ("-$12.50" and "$-12.50" both occur in accounting exports).
_NUMBER_RE: Final = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_INT_RE: Final = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)$")


class ParseError(ValueError):
    """A value could not be parsed under the declared schema."""


def parse_money(raw: str | None) -> float | None:
    """Parse ``"$1,016,806.46"`` -> ``1016806.46``.

    Returns ``None`` for blank/missing input so callers can count data-quality
    problems instead of crashing. Raises :class:`ParseError` for a value that is
    present but malformed -- a silent ``None`` there would hide real corruption.

    Mirrors the SQL in :attr:`Column.sql` exactly: strip ``$``, strip ``,``,
    cast to double. ``tests/test_schema.py`` asserts the two agree.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.count("$") > 1:
        raise ParseError(f"not a money value: {raw!r}")
    stripped = text.replace("$", "")
    if not _NUMBER_RE.match(stripped):
        raise ParseError(f"not a money value: {raw!r}")
    return float(stripped.replace(",", ""))


def parse_integer(raw: str | None) -> int | None:
    """Parse ``"1,134"`` -> ``1134``.

    This is the value that breaks sampling-based inference at row 111,180 of the
    FY2016 file.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if not _INT_RE.match(text):
        raise ParseError(f"not an integer value: {raw!r}")
    return int(text.replace(",", "").replace("+", ""))


def parse_identifier(raw: str | None) -> str | None:
    """Return an identifier verbatim (trimmed), preserving leading zeros.

    ``"010033"`` stays ``"010033"``. Casting to ``int`` here is the bug.
    """
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def parse_text(raw: str | None) -> str | None:
    """Trim free text; collapse an empty string to ``None``."""
    if raw is None:
        return None
    text = " ".join(raw.split())
    return text or None


PARSERS = {
    Kind.TEXT: parse_text,
    Kind.IDENTIFIER: parse_identifier,
    Kind.INTEGER: parse_integer,
    Kind.MONEY: parse_money,
}


def parse_row(raw_row: dict[str, str | None]) -> dict[str, object]:
    """Parse one raw CSV row (keyed by CMS header) into canonical fields."""
    out: dict[str, object] = {}
    for column in COLUMNS:
        out[column.canonical] = PARSERS[column.kind](raw_row.get(column.raw))
    return out
