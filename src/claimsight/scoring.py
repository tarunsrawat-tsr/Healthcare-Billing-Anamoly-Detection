"""Outlier scoring: peer-relative robust statistics, with a second independent opinion.

The central difficulty
----------------------
There are **no labels**. Nobody has marked which of these 197,283 rows are
genuine billing anomalies, so there is no accuracy to optimise and no test set
to validate against. Any approach that claims a score here is unfalsifiable
unless it is defended structurally. Two defences are used:

1. **Interpretability over cleverness.** The primary signal is a robust z-score
   against a peer group -- median and MAD, not mean and standard deviation,
   because the markup distribution is violently right-skewed (0.26 to 55.7,
   median 4.75). Every flag can be explained in one sentence a hospital
   administrator would accept: *"you charged 11.4x what Medicare paid for this
   procedure, while your 31 regional peers charged a median of 4.2x."*

2. **Two independent methods must agree.** An Isolation Forest runs over a
   multivariate feature set with no peer structure at all. Rows flagged by both
   a peer-relative test and an unsupervised multivariate test are far more
   credible than rows flagged by either. The overlap is reported, not assumed.

Degenerate dispersion
---------------------
48 rows sit in peer groups where MAD is exactly zero (more than half the group
bills an identical markup). Dividing by zero there would silently produce NULL
scores that look like missing data. Instead the scale falls back to the IQR,
and if that is also zero the row is explicitly marked
``dispersion_degenerate`` and excluded from flagging. A row that cannot be
scored says so.

Confidence is a first-class output
----------------------------------
A score computed from 8 peers on a hospital with 11 discharges is not as
trustworthy as one computed from 2,837 peers on 4,043 discharges. Rather than
discarding low-volume rows (which would systematically delete small and rural
hospitals -- see the fairness note in the README), every score carries a
``confidence`` in (0, 1] and flagging requires it to clear a floor.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

import duckdb
import numpy as np

logger = logging.getLogger(__name__)

#: Consistency factor making MAD comparable to a standard deviation under
#: normality: sigma ~= 1.4826 * MAD.
MAD_SCALE: Final[float] = 1.4826

#: Same idea for the IQR fallback: sigma ~= IQR / 1.349.
IQR_SCALE: Final[float] = 1.349

#: Unique natural key for one claim row, verified on the FY2016 file
#: (197,283 rows == 197,283 distinct (provider_id, drg) pairs).
CLAIM_KEY: Final[tuple[str, str]] = ("provider_id", "drg")

#: Raw-magnitude features: the ORIGINAL forest input, retained for comparison.
#: Dominated by case-mix -- a heart transplant is extreme in absolute dollars
#: without being an anomalous bill, so the forest mostly detects "rare,
#: expensive procedure" rather than "unusual billing". Kept as a named constant
#: so the improvement over it is reproducible from a single run instead of being
#: a claim in the README that nobody can check.
RAW_MAGNITUDE_FEATURES: Final[tuple[str, ...]] = (
    "log_charges",
    "log_total_payments",
    "log_medicare_payments",
    "markup_ratio",
    "log_discharges",
    "non_medicare_share",
)

#: Case-mix-normalised features: every magnitude expressed as a log-ratio
#: against its DRG's NATIONAL median. This removes procedure-mix confounding
#: while staying independent of the peer hierarchy -- national DRG medians are a
#: strictly coarser grouping than the (possibly HRR- or state-level) peer group
#: the robust z-score is measured against, and they come from a separate window
#: function rather than reusing peer_median / peer_mad.
CASE_MIX_NORMALIZED_FEATURES: Final[tuple[str, ...]] = (
    "charges_vs_drg",
    "total_vs_drg",
    "medicare_vs_drg",
    "volume_vs_drg",
    "markup_vs_drg",
    "non_medicare_share",
)


@dataclass(frozen=True)
class ScoringConfig:
    """Every tunable, in one typed place with its rationale attached."""

    metric: str = "markup_ratio"
    #: Robust z above which a row is a candidate outlier. 3.5 is deliberately
    #: conservative: on ~197k rows a 3.0 threshold flags routine variation.
    z_threshold: float = 3.5
    #: Half-widths in units of sigma for the support and volume saturating
    #: terms of the confidence calculation.
    support_halfwidth: float = 20.0
    volume_halfwidth: float = 50.0
    #: Minimum confidence for a row to be flaggable at all.
    confidence_floor: float = 0.25
    #: A robust z is only meaningful if the peer group has *some* dispersion.
    #: Requiring MAD >= 5% of the peer median rejects groups where almost
    #: everyone bills identically: there, a trivially different row divides by a
    #: near-zero scale and produces an enormous z. On the FY2016 file this was
    #: not hypothetical -- the naive ranking was swept by Washington DC
    #: hospitals at z = 134..192, because DC has a single HRR and few hospitals,
    #: so its peer groups are small and homogeneous. Those were artifacts of a
    #: tiny denominator, not billing anomalies.
    min_relative_dispersion: float = 0.05
    #: Practical significance floor: a flag must also exceed the peer median by
    #: this factor. Statistical significance on 197k rows is cheap; a 3.5-sigma
    #: deviation that amounts to billing 4% above peers is not worth reviewing.
    min_relative_excess: float = 1.5
    #: Isolation Forest settings. Deterministic so CI can assert stability.
    iforest_estimators: int = 150
    iforest_contamination: float = 0.01
    iforest_sample_size: int = 50_000
    random_state: int = 42
    #: Features for the Isolation Forest. Defaults to the case-mix-normalised
    #: set. Excludes everything derived from the peer hierarchy (peer_median,
    #: peer_mad, robust z) so the forest remains a genuinely second opinion.
    iforest_features: Sequence[str] = field(default=CASE_MIX_NORMALIZED_FEATURES)


SCORED_VIEW: Final[str] = "claim_scores"


def _log_ratio(numerator: str, denominator: str) -> str:
    """Symmetric log-ratio, guarded against zero denominators.

    ``greatest(x, 1e-9)`` stops a legitimate $0 payment from producing
    ``ln(0) = -inf``, which would otherwise reach the forest as a non-finite
    feature and quietly dominate every split it appears in.
    """
    return f"ln(greatest({numerator}, 1e-9) / greatest({denominator}, 1e-9))"


def _normalization_sql() -> str:
    """DRG national medians, as window functions over the whole relation.

    Deliberately independent of :mod:`claimsight.peers`: this always partitions
    by DRG nationwide, whatever level the peer hierarchy selected for the robust
    z-score. That separation is what keeps the Isolation Forest a second opinion
    rather than a rescaling of the first one.
    """
    return """
        CAST(median(avg_covered_charges)   OVER (PARTITION BY drg) AS DOUBLE)
            AS drg_med_charges,
        CAST(median(avg_total_payments)    OVER (PARTITION BY drg) AS DOUBLE)
            AS drg_med_total,
        CAST(median(avg_medicare_payments) OVER (PARTITION BY drg) AS DOUBLE)
            AS drg_med_medicare,
        CAST(median(total_discharges)      OVER (PARTITION BY drg) AS DOUBLE)
            AS drg_med_discharges,
        CAST(median(markup_ratio)          OVER (PARTITION BY drg) AS DOUBLE)
            AS drg_med_markup"""


def _feature_sql(cfg: ScoringConfig) -> str:
    """Derived columns used by both the robust score and the Isolation Forest.

    References the DRG medians from :func:`_normalization_sql`, so this must run
    in a CTE *after* the normalisation one -- SQL cannot reference an alias
    defined in the same SELECT list.

    Both feature sets are computed on every run; ``cfg.iforest_features`` only
    chooses which the forest consumes. Computing both is free at this scale and
    makes the raw-vs-normalised comparison reproducible from a single pipeline
    run rather than a claim nobody can verify.
    """
    metric = cfg.metric
    return f"""
        -- Raw magnitudes: the original forest input, case-mix confounded.
        ln(1 + greatest(avg_covered_charges, 0))   AS log_charges,
        ln(1 + greatest(avg_total_payments, 0))    AS log_total_payments,
        ln(1 + greatest(avg_medicare_payments, 0)) AS log_medicare_payments,
        ln(1 + greatest(total_discharges, 0))      AS log_discharges,
        -- Case-mix-normalised: log-ratio against this DRG's national median.
        {_log_ratio("avg_covered_charges", "drg_med_charges")}    AS charges_vs_drg,
        {_log_ratio("avg_total_payments", "drg_med_total")}       AS total_vs_drg,
        {_log_ratio("avg_medicare_payments", "drg_med_medicare")} AS medicare_vs_drg,
        {_log_ratio("total_discharges", "drg_med_discharges")}    AS volume_vs_drg,
        {_log_ratio(metric, "drg_med_markup")}                    AS markup_vs_drg,
        -- Scale-free in either framing: the share of the bill Medicare did NOT
        -- pay, a genuinely distinct signal from the markup ratio.
        CASE WHEN avg_total_payments > 0
             THEN (avg_total_payments - avg_medicare_payments) / avg_total_payments
             ELSE NULL END                          AS non_medicare_share,
        {metric}                                    AS metric_value,
        peer_median, peer_mad, peer_p25, peer_p75, peer_n, peer_level,
        -- Dispersion scale with a documented fallback chain: MAD -> IQR -> NULL.
        CASE
            WHEN peer_mad > 0 THEN {MAD_SCALE} * peer_mad
            WHEN (peer_p75 - peer_p25) > 0 THEN (peer_p75 - peer_p25) / {IQR_SCALE}
            ELSE NULL
        END AS dispersion_scale,
        CASE WHEN peer_mad = 0 AND (peer_p75 - peer_p25) = 0 THEN true ELSE false END
            AS dispersion_degenerate,
        -- Dispersion too thin to make a z-score meaningful, relative to the
        -- group's own scale. Guards against near-zero-MAD explosions.
        CASE WHEN peer_median > 0 AND peer_mad < {cfg.min_relative_dispersion} * peer_median
             THEN true ELSE false END AS dispersion_thin,
        -- Practical effect size: how many times the peer median this row bills.
        CASE WHEN peer_median > 0 THEN {metric} / peer_median ELSE NULL END
            AS relative_excess"""


def _confidence_sql(cfg: ScoringConfig) -> str:
    """Geometric mean of two saturating terms, each in (0, 1).

    ``n / (n + k)`` rises quickly from 0 and flattens near 1, so going from 8
    peers to 30 matters a lot while 300 to 3,000 matters little -- which is the
    right shape for statistical support. The geometric mean punishes a row that
    is weak on *either* axis instead of letting strength on one compensate.
    """
    return f"""
        sqrt(
            (peer_n / (peer_n + {cfg.support_halfwidth}))
            * (total_discharges / (total_discharges + {cfg.volume_halfwidth}))
        ) AS confidence"""


def flagged_expression(cfg: ScoringConfig) -> str:
    """SQL boolean: should this row be flagged?

    Shared by the batch pipeline and the API's single-claim ``POST /score``
    endpoint. Extracted deliberately -- a second hand-written copy of this
    predicate would eventually disagree with the first, and the API would then
    answer "would this claim be flagged?" differently from the batch run that
    produced the report. One expression, two callers, pinned by tests.
    """
    return f"""CASE
        WHEN dispersion_degenerate THEN false
        WHEN dispersion_thin       THEN false
        WHEN robust_z IS NULL      THEN false
        WHEN relative_excess IS NULL THEN false
        WHEN confidence < {cfg.confidence_floor} THEN false
        WHEN abs(robust_z) < {cfg.z_threshold}   THEN false
        ELSE relative_excess >= {cfg.min_relative_excess}
          OR relative_excess <= 1.0 / {cfg.min_relative_excess}
    END"""


def reason_expression(cfg: ScoringConfig) -> str:
    """SQL string: why this row was or was not flagged.

    Order matters and every branch must be reachable. Degenerate and thin
    dispersion both *imply* a meaningless z, so they are tested first; testing
    the NULL z first would make them dead code (a bug this project actually
    shipped once, caught by ``test_every_reason_branch_is_reachable``).
    """
    return f"""CASE
        WHEN dispersion_degenerate THEN 'degenerate_peers'
        WHEN dispersion_thin       THEN 'thin_dispersion'
        WHEN robust_z IS NULL      THEN 'missing_metric'
        WHEN confidence < {cfg.confidence_floor} THEN 'low_confidence'
        WHEN abs(robust_z) < {cfg.z_threshold}   THEN 'within_peer_range'
        WHEN relative_excess >= {cfg.min_relative_excess} THEN 'high_markup'
        WHEN relative_excess <= 1.0 / {cfg.min_relative_excess} THEN 'low_markup'
        ELSE 'immaterial_excess'
    END"""


def dispersion_scale(
    mad: float, p25: float | None = None, p75: float | None = None
) -> float | None:
    """Pure-Python twin of the SQL dispersion fallback chain: MAD -> IQR -> None."""
    if mad and mad > 0:
        return MAD_SCALE * mad
    if p25 is not None and p75 is not None and (p75 - p25) > 0:
        return (p75 - p25) / IQR_SCALE
    return None


def is_degenerate(mad: float, p25: float | None, p75: float | None) -> bool:
    """Twin of the SQL ``dispersion_degenerate`` column."""
    return (mad == 0) and (p25 is not None and p75 is not None and (p75 - p25) == 0)


def is_thin(mad: float, peer_median: float, cfg: ScoringConfig = ScoringConfig()) -> bool:
    """Twin of the SQL ``dispersion_thin`` column."""
    return bool(peer_median > 0 and mad < cfg.min_relative_dispersion * peer_median)


def score_claims(
    con: duckdb.DuckDBPyConnection,
    cfg: ScoringConfig = ScoringConfig(),
    *,
    source_view: str = "claims_with_peers",
    view: str = SCORED_VIEW,
) -> duckdb.DuckDBPyConnection:
    """Create ``view`` with robust z-scores, confidence and preliminary flags.

    Expects :func:`claimsight.peers.build_peer_stats` to have run, so
    ``peer_median`` / ``peer_mad`` / ``peer_p25`` / ``peer_p75`` / ``peer_n`` /
    ``peer_level`` are available on ``source_view``.
    """
    # Drop the dependent view first. DuckDB refuses to replace a view that
    # other views reference, so a naive re-run of this function would fail as
    # soon as add_isolation_forest() had built on top of it. Idempotency here
    # is what lets the CLI and the test suite re-score freely.
    con.execute(f"DROP VIEW IF EXISTS {view}_final")
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {view} AS
        WITH normalized AS (
            SELECT c.*, {_normalization_sql()}
            FROM {source_view} AS c
        ),
        featured AS (
            SELECT n.*, {_feature_sql(cfg)}
            FROM normalized AS n
        )
        SELECT
            f.*,
            {_confidence_sql(cfg)},
            CASE WHEN dispersion_scale IS NULL OR dispersion_scale = 0 THEN NULL
                 ELSE (metric_value - peer_median) / dispersion_scale
            END AS robust_z
        FROM featured AS f
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE VIEW {view}_flagged AS
        SELECT *,
            {flagged_expression(cfg)} AS flagged_peer,
            {reason_expression(cfg)}  AS flag_reason
        FROM {view}
        """
    )
    return con


def add_isolation_forest(
    con: duckdb.DuckDBPyConnection,
    cfg: ScoringConfig = ScoringConfig(),
    *,
    view: str = SCORED_VIEW,
) -> dict[str, object]:
    """Add an Isolation Forest anomaly score as an independent second opinion.

    Fits on a deterministic subsample (the forest's own ``max_samples`` already
    subsamples per tree, so fitting on everything buys little and costs a lot on
    2 CPU cores), then scores every row. Results are written back into DuckDB
    keyed by :data:`CLAIM_KEY` so the join happens in the engine.

    Returns:
        Diagnostics: rows scored, fit sample size, elapsed seconds.
    """
    import time

    from sklearn.ensemble import IsolationForest

    features = list(cfg.iforest_features)
    keys = list(CLAIM_KEY)

    t0 = time.perf_counter()
    # ORDER BY the unique claim key. Not cosmetic:
    #
    # The fit subsample below is chosen *positionally* (rng.choice over row
    # indices), so the fitted forest depends on the order rows arrive in. This
    # view is built from joins and window aggregates, and DuckDB does not
    # guarantee a plan's output order across runs or thread schedules. Two
    # identical runs of the full FY2016 file disagreed by 37 forest flags
    # (1,923 vs 1,960) for exactly this reason -- which quietly invalidated the
    # "the pipeline is deterministic, so no model needs to be persisted" claim.
    # Sorting by a unique key costs ~0.1s on 197k rows and makes the subsample,
    # and therefore the forest, reproducible.
    frame = con.sql(
        f"""
        SELECT {", ".join(keys)}, {", ".join(features)}
        FROM {view}
        WHERE {" AND ".join(f"{f} IS NOT NULL" for f in features)}
        ORDER BY {", ".join(keys)}
        """
    ).df()

    if frame.empty:
        raise ValueError("no rows with complete Isolation Forest features")

    x = frame[features].to_numpy(dtype=float)
    # A row with a null feature would poison the fit; drop and report honestly.
    complete = ~np.isnan(x).any(axis=1)
    dropped = int((~complete).sum())
    x = x[complete]

    rng = np.random.default_rng(cfg.random_state)
    if len(x) > cfg.iforest_sample_size:
        idx = rng.choice(len(x), size=cfg.iforest_sample_size, replace=False)
        fit_x = x[idx]
    else:
        fit_x = x

    model = IsolationForest(
        n_estimators=cfg.iforest_estimators,
        contamination=cfg.iforest_contamination,
        random_state=cfg.random_state,
        n_jobs=-1,
    )
    model.fit(fit_x)
    # score_samples: higher (less negative) == more normal. Negate so that
    # larger == more anomalous, matching the direction of robust_z.
    scores = -model.score_samples(x)
    elapsed = time.perf_counter() - t0

    scored = frame.loc[complete, keys].copy()
    scored["iforest_score"] = scores
    # Rank into (0, 1] so the score is comparable across runs and explainable
    # as "more anomalous than 99.2% of claims".
    order = np.argsort(np.argsort(scores))
    scored["iforest_percentile"] = (order + 1) / len(scores)
    scored["iforest_flag"] = model.predict(x) == -1

    # Materialise the scores into a real catalog TABLE rather than leaving them
    # as a registered DataFrame.
    #
    # `con.register()` creates a *connection-local* virtual table. A view built
    # on top of it works on `con`, but breaks on any `con.cursor()` with
    # "Catalog Error: iforest_scores does not exist" -- because DuckDB cursors
    # share the database catalog, not Python-side replacement scans. The API
    # takes a cursor per request, so this distinction is the difference between
    # a working service and a 500 on every scored endpoint. Materialising also
    # avoids re-scanning a pandas frame on every query.
    con.register("_iforest_scores_frame", scored)
    con.execute("CREATE OR REPLACE TABLE iforest_scores AS SELECT * FROM _iforest_scores_frame")
    con.unregister("_iforest_scores_frame")

    con.execute(
        f"""
        CREATE OR REPLACE VIEW {view}_final AS
        SELECT s.*, i.iforest_score, i.iforest_percentile, i.iforest_flag,
               CASE WHEN s.flagged_peer AND i.iforest_flag THEN true ELSE false END
                   AS flagged_both
        FROM {view}_flagged AS s
        LEFT JOIN iforest_scores AS i
          ON {" AND ".join(f"s.{k} = i.{k}" for k in keys)}
        """
    )

    logger.info(
        "isolation forest: fit on %s rows, scored %s, dropped %s incomplete, %.2fs",
        f"{len(fit_x):,}",
        f"{len(scored):,}",
        f"{dropped:,}",
        elapsed,
    )
    return {
        "fit_sample_rows": len(fit_x),
        "scored_rows": len(scored),
        "dropped_incomplete": dropped,
        "elapsed_seconds": round(elapsed, 3),
    }


def summarise(
    con: duckdb.DuckDBPyConnection, view: str = f"{SCORED_VIEW}_final"
) -> dict[str, object]:
    """Headline numbers for the report and the README.

    Results are bound **by column name**, not by position. An earlier version
    indexed a result tuple, and adding one column to the SELECT silently shifted
    every downstream value. That is the kind of bug that produces a confident,
    wrong report -- so it is designed out here.
    """
    relation = con.sql(
        f"""
        SELECT
            count(*)                                               AS rows_scored,
            sum(CASE WHEN flagged_peer THEN 1 ELSE 0 END)          AS flagged_peer,
            sum(CASE WHEN iforest_flag THEN 1 ELSE 0 END)          AS flagged_iforest,
            sum(CASE WHEN flagged_both THEN 1 ELSE 0 END)          AS flagged_both,
            sum(CASE WHEN dispersion_degenerate THEN 1 ELSE 0 END) AS degenerate_rows,
            sum(CASE WHEN dispersion_thin THEN 1 ELSE 0 END)       AS thin_dispersion_rows,
            median(confidence)                                     AS median_confidence,
            max(robust_z)                                          AS max_robust_z,
            max(relative_excess)                                   AS max_relative_excess,
            -- Reported separately: "providers flagged" is ambiguous when two
            -- methods disagree, which on this dataset they mostly do.
            count(DISTINCT CASE WHEN flagged_peer THEN provider_id END) AS providers_peer,
            count(DISTINCT CASE WHEN flagged_both THEN provider_id END) AS providers_both
        FROM {view}
        """
    )
    row = dict(zip(relation.columns, relation.fetchone(), strict=True))
    total = int(row["rows_scored"] or 0)

    def pct(n: object) -> float:
        return round(100.0 * int(n) / total, 3) if total else 0.0

    flagged_peer = int(row["flagged_peer"] or 0)
    flagged_if = int(row["flagged_iforest"] or 0)
    flagged_both = int(row["flagged_both"] or 0)
    union = flagged_peer + flagged_if - flagged_both
    # Jaccard agreement between two unsupervised methods with no ground truth.
    jaccard = flagged_both / union if union else 0.0

    def num(key: str, digits: int = 4) -> float | None:
        value = row[key]
        return round(float(value), digits) if value is not None else None

    # How often each guard fired. This is the diagnostic that shows the
    # practical-significance and thin-dispersion filters are doing real work
    # rather than sitting inert in the query.
    reasons = con.sql(
        f"""
        SELECT flag_reason, count(*) AS n,
               round(100.0 * count(*) / sum(count(*)) OVER (), 3) AS pct
        FROM {view} GROUP BY 1 ORDER BY n DESC
        """
    ).fetchall()

    return {
        "rows_scored": total,
        "flagged_peer": flagged_peer,
        "flagged_peer_pct": pct(flagged_peer),
        "flagged_iforest": flagged_if,
        "flagged_iforest_pct": pct(flagged_if),
        "flagged_both": flagged_both,
        "flagged_both_pct": pct(flagged_both),
        "method_agreement_jaccard": round(jaccard, 4),
        "degenerate_dispersion_rows": int(row["degenerate_rows"] or 0),
        "thin_dispersion_rows": int(row["thin_dispersion_rows"] or 0),
        "median_confidence": num("median_confidence"),
        "max_robust_z": num("max_robust_z", 2),
        "max_relative_excess": num("max_relative_excess", 2),
        "distinct_providers_flagged_peer": int(row["providers_peer"] or 0),
        "distinct_providers_flagged_both": int(row["providers_both"] or 0),
        "flag_reasons": [{"reason": r[0], "rows": int(r[1]), "pct": float(r[2])} for r in reasons],
    }


def robust_z(
    value: float,
    median: float,
    mad: float,
    p25: float | None = None,
    p75: float | None = None,
) -> float | None:
    """Pure-Python twin of the SQL robust z, for the API and unit tests.

    Delegates to :func:`dispersion_scale` so it walks the *same* MAD -> IQR ->
    None chain as the SQL. This was a genuine bug before: an earlier version
    returned ``None`` whenever MAD was zero, while the SQL fell back to the IQR.
    For a peer group like ``[1, 5, 5, 5, 5, 5, 9, 9]`` (median 5, MAD 0, but
    IQR > 0) the two paths produced different answers for the same claim, so
    ``POST /score`` could contradict the batch report. A twin that diverges from
    its original is worse than no twin at all.

    Returns ``None`` only when no dispersion measure exists at all.
    """
    scale = dispersion_scale(mad, p25, p75)
    if not scale:
        return None
    return (value - median) / scale


def confidence(peer_n: int, discharges: int, cfg: ScoringConfig = ScoringConfig()) -> float:
    """Pure-Python twin of :func:`_confidence_sql`."""
    support = peer_n / (peer_n + cfg.support_halfwidth)
    volume = discharges / (discharges + cfg.volume_halfwidth)
    return math.sqrt(support * volume)
