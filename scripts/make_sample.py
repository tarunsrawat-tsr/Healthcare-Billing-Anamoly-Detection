"""Build the committed stratified sample from the full CMS file.

Why a sample at all
-------------------
The full FY2016 file is 34 MB / 197,283 rows. It is fetched by ``make data`` and
never committed: large datasets do not belong in Git. But a repository whose
tests and demo depend on a 34 MB download is a repository whose CI breaks the
day that host moves. So a **stratified ~20k-row sample is committed instead**,
built to preserve the properties the pipeline actually depends on:

* every DRG is represented, so all peer-hierarchy fallback paths get exercised
  (a plain random sample would drop rare DRGs entirely);
* every state is represented;
* **every row flagged as an outlier on the full data is force-included**, so the
  demo and tests have real anomalies to find instead of a bland sample where the
  interesting rows were sampled away;
* selection is seeded and hash-based, so the artifact is reproducible.

Usage::

    python scripts/make_sample.py path/to/full.csv
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import duckdb

from claimsight import schema
from claimsight.ingest import load_claims
from claimsight.peers import build_peer_stats
from claimsight.scoring import ScoringConfig, add_isolation_forest, score_claims

TARGET_ROWS = 20_000
SEED = "42"


def _select_keys(con: duckdb.DuckDBPyConnection, total: int) -> int:
    """Materialise the set of (provider_id, drg) keys to keep.

    Returns the number of keys selected.
    """
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE flagged_keys AS
        SELECT provider_id, drg FROM claim_scores_final WHERE flagged_peer
        """
    )
    frac = TARGET_ROWS / total
    con.execute(f"CREATE OR REPLACE TEMP TABLE quota AS SELECT {frac!r}::DOUBLE AS frac")

    # Proportional draw per DRG over the non-flagged remainder. Ordering by a
    # seeded hash gives a deterministic pseudo-random selection without having
    # to permute 197k rows in memory.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE sampled_keys AS
        SELECT provider_id, drg FROM (
            SELECT provider_id, drg,
                   row_number() OVER (
                       PARTITION BY drg
                       ORDER BY md5('{SEED}' || provider_id || '|' || drg)
                   ) AS rn,
                   count(*) OVER (PARTITION BY drg) AS n_drg
            FROM claims
            WHERE (provider_id, drg) NOT IN (SELECT provider_id, drg FROM flagged_keys)
        )
        WHERE rn <= greatest(1, CAST(n_drg * (SELECT frac FROM quota) AS INT))
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE selected_keys AS
        SELECT provider_id, drg FROM flagged_keys
        UNION
        SELECT provider_id, drg FROM sampled_keys
        """
    )
    return int(con.sql("SELECT count(*) FROM selected_keys").fetchone()[0])


def build_sample(source: Path, destination: Path) -> dict[str, object]:
    """Write a stratified sample of ``source`` to ``destination``, preserving raw formatting.

    The sample is written from the *raw* file rather than from parsed views, so
    it keeps the original ``"$1,016,806.46"`` currency strings and zero-padded
    provider ids. A sample that had already been cleaned would let the ingest
    layer's parsing tests pass trivially and hide regressions.
    """
    con = duckdb.connect()
    load_claims(source, con)
    build_peer_stats(con)
    cfg = ScoringConfig()
    score_claims(con, cfg)
    add_isolation_forest(con, cfg)

    total = int(con.sql("SELECT count(*) FROM claims").fetchone()[0])
    n_keys = _select_keys(con, total)

    destination.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"""
        COPY (
            SELECT raw.*
            FROM {schema.source_relation(str(source))} AS raw
            JOIN selected_keys AS k
              ON trim(raw."Provider Id") = k.provider_id
             AND trim(raw."DRG Definition") = k.drg
        ) TO '{destination}' (HEADER, DELIMITER ',')
        """
    )

    verify = duckdb.connect()
    check = lambda sql: verify.sql(sql).fetchone()[0]  # noqa: E731
    sample_src = schema.source_relation(str(destination))

    return {
        "source_rows": total,
        "keys_selected": n_keys,
        "sample_rows": int(check(f"SELECT count(*) FROM {sample_src}")),
        "distinct_drgs": int(check(f'SELECT count(DISTINCT "DRG Definition") FROM {sample_src}')),
        "distinct_states": int(check(f'SELECT count(DISTINCT "Provider State") FROM {sample_src}')),
        "distinct_providers": int(check(f'SELECT count(DISTINCT "Provider Id") FROM {sample_src}')),
        "sample_bytes": destination.stat().st_size,
        "sample_md5": hashlib.md5(destination.read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the committed stratified sample.")
    parser.add_argument("source", type=Path, help="full CMS CSV (see `make data`)")
    parser.add_argument(
        "destination",
        type=Path,
        nargs="?",
        default=Path("data/sample/cms_inpatient_fy2016_sample.csv"),
    )
    args = parser.parse_args()

    stats = build_sample(args.source, args.destination)
    width = max(len(k) for k in stats)
    for key, value in stats.items():
        print(f"{key:>{width}}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
