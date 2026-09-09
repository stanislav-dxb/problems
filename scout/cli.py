"""Problem Scout command-line interface."""
from __future__ import annotations

import os
import sqlite3
from typing import Optional

import typer

from . import __version__
from .config import load_config
from .util import setup_logging

app = typer.Typer(help="Problem Scout: collect → classify → cluster → news → reports → triangulate → evaluate → score → digest.",
                  no_args_is_help=True, add_completion=False)
_state: dict = {"cfg": None}


def _cfg() -> dict:
    if _state["cfg"] is None:
        _state["cfg"] = load_config()
    return _state["cfg"]


def _conn() -> sqlite3.Connection:
    from . import db as dbm
    return dbm.connect()


def _llm_ready(conn: sqlite3.Connection, probe: bool = True) -> None:
    """Configure the model backend and fail early with a clear message if it cannot be used."""
    from . import llm
    cfg = _cfg()
    try:
        llm.configure(cfg, conn)
        info = llm.check_backend(probe=probe and bool(cfg.get("llm", {}).get("probe_on_start", True)))
    except llm.LLMUnavailable as e:
        typer.secho(f"Model backend unavailable: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    typer.echo(f"backend: {info}")


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
             reclassify: bool = typer.Option(False, "--reclassify", help="Drop existing classifications and redo all"),
             retry_failed: bool = typer.Option(False, "--retry-failed", help="Also retry batches that failed before")):
    """Classify items: model backend (claude_code / anthropic_api) in batches, or keyword rules."""
    from .classify import classify as run_classify
    with _conn() as conn:
        _llm_ready(conn)
        stats = run_classify(_cfg(), conn, limit=limit, reclassify=reclassify, retry_failed=retry_failed)
    typer.echo(f"classify: {stats}")


@app.command()
def evaluate(force: bool = typer.Option(False, "--force", help="Re-evaluate clusters that already have an evaluation"),
             limit: Optional[int] = typer.Option(None, "--limit", help="Max clusters to evaluate this run")):
    """Evaluate each cluster with enough items against the founder's criteria (one model call per cluster)."""
    from .evaluate import evaluate as run_evaluate
    with _conn() as conn:
        _llm_ready(conn)
        stats = run_evaluate(_cfg(), conn, force=force, limit=limit)
    typer.echo(f"evaluate: {stats}")


@app.command()
def plan():
    """Show the model backend, model per stage and how many model calls the next run would make."""
    from . import db as dbm
    from . import llm
    cfg = _cfg()
    with _conn() as conn:
        _llm_ready(conn, probe=False)
        st = llm.settings()
        pending = len(dbm.unclassified_items(conn))
        bs = int(cfg.get("classify", {}).get("batch_size", 15))
        min_size = int(cfg.get("evaluate", {}).get("min_cluster_size", 3))
        evaluated = dbm.latest_evaluations(conn)
        to_eval = [c for c in conn.execute("SELECT id FROM clusters WHERE item_count >= ?", (min_size,)) if c["id"] not in evaluated]
    calls_classify = 0 if st["backend"] == "rules" else -(-pending // bs)
    calls_eval = 0 if st["backend"] == "rules" else len(to_eval)
    typer.echo(f"backend:        {st['backend']}")
    typer.echo(f"classify model: {st['model']}   ({pending} unclassified items ÷ {bs} per batch = {calls_classify} calls, "
               f"{st['max_parallel']} in parallel)")
    typer.echo(f"evaluate model: {st['evaluate_model']}   ({len(to_eval)} clusters with ≥{min_size} items not yet evaluated = {calls_eval} calls, sequential)")
    typer.echo(f"total model calls next run: {calls_classify + calls_eval}")
    if st["backend"] == "claude_code":
        typer.echo("These calls run through the Claude Code CLI and draw on your Claude subscription's usage quota; "
                   "there is no per-token bill and no API key.")
    elif st["backend"] == "anthropic_api":
        typer.echo("These calls are billed per token to your Anthropic Console organisation.")
    else:
        typer.echo("rules backend: no model calls; classification uses keyword rules and evaluate is skipped.")


@app.command()
def cluster():
    """Re-cluster all flagged problems from scratch with local multilingual embeddings."""
    from .cluster import run_clustering
    with _conn() as conn:
        stats = run_clustering(_cfg(), conn)
    typer.echo(f"cluster: {stats}")


@app.command()
def news(since: Optional[int] = typer.Option(None, "--since", help="Look-back window in days")):
    """Fetch news (Google News, trade press, regulators, GDELT) and flag catalysts with keyword rules."""
    from .catalysts import run_news
    cfg = _cfg()
    with _conn() as conn:
        stats = run_news(cfg, conn, since or int(cfg.get("default_since_days", 7)))
    typer.echo(f"news: {stats}")


@app.command()
def reports(since: Optional[int] = typer.Option(None, "--since", help="Look-back window in days"),
            reprocess: bool = typer.Option(False, "--reprocess", help="Re-chunk and re-classify stored reports without fetching (after rule changes)")):
    """Fetch reports (World Bank, publisher feeds, arXiv), extract PDF text, store typed claims."""
    from .claims import reprocess_reports, run_reports
    cfg = _cfg()
    with _conn() as conn:
        if reprocess:
            stats = reprocess_reports(cfg, conn)
        else:
            stats = run_reports(cfg, conn, since or int(cfg.get("reports", {}).get("since_days", 30)))
    typer.echo(f"reports: {stats}")


@app.command()
def triangulate():
    """Join pain clusters with report claims and news catalysts; write triangulations and hypotheses."""
    from .triangulate import triangulate as run_tri
    with _conn() as conn:
        stats = run_tri(_cfg(), conn)
    typer.echo(f"triangulate: {stats}")


@app.command()
def score():
    """Compute the evidence score for every cluster with enough items."""
    from .score import score as run_score
    with _conn() as conn:
        stats = run_score(_cfg(), conn)
    typer.echo(f"score: {stats}")


@app.command()
def digest(top: Optional[int] = typer.Option(None, "--top", help="Number of clusters to include"),
           out: Optional[str] = typer.Option(None, "--out", help="Output path (default digests/YYYY-MM-DD.md)"),
           corridor: bool = typer.Option(False, "--corridor", help="Only report/news signals touching 2+ of Gulf, Russian-speaking, India, China, EU")):
    """Write the ranked Markdown digest."""
    from .digest import write_digest
    with _conn() as conn:
        path = write_digest(_cfg(), conn, top_n=top, out=out, corridor=corridor)
    typer.echo(f"digest: wrote {path}")


@app.command()
def run(since: Optional[int] = typer.Option(None, "--since", help="Look-back window in days for collection"),
        top: Optional[int] = typer.Option(None, "--top", help="Clusters in the digest"),
        out: Optional[str] = typer.Option(None, "--out", help="Digest output path"),
        corridor: bool = typer.Option(False, "--corridor", help="Corridor-filtered digest"),
        reclassify: bool = typer.Option(False, "--reclassify", help="Drop existing classifications and redo all items"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Describe every stage without fetching")):
    """Full pipeline: collect → classify → cluster → news → reports → triangulate → evaluate → score → digest."""
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
        lcfg = cfg.get("llm", {})
        if lcfg.get("backend") == "rules":
            typer.echo(f"  {pending} unclassified items would be scored against the phrase rules (threshold "
                       f"{cfg.get('classify', {}).get('threshold')})")
        else:
            bs = int(cfg.get("classify", {}).get("batch_size", 15))
            typer.echo(f"  {pending} unclassified items -> {-(-pending // bs)} {lcfg.get('backend')} calls of up to {bs} items "
                       f"(model {lcfg.get('model')}); see `scout plan`")
        typer.echo("== cluster (dry run) ==")
        typer.echo(f"  {problems} problem summaries would be embedded with {cfg['clustering'].get('embedding_model')} "
                   f"and clustered at cosine distance {cfg['clustering'].get('distance_threshold')}")
        from .catalysts import describe as news_describe
        from .claims import describe as reports_describe
        typer.echo("== news (dry run) ==")
        for line in news_describe(days, cfg):
            typer.echo("  " + line)
        typer.echo("== reports (dry run) ==")
        for line in reports_describe(int(cfg.get("reports", {}).get("since_days", 30)), cfg):
            typer.echo("  " + line)
        typer.echo("== triangulate / score / digest (dry run) ==")
        typer.echo(f"  clusters with >= {cfg['scoring'].get('min_cluster_size')} items would be matched to claims and "
                   f"catalysts at distance {cfg['triangulation'].get('distance_threshold')}, scored, and written to "
                   f"digests/YYYY-MM-DD.md (top {top or cfg['digest'].get('top_n')})")
        return
    from .catalysts import run_news
    from .claims import run_reports
    from .classify import classify as run_classify
    from .cluster import run_clustering
    from .digest import write_digest
    from .evaluate import evaluate as run_evaluate
    from .score import score as run_score
    from .triangulate import triangulate as run_tri
    with _conn() as conn:
        _llm_ready(conn)
        typer.echo("== collect ==")
        for name, r in run_collect(cfg, conn, days).items():
            typer.echo(f"  {name}: {r}")
        typer.echo("== classify ==")
        typer.echo(f"  {run_classify(cfg, conn, reclassify=reclassify)}")
        typer.echo("== cluster ==")
        typer.echo(f"  {run_clustering(cfg, conn)}")
        typer.echo("== news ==")
        typer.echo(f"  {run_news(cfg, conn, days)}")
        typer.echo("== reports ==")
        typer.echo(f"  {run_reports(cfg, conn, int(cfg.get('reports', {}).get('since_days', 30)))}")
        typer.echo("== triangulate ==")
        typer.echo(f"  {run_tri(cfg, conn)}")
        typer.echo("== evaluate ==")
        typer.echo(f"  {run_evaluate(cfg, conn)}")
        typer.echo("== score ==")
        typer.echo(f"  {run_score(cfg, conn)}")
        typer.echo("== digest ==")
        typer.echo(f"  wrote {write_digest(cfg, conn, top_n=top, out=out, corridor=corridor)}")


@app.command()
def stats():
    """Counts per source, language, domain and week, plus pipeline state."""
    from .stats import gather, render
    with _conn() as conn:
        typer.echo(render(gather(conn)))


if __name__ == "__main__":  # pragma: no cover
    app()
