"""Command-line interface: ``claimsight <command>``.

Every command prints real numbers rather than "done", because the point of this
project is that the pipeline is inspectable at each stage.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb
import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import get_settings
from .ingest import load_claims, write_parquet
from .peers import build_peer_stats, peer_level_summary
from .registry import (
    drift_against_run,
    latest_run,
    list_runs,
    load_run,
    save_run,
)
from .scoring import (
    SCORED_VIEW,
    ScoringConfig,
    add_isolation_forest,
    score_claims,
    summarise,
)

#: Final view name, kept in sync with scoring.add_isolation_forest().
FINAL_VIEW = f"{SCORED_VIEW}_final"

app = typer.Typer(
    help="Billing-anomaly detection over CMS Medicare inpatient claims.",
    no_args_is_help=True,
)
console = Console()


def _pipeline(
    source: Path,
    cfg: ScoringConfig,
    *,
    with_forest: bool,
    persist: Path | None,
) -> tuple[duckdb.DuckDBPyConnection, dict[str, object]]:
    """Run ingest -> peers -> score -> (forest) and collect diagnostics."""
    started = time.perf_counter()
    con = duckdb.connect(str(persist) if persist else ":memory:")
    con, ingest_report = load_claims(source, con)
    build_peer_stats(con, min_support=get_settings().min_support, metric=cfg.metric)
    score_claims(con, cfg)
    if with_forest:
        forest_diag = add_isolation_forest(con, cfg)
    else:
        forest_diag = None
        # Maintain one view contract: downstream queries always read
        # claim_scores_final, whether or not the forest ran. Skipping the forest
        # must not change the shape of the output.
        con.execute(
            f"""
            CREATE OR REPLACE VIEW {FINAL_VIEW} AS
            SELECT *,
                   NULL::DOUBLE AS iforest_score,
                   NULL::DOUBLE AS iforest_percentile,
                   false        AS iforest_flag,
                   false        AS flagged_both
            FROM {SCORED_VIEW}_flagged
            """
        )
    elapsed = time.perf_counter() - started
    return con, {
        "elapsed_seconds": round(elapsed, 3),
        "ingest": ingest_report.as_dict(),
        "peer_levels": peer_level_summary(con),
        "isolation_forest": forest_diag,
        # The FINAL_VIEW contract holds whether or not the forest ran, so the
        # summary is always available.
        "summary": summarise(con),
    }


@app.command()
def version() -> None:
    """Print the package version."""
    console.print(f"claimsight {__version__}")


@app.command()
def ingest(
    source: Path = typer.Argument(None, help="CMS CSV (defaults to full file, else sample)"),
    parquet: Path = typer.Option(None, "--parquet", help="also write a Parquet copy here"),
) -> None:
    """Load and validate a CMS charge file, reporting data quality."""
    settings = get_settings()
    path = source or settings.resolve_input()
    console.print(f"[bold]ingesting[/bold] {path}")

    con, report = load_claims(path, duckdb.connect())
    data = report.as_dict()
    nulls = {k: v for k, v in data["null_counts"].items() if v}
    data["null_counts"] = nulls or "none"
    console.print_json(json.dumps(data, default=str))

    if parquet:
        write_parquet(con, parquet)
        console.print(f"[green]wrote[/green] {parquet}")

    if report.rows_rejected:
        console.print(
            f"[yellow]{report.rows_rejected} row(s) rejected[/yellow] -- "
            f"inspect them with: SELECT * FROM claims_raw WHERE <required col> IS NULL"
        )


@app.command()
def peers(
    source: Path = typer.Argument(None),
    min_support: int = typer.Option(8, help="minimum peer-group size before its stats are trusted"),
) -> None:
    """Show which rung of the peer hierarchy each row ended up using."""
    settings = get_settings()
    path = source or settings.resolve_input()
    con, _ = load_claims(path, duckdb.connect())
    build_peer_stats(con, min_support=min_support)

    table = Table(title=f"Peer hierarchy usage — {path.name} (min_support={min_support})")
    for column in (
        "peer level",
        "rows",
        "% of rows",
        "smallest group",
        "median group",
        "largest group",
    ):
        table.add_column(column, justify="right")
    for row in peer_level_summary(con):
        table.add_row(
            row["peer_level"],
            f"{row['rows_scored']:,}",
            f"{row['pct_of_rows']}%",
            f"{row['smallest_group']:,}",
            f"{row['median_group_size']:,.0f}",
            f"{row['largest_group']:,}",
        )
    console.print(table)


@app.command()
def report(
    source: Path = typer.Argument(None),
    z_threshold: float = typer.Option(3.5, help="robust z above which a row is an outlier"),
    confidence_floor: float = typer.Option(0.25, help="minimum confidence to flag"),
    top: int = typer.Option(15, help="how many worst offenders to list"),
    no_forest: bool = typer.Option(False, help="skip the Isolation Forest (faster)"),
    json_out: Path = typer.Option(None, "--json", help="also write diagnostics as JSON"),
    save: bool = typer.Option(
        False, "--save", help="persist this run to artifacts/runs/ (manifest + review queue)"
    ),
) -> None:
    """Run the full pipeline and print the anomaly report."""
    settings = get_settings()
    path = source or settings.resolve_input()
    cfg = ScoringConfig(z_threshold=z_threshold, confidence_floor=confidence_floor)

    console.print(f"[bold]scoring[/bold] {path}")
    con, diagnostics = _pipeline(path, cfg, with_forest=not no_forest, persist=None)

    summary = diagnostics["summary"]
    if summary:
        table = Table(title="Scoring summary")
        table.add_column("metric", style="cyan")
        table.add_column("value", justify="right")
        for key, value in summary.items():
            if key == "flag_reasons":
                continue  # rendered as its own table below
            table.add_row(
                key.replace("_", " "),
                f"{value:,}" if isinstance(value, int) else str(value),
            )
        console.print(table)

    level_table = Table(title="Peer hierarchy usage")
    level_table.add_column("peer level", style="cyan")
    level_table.add_column("rows", justify="right")
    level_table.add_column("% of rows", justify="right")
    level_table.add_column("median group size", justify="right")
    for row in diagnostics["peer_levels"]:
        level_table.add_row(
            str(row["peer_level"]),
            f"{row['rows_scored']:,}",
            f"{row['pct_of_rows']}%",
            f"{row['median_group_size']:,.0f}",
        )
    console.print(level_table)

    reasons = Table(title="Why rows were not flagged (guard diagnostics)")
    reasons.add_column("reason", style="cyan")
    reasons.add_column("rows", justify="right")
    reasons.add_column("%", justify="right")
    for entry in (summary or {}).get("flag_reasons", []):
        reasons.add_row(entry["reason"], f"{entry['rows']:,}", f"{entry['pct']}%")
    console.print(reasons)

    console.print(f"[bold]top {top} peer-flagged claims[/bold]")
    # Ranked by relative_excess (how many times the peer median), NOT by z.
    # A raw-z ranking is dominated by peer groups with a near-zero denominator:
    # on the FY2016 file it returned eight Washington DC hospitals at z=134..192,
    # which was a small-MAD artifact rather than a billing signal.
    con.sql(
        f"""
        SELECT provider_name, city, state, substr(drg, 1, 30) AS drg,
               total_discharges AS disch,
               round(markup_ratio, 2)    AS markup,
               round(peer_median, 2)     AS peer_med,
               round(relative_excess, 2) AS x_peer,
               peer_n, peer_level,
               round(robust_z, 1) AS z,
               round(confidence, 2) AS conf
        FROM claim_scores_final
        WHERE flagged_peer
        ORDER BY relative_excess DESC
        LIMIT {top}
        """
    ).show(max_col_width=30)

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(diagnostics, indent=2, default=str))
        console.print(f"[green]wrote[/green] {json_out}")

    if save:
        record = save_run(
            con,
            cfg,
            source=path,
            min_support=get_settings().min_support,
            ingest_report=diagnostics.get("ingest"),
            elapsed_seconds=diagnostics.get("elapsed_seconds"),
        )
        console.print(
            f"[green]saved run[/green] {record.run_id} -> {record.path}\n"
            f"  manifest:    {record.path / 'manifest.json'}\n"
            f"  review queue: {record.outliers_path} ({record.rows_flagged:,} rows)"
        )
        console.print(
            "[dim]compare a future file against this baseline with: "
            f"claimsight drift <new.csv> --baseline {record.run_id}[/dim]"
        )


@app.command()
def fairness(
    source: Path = typer.Argument(None),
    volume_bucket: int = typer.Option(20, help="discharges below this count as low-volume"),
) -> None:
    """Audit whether flagging is distributed fairly across hospital sizes.

    Small and rural hospitals have thin peer groups and noisy per-discharge
    averages, so a naive scorer can systematically over-flag them. This command
    makes that risk measurable instead of assumed away.
    """
    settings = get_settings()
    path = source or settings.resolve_input()
    cfg = ScoringConfig()
    con, _ = _pipeline(path, cfg, with_forest=False, persist=None)

    con.sql(
        f"""
        SELECT
            CASE WHEN total_discharges < {volume_bucket} THEN 'low_volume'
                 WHEN total_discharges < 100 THEN 'mid_volume'
                 ELSE 'high_volume' END AS volume_band,
            count(*) AS rows,
            round(avg(peer_n), 1) AS avg_peer_n,
            round(avg(confidence), 3) AS avg_confidence,
            sum(CASE WHEN flagged_peer THEN 1 ELSE 0 END) AS flagged,
            round(100.0 * sum(CASE WHEN flagged_peer THEN 1 ELSE 0 END)
                  / count(*), 3) AS flag_rate_pct,
            round(100.0 * sum(CASE WHEN dispersion_degenerate THEN 1 ELSE 0 END) / count(*), 2)
                AS degenerate_pct
        FROM claim_scores_final
        GROUP BY 1 ORDER BY 1
        """
    ).show()

    con.sql(
        """
        SELECT state, count(*) AS rows,
               round(100.0 * sum(CASE WHEN flagged_peer THEN 1 ELSE 0 END) / count(*), 2)
                   AS flag_rate_pct
        FROM claim_scores_final
        GROUP BY 1 HAVING count(*) > 200
        ORDER BY flag_rate_pct DESC LIMIT 10
        """
    ).show()


@app.command()
def runs(
    run_id: str = typer.Argument(None, help="inspect one run's manifest instead of listing all"),
    as_json: bool = typer.Option(False, "--json", help="print the raw manifest as JSON"),
) -> None:
    """List saved runs in the artifact registry, or dump one manifest."""
    if run_id:
        record = load_run(run_id)
        if as_json:
            console.print_json(json.dumps(record.manifest, default=str))
            return
        manifest = record.manifest
        table = Table(title=f"Run {record.run_id}")
        table.add_column("field", style="cyan")
        table.add_column("value")
        metrics = manifest.get("metrics", {})
        source = manifest.get("source", {})
        for key, value in (
            ("created at", manifest.get("created_at")),
            ("source", source.get("name")),
            ("source sha256", (source.get("sha256") or "")[:16] + "..."),
            ("rows", f"{metrics.get('rows_scored', 0):,}"),
            ("flagged (peer)", f"{metrics.get('flagged_peer', 0):,}"),
            ("flag rate %", metrics.get("flagged_peer_pct")),
            ("flagged (forest)", f"{metrics.get('flagged_iforest', 0):,}"),
            ("jaccard", metrics.get("jaccard")),
            ("elapsed s", manifest.get("elapsed_seconds")),
            ("review queue", f"{manifest.get('review_queue', {}).get('rows', 0):,} rows"),
            ("duckdb", manifest.get("environment", {}).get("duckdb")),
            ("claimsight", manifest.get("environment", {}).get("claimsight")),
        ):
            table.add_row(key, str(value))
        console.print(table)
        console.print(f"[dim]queue:[/dim] {record.outliers_path}")
        return

    records = list_runs()
    if not records:
        console.print(
            "[yellow]no saved runs yet[/yellow] -- create one with "
            "[bold]claimsight report --save[/bold]"
        )
        return

    table = Table(title=f"Saved runs ({len(records)}) — newest first")
    # The run id must stay readable in full: it is what gets pasted into
    # --baseline. Fold rather than ellipsise so a narrow terminal wraps it
    # instead of hiding the part the user needs.
    table.add_column("run id", justify="left", no_wrap=True, overflow="fold")
    table.add_column("date", justify="left", no_wrap=True)
    # No sha column here: it is in `runs <id>`, and the listing has to fit a
    # run id, a date and five numbers in 80 characters.
    for column in ("rows", "flagged", "flag %", "source", "secs"):
        table.add_column(column, justify="left" if column == "source" else "right")
    for record in records:
        metrics = record.manifest.get("metrics", {})
        source = record.manifest.get("source", {})
        table.add_row(
            record.run_id,
            str(record.manifest.get("created_at", ""))[:10],
            f"{metrics.get('rows_scored', 0):,}",
            f"{metrics.get('flagged_peer', 0):,}",
            str(metrics.get("flagged_peer_pct")),
            str(source.get("name", ""))[:18],
            str(record.manifest.get("elapsed_seconds")),
        )
    console.print(table)


@app.command()
def drift(
    source: Path = typer.Argument(
        None, help="new claims file (defaults to full file, else sample)"
    ),
    baseline: str = typer.Option(
        None, "--baseline", help="run id to compare against (default: most recent saved run)"
    ),
    z_threshold: float = typer.Option(3.5, help="robust z above which a row is an outlier"),
    confidence_floor: float = typer.Option(0.25, help="minimum confidence to flag"),
    json_out: Path = typer.Option(None, "--json", help="write the drift report as JSON"),
) -> None:
    """Check whether a new claims file has drifted from a saved baseline run.

    Scores the new file with the same config, then computes the Population
    Stability Index of each monitored feature against the baseline's stored
    quantile edges. Exits non-zero on a significant shift so this can gate a
    scheduled job.
    """
    settings = get_settings()
    path = source or settings.resolve_input()
    record = load_run(baseline) if baseline else latest_run()
    if record is None:
        console.print(
            "[red]no baseline run saved[/red] -- create one first with "
            "[bold]claimsight report --save[/bold]"
        )
        raise typer.Exit(code=2)

    cfg = ScoringConfig(z_threshold=z_threshold, confidence_floor=confidence_floor)
    console.print(
        f"[bold]drift check[/bold] {path.name} [dim]vs[/dim] baseline {record.run_id} "
        f"[dim]({record.manifest.get('source', {}).get('name')})[/dim]"
    )
    # The forest is skipped deliberately: every monitored feature is a peer or
    # payment statistic, and re-fitting 100 trees to detect distribution shift
    # would buy nothing.
    con, _ = _pipeline(path, cfg, with_forest=False, persist=None)
    report = drift_against_run(record, con)

    table = Table(title=f"Population Stability Index — {report.verdict}")
    for column in ("feature", "PSI", "band", "baseline median", "current median", "Δ median"):
        table.add_column(column, justify="left" if column in ("feature", "band") else "right")
    for entry in report.features:
        base, curr = entry.baseline_median, entry.current_median
        delta = ""
        if base not in (None, 0) and curr is not None:
            delta = f"{100.0 * (curr - base) / abs(base):+.1f}%"
        table.add_row(
            entry.feature,
            f"{entry.psi:.4f}",
            entry.interpretation,
            f"{base:,.2f}" if base is not None else "—",
            f"{curr:,.2f}" if curr is not None else "—",
            delta,
        )
    console.print(table)

    console.print(
        f"rows: {report.baseline_rows or 0:,} -> {report.current_rows:,}   "
        f"flag rate: {report.baseline_flag_rate_pct}% -> {report.current_flag_rate_pct}% "
        f"({report.flag_rate_delta:+.2f} pp)"
        if report.flag_rate_delta is not None
        else f"rows: {report.baseline_rows or 0:,} -> {report.current_rows:,}"
    )
    if report.max_psi >= 0.25:
        console.print(
            f"[red]significant drift[/red] in [bold]{report.worst_feature}[/bold] "
            "(PSI >= 0.25): the baseline is no longer representative. Re-baseline "
            "and re-review the thresholds before trusting this run's flags."
        )
    elif report.max_psi >= 0.10:
        console.print(
            f"[yellow]moderate drift[/yellow] in [bold]{report.worst_feature}[/bold] "
            "(PSI >= 0.10): worth a look, not an alarm."
        )
    else:
        console.print("[green]distributions stable[/green] (all PSI < 0.10).")

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {"baseline_run": record.run_id, **report.as_dict()}
        json_out.write_text(json.dumps(payload, indent=2, default=str))
        console.print(f"[green]wrote[/green] {json_out}")

    if report.max_psi >= 0.25:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
