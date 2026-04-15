"""Pydantic schemas for the claimsight API.

Validation lives here rather than in handlers so that every constraint is
declarative, appears in the generated OpenAPI spec, and is enforced before any
query runs. Numeric fields carry explicit bounds: a negative charge or a
zero-discharge row is not a scoring edge case, it is an invalid request, and
saying so at the boundary is cheaper than defending against it downstream.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: A DRG definition string, e.g.
#: ``"470 - MAJOR JOINT REPLACEMENT OR REATTACHMENT OF LOWER EXTREMITY W/O MCC"``.
DrgStr = str


class PeerLevelUsage(BaseModel):
    """How many rows each rung of the peer hierarchy served."""

    peer_level: str
    rows_scored: int
    pct_of_rows: float
    smallest_group: int
    median_group_size: float
    largest_group: int


class FlagReason(BaseModel):
    reason: str
    rows: int
    pct: float


class SummaryResponse(BaseModel):
    rows_scored: int
    flagged_peer: int
    flagged_peer_pct: float
    flagged_iforest: int
    flagged_iforest_pct: float
    flagged_both: int
    flagged_both_pct: float
    method_agreement_jaccard: float
    degenerate_dispersion_rows: int
    thin_dispersion_rows: int
    median_confidence: float | None
    max_robust_z: float | None
    max_relative_excess: float | None
    distinct_providers_flagged_peer: int
    distinct_providers_flagged_both: int
    flag_reasons: list[FlagReason]


class OutlierRow(BaseModel):
    provider_id: str
    provider_name: str
    city: str
    state: str
    drg: str
    total_discharges: int
    #: Raw dollars, not just the ratio: "$53,062 charged against $953 paid" is
    #: the sentence a reviewer needs, and it cannot be recovered from the ratio.
    avg_covered_charges: float
    avg_medicare_payments: float
    markup_ratio: float
    peer_median: float
    peer_n: int
    peer_level: str
    relative_excess: float
    robust_z: float | None
    confidence: float
    flag_reason: str
    #: Present because a provider scorecard mixes flagged and unflagged claims;
    #: in ``GET /outliers`` it is always true by construction.
    flagged_peer: bool = True
    flagged_both: bool


class ProviderScorecard(BaseModel):
    provider_id: str
    provider_name: str
    city: str
    state: str
    hrr: str
    drgs_reported: int
    total_discharges: int
    flagged_claims: int
    median_markup_ratio: float | None
    median_confidence: float | None
    max_relative_excess: float | None
    claims: list[OutlierRow] = Field(
        default_factory=list, description="highest-excess claims, most recent first"
    )


class ScoreRequest(BaseModel):
    """A hypothetical claim to score against the loaded peer statistics.

    This is the endpoint that justifies keeping pure-Python twins of the SQL
    scoring maths: the batch pipeline scores 197k rows in DuckDB, but a single
    ad-hoc "what if" request must produce an identical answer, and it does so
    through :func:`claimsight.scoring.robust_z` and
    :func:`claimsight.scoring.confidence`. ``tests/test_scoring.py`` pins the
    twins to the SQL so the two paths cannot diverge.
    """

    #: The example below is a REAL row from the FY2016 file -- Denton Regional
    #: Medical Center (TX) billing DRG 440 in the Dallas HRR: $53,062 charged
    #: against $953 of Medicare payment, a 55.7x markup.
    #:
    #: It is the highest-excess claim in the *committed 24,370-row sample*
    #: (6.09x..7.46x its peer median depending on which file is loaded: the
    #: sample's thinner Texas peer group resolves to the national rung with 49
    #: peers at a 7.5x median, the full file's to drg_state with 31 peers at
    #: 9.15x). On the full 197,283-row file it ranks 8th. Either way it flags,
    #: which is the property that matters here.
    #:
    #: Chosen so that a reviewer clicking "Try it out" in Swagger gets a flagged
    #: verdict with a dramatic, explainable number on their very first request.
    #: An earlier example was a real but *unremarkable* DRG-470 row (markup 15.3
    #: against a peer median of 14.5), which correctly returned "within peer
    #: range" -- accurate, and a terrible demo. ``tests/test_docs.py`` asserts
    #: both that the example resolves against the data and that it flags.
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "examples": [
            {
                "drg": "440 - DISORDERS OF PANCREAS EXCEPT MALIGNANCY W/O CC/MCC",
                "hrr": "TX - Dallas",
                "state": "TX",
                "total_discharges": 11,
                "avg_covered_charges": 53062.36,
                "avg_medicare_payments": 953.09,
            }
        ]
    })

    drg: DrgStr = Field(min_length=1, max_length=200)
    hrr: str = Field(min_length=1, max_length=200)
    state: str = Field(min_length=2, max_length=2, description="Two-letter state code")
    total_discharges: int = Field(gt=0, le=100_000)
    avg_covered_charges: float = Field(gt=0, le=1e9)
    avg_medicare_payments: float = Field(gt=0, le=1e9)

    @field_validator("state")
    @classmethod
    def _upper_state(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("drg", "hrr")
    @classmethod
    def _collapse_whitespace(cls, value: str) -> str:
        return " ".join(value.split())

    @property
    def markup_ratio(self) -> float:
        return self.avg_covered_charges / self.avg_medicare_payments


class ScoreVerdict(BaseModel):
    """Result of scoring one hypothetical claim."""

    markup_ratio: float
    peer_found: bool
    peer_level: str | None = None
    peer_n: int | None = None
    peer_median: float | None = None
    peer_mad: float | None = None
    relative_excess: float | None = None
    robust_z: float | None = None
    confidence: float | None = None
    would_flag: bool = False
    reason: str = Field(description="Human-readable explanation of the verdict")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    rows_loaded: int
    source_file: str
    pipeline_seconds: float


class DistributionBin(BaseModel):
    """One histogram bucket, in the column's own units."""

    index: int = Field(description="1-based bucket position")
    lo: float = Field(description="Lower edge, transformed back to original units")
    hi: float = Field(description="Upper edge, transformed back to original units")
    count: int
    flagged: int = Field(description="Rows in this bucket that the peer scorer flagged")


class DistributionResponse(BaseModel):
    """Server-side histogram of one whitelisted numeric column.

    Binning happens in DuckDB rather than shipping 197k values to the browser:
    the payload is ~40 numbers either way, and the server already has the data
    in a columnar engine that can compute it in milliseconds.
    """

    column: str
    transform: Literal["log", "identity"]
    bins: int
    rows_total: int = Field(description="Non-null rows for this column")
    rows_binned: int = Field(
        description="Rows actually placed in buckets; fewer than rows_total when "
        "a log transform excluded non-positive values"
    )
    min: float | None = None
    max: float | None = None
    median: float | None = None
    mean: float | None = None
    p95: float | None = None
    p99: float | None = None
    buckets: list[DistributionBin]
    note: str | None = Field(
        default=None, description="Anything the reader must know to interpret the chart"
    )


class ReferenceData(BaseModel):
    """Distinct dimension values, so a client can build valid requests.

    A DRG definition string is ~70 characters and must match the dataset
    exactly; asking a human to type one is asking for a 404. This is what lets
    the dashboard offer a picker instead of a text box.
    """

    drgs: list[str]
    hrrs: list[str]
    states: list[str]
    drg_count: int
    hrr_count: int
    state_count: int


class ErrorResponse(BaseModel):
    detail: str
