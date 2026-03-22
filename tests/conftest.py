"""Shared fixtures.

``tiny_claims.csv`` is hand-built rather than sampled so that every branch of
the pipeline has a row that exercises it: a flaggable outlier, a degenerate
peer group, a singleton forcing full hierarchy fallback, comma-formatted
counts, zero-padded identifiers, and a corrupt money value that must be
rejected rather than silently dropped.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from claimsight.ingest import load_claims
from claimsight.peers import build_peer_stats
from claimsight.scoring import ScoringConfig, score_claims

FIXTURES = Path(__file__).parent / "fixtures"
TINY_CLAIMS = FIXTURES / "tiny_claims.csv"

DRG_A = (
    "470 - MAJOR JOINT REPLACEMENT OR REATTACHMENT OF LOWER EXTREMITY W/O MCC"
)
DRG_B_UNIFORM = "291 - HEART FAILURE & SHOCK W MCC"
DRG_C_SINGLETON = "001 - HEART TRANSPLANT OR IMPLANT OF HEART ASSIST SYSTEM W MCC"
OUTLIER_PROVIDER = "010040"
CORRUPT_PROVIDER = "010050"


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


@pytest.fixture
def ingested(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    load_claims(TINY_CLAIMS, con)
    return con


@pytest.fixture
def with_peers(ingested: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    build_peer_stats(ingested)
    return ingested


@pytest.fixture
def scored(with_peers: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    score_claims(with_peers, ScoringConfig())
    return with_peers


@pytest.fixture
def cfg() -> ScoringConfig:
    return ScoringConfig()
