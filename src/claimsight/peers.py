"""Peer-group construction with hierarchical fallback.

The problem this module solves
------------------------------
The obvious way to find billing outliers is to compare each row against its
peers: same DRG (procedure group), same HRR (hospital referral region). But on
the real FY2016 file that grouping is **too granular to be usable**:

    DRG x HRR groups .......... 58,441
    singletons (n = 1) ........ 24,976   (43%)
    groups with n < 5 ......... 46,847   (80%)
    median group size ......... 2

A robust z-score over a group of one is undefined, and over a group of two it
is noise. Scoring naively therefore returns confident-looking garbage for
nearly half the data -- the worst kind of bug, because nothing errors.

The fix is a **hierarchy of peer definitions**, finest to coarsest::

    1. drg_hrr       (DRG x Hospital Referral Region)  -- most comparable
    2. drg_state     (DRG x State)
    3. drg_national  (DRG)                             -- always well populated

Each row is scored against the *finest* level whose group has at least
``min_support`` members. Crucially, the level actually used is recorded on the
output (``peer_level``, ``peer_n``), so a score computed from 6 peers is
visibly less trustworthy than one computed from 300. **Uncertainty is part of
the output, not hidden inside it.**

Statistical note
----------------
Peer statistics use median and MAD (median absolute deviation) rather than mean
and standard deviation, because charges are heavily right-skewed -- the file's
markup ratio runs from 0.26 to 55.7 with a median of 4.75. Mean/std would let a
single extreme hospital define "normal" for its whole peer group and mask the
very outliers we are looking for.

Peer statistics are unweighted. Each row is itself an *average* over
``total_discharges`` cases, so an 11-discharge row is a noisier estimate than a
4,043-discharge row; that is handled by :mod:`claimsight.scoring` via an
explicit confidence term rather than by silently discarding low-volume
hospitals. See the limitations section of the README.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import duckdb

logger = logging.getLogger(__name__)

#: Minimum members a peer group needs before its statistics are trusted.
DEFAULT_MIN_SUPPORT: Final[int] = 8


@dataclass(frozen=True)
class PeerLevel:
    """One rung of the peer hierarchy."""

    name: str
    keys: tuple[str, ...]

    @property
    def group_id_sql(self) -> str:
        """A single string identifying the group, for joins and display."""
        return " || '|' || ".join(f"CAST({k} AS VARCHAR)" for k in self.keys)


#: Finest first. Order matters: the first level meeting ``min_support`` wins.
DEFAULT_LEVELS: Final[tuple[PeerLevel, ...]] = (
    PeerLevel("drg_hrr", ("drg", "hrr")),
    PeerLevel("drg_state", ("drg", "state")),
    PeerLevel("drg_national", ("drg",)),
)

#: The signal being compared within a peer group.
DEFAULT_METRIC: Final[str] = "markup_ratio"


def _stats_cte(level: PeerLevel, metric: str) -> str:
    """SQL computing n / median / MAD for one peer level.

    The window function in the inner CTE gives every row its group median; the
    outer aggregate then takes the median absolute deviation of those. This
    avoids a self-join and needs no surrogate row key.
    """
    keys = ", ".join(level.keys)
    return f"""
    {level.name}_w AS (
        SELECT {keys}, {metric},
               median({metric}) OVER (PARTITION BY {keys}) AS peer_median
        FROM claims
        WHERE {metric} IS NOT NULL
    ),
    {level.name}_stats AS (
        SELECT {keys},
               count(*)               AS peer_n,
               any_value(peer_median) AS peer_median,
               median(abs({metric} - peer_median)) AS peer_mad,
               quantile_cont({metric}, 0.25) AS peer_p25,
               quantile_cont({metric}, 0.75) AS peer_p75,
               min({metric}) AS peer_min,
               max({metric}) AS peer_max
        FROM {level.name}_w
        GROUP BY {keys}
    )"""


def _join_for(level: PeerLevel) -> str:
    on = " AND ".join(f"c.{k} = s_{level.name}.{k}" for k in level.keys)
    return f"""
        LEFT JOIN {level.name}_stats AS s_{level.name} ON {on}"""


def build_peer_stats(
    con: duckdb.DuckDBPyConnection,
    *,
    levels: Sequence[PeerLevel] = DEFAULT_LEVELS,
    metric: str = DEFAULT_METRIC,
    min_support: int = DEFAULT_MIN_SUPPORT,
    view: str = "claims_with_peers",
) -> duckdb.DuckDBPyConnection:
    """Create a view joining every claim to its chosen peer group's statistics.

    Output columns added to the claim columns:

    ``peer_level``
        Name of the hierarchy rung actually used (e.g. ``drg_state``).
    ``peer_n``
        How many rows backed that group. Low ``peer_n`` means low confidence.
    ``peer_median`` / ``peer_mad`` / ``peer_p25`` / ``peer_p75``
        The chosen group's robust statistics for ``metric``.

    The level selection is a ``CASE`` chain ordered finest-to-coarsest: the
    first level with ``peer_n >= min_support`` wins. The national level is the
    guaranteed fallback because every DRG has hundreds of rows nationally.

    Args:
        con: Connection that already has a ``claims`` view (see
            :func:`claimsight.ingest.load_claims`).
        levels: Peer hierarchy, finest first.
        metric: Column compared within a group.
        min_support: Minimum group size for a level to be used.
        view: Name of the view to create.

    Returns:
        The same connection, with ``view`` created.
    """
    if not levels:
        raise ValueError("at least one peer level is required")
    if min_support < 2:
        raise ValueError(
            f"min_support must be >= 2 (got {min_support}); a peer group of one "
            "has no dispersion and cannot support an outlier score"
        )

    ctes = ",\n".join(_stats_cte(level, metric) for level in levels)
    joins = "".join(_join_for(level) for level in levels)

    # Walk the hierarchy finest -> coarsest; take the first level with support.
    # NOTE: these CASE expressions reference the joined tables (s_<level>) and
    # not select-list aliases -- SQL cannot see an alias defined in the same
    # SELECT list, which is a mistake worth making only once.
    def qualified(level: PeerLevel, stat: str) -> str:
        return f"s_{level.name}.{stat}"

    case_n = " ".join(
        f"WHEN {qualified(lvl, 'peer_n')} >= {min_support} THEN {qualified(lvl, 'peer_n')}"
        for lvl in levels
    )
    case_level = " ".join(
        f"WHEN {qualified(lvl, 'peer_n')} >= {min_support} THEN '{lvl.name}'" for lvl in levels
    )
    fallback = levels[-1].name

    def pick(stat: str) -> str:
        """Pick the statistic from the first level that has enough support."""
        parts = [
            f"WHEN {qualified(lvl, 'peer_n')} >= {min_support} THEN {qualified(lvl, stat)}"
            for lvl in levels
        ]
        return f"CASE {' '.join(parts)} ELSE {qualified(levels[-1], stat)} END"

    con.execute(
        f"""
        CREATE OR REPLACE VIEW {view} AS
        WITH {ctes}
        SELECT
            c.*,
            CASE {case_level} ELSE '{fallback}' END AS peer_level,
            CASE {case_n} ELSE s_{fallback}.peer_n END AS peer_n,
            {pick("peer_median")} AS peer_median,
            {pick("peer_mad")}    AS peer_mad,
            {pick("peer_p25")}    AS peer_p25,
            {pick("peer_p75")}    AS peer_p75
        FROM claims AS c
        {joins}
        """
    )

    used = con.sql(
        f"SELECT peer_level, count(*) AS n, min(peer_n) AS min_n, median(peer_n) AS med_n "
        f"FROM {view} GROUP BY 1 ORDER BY n DESC"
    ).fetchall()
    logger.info(
        "peer levels used: %s",
        ", ".join(f"{lvl}={n:,} (min group {mn})" for lvl, n, mn, _ in used),
    )
    return con


def peer_level_summary(
    con: duckdb.DuckDBPyConnection, view: str = "claims_with_peers"
) -> list[dict[str, object]]:
    """How many rows each hierarchy rung ended up serving, and how thin they were.

    This is the diagnostic that proves the fallback is doing real work rather
    than quietly masking a broken grouping.
    """
    rows = con.sql(
        f"""
        SELECT peer_level,
               count(*)              AS rows_scored,
               round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct_of_rows,
               min(peer_n)           AS smallest_group,
               median(peer_n)        AS median_group_size,
               max(peer_n)           AS largest_group
        FROM {view}
        GROUP BY 1
        ORDER BY rows_scored DESC
        """
    ).fetchall()
    return [
        {
            "peer_level": r[0],
            "rows_scored": int(r[1]),
            "pct_of_rows": float(r[2]),
            "smallest_group": int(r[3]),
            "median_group_size": float(r[4]),
            "largest_group": int(r[5]),
        }
        for r in rows
    ]
