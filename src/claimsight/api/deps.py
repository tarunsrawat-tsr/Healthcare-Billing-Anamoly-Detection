"""Pipeline lifecycle for the API process.

The full pipeline (ingest -> peers -> score -> forest) is run **once** at startup
and the resulting DuckDB connection is shared read-only across requests. At ~9
seconds for 197k rows on 2 cores that is cheap enough to do eagerly, and it means
request latency is a lookup rather than a recompute.

DuckDB connections are not safe to share across threads for concurrent writes,
so every request obtains its own ``cursor()``. Reads are the only operation
performed after startup, which keeps this simple and correct.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ..config import Settings, get_settings
from ..ingest import IngestReport, load_claims
from ..peers import build_peer_stats, peer_level_summary
from ..scoring import (
    SCORED_VIEW,
    ScoringConfig,
    add_isolation_forest,
    score_claims,
    summarise,
)

logger = logging.getLogger(__name__)

FINAL_VIEW = f"{SCORED_VIEW}_final"


@dataclass
class Pipeline:
    """A loaded, scored claims dataset ready to serve."""

    con: duckdb.DuckDBPyConnection
    source: Path
    ingest: IngestReport
    config: ScoringConfig
    elapsed_seconds: float

    def cursor(self) -> duckdb.DuckDBPyConnection:
        """A per-request cursor over the shared read-only connection."""
        return self.con.cursor()

    @property
    def rows_loaded(self) -> int:
        return self.ingest.rows_usable


def build_pipeline(
    settings: Settings | None = None,
    config: ScoringConfig | None = None,
    *,
    source: Path | None = None,
    with_forest: bool = True,
) -> Pipeline:
    """Run the whole pipeline and return a servable :class:`Pipeline`.

    Raises:
        FileNotFoundError: if neither the full file nor the committed sample
            exists. The message tells the reviewer which ``make`` target to run.
    """
    settings = settings or get_settings()
    config = config or ScoringConfig()
    path = source or settings.resolve_input()

    started = time.perf_counter()
    con = duckdb.connect()
    con, ingest_report = load_claims(path, con)
    build_peer_stats(con, min_support=settings.min_support, metric=config.metric)
    score_claims(con, config)
    if with_forest:
        add_isolation_forest(con, config)
    else:
        # Single view contract: callers always read FINAL_VIEW.
        con.execute(
            f"""
            CREATE OR REPLACE VIEW {FINAL_VIEW} AS
            SELECT *, NULL::DOUBLE AS iforest_score, NULL::DOUBLE AS iforest_percentile,
                   false AS iforest_flag, false AS flagged_both
            FROM {SCORED_VIEW}_flagged
            """
        )
    elapsed = time.perf_counter() - started

    logger.info(
        "pipeline ready: %d rows from %s in %.2fs",
        ingest_report.rows_usable,
        path,
        elapsed,
    )
    return Pipeline(
        con=con,
        source=path,
        ingest=ingest_report,
        config=config,
        elapsed_seconds=round(elapsed, 3),
    )


def pipeline_summary(pipeline: Pipeline) -> dict:
    return summarise(pipeline.cursor(), FINAL_VIEW)


def pipeline_peer_levels(pipeline: Pipeline) -> list[dict]:
    return peer_level_summary(pipeline.cursor(), "claims_with_peers")
