"""Run registry: persist what was computed, from what, with which settings.

Why provenance rather than a pickled model
------------------------------------------
This module deliberately does **not** serialise the Isolation Forest. The whole
pipeline is deterministic (``random_state`` is fixed) and rebuilds 197,283 rows
in about nine seconds from inputs that are either committed to the repository or
fetched by a checksummed script. Persisting a joblib artifact would add a
version-compatibility surface -- joblib and scikit-learn both break unpickling
across releases -- in exchange for saving nine seconds.

What actually makes a run reproducible is knowing the input, the configuration
and the code version. That is what gets written here:

    artifacts/runs/<run_id>/
    ├── manifest.json      # inputs, config, metrics, environment, timings
    └── outliers.parquet   # the flagged rows: the actual human review queue

``outliers.parquet`` is persisted because it *is* the product. A reviewer queue
that only exists inside a live process is not something an analyst can work
through, hand to a colleague, or diff against next month's run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from . import __version__
from .config import Settings, get_settings
from .monitor import DriftReport, compute_drift, distribution_profile
from .peers import peer_level_summary
from .scoring import SCORED_VIEW, ScoringConfig, summarise

logger = logging.getLogger(__name__)

FINAL_VIEW = f"{SCORED_VIEW}_final"
OUTLIERS_FILE = "outliers.parquet"
MANIFEST_FILE = "manifest.json"

#: Columns written to the review queue. Ordered so a human reading the Parquet
#: sees who/what first, then the evidence, then the score.
REVIEW_COLUMNS: tuple[str, ...] = (
    "provider_id",
    "provider_name",
    "city",
    "state",
    "hrr",
    "drg",
    "total_discharges",
    "avg_covered_charges",
    "avg_total_payments",
    "avg_medicare_payments",
    "markup_ratio",
    "peer_level",
    "peer_n",
    "peer_median",
    "peer_mad",
    "relative_excess",
    "robust_z",
    "confidence",
    "flag_reason",
    "flagged_peer",
    "iforest_flag",
    "flagged_both",
)


@dataclass(frozen=True)
class RunRecord:
    """One scored run, identified by content and time rather than a mutable tag."""

    run_id: str
    path: Path
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def outliers_path(self) -> Path:
        return self.path / OUTLIERS_FILE

    @property
    def created_at(self) -> str:
        return str(self.manifest.get("created_at", ""))

    @property
    def rows_flagged(self) -> int:
        metrics = self.manifest.get("metrics", {})
        return int(metrics.get("flagged_peer", 0))


def _file_digest(path: Path, algorithm: str = "sha256", limit: int = 1 << 22) -> str:
    """Hash a file, streaming.

    Reads in chunks so a 34 MB input does not get loaded whole; ``limit`` caps
    nothing about correctness, it just documents the chunk size used.
    """
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while chunk := handle.read(limit):
            digest.update(chunk)
    return digest.hexdigest()


def _environment() -> dict[str, str]:
    """Versions that could change a result. Not decorative: a DuckDB upgrade has
    changed aggregate behaviour before, and a manifest without versions cannot
    explain a metric that moved."""
    import duckdb as _duckdb
    import numpy as _numpy
    import sklearn as _sklearn

    return {
        "claimsight": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "duckdb": _duckdb.__version__,
        "numpy": _numpy.__version__,
        "scikit-learn": _sklearn.__version__,
    }


def save_run(
    con: duckdb.DuckDBPyConnection,
    config: ScoringConfig,
    *,
    source: Path,
    settings: Settings | None = None,
    min_support: int = 8,
    ingest_report: dict[str, Any] | None = None,
    elapsed_seconds: float | None = None,
    run_id: str | None = None,
) -> RunRecord:
    """Persist a scored run: manifest plus the flagged review queue.

    Args:
        con: Connection holding the scored views (``claim_scores_final``).
        config: The scoring configuration actually used.
        source: The claims file that was scored -- hashed into the manifest.
        run_id: Override the generated identifier (useful in tests).

    Returns:
        A :class:`RunRecord` pointing at the written directory.
    """
    settings = settings or get_settings()
    runs_dir = settings.artifacts_dir / "runs"
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = runs_dir / run_id
    destination.mkdir(parents=True, exist_ok=True)

    available = {
        row[0]
        for row in con.sql(
            f"SELECT column_name FROM information_schema.columns WHERE table_name = '{FINAL_VIEW}'"
        ).fetchall()
    }
    columns = [c for c in REVIEW_COLUMNS if c in available]
    missing = [c for c in REVIEW_COLUMNS if c not in available]
    if missing:
        # Warn rather than fail: the forest may have been skipped, in which case
        # iforest_flag is legitimately absent.
        logger.warning("review queue missing columns (not written): %s", missing)

    con.execute(
        f"""
        COPY (
            SELECT {", ".join(columns)}
            FROM {FINAL_VIEW}
            WHERE flagged_peer
            ORDER BY relative_excess DESC
        ) TO '{destination / OUTLIERS_FILE}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    queue_rows = con.sql(f"SELECT count(*) FROM {FINAL_VIEW} WHERE flagged_peer").fetchone()[0]

    manifest: dict[str, Any] = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_version": 1,
        "source": {
            "path": str(source),
            "name": source.name,
            "bytes": source.stat().st_size if source.exists() else None,
            "sha256": _file_digest(source) if source.exists() else None,
        },
        "config": {**asdict(config), "min_support": min_support},
        "environment": _environment(),
        "ingest": ingest_report or {},
        "metrics": summarise(con, FINAL_VIEW),
        "peer_levels": peer_level_summary(con, "claims_with_peers"),
        # Quantile edges per monitored feature. This is what makes drift
        # computable against this run later without keeping its source file.
        "distribution": distribution_profile(con, FINAL_VIEW),
        "review_queue": {
            "file": OUTLIERS_FILE,
            "rows": int(queue_rows),
            "columns": columns,
            "omitted_columns": missing,
        },
        "elapsed_seconds": elapsed_seconds,
        "reproducibility": {
            "deterministic": True,
            "note": (
                "The Isolation Forest is seeded (random_state) and the peer "
                "statistics are pure aggregates, so re-running on the same input "
                "with the same config reproduces this manifest exactly. No model "
                "artifact is stored; the manifest plus the committed data is the "
                "reproduction recipe."
            ),
        },
    }

    (destination / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, default=str))
    logger.info("saved run %s: %d flagged rows -> %s", run_id, queue_rows, destination)
    return RunRecord(run_id=run_id, path=destination, manifest=manifest)


def list_runs(settings: Settings | None = None) -> list[RunRecord]:
    """Every saved run, newest first."""
    settings = settings or get_settings()
    runs_dir = settings.artifacts_dir / "runs"
    if not runs_dir.exists():
        return []
    records = []
    for directory in sorted(runs_dir.iterdir(), reverse=True):
        manifest_path = directory / MANIFEST_FILE
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            logger.warning("skipping unreadable manifest: %s", manifest_path)
            continue
        records.append(RunRecord(run_id=directory.name, path=directory, manifest=manifest))
    return records


def load_run(run_id: str, settings: Settings | None = None) -> RunRecord:
    """Load one run by identifier.

    Raises:
        KeyError: if no such run exists. The message lists what *is* available,
            because a typo'd run id is otherwise a dead end.
    """
    for record in list_runs(settings):
        if record.run_id == run_id:
            return record
    available = [r.run_id for r in list_runs(settings)]
    raise KeyError(f"no run {run_id!r}; available: {available or '(none)'}")


def latest_run(settings: Settings | None = None) -> RunRecord | None:
    """The most recent run, or ``None`` if nothing has been saved."""
    runs = list_runs(settings)
    return runs[0] if runs else None


def compare_runs(baseline: RunRecord, current: RunRecord) -> dict[str, Any]:
    """Diff two runs' headline metrics.

    The point of a registry is not storage, it is being able to say what changed.
    Only metrics that are numeric and present in both manifests are compared.
    """
    before = baseline.manifest.get("metrics", {})
    after = current.manifest.get("metrics", {})
    changes: dict[str, dict[str, float | None]] = {}
    for key in sorted(set(before) & set(after)):
        old, new = before[key], after[key]
        if isinstance(old, (int, float)) and isinstance(new, (int, float)):
            delta = new - old
            pct = (100.0 * delta / old) if old else None
            changes[key] = {
                "baseline": old,
                "current": new,
                "delta": round(delta, 6),
                "pct_change": round(pct, 3) if pct is not None else None,
            }
    return {
        "baseline_run": baseline.run_id,
        "current_run": current.run_id,
        "baseline_source_sha256": baseline.manifest.get("source", {}).get("sha256"),
        "current_source_sha256": current.manifest.get("source", {}).get("sha256"),
        "same_input": baseline.manifest.get("source", {}).get("sha256")
        == current.manifest.get("source", {}).get("sha256"),
        "config_changed": baseline.manifest.get("config") != current.manifest.get("config"),
        "metric_changes": changes,
    }


def drift_against_run(
    baseline: RunRecord | str,
    con: duckdb.DuckDBPyConnection,
    view: str = FINAL_VIEW,
    settings: Settings | None = None,
) -> DriftReport:
    """Score drift of the data currently in ``con`` against a saved run.

    Args:
        baseline: A :class:`RunRecord`, or a run id to look up.
        con: Connection holding the freshly scored relation.
        view: Name of that relation.

    Returns:
        A :class:`~claimsight.monitor.DriftReport`.

    Raises:
        KeyError: if the run id is unknown.
        ValueError: if the baseline manifest predates distribution profiling.
            Failing loudly beats silently reporting "no drift" for a comparison
            that never happened.
    """
    record = baseline if isinstance(baseline, RunRecord) else load_run(baseline, settings)
    profile = record.manifest.get("distribution")
    if not profile:
        raise ValueError(
            f"run {record.run_id!r} has no distribution profile in its manifest; "
            "it was saved before drift monitoring existed. Re-save a baseline run "
            "with the current version to enable comparison."
        )
    return compute_drift(profile, con, view)
