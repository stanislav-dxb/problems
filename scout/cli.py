"""Problem Scout command-line interface."""
from __future__ import annotations

import os
import sqlite3
from typing import Optional

import typer

from . import __version__
from .config import load_config
from .util import setup_logging

app = typer.Typer(help="Problem Scout: collect → classify → cluster → evaluate → digest.",
                  no_args_is_help=True, add_completion=False)

_state: dict = {"cfg": None}


def _cfg() -> dict:
    if _state["cfg"] is None:
        _state["cfg"] = load_config()
    return _state["cfg"]


def _conn() -> sqlite3.Connection:
    from . import db as dbm
    return dbm.connect()


def _llm(conn: sqlite3.Connection):
    from .llm import LLMUnavailable, get_llm
    try:
        return get_llm(_cfg(), conn)
    except LLMUnavailable as e:
        typer.secho(f"Claude unavailable: {e}", fg=typer.colors.RED, err=True)
        typer.secho("Add ANTHROPIC_API_KEY to .env (see .env.example), or set SCOUT_LLM=stub for an offline smoke run.",
                    err=True)
        raise typer.Exit(code=2)


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
             retry_failed: bool = typer.Option(False, "--retry-failed", help="Also retry items that failed before")):
    """Classify unclassified items with Claude (batches of 20)."""
    from .classify import classify as run_classify
    with _conn() as conn:
        llm = _llm(conn)
        stats = run_classify(_cfg(), conn, llm, limit=limit, retry_failed=retry_failed)
    typer.echo(f"classify: {stats}")


@app.command()
def cluster(no_labels: bool = typer.Option(False, "--no-labels", help="Skip Claude labelling (use member summaries)")):
    """Re-cluster all classified problems from scratch (local embeddings + Claude labels)."""
    from .cluster import run_clustering
    with _conn() as conn:
        llm = None if no_labels else _llm(conn)
        stats = run_clustering(_cfg(), conn, llm)
    typer.echo(f"cluster: {stats}")


@app.command()
def evaluate(force: bool = typer.Option(False, "--force", help="Re-evaluate clusters that already have an evaluation"),
             limit: Optional[int] = typer.Option(None, "--limit", help="Max clusters to evaluate this run")):
    """Evaluate every cluster with enough items against the founder's criteria."""
    from .evaluate import evaluate as run_evaluate
    with _conn() as conn:
        llm = _llm(conn)
        stats = run_evaluate(_cfg(), conn, llm, force=force, limit=limit)
    typer.echo(f"evaluate: {stats}")


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
        dry_run: bool = typer.Option(False, "--dry-run", help="Describe every stage without fetching or calling Claude")):
    """Full pipeline: collect → classify → cluster → evaluate → digest."""
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
            min_size = int(cfg.get("evaluation", {}).get("min_cluster_size", 5))
            to_eval = len(dbm.clusters_to_evaluate(conn, min_size))
        bs = int(cfg.get("classify_batch_size", 20))
        typer.echo("== classify (dry run) ==")
        typer.echo(f"  {pending} unclassified items -> {-(-pending // bs)} Claude calls of up to {bs} items "
                   f"(model {cfg.get('model')})")
        typer.echo("== cluster (dry run) ==")
        typer.echo(f"  {problems} problem summaries would be embedded with {cfg['clustering'].get('embedding_model')} "
                   f"and clustered at cosine distance {cfg['clustering'].get('distance_threshold')}; "
                   f"one Claude label call per cluster with >= {cfg['clustering'].get('label_min_size', 2)} items")
        typer.echo("== evaluate (dry run) ==")
        typer.echo(f"  {to_eval} clusters with >= {min_size} items currently lack an evaluation -> {to_eval} Claude calls "
                   "(re-clustering may change this)")
        typer.echo("== digest (dry run) ==")
        typer.echo(f"  would write digests/YYYY-MM-DD.md with top {top or cfg['digest'].get('top_n')} clusters")
        return
    from .classify import classify as run_classify
    from .cluster import run_clustering
    from .digest import write_digest
    from .evaluate import evaluate as run_evaluate
    with _conn() as conn:
        typer.echo("== collect ==")
        for name, r in run_collect(cfg, conn, days).items():
            typer.echo(f"  {name}: {r}")
        llm = _llm(conn)
        typer.echo("== classify ==")
        typer.echo(f"  {run_classify(cfg, conn, llm)}")
        typer.echo("== cluster ==")
        typer.echo(f"  {run_clustering(cfg, conn, llm)}")
        typer.echo("== evaluate ==")
        typer.echo(f"  {run_evaluate(cfg, conn, llm)}")
        typer.echo("== digest ==")
        path = write_digest(cfg, conn, top_n=top, out=out)
        typer.echo(f"  wrote {path}")


@app.command()
def stats():
    """Counts per source, domain and week, plus API token usage and estimated daily cost."""
    from .stats import gather, render
    with _conn() as conn:
        typer.echo(render(gather(conn)))


if __name__ == "__main__":  # pragma: no cover
    app()
