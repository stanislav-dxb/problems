# Problem Scout

`scout` is a local Python tool that continuously collects public discussions where people describe
problems, classifies them with the Claude API, clusters recurring problems, evaluates each cluster
against a fixed set of business criteria, and produces a ranked weekly digest.

It exists to produce a short, evidence-backed list of candidate problems for a founder looking for
problems that could support a $1B+ company. It does not make the decision.

```
collect  →  classify  →  cluster  →  evaluate  →  digest
(sources)   (Claude)     (local      (Claude)     (Markdown)
                          embeddings
                          + Claude labels)
```

## Sources

| Source | Access | Key needed | Default |
|---|---|---|---|
| Hacker News | Algolia search API | none | on |
| Apple App Store reviews (1–2 star) | public RSS/JSON feed | none | on |
| Reddit | official OAuth API (script app) | `REDDIT_*` | on, skipped without keys |
| Product Hunt | GraphQL API | `PRODUCTHUNT_TOKEN` | on, skipped without key |
| YouTube comments | Data API v3 | `YOUTUBE_API_KEY` + channel IDs in config | on, skipped without key |
| Telegram public channels | Telethon (user account) | `TELEGRAM_API_ID/HASH` + `pip install telethon` | off |
| Adzuna job ads | Adzuna API | `ADZUNA_APP_ID/KEY` | off |

No headless browsers, no HTML scraping. If a source cannot be accessed legitimately it is skipped and
the reason is logged. Author handles are salted-hashed before storage; author/name fields are stripped
from stored raw payloads; personal names never appear in the digest.

## Why an Anthropic key

Classification, cluster labelling and evaluation call the Claude API directly through the
official Python SDK (model `claude-opus-5`). API usage is billed per token to an Anthropic Console
organisation, separately from a claude.ai or Claude Code subscription, so the tool needs its own
credential. Two options:

- `ANTHROPIC_API_KEY` in `.env` — create one at https://console.anthropic.com.
- `ant auth login` (the Anthropic CLI) — an OAuth profile under `~/.config/anthropic/`, no static
  key to manage; the SDK picks it up automatically when no key is exported.

Collection (`scout collect`) never needs it. Every call is logged with token counts in `api_calls`;
`scout stats` shows the running cost.

## Setup

Requirements: Python 3.11+, ~1 GB disk for PyTorch + the embedding model (CPU is fine).

```bash
git clone <this repo> && cd problems
make install            # creates .venv, installs deps, copies .env.example -> .env
# or manually:
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install -e .
cp .env.example .env
```

Put your keys in `.env` (never committed):

| Variable | Needed for | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | classify, cluster labels, evaluate | https://console.anthropic.com (or `ant auth login`) |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`, `REDDIT_USER_AGENT` | Reddit | https://www.reddit.com/prefs/apps → create a **script** app |
| `PRODUCTHUNT_TOKEN` | Product Hunt | https://www.producthunt.com/v2/oauth/applications → developer token |
| `YOUTUBE_API_KEY` | YouTube | Google Cloud console → enable *YouTube Data API v3* → API key |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (optional) | Telegram | https://my.telegram.org; first run asks for a login code |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` (optional) | Adzuna | https://developer.adzuna.com |
| `SCOUT_HASH_SALT` | handle hashing | any random string; keep it stable |

HN and App Store work with no keys at all, so you can prove the collection step immediately:

```bash
scout collect --source hn --since 7
scout collect --source appstore
scout stats
```

The first `scout cluster` downloads `sentence-transformers/all-MiniLM-L6-v2` (~90 MB) into the
Hugging Face cache.

## Running

```bash
scout collect [--source NAME] [--since DAYS] [--dry-run]
scout classify [--limit N] [--retry-failed]
scout cluster [--no-labels]
scout evaluate [--force] [--limit N]
scout digest [--top N] [--out digest.md]
scout run [--since DAYS] [--top N] [--out PATH] [--dry-run]   # whole pipeline
scout stats                                                    # counts + API cost
```

Global options: `--config PATH`, `--db PATH`, `-v`. Environment: `SCOUT_CONFIG`, `SCOUT_DB`.

`scout run --dry-run` prints what each stage would fetch, embed and send to Claude without doing it.

### What each stage does

- **collect** — runs every enabled source, filters posts against the multilingual query terms in
  `config.yaml`, detects language (`langdetect`), hashes handles, deduplicates on `(source, source_id)`.
- **classify** — batches of 20 items per Claude call. Strict JSON output: `is_problem`, `domain`,
  `who_has_it`, `problem_summary` (always English), `pain_score` 1–5, `money_mentioned`,
  `workaround_described`, `solution_requested`, `existing_solutions_named`. Malformed JSON is retried
  once, then the batch is marked `classification_failed` (re-run with `--retry-failed`). Already
  classified items are skipped.
- **cluster** — embeds `problem_summary` locally with MiniLM, agglomerative clustering with a cosine
  distance threshold (`clustering.distance_threshold`, default 0.35), rebuilt from scratch every
  run. Each cluster with ≥ 2 members gets a Claude-written `label` and `canonical_summary` from a
  sample of 10 member summaries (singletons reuse their one summary; no call). `growth_30d` = items in
  the last 30 days ÷ items in the 30 days before (denominator floored at 3).
- **evaluate** — one Claude call per cluster with `item_count >= 5` (config `evaluation.min_cluster_size`),
  using the founder's criteria verbatim plus founder context. Produces market-size arithmetic,
  1–5 scores, a written path to $1B, `what_would_kill_it`, `quickest_test` and a cross-market flag.
  Clusters whose membership is unchanged since their last evaluation reuse it (no new call);
  `--force` re-evaluates everything.
- **digest** — Markdown with a header (run date, items this week, new clusters, fastest-growing),
  the top N clusters by `overall_score` with three verbatim quotes each (≤ 25 words, linked), a
  *Cross-market gaps* section (clusters heavy in English sources but thin in RU/AR/HI, or the
  reverse) and a *Rising* section (`growth_30d > 2` regardless of score).

`overall_score` (0–100) = path_to_1b 0.35 + monopoly_potential 0.25 + location_independent 0.15 +
capital_light 0.10 + measurable_90d 0.05 + growth 0.10, where 1–5 scores map to 0–1 and growth is
normalised as `log2(growth)/4 + 0.5` clipped to 0–1 (flat = 0.5, 4× = 1). Weights live in
`config.yaml → evaluation.weights`.

### Model, effort and cost

The model is `claude-opus-5` (`config.yaml → model`). Thinking stays on (adaptive, the Opus 5
default); cost and latency are controlled per call type with `output_config.effort`
(`config.yaml → llm.effort`): `low` for classification and cluster labels, `high` for evaluation.
`llm.fallbacks: default` re-runs any request that the model's safety classifiers decline on
Anthropic's recommended fallback model, server-side, so a batch is not lost to a spurious refusal;
set it to `none` to disable. The system prompts are cache breakpoints, so repeated batches reuse
the cached prefix.

Every Claude call is logged in the `api_calls` table with token counts and an estimated cost;
`scout stats` prints totals, today's spend and an estimated daily cost. Rough guide at Opus 5
pricing ($5 / $25 per million input / output tokens): classification ≈ $0.05–0.10 per batch of 20
items at `low` effort, one label ≈ $0.01, one evaluation ≈ $0.15–0.40 at `high` effort. A daily
run over ~700 new items with ~20 clusters to evaluate is on the order of $5–10; unchanged clusters
are not re-evaluated.

### Offline smoke mode

`SCOUT_LLM=stub scout run` replaces Claude with a deterministic keyword stub so the whole pipeline
can be exercised without a key (tests use it). Stub output is placeholder text, the digest is stamped
**STUB MODE**, and stub classifications are stored like real ones — point it at a scratch database
(`--db scratch.db`) so real runs are not skipped later.

## Scheduling

```bash
make daily                # run now, digest to ~/scout/digests/YYYY-MM-DD.md
make install-launchd      # macOS: run every day at 06:00 via launchd
make uninstall-launchd
```

`scheduling/com.problemscout.daily.plist` is the launchd template; `make install-launchd` substitutes
the project path and home directory, copies it to `~/Library/LaunchAgents/` and loads it. Logs go to
`~/scout/logs/`. On Linux use cron: `0 6 * * * cd /path/to/problems && make daily`.

## Configuration

`config.yaml` holds the subreddit list, App Store app IDs (per category and country), YouTube
channel IDs (per industry), Telegram channels, Adzuna queries, query terms per language
(EN/RU/AR/HI), the cluster threshold, digest top-N and the evaluation weights. Every key has a
default in `scout/config.py`; omit what you do not need.

## Adding a source

1. Create `scout/sources/<name>.py` exposing:
   - `NAME = "<name>"`
   - `available(cfg) -> (bool, reason)` — check keys/config; never raise.
   - `describe(since_days, cfg) -> list[str]` — what a fetch would do (used by `--dry-run`).
   - `fetch(since_days, cfg) -> list[dict]` — return items built with
     `scout.sources.base.make_item(...)`, which hashes the author handle and strips name-like keys
     from the raw payload. Raise `SourceUnavailable` if the source cannot be used legitimately.
2. Use `http_client()` / `request_json()` from `base.py` (polite retries on 429/5xx) and sleep between
   calls.
3. Add the name to `SOURCE_NAMES` in `scout/sources/__init__.py` and a `sources.<name>` block to
   `config.yaml` (and `DEFAULTS` in `scout/config.py`).
4. Only official APIs, public JSON endpoints or RSS feeds. No browsers, no HTML scraping.

## Data model (SQLite, `scout.db`)

- `items` — one row per collected post/comment/review; unique on `(source, source_id)`.
- `problems` — one row per classified item (`is_problem` true or false, plus `classification_failed`),
  with `cluster_id` set by the cluster stage.
- `clusters` — rebuilt each run: label, canonical summary, domain, counts, sources, `growth_30d`,
  `member_hash`.
- `evaluations` — one row per cluster per evaluation run; kept across re-clustering and matched
  by `member_hash`.
- `api_calls` — every Claude call with tokens and cost. `runs` — stage timings.

## Tests

```bash
make test        # or: .venv/bin/python -m pytest -q
```

Covers deduplication, classifier JSON parsing and retry, clustering determinism on a fixture,
score arithmetic, and an end-to-end run with the stub model.

## First digest

`digests/first-digest-2026-09-08-stub.md` was produced from a live collection (523 Hacker News
items and 188 App Store reviews from the previous 7 days) run through the full pipeline in
**stub mode**, because the build environment had no Anthropic key. It proves collection, storage,
embedding, clustering, evaluation bookkeeping and rendering end to end; the problem summaries,
labels and evaluations in it are placeholders. Add `ANTHROPIC_API_KEY` to `.env` and run
`scout run` for the real thing.
