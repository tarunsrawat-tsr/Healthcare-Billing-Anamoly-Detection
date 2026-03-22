"""FastAPI service over the scored claims dataset.

Design notes
------------
The pipeline runs **once at startup** (~9s for 197k rows, ~2s for the committed
sample) and every request is then a lookup against DuckDB views. That trades
startup time for per-request latency, which is the right trade for a service
whose data is a static published file.

``POST /score`` reuses the *same* SQL guard expressions as the batch pipeline
(:func:`claimsight.scoring.flagged_expression` and
:func:`claimsight.scoring.reason_expression`) evaluated against a one-row inline
relation. It does not reimplement the flagging rules in Python, because a second
copy would eventually disagree with the first -- and then the API would answer
"would this be flagged?" differently from the report that produced the numbers.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import duckdb
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .. import __version__
from ..peers import peer_level_summary
from ..scoring import (
    ScoringConfig,
    flagged_expression,
    is_degenerate,
    is_thin,
    reason_expression,
    summarise,
)
from ..scoring import (
    confidence as confidence_twin,
)
from ..scoring import (
    robust_z as robust_z_twin,
)
from .deps import FINAL_VIEW, Pipeline, build_pipeline
from .schemas import (
    ErrorResponse,
    HealthResponse,
    OutlierRow,
    PeerLevelUsage,
    ProviderScorecard,
    ScoreRequest,
    ScoreVerdict,
    SummaryResponse,
)

logger = logging.getLogger(__name__)


def get_pipeline(request: Request) -> Pipeline:
    """Resolve the pipeline built at startup.

    Module-level rather than nested inside the app factory: under
    ``from __future__ import annotations`` FastAPI resolves parameter
    annotations with ``typing.get_type_hints``, which can only see module
    globals. A locally-defined alias silently degrades into a required *query
    parameter*, which surfaces as a confusing 422 rather than a wiring error.
    """
    pipeline: Pipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:  # pragma: no cover - lifespan guarantees this
        raise HTTPException(status_code=503, detail="pipeline not ready")
    return pipeline


#: Dependency alias, injectable into any route.
PipelineDep = Annotated[Pipeline, Depends(get_pipeline)]

#: Decimal places for floats in ``POST /score`` responses.
#:
#: The batch pipeline stores full precision in DuckDB; the API rounds for
#: presentation so JSON stays readable. This is a deliberate, documented
#: difference -- rounding is applied *after* every guard and threshold has been
#: evaluated, so it can never change a verdict. Tests compare these fields with
#: a tolerance of ``10 ** -RESPONSE_PRECISION``.
RESPONSE_PRECISION = 4


def _round(value: float | None, digits: int = RESPONSE_PRECISION) -> float | None:
    """Round for presentation, passing None through untouched."""
    return round(float(value), digits) if value is not None else None


def create_app(
    *,
    source: Path | None = None,
    config: ScoringConfig | None = None,
    with_forest: bool = True,
) -> FastAPI:
    """Application factory.

    Injectable so tests can point the service at a small fixture instead of the
    24k-row sample, and so a module-level ``app`` can still exist for uvicorn.
    """

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.pipeline = build_pipeline(
            config=config, source=source, with_forest=with_forest
        )
        yield
        application.state.pipeline.con.close()

    application = FastAPI(
        title="claimsight",
        version=__version__,
        summary="Billing-anomaly detection over real CMS Medicare inpatient claims.",
        description=(
            "Scores 197,283 rows of CMS FY2016 inpatient claims for peer-relative "
            "billing outliers. Flags indicate statistical unusualness worth human "
            "review -- **not** a finding of fraud or wrongdoing. See the project "
            "README for methodology, guards and limitations."
        ),
        lifespan=lifespan,
    )

    @application.exception_handler(duckdb.Error)
    async def _duckdb_error(_: Request, exc: duckdb.Error) -> JSONResponse:
        """A query failure is a 500, but never leak SQL internals to the client."""
        logger.exception("duckdb error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "internal query failure"})

    @application.get("/", tags=["service"])
    def root(pipeline: PipelineDep) -> dict[str, Any]:
        """Landing page. A reviewer opening the base URL should not get a 404.

        Lists the real endpoints and repeats the not-a-fraud-finding caveat,
        because the root of a public API is the most likely thing to be read
        without the README open.
        """
        return {
            "service": "claimsight",
            "version": __version__,
            "rows_loaded": pipeline.rows_loaded,
            "source_file": str(pipeline.source),
            "notice": (
                "Flags indicate billing patterns that are statistically unusual "
                "relative to peer hospitals and are worth human review. They are "
                "not findings of fraud, waste or wrongdoing."
            ),
            "endpoints": {
                "GET /health": "liveness, version, dataset loaded, pipeline seconds",
                "GET /summary": "headline statistics + how often each guard fired",
                "GET /peer-levels": "which rung of the peer hierarchy served each row",
                "GET /outliers": "flagged claims ranked by practical significance "
                                 "(?limit=&state=&min_excess=&agreed_only=)",
                "GET /providers/{provider_id}": "one hospital's billing profile",
                "POST /score": "score a hypothetical claim against loaded peers",
                "GET /docs": "interactive OpenAPI / Swagger UI",
            },
        }

    @application.get("/health", response_model=HealthResponse, tags=["service"])
    def health(pipeline: PipelineDep) -> HealthResponse:
        """Liveness plus enough state to tell which dataset is being served."""
        return HealthResponse(
            version=__version__,
            rows_loaded=pipeline.rows_loaded,
            source_file=str(pipeline.source),
            pipeline_seconds=pipeline.elapsed_seconds,
        )

    @application.get("/summary", response_model=SummaryResponse, tags=["analysis"])
    def summary(pipeline: PipelineDep) -> dict[str, Any]:
        """Headline scoring statistics, including how often each guard fired."""
        return summarise(pipeline.cursor(), FINAL_VIEW)

    @application.get(
        "/peer-levels",
        response_model=list[PeerLevelUsage],
        tags=["analysis"],
    )
    def peer_levels(pipeline: PipelineDep) -> list[dict[str, Any]]:
        """Which rung of the peer hierarchy served how many rows."""
        return peer_level_summary(pipeline.cursor(), "claims_with_peers")

    @application.get(
        "/outliers",
        response_model=list[OutlierRow],
        responses={422: {"model": ErrorResponse}},
        tags=["analysis"],
    )
    def outliers(
        pipeline: PipelineDep,
        limit: Annotated[int, Query(ge=1, le=500)] = 25,
        state: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
        min_excess: Annotated[float, Query(ge=0.0)] = 0.0,
        agreed_only: Annotated[
            bool, Query(description="only rows both detectors flagged")
        ] = False,
    ) -> list[dict[str, Any]]:
        """Peer-flagged claims, ranked by practical significance.

        Ranked by ``relative_excess`` (how many times the peer median) rather than
        raw z: a raw-z ranking is dominated by peer groups with a near-zero MAD
        denominator and returns artifacts instead of anomalies.
        """
        cursor = pipeline.cursor()
        predicates = ["flagged_peer"]
        params: list[Any] = []
        if state:
            predicates.append("upper(state) = upper(?)")
            params.append(state)
        if min_excess:
            predicates.append("relative_excess >= ?")
            params.append(min_excess)
        if agreed_only:
            predicates.append("flagged_both")

        sql = f"""
            SELECT provider_id, provider_name, city, state, drg, total_discharges,
                   markup_ratio, peer_median, peer_n, peer_level, relative_excess,
                   robust_z, confidence, flag_reason, flagged_both
            FROM {FINAL_VIEW}
            WHERE {' AND '.join(predicates)}
            ORDER BY relative_excess DESC
            LIMIT ?
        """
        params.append(limit)
        rows = cursor.execute(sql, params).fetchall()
        columns = [
            "provider_id", "provider_name", "city", "state", "drg", "total_discharges",
            "markup_ratio", "peer_median", "peer_n", "peer_level", "relative_excess",
            "robust_z", "confidence", "flag_reason", "flagged_both",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]

    @application.get(
        "/providers/{provider_id}",
        response_model=ProviderScorecard,
        responses={404: {"model": ErrorResponse}},
        tags=["analysis"],
    )
    def provider_scorecard(
        pipeline: PipelineDep,
        provider_id: str,
        limit: Annotated[int, Query(ge=1, le=200)] = 20,
    ) -> dict[str, Any]:
        """One hospital's billing profile across every DRG it reported."""
        cursor = pipeline.cursor()
        head = cursor.execute(
            f"""
            SELECT provider_id, provider_name, city, state, hrr,
                   count(*)                        AS drgs_reported,
                   sum(total_discharges)           AS total_discharges,
                   sum(CASE WHEN flagged_peer THEN 1 ELSE 0 END) AS flagged_claims,
                   median(markup_ratio)            AS median_markup_ratio,
                   median(confidence)              AS median_confidence,
                   max(relative_excess)            AS max_relative_excess
            FROM {FINAL_VIEW}
            WHERE provider_id = ?
            GROUP BY 1, 2, 3, 4, 5
            """,
            [provider_id],
        ).fetchone()
        if head is None:
            raise HTTPException(status_code=404, detail=f"unknown provider_id: {provider_id}")

        claims = cursor.execute(
            f"""
            SELECT provider_id, provider_name, city, state, drg, total_discharges,
                   markup_ratio, peer_median, peer_n, peer_level, relative_excess,
                   robust_z, confidence, flag_reason, flagged_both
            FROM {FINAL_VIEW} WHERE provider_id = ?
            ORDER BY relative_excess DESC NULLS LAST LIMIT ?
            """,
            [provider_id, limit],
        ).fetchall()
        columns = [
            "provider_id", "provider_name", "city", "state", "drg", "total_discharges",
            "markup_ratio", "peer_median", "peer_n", "peer_level", "relative_excess",
            "robust_z", "confidence", "flag_reason", "flagged_both",
        ]
        payload = {
            "provider_id": head[0],
            "provider_name": head[1],
            "city": head[2],
            "state": head[3],
            "hrr": head[4],
            "drgs_reported": int(head[5]),
            "total_discharges": int(head[6]),
            "flagged_claims": int(head[7]),
            "median_markup_ratio": head[8],
            "median_confidence": head[9],
            "max_relative_excess": head[10],
            "claims": [dict(zip(columns, row, strict=True)) for row in claims],
        }
        return payload

    @application.post(
        "/score",
        response_model=ScoreVerdict,
        responses={422: {"model": ErrorResponse}},
        tags=["analysis"],
    )
    def score(pipeline: PipelineDep, request: ScoreRequest) -> ScoreVerdict:
        """Score a hypothetical claim against the loaded peer statistics.

        Walks the same peer hierarchy as the batch pipeline (finest level with
        enough support wins), then evaluates the *shared* guard expressions so the
        verdict is identical to what the batch run would have produced.
        """
        cursor = pipeline.cursor()
        cfg = pipeline.config

        lookup = cursor.execute(
            """
            SELECT peer_level, peer_n, peer_median, peer_mad, peer_p25, peer_p75
            FROM claims_with_peers
            WHERE drg = ? AND hrr = ? LIMIT 1
            """,
            [request.drg, request.hrr],
        ).fetchone()
        if lookup is None:
            lookup = cursor.execute(
                """
                SELECT peer_level, peer_n, peer_median, peer_mad, peer_p25, peer_p75
                FROM claims_with_peers WHERE drg = ? AND state = ? LIMIT 1
                """,
                [request.drg, request.state],
            ).fetchone()
        if lookup is None:
            lookup = cursor.execute(
                """
                SELECT peer_level, peer_n, peer_median, peer_mad, peer_p25, peer_p75
                FROM claims_with_peers WHERE drg = ? LIMIT 1
                """,
                [request.drg],
            ).fetchone()

        markup = request.markup_ratio
        if lookup is None:
            return ScoreVerdict(
                markup_ratio=_round(markup),
                peer_found=False,
                would_flag=False,
                reason=(
                    f"No peer group found for DRG {request.drg!r} in this dataset, "
                    "so the claim cannot be compared to anything."
                ),
            )

        peer_level, peer_n, peer_median, peer_mad, peer_p25, peer_p75 = lookup
        degenerate = is_degenerate(peer_mad, peer_p25, peer_p75)
        thin = is_thin(peer_mad, peer_median, cfg)
        # Same MAD -> IQR -> None chain as the batch SQL; returns None when the
        # peer group has no dispersion at all.
        z = robust_z_twin(markup, peer_median, peer_mad, peer_p25, peer_p75)
        excess = markup / peer_median if peer_median else None
        conf = confidence_twin(int(peer_n), request.total_discharges, cfg)

        verdict = cursor.execute(
            f"""
            SELECT {flagged_expression(cfg)} AS would_flag,
                   {reason_expression(cfg)}  AS reason
            FROM (
                SELECT CAST(? AS BOOLEAN) AS dispersion_degenerate,
                       CAST(? AS BOOLEAN) AS dispersion_thin,
                       CAST(? AS DOUBLE)  AS robust_z,
                       CAST(? AS DOUBLE)  AS relative_excess,
                       CAST(? AS DOUBLE)  AS confidence
            )
            """,
            [degenerate, thin, z, excess, conf],
        ).fetchone()

        explanations = {
            "degenerate_peers": "peer group has zero dispersion; no meaningful comparison exists",
            "thin_dispersion": "peer group is too homogeneous for a z-score to mean anything",
            "missing_metric": "markup ratio could not be computed",
            "low_confidence": "too few peers or too few discharges to trust the score",
            "within_peer_range": "within normal variation for this peer group",
            "high_markup": "charges far above this peer group, and materially so",
            "low_markup": "charges far below this peer group, and materially so",
            "immaterial_excess": "statistically unusual but too small to matter in practice",
        }
        reason_code = verdict[1]
        detail = explanations.get(reason_code, reason_code)

        return ScoreVerdict(
            markup_ratio=_round(markup),
            peer_found=True,
            peer_level=peer_level,
            peer_n=int(peer_n),
            peer_median=_round(peer_median),
            peer_mad=_round(peer_mad),
            relative_excess=_round(excess),
            robust_z=_round(z),
            confidence=_round(conf),
            would_flag=bool(verdict[0]),
            reason=f"{reason_code}: {detail}",
        )

    return application


#: Module-level app for `uvicorn claimsight.api.main:app`.
app = create_app()
