"""Drift monitoring: detect when a new claims file stops looking like the baseline.

Why this exists
---------------
A scoring pipeline that runs once is a report. One that runs monthly against new
CMS releases is a system, and systems need to know when their inputs change.
Nothing here retrains anything -- the honest answer to "what do you do about
drift?" is *tell a human*, with a number attached.

Method: Population Stability Index
----------------------------------
PSI compares a current distribution against baseline bin edges derived from the
baseline's own quantiles::

    PSI = sum_i (actual_i - expected_i) * ln(actual_i / expected_i)

Because the baseline bins are its deciles, ``expected_i`` is 1/10 by
construction. Conventional interpretation:

    PSI < 0.10    no meaningful shift
    0.10 - 0.25   moderate shift, worth a look
    PSI > 0.25    significant shift, the baseline is no longer representative

PSI is used over a KS test because it is bucket-based and therefore explainable
to a non-statistician ("the share of claims in the top charge decile doubled"),
and over mean/variance comparison because billing distributions are heavily
skewed -- a mean can stay flat while the tail moves substantially, which is
exactly the change that matters here.

The baseline profile is stored in the run manifest, so drift can be computed
against a run from last month **without keeping that month's 34 MB file**.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import duckdb
import numpy as np

logger = logging.getLogger(__name__)

#: Columns whose distributions are monitored. Chosen because a shift in any of
#: them changes what "an outlier" means: charge levels, payment levels, case
#: volume, and the two derived signals the scorer actually thresholds on.
MONITORED_FEATURES: tuple[str, ...] = (
    "markup_ratio",
    "avg_covered_charges",
    "avg_total_payments",
    "avg_medicare_payments",
    "total_discharges",
    "relative_excess",
    "non_medicare_share",
)

#: Number of baseline quantile bins. Deciles are the convention for PSI and are
#: coarse enough to stay stable on small samples.
DEFAULT_BINS = 10

#: Epsilon so an empty bin yields a large-but-finite contribution instead of inf.
EPSILON = 1e-6

NO_DRIFT = 0.10
MODERATE_DRIFT = 0.25

#: Precision for values stored in a manifest. Rounding is not cosmetic: float
#: summation is order-dependent, so an unrounded mean can differ in its last ULP
#: between two saves of the same file depending on how the engine happened to
#: order rows. That would break byte-level manifest comparison, which is the
#: evidence for the claim that no model artifact needs to be persisted.
STORED_PRECISION = 6
STORED_PROPORTION_PRECISION = 9


@dataclass(frozen=True)
class FeatureDrift:
    """Drift for one monitored feature."""

    feature: str
    psi: float
    baseline_median: float | None
    current_median: float | None
    baseline_null_rate: float
    current_null_rate: float
    rows: int

    @property
    def interpretation(self) -> str:
        return interpret_psi(self.psi)

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "psi": round(self.psi, 5),
            "interpretation": self.interpretation,
            "baseline_median": self.baseline_median,
            "current_median": self.current_median,
            "baseline_null_rate": round(self.baseline_null_rate, 5),
            "current_null_rate": round(self.current_null_rate, 5),
            "rows": self.rows,
        }


@dataclass(frozen=True)
class DriftReport:
    """Drift across every monitored feature, plus a single verdict."""

    features: tuple[FeatureDrift, ...]
    baseline_flag_rate_pct: float | None
    current_flag_rate_pct: float | None
    baseline_rows: int | None
    current_rows: int

    @property
    def max_psi(self) -> float:
        return max((f.psi for f in self.features), default=0.0)

    @property
    def worst_feature(self) -> str | None:
        if not self.features:
            return None
        return max(self.features, key=lambda f: f.psi).feature

    @property
    def verdict(self) -> str:
        return interpret_psi(self.max_psi)

    @property
    def flag_rate_delta(self) -> float | None:
        if self.baseline_flag_rate_pct is None or self.current_flag_rate_pct is None:
            return None
        return round(self.current_flag_rate_pct - self.baseline_flag_rate_pct, 4)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "max_psi": round(self.max_psi, 5),
            "worst_feature": self.worst_feature,
            "current_rows": self.current_rows,
            "baseline_rows": self.baseline_rows,
            "baseline_flag_rate_pct": self.baseline_flag_rate_pct,
            "current_flag_rate_pct": self.current_flag_rate_pct,
            "flag_rate_delta_pp": self.flag_rate_delta,
            "features": [f.as_dict() for f in self.features],
        }


def interpret_psi(psi: float) -> str:
    """Conventional PSI bands, in one place so they cannot drift apart."""
    if psi < NO_DRIFT:
        return "no meaningful shift"
    if psi < MODERATE_DRIFT:
        return "moderate shift"
    return "significant shift"


def distribution_profile(
    con: duckdb.DuckDBPyConnection,
    view: str = "claim_scores_final",
    *,
    features: Sequence[str] = MONITORED_FEATURES,
    bins: int = DEFAULT_BINS,
) -> dict[str, Any]:
    """Summarise each monitored feature so a later run can be compared to it.

    Stores interior quantile edges (not raw data), so a manifest carries enough
    to compute PSI months later without retaining the source file. Also stores
    count, median and null rate for a cheap human-readable diff.
    """
    available = {
        row[0]
        for row in con.sql(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{view}'"
        ).fetchall()
    }
    profile: dict[str, Any] = {"view": view, "bins": bins, "features": {}}

    for feature in features:
        if feature not in available:
            logger.warning("cannot profile absent column: %s", feature)
            continue
        # ORDER BY makes the profile canonical. Without it the value *set* is
        # identical but the *sequence* is not, and np.mean over a reordered
        # array can differ by an ULP.
        values = np.asarray(
            [
                row[0]
                for row in con.sql(
                    f'SELECT "{feature}" FROM {view} WHERE "{feature}" IS NOT NULL ORDER BY 1'
                ).fetchall()
            ],
            dtype=float,
        )
        total = con.sql(f'SELECT count(*) FROM {view}').fetchone()[0]
        null_rate = 1.0 - (len(values) / total) if total else 0.0

        if len(values) < bins:
            # Too few points for stable quantiles; record what we can and skip
            # the edges so compute_drift reports it as incomparable rather than
            # inventing a PSI from noise.
            profile["features"][feature] = {
                "count": len(values),
                "null_rate": round(float(null_rate), STORED_PROPORTION_PRECISION),
                "median": (
                    round(float(np.median(values)), STORED_PRECISION) if len(values) else None
                ),
                "edges": [],
                "expected": [],
            }
            continue

        quantiles = np.linspace(0.0, 1.0, bins + 1)[1:-1]
        # np.unique matters for tied columns: total_discharges is a small
        # integer, so several deciles land on the same value and collapse into
        # one edge. That collapse is why the baseline proportions are measured
        # rather than assumed uniform -- with ties the bins are NOT equal-sized.
        # Round BEFORE counting, and re-unique afterwards. The stored edges are
        # what a future run is binned against, so the baseline proportions must
        # be measured with those exact edges. Counting with unrounded edges and
        # storing rounded ones lets a value sitting on a boundary fall into a
        # different bin at comparison time -- which shows up as "drift" when
        # nothing moved.
        edges = np.unique(np.round(np.quantile(values, quantiles), STORED_PRECISION))
        counts, _ = np.histogram(values, bins=np.concatenate([[-np.inf], edges, [np.inf]]))
        profile["features"][feature] = {
            "count": len(values),
            "null_rate": round(float(null_rate), STORED_PROPORTION_PRECISION),
            "median": round(float(np.median(values)), STORED_PRECISION),
            "mean": round(float(np.mean(values)), STORED_PRECISION),
            "min": round(float(np.min(values)), STORED_PRECISION),
            "max": round(float(np.max(values)), STORED_PRECISION),
            "edges": [float(e) for e in edges],
            "expected": [
                round(float(c / counts.sum()), STORED_PROPORTION_PRECISION) for c in counts
            ],
        }

    row_count = con.sql(f"SELECT count(*) FROM {view}").fetchone()[0]
    profile["rows"] = int(row_count)
    flag_rate = con.sql(
        f"SELECT 100.0 * sum(CASE WHEN flagged_peer THEN 1 ELSE 0 END) / count(*) FROM {view}"
    ).fetchone()[0]
    profile["flag_rate_pct"] = (
        round(float(flag_rate), STORED_PRECISION) if flag_rate is not None else None
    )
    return profile


def population_stability_index(
    baseline_edges: Sequence[float],
    current_values: np.ndarray,
    *,
    expected: Sequence[float] | None = None,
) -> float:
    """PSI of ``current_values`` against baseline bin ``edges``.

    Args:
        baseline_edges: Interior bin edges, normally the baseline's own deciles.
        current_values: Observations to score against them.
        expected: Baseline proportion per bin. Pass the measured value from
            :func:`distribution_profile`; ``None`` assumes uniform bins, which is
            only correct when the baseline has no ties. Getting this wrong is
            not cosmetic -- it makes a distribution differ from *itself*.

    Empty bins are floored at :data:`EPSILON` on both sides: a bin that was
    populated at baseline and is empty now should score high, not infinite.
    """
    edges = np.asarray(baseline_edges, dtype=float)
    if edges.size == 0:
        raise ValueError("baseline has no bin edges; cannot compute PSI")

    n_bins = edges.size + 1
    if expected is None:
        expected_arr = np.full(n_bins, 1.0 / n_bins)
    else:
        expected_arr = np.asarray(expected, dtype=float)
        if expected_arr.size != n_bins:
            raise ValueError(
                f"expected {n_bins} baseline proportions, got {expected_arr.size}"
            )

    if current_values.size == 0:
        # Nothing to compare: put all mass in the first bin so PSI is large and
        # finite rather than nan.
        actual = np.full(n_bins, EPSILON)
        actual[0] = 1.0 - EPSILON * (n_bins - 1)
    else:
        counts, _ = np.histogram(current_values, bins=np.concatenate(
            [[-np.inf], edges, [np.inf]]
        ))
        actual = counts / counts.sum()

    actual = np.clip(actual, EPSILON, None)
    expected_arr = np.clip(expected_arr, EPSILON, None)
    # Renormalise after clipping so both are proper distributions.
    actual = actual / actual.sum()
    expected_arr = expected_arr / expected_arr.sum()
    return float(np.sum((actual - expected_arr) * np.log(actual / expected_arr)))


def compute_drift(
    baseline_profile: dict[str, Any],
    con: duckdb.DuckDBPyConnection,
    view: str = "claim_scores_final",
    *,
    features: Sequence[str] = MONITORED_FEATURES,
) -> DriftReport:
    """Compare the current relation against a stored baseline profile.

    Args:
        baseline_profile: ``distribution_profile()`` output from an earlier run,
            typically read back out of a run manifest.
        con: Connection holding the current scored view.
        view: Current view name.
    """
    baseline_features = baseline_profile.get("features", {})
    results: list[FeatureDrift] = []

    for feature in features:
        spec = baseline_features.get(feature)
        if spec is None:
            logger.warning("baseline has no profile for %s; skipping", feature)
            continue
        edges = spec.get("edges") or []
        if not edges:
            logger.warning("baseline profile for %s has no bin edges; skipping", feature)
            continue

        current = np.asarray(
            [
                row[0]
                for row in con.sql(
                    f'SELECT "{feature}" FROM {view} WHERE "{feature}" IS NOT NULL'
                ).fetchall()
            ],
            dtype=float,
        )
        total = con.sql(f"SELECT count(*) FROM {view}").fetchone()[0]
        current_median = con.sql(
            f'SELECT median("{feature}") FROM {view}'
        ).fetchone()[0]

        results.append(
            FeatureDrift(
                feature=feature,
                psi=population_stability_index(edges, current, expected=spec.get("expected")),
                baseline_median=spec.get("median"),
                current_median=float(current_median) if current_median is not None else None,
                baseline_null_rate=float(spec.get("null_rate", 0.0)),
                current_null_rate=1.0 - (len(current) / total) if total else 0.0,
                rows=int(total),
            )
        )

    current_flag_rate = con.sql(
        f"SELECT 100.0 * sum(CASE WHEN flagged_peer THEN 1 ELSE 0 END) / count(*) FROM {view}"
    ).fetchone()[0]

    return DriftReport(
        features=tuple(results),
        baseline_flag_rate_pct=baseline_profile.get("flag_rate_pct"),
        current_flag_rate_pct=(
            round(float(current_flag_rate), 4) if current_flag_rate is not None else None
        ),
        baseline_rows=baseline_profile.get("rows"),
        current_rows=int(con.sql(f"SELECT count(*) FROM {view}").fetchone()[0]),
    )
