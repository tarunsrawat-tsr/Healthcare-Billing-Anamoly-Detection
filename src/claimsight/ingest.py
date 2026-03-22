"""Ingest the CMS Medicare inpatient charge file into a typed, queryable relation.

Design notes
------------
Parsing is pushed *into* DuckDB rather than done in a Python row loop. At
197,283 rows this is the difference between milliseconds and seconds, and it
keeps one code path for both the CLI and the API. The pure-Python parsers in
:mod:`claimsight.schema` exist for unit tests and request validation, and
``tests/test_schema.py`` asserts the two implementations agree.

Every read goes through :func:`claimsight.schema.source_relation`, which forces
``all_varchar=true``. Type inference is therefore never in the pipeline: see the
module docstring in :mod:`claimsight.schema` for the three concrete failures
that inference causes on this exact file.

Ingest always emits a :class:`IngestReport`. A loader that cannot tell you how
many rows it dropped is a loader you cannot trust.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from . import schema

logger = logging.getLogger(__name__)

#: Canonical view name created by :func:`load_claims`.
VIEW_NAME = "claims"


@dataclass(frozen=True)
class IngestReport:
    """What happened during a load. Surfaces data-quality problems explicitly."""

    source: str
    rows_read: int
    rows_usable: int
    rows_rejected: int
    null_counts: dict[str, int] = field(default_factory=dict)
    distinct_providers: int = 0
    distinct_drgs: int = 0
    distinct_hrrs: int = 0

    @property
    def rejection_rate(self) -> float:
        if self.rows_read == 0:
            return 0.0
        return self.rows_rejected / self.rows_read

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "rows_read": self.rows_read,
            "rows_usable": self.rows_usable,
            "rows_rejected": self.rows_rejected,
            "rejection_rate": round(self.rejection_rate, 6),
            "null_counts": dict(self.null_counts),
            "distinct_providers": self.distinct_providers,
            "distinct_drgs": self.distinct_drgs,
            "distinct_hrrs": self.distinct_hrrs,
        }


def _usable_predicate() -> str:
    """SQL predicate: a row is usable if every required column parsed non-null."""
    return " AND ".join(f'"{c}" IS NOT NULL' for c in sorted(schema.REQUIRED_COLUMNS))


def _validate_header(path: Path) -> None:
    """Check the CSV header independently of DuckDB, with a useful error.

    Because :func:`claimsight.schema.source_relation` declares columns
    explicitly, DuckDB validates the header too -- but its error message is
    about binding, not about "you pointed me at the wrong file". Reading the
    first line here costs nothing and produces an error a human can act on.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        first = next(csv.reader(handle), None)
    if first is None:
        raise ValueError(f"{path} is empty")
    missing = [name for name in schema.RAW_NAMES if name not in first]
    if missing:
        raise ValueError(
            f"{path.name} is not a CMS inpatient provider charge file. "
            f"Missing expected columns: {missing}. Found: {first[:4]}..."
        )


def load_claims(
    path: str | Path,
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    view: str = VIEW_NAME,
) -> tuple[duckdb.DuckDBPyConnection, IngestReport]:
    """Load a CMS charge file into ``con`` as a typed view named ``view``.

    Two views are created:

    * ``<view>_raw`` -- every row as parsed, including unparseable ones;
    * ``<view>``      -- rows usable for scoring (all required columns non-null),
      plus a ``markup_ratio`` derived column.

    Splitting them means rejected rows remain inspectable instead of vanishing,
    which is what lets the report state exactly what was dropped and why.

    Args:
        path: Location of the CMS CSV.
        con: Optional existing DuckDB connection. An in-memory one is created if
            omitted. Callers that want persistence pass their own connection.
        view: Name of the view to create.

    Returns:
        The connection and an :class:`IngestReport`.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        duckdb.Error: If the CSV cannot be read at all (e.g. wrong header).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"claims file not found: {path}")

    _validate_header(path)

    con = con if con is not None else duckdb.connect()
    source = schema.source_relation(str(path))

    con.execute(
        f"""
        CREATE OR REPLACE VIEW {view}_raw AS
        SELECT
          {schema.projection_sql()},
          -- Derived: charges per dollar of Medicare payment. This is the
          -- headline 'markup' measure and the primary anomaly signal.
          TRY_CAST(
            replace(replace(trim("Average Covered Charges"), '$', ''), ',', '') AS DOUBLE
          ) / NULLIF(
            TRY_CAST(
              replace(replace(trim("Average Medicare Payments"), '$', ''), ',', '') AS DOUBLE
            ), 0
          ) AS markup_ratio
        FROM {source}
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE VIEW {view} AS
        SELECT * FROM {view}_raw WHERE {_usable_predicate()}
        """
    )

    report = _build_report(con, str(path), view)
    logger.info(
        "ingested %d rows (%d usable, %d rejected) from %s",
        report.rows_read,
        report.rows_usable,
        report.rows_rejected,
        path.name,
    )
    return con, report


def _build_report(
    con: duckdb.DuckDBPyConnection, source: str, view: str
) -> IngestReport:
    """Compute the data-quality report with a small number of set-based queries."""
    rows_read = con.sql(f"SELECT count(*) FROM {view}_raw").fetchone()[0]
    rows_usable = con.sql(f"SELECT count(*) FROM {view}").fetchone()[0]

    null_counts = {
        name: con.sql(
            f'SELECT count(*) FROM {view}_raw WHERE "{name}" IS NULL'
        ).fetchone()[0]
        for name in schema.CANONICAL_NAMES
    }

    card = con.sql(
        f"""
        SELECT count(DISTINCT provider_id), count(DISTINCT drg), count(DISTINCT hrr)
        FROM {view}
        """
    ).fetchone()

    return IngestReport(
        source=source,
        rows_read=int(rows_read),
        rows_usable=int(rows_usable),
        rows_rejected=int(rows_read - rows_usable),
        null_counts=null_counts,
        distinct_providers=int(card[0]),
        distinct_drgs=int(card[1]),
        distinct_hrrs=int(card[2]),
    )


def write_parquet(
    con: duckdb.DuckDBPyConnection,
    destination: str | Path,
    *,
    view: str = VIEW_NAME,
) -> Path:
    """Materialise the usable view to Parquet so later stages skip CSV parsing.

    Parquet preserves the resolved types, which is the point: downstream code
    cannot accidentally re-introduce the VARCHAR-money problem.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"COPY (SELECT * FROM {view}) TO '{destination}' (FORMAT PARquet, COMPRESSION ZSTD)"
    )
    logger.info("wrote parquet to %s", destination)
    return destination
