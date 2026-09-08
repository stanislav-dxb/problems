# Problem Scout

`scout` is a local Python tool that collects public discussions where people describe problems
(Reddit, Hacker News, App Store reviews, Product Hunt, YouTube, optionally Telegram and job ads),
flags the ones that describe a real difficulty, clusters recurring problems, ranks the clusters by
measurable evidence, and writes a weekly Markdown digest.

**No LLM, no API spend.** Everything runs on your machine: phrase rules for classification, a
multilingual sentence-embedding model for clustering, and an evidence score for ranking. The only
credentials involved are the free ones for the sources themselves, and HN plus App Store need none.

```
collect  →  classify  →  cluster  →  score  →  digest
(source     (phrase       (local        (evidence   (Markdown)
 APIs)       rules)        embeddings)   score)
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
the reason is logged. Author handles are salted-hashed before storage, name-like fields are stripped
from stored raw payloads, and personal names never appear in the digest.

## Setup

Requirements: Python 3.11+, about 1 GB of disk for PyTorch plus the embedding model (CPU is fine).

```bash
git clone <this repo> && cd problems
make install            # creates .venv, installs deps, copies .env.example -> .env
# or manually:
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install -e .
cp .env.example .env
```

Source credentials go in `.env` (never committed):

| Variable | Needed for | Where to get it |
|---|---|---|
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`, `REDDIT_USER_AGENT` | Reddit | https://www.reddit.com/prefs/apps → create a **script** app (free) |
| `PRODUCTHUNT_TOKEN` | Product Hunt | https://www.producthunt.com/v2/oauth/applications → developer token |
| `YOUTUBE_API_KEY` | YouTube | Google Cloud console → enable *YouTube Data API v3* → API key |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (optional) | Telegram | https://my.telegram.org; first run asks for a login code |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` (optional) | Adzuna | https://developer.adzuna.com |
| `SCOUT_HASH_SALT` | handle hashing | any random string; keep it stable |

The first `scout cluster` downloads `paraphrase-multilingual-MiniLM-L12-v2` (~470 MB) into the
Hugging Face cache. Switch to `all-MiniLM-L6-v2` in `config.yaml` for a smaller, English-only model.

## Running

```bash
scout collect [--source NAME] [--since DAYS] [--dry-run]
scout classify [--limit N] [--reclassify]
scout cluster
scout score
scout digest [--top N] [--out digest.md]
scout run [--since DAYS] [--top N] [--out PATH] [--dry-run]   # whole pipeline
scout stats
```

Global options: `--config PATH`, `--db PATH`, `-v`. Environment: `SCOUT_CONFIG`, `SCOUT_DB`.
A full run over a week of HN and App Store data takes about three minutes, almost all of it
waiting politely on the source APIs.

### What each stage does

- **collect** — runs every enabled source, filters posts against the multilingual query terms in
  `config.yaml`, detects language, hashes handles, deduplicates on `(source, source_id)`.
- **classify** — rule-based. Each item gets signal points from problem phrases in English, Russian,
  Arabic and Hindi ("is there a tool", "вручную", "هل يوجد", "koi tool hai", …), first-person
  markers and source metadata (a 1–2 star review counts), minus promotional markers ("Show HN",
  "we just launched"). Items at or above `classify.threshold` become problems. Also recorded:
  `pain_score` (1–5 from signal strength), `money_mentioned`, `workaround_described`,
  `solution_requested`, and `existing_solutions_named` (a list of ~90 common tools matched by name).
  The `problem_summary` is the first sentence carrying a signal, kept in its original language.
  The rules live at the top of `scout/classify.py`; edit them and run `scout classify --reclassify`.
- **cluster** — embeds summaries with a local multilingual MiniLM model, so a Russian and an
  English post about the same pain land together, then agglomerative clustering on cosine distance
  (`clustering.distance_threshold`, default 0.55), rebuilt from scratch every run. Each cluster's
  label and canonical summary come from its medoid, the member closest to the cluster centre.
  `growth_30d` = items in the last 30 days ÷ items in the 30 before (denominator floored at 3).
- **score** — for every cluster with `item_count >= scoring.min_cluster_size` (default 3), an
  evidence score from eight 0–1 components: volume (log-scaled count), growth, source spread,
  language spread across EN/RU/AR/HI, mean pain, share mentioning money, share asking for a
  solution, share describing a workaround. Weighted per `scoring.weights` onto 0–100. It also counts
  the distinct tools members already use as a competition indicator.
- **digest** — Markdown with a header (run date, items this week, new clusters, fastest-growing),
  the top N clusters by score, each with its representative post, evidence line, signal breakdown,
  tools already in use and three verbatim quotes (≤ 25 words, linked to the source), a
  *Cross-market gaps* section (clusters heavy in English but thin in RU/AR/HI, or the reverse) and
  a *Rising* section (`growth_30d > 2` regardless of score).

The tool ranks evidence; the judgement about market size, defensibility and whether a problem can
carry a large company stays with you. The digest is built to make that judgement fast: read the
quotes, note the growth, see what people already pay for.

### Tuning

- Too much noise in "problems": raise `classify.threshold` to 4 or 5, or add promotional phrases to
  `PROMO_SIGNALS`. Too few: add phrases to `PROBLEM_SIGNALS` (any language) or lower the threshold.
- Clusters too broad or too fragmented: lower or raise `clustering.distance_threshold` (0.45–0.65
  is the useful range). Re-clustering is cheap.
- What "important" means: change `scoring.weights`. Weights are normalised, so only ratios matter.

## Scheduling

```bash
make daily                # run now, digest to ~/scout/digests/YYYY-MM-DD.md
make install-launchd      # macOS: run every day at 06:00 via launchd
make uninstall-launchd
```

`scheduling/com.problemscout.daily.plist` is the launchd template; `make install-launchd` fills in
the project path and home directory, copies it to `~/Library/LaunchAgents/` and loads it. Logs go to
`~/scout/logs/`. On Linux use cron: `0 6 * * * cd /path/to/problems && make daily`.

## Configuration

`config.yaml` holds the subreddit list, App Store app IDs (per category and country), YouTube
channel IDs (per industry), Telegram channels, Adzuna queries, query terms per language
(EN/RU/AR/HI), the classification threshold, cluster threshold, scoring weights and digest top-N.
Every key has a default in `scout/config.py`; omit what you do not need.

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
   `config.yaml` (and `DEFAULTS` in `scout/config.py`). If the source has a natural domain
   (subreddit, category, channel), map it in `domain_for()` in `scout/classify.py`.
4. Only official APIs, public JSON endpoints or RSS feeds. No browsers, no HTML scraping.

## Data model (SQLite, `scout.db`)

- `items` — one row per collected post/comment/review; unique on `(source, source_id)`.
- `problems` — one row per classified item (`is_problem`, signals, `signal_score`), with
  `cluster_id` set by the cluster stage.
- `clusters` — rebuilt each run: label, canonical summary, domain, counts, sources, `growth_30d`.
- `scores` — one row per scored cluster: the eight components, competition count, `overall_score`.
- `runs` — stage timings.

## Tests

```bash
make test        # or: .venv/bin/python -m pytest -q
```

Covers deduplication and handle hashing, the phrase-rule classifier in four languages, clustering
determinism on a fixture, score arithmetic, and an end-to-end run with a fake embedder.

## First digest

`digests/first-digest-2026-09-08.md` was produced by this pipeline from a live collection (30 days
of Hacker News, 7 days of App Store reviews, 1,245 items) with no external model calls. Hacker News
comments are a noisy source for rule-based detection; Reddit, once its free script-app keys are in
`.env`, is the richer one for this tool.
