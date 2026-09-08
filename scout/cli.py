"""Problem Scout command-line interface."""
from __future__ import annotations

import os
import sqlite3
from typing import Optional

import typer

from . import __version__
from .config import load_config
from .util import setup_logging

app = typer.Typer(help="Problem Scout: collect → classify → cluster → score → digest. No LLM, runs locally.",
                  no_args_is_help=True, add_completion=False)
_state: dict = {"cfg": None}


def _cfg() -> dict:
    if _state["cfg"] is None:
        _state["cfg"] = load_config()
    return _state["cfg"]


def _conn() -> sqlite3.Connection:
    from . import db as dbm
    return dbm.connect()


@app.callback()
def main(config: Optional[str] = typer.Option(None, "--config", help="Path to config.yaml (default ./config.yaml or $SCOUT_CONFIG)"),
         db: Optional[str] = typer.Option(None, "--db", help="Path to SQLite database (default ./scout.db or $SCOUT_DB)"),
         verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
         version: bool = typer.Option(False, "--version", help="Print version and exit")):
    if version:
        typer.echo(f"scout {__version__}")
        raise typer.Exit()
    if config:
        os.environ["SCOUT_CONFIG"] = config
    if db:
        os.environ["SCOUT_DB"] = db
    setup_logging(verbose)


@app.command()
def collect(source: Optional[str] = typer.Option(None, "--source", help="Only this source (hn, reddit, producthunt, appstore, youtube, telegram, adzuna)"),
            since: Optional[int] = typer.Option(None, "--since", help="Look-back window in days"),
            dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be fetched, fetch nothing")):
    """Fetch new posts/comments/reviews from enabled sources into scout.db."""
    from .collect import collect as run_collect, dry_run_plan
    cfg = _cfg()
    days = since or int(cfg.get("default_since_days", 7))
    if dry_run:
        for line in dry_run_plan(cfg, days, source):
            typer.echo(line)
        return
    with _conn() as conn:
        results = run_collect(cfg, conn, days, source)
    for name, r in results.items():
        typer.echo(f"{name}: {r}")


@app.command()
def classify(limit: Optional[int] = typer.Option(None, "--limit", help="Max items to classify this run"),
             reclassify: bool = typer.Option(False, "--reclassify", help="Drop existing classifications and redo all (after changing rules/threshold)")):
    """Flag items that describe a problem using multilingual phrase rules (no model calls)."""
    from .classify import classify as run_classify
    with _conn() as conn:
        stats = run_classify(_cfg(), conn, limit=limit, reclassify=reclassify)
    typer.echo(f"classify: {stats}")


@app.command()
def cluster():
    """Re-cluster all flagged problems from scratch with local multilingual embeddings."""
    from .cluster import run_clustering
    with _conn() as conn:
        stats = run_clustering(_cfg(), conn)
    typer.echo(f"cluster: {stats}")


@app.command()
def score():
    """Compute the evidence score for every cluster with enough items."""
    from .score import score as run_score
    with _conn() as conn:
        stats = run_score(_cfg(), conn)
    typer.echo(f"score: {stats}")


@app.command()
def digest(top: Optional[int] = typer.Option(None, "--top", help="Number of clusters to include"),
           out: Optional[str] = typer.Option(None, "--out", help="Output path (default digests/YYYY-MM-DD.md)")):
    """Write the ranked Markdown digest."""
    from .digest import write_digest
    with _conn() as conn:
        path = write_digest(_cfg(), conn, top_n=top, out=out)
    typer.echo(f"digest: wrote {path}")


@app.command()
def run(since: Optional[int] = typer.Option(None, "--since", help="Look-back window in days for collection"),
        top: Optional[int] = typer.Option(None, "--top", help="Clusters in the digest"),
        out: Optional[str] = typer.Option(None, "--out", help="Digest output path"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Describe every stage without fetching")):
    """Full pipeline: collect → classify → cluster → score → digest."""
    from . import db as dbm
    from .collect import collect as run_collect, dry_run_plan
    cfg = _cfg()
    days = since or int(cfg.get("default_since_days", 7))
    if dry_run:
        typer.echo("== collect (dry run) ==")
        for line in dry_run_plan(cfg, days):
            typer.echo(line)
        with _conn() as conn:
            pending = len(dbm.unclassified_items(conn))
            problems = conn.execute("SELECT COUNT(*) FROM problems WHERE is_problem = 1").fetchone()[0]
        typer.echo("== classify (dry run) ==")
        typer.echo(f"  {pending} unclassified items would be scored against the phrase rules (threshold "
                   f"{cfg.get('classify', {}).get('threshold')})")
        typer.echo("== cluster (dry run) ==")
        typer.echo(f"  {problems} problem summaries would be embedded with {cfg['clustering'].get('embedding_model')} "
                   f"and clustered at cosine distance {cfg['clustering'].get('distance_threshold')}")
        typer.echo("== score / digest (dry run) ==")
        typer.echo(f"  clusters with >= {cfg['scoring'].get('min_cluster_size')} items would be scored; "
                   f"digest with top {top or cfg['digest'].get('top_n')} written to digests/YYYY-MM-DD.md")
        return
    from .classify import classify as run_classify
    from .cluster import run_clustering
    from .digest import write_digest
    from .score import score as run_score
    with _conn() as conn:
        typer.echo("== collect ==")
        for name, r in run_collect(cfg, conn, days).items():
            typer.echo(f"  {name}: {r}")
        typer.echo("== classify ==")
        typer.echo(f"  {run_classify(cfg, conn)}")
        typer.echo("== cluster ==")
        typer.echo(f"  {run_clustering(cfg, conn)}")
        typer.echo("== score ==")
        typer.echo(f"  {run_score(cfg, conn)}")
        typer.echo("== digest ==")
        typer.echo(f"  wrote {write_digest(cfg, conn, top_n=top, out=out)}")


@app.command()
def stats():
    """Counts per source, language, domain and week, plus pipeline state."""
    from .stats import gather, render
    with _conn() as conn:
        typer.echo(render(gather(conn)))


if __name__ == "__main__":  # pragma: no cover
    app()
