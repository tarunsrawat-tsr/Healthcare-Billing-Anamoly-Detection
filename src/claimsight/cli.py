"""Command-line interface: ``claimsight <command>``.

Every command prints real numbers rather than "done", because the point of this
project is that the pipeline is inspectable at each stage.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import get_settings
from .ingest import load_claims, write_parquet
from .peers import build_peer_stats, peer_level_summary
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
    return con, {
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


if __name__ == "__main__":
    app()
