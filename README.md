# Problem Scout

`scout` is a local Python tool that collects public discussions where people describe problems
(Reddit, Hacker News, App Store reviews, Product Hunt, YouTube, optionally Telegram and job ads),
flags the ones that describe a real difficulty, clusters recurring problems, ranks the clusters by
measurable evidence, and writes a weekly Markdown digest.

**No API key, no per-token bill.** Model calls go through the Claude Code CLI in headless mode on
your logged-in Claude subscription (see *Model backend*). Clustering runs on a local embedding model;
collection uses the sources' own free APIs, and HN plus App Store need no credentials at all.

```
collect → classify → cluster → news → reports → triangulate → evaluate → score → digest
(source    (model or    (local      (why    (category   (pain × market   (model,       (evidence  (Markdown)
 APIs)      rules)       embeddings) now)    size)       × timing)        criteria)     score)
```

## Model backend

`config.yaml → llm.backend` selects how the classify and evaluate stages call a model:

| backend | how it works | credentials | cost |
|---|---|---|---|
| `claude_code` (default) | shells out to `claude -p <prompt> --system-prompt <system> --output-format json --model <model> --tools ""` and parses the JSON envelope | the Claude Code CLI, logged in once (`claude`) | draws on your Claude subscription's usage quota; no API key, no per-token bill |
| `anthropic_api` | the Anthropic Python SDK (adaptive thinking, server-side safety fallbacks) | `ANTHROPIC_API_KEY` or an `ant auth login` profile; `pip install anthropic` | billed per token to a Console organisation |
| `rules` | multilingual keyword classifier, no model at all | none | free; `scout evaluate` is skipped with a logged reason |

`llm.model` is the classify model (`sonnet` / `opus` / `haiku` CLI aliases); `llm.evaluate_model`
(default `opus`) is used for `scout evaluate`. `llm.max_parallel` caps concurrent CLI processes during
classification; `llm.timeout_s` bounds each call. With `claude_code`, startup runs `claude --version`
and one tiny probe call, and stops with a clear message if the CLI is missing or not logged in. The
tool never falls back to another backend silently. Headless runs consume subscription usage exactly
like typing into Claude Code; `scout plan` shows how many calls the next run will make.

Classification sends batches of `classify.batch_size` (15) items with a strict-JSON contract
(`is_problem`, English `problem_summary`, `who_has_it`, `domain`, `pain_score` 1–5, `money_mentioned`,
`workaround_described`, `solution_requested`, `existing_solutions_named`). A call that exits non-zero
or times out is retried once, then the batch is marked failed (`--retry-failed` re-runs those). A
prompt over 60k characters halves the batch. Markdown fences and a leading "json" label are stripped
before parsing. Token counts are not exposed by the CLI, so `scout stats` logs calls and item counts
per stage per day instead.

Evaluation makes one call per cluster with `item_count >= evaluate.min_cluster_size` (3), sequential,
passing the cluster's quotes plus its matched report claims and news catalysts. Output: market size
with source, the five criteria scores, `runs_without_founder`, path to $1B, why now, what would kill
it, quickest test, evidence gaps, corridor advantage. Unchanged clusters reuse their evaluation; a
cluster is re-evaluated when it grew by `evaluate.reevaluate_on_growth` (20%) or more. `overall_score`
weights: path_to_1b 0.30, monopoly_potential 0.20, triangulation_score 0.20, location_independent
0.12, capital_light 0.08, measurable_90d 0.05, growth_30d 0.05.

## Sources

Pain sources (individual people describing a problem):

| Source | Access | Key needed | Default |
|---|---|---|---|
| Hacker News | Algolia search API | none | on |
| Apple App Store reviews (1–2 star) | public RSS/JSON feed | none | on |
| Reddit | official OAuth API (script app) | `REDDIT_*` | on, skipped without keys |
| Product Hunt | GraphQL API | `PRODUCTHUNT_TOKEN` | on, skipped without key |
| YouTube comments | Data API v3 | `YOUTUBE_API_KEY` + channel IDs in config | on, skipped without key |
| Telegram public channels | Telethon (user account) | `TELEGRAM_API_ID/HASH` + `pip install telethon` | off |
| Adzuna job ads | Adzuna API | `ADZUNA_APP_ID/KEY` | off |

Reports and news (category size and timing, see *Reports & news layer* below):

| Family | Sources | Key needed |
|---|---|---|
| Reports | World Bank Documents API, publisher RSS feeds (McKinsey Insights by default), arXiv econ/finance abstracts | none |
| News | Google News RSS per query and locale (EN/RU/AR/HI, Gulf/India/US/Russia), trade press and regulator RSS (Economic Times, Mint, Kommersant, Vedomosti, RBC, TechCrunch, Sifted, Tech in Asia, RBI, SEC, EU Commission), GDELT 2.0 | none |

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
scout plan                          # backend, models, number of model calls the next run will make
scout classify [--limit N] [--reclassify] [--retry-failed]
scout cluster
scout news [--since DAYS]          # articles -> catalysts
scout reports [--since DAYS] [--reprocess]   # reports -> claims (default window 730 days; --reprocess re-runs rules on stored reports)
scout triangulate                  # clusters x claims x catalysts -> triangulations, hypotheses
scout evaluate [--force] [--limit N]   # one model call per cluster against the founder's criteria
scout score
scout digest [--top N] [--out digest.md] [--corridor]
scout run [--since DAYS] [--top N] [--out PATH] [--corridor] [--reclassify] [--dry-run]   # whole pipeline
scout stats
```

Global options: `--config PATH`, `--db PATH`, `-v`. Environment: `SCOUT_CONFIG`, `SCOUT_DB`.
A full run over a week of HN and App Store data takes about three minutes, almost all of it
waiting politely on the source APIs.

### What each stage does

- **collect** — runs every enabled source, filters posts against the multilingual query terms in
  `config.yaml`, detects language, hashes handles, deduplicates on `(source, source_id)`.
- **classify** — with a model backend, batches go to the model (see *Model backend*) and come back
  with English summaries, who has the problem and a domain. With `rules`, each item gets signal
  points from problem phrases in English, Russian, Arabic and Hindi ("is there a tool", "вручную",
  "هل يوجد", "koi tool hai", …), first-person markers and source metadata, minus promotional
  markers; items at or above `classify.threshold` become problems and the summary is the first
  signal-bearing sentence in its original language. `scout classify --reclassify` redoes everything.
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

### Reports & news layer

Forum posts and reviews capture one person's pain. Two more families add what they cannot:

- **Reports** (`scout reports`) tell you which pain sits inside a large category. Each report's text
  (PDF via pymupdf, or article HTML) is chunked into ~2,000-token sections with headings kept, passed
  through a cheap keyword prefilter (`reports.prefilter_keywords`, skip rate logged), and each surviving
  chunk becomes one `report_claims` row typed by multilingual keyword rules: `market_size`,
  `growth_rate`, `structural_gap`, `regulatory_change`, `technology_shift`, `incumbent_weakness` or
  `demand_shift`, with extracted figures (currency, percent, year), geographies and a per-publisher
  `confidence_in_source` (World Bank 4, consultancies 3). Claim sentences that look like table rows,
  references or boilerplate are dropped. After editing the rules, `scout reports --reprocess` re-chunks
  the stored reports without fetching.
- **News** (`scout news`) tells you *why now*. Each article's headline and summary is typed as a
  catalyst (`regulation`, `new_mandate`, `cost_collapse`, `shortage`, `incumbent_exit`,
  `incumbent_failure`, `funding_signal`, `demographic`, `geopolitical`) with a strength of 1–5, a time
  horizon (from future years in the text, else a per-type default) and geographies. Full article text
  is off by default (`news.fetch_full_text`); when on, trafilatura fetches it and robots.txt is honoured.
- **Triangulate** (`scout triangulate`) embeds cluster summaries, claims and catalysts with the same
  local multilingual model and matches them within `triangulation.distance_threshold`. Three legs are
  scored 0–1: pain (volume, mean pain, growth), market (best matched claim: confidence × claim-type
  weight × similarity) and timing (best matched catalyst: strength × recency × similarity). The
  triangulation score is their geometric mean, so a missing leg gives zero on purpose. Every cluster
  also records its weakest leg and what would fill it. Strong claims or catalysts that match no
  cluster become **hypotheses** with a suggested person-type to ask.

The digest gains three sections: *Triangulated* (all three legs, with the market figure and source,
the catalysts with dates), *Hypotheses* (clearly labelled unverified) and *Catalyst watch* (top
catalysts this week with horizon). `--corridor` keeps only report/news signals touching two or more
of Gulf, Russian-speaking, India, China and the EU. The evidence score gets a `triangulation`
component (weight 0.20 by default).

Because everything is rule-based, `affected_parties` is left empty and claim domains fall back to the
chunk heading; treat both claims and catalysts as leads to read, not conclusions.

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
- `scores` — one row per scored cluster: the nine components, competition count, `overall_score`.
- `reports`, `report_claims` — fetched reports (PDFs under `data/reports/`) and their typed claims.
- `news_articles`, `news_catalysts` — fetched articles and their typed catalysts.
- `triangulations`, `hypotheses` — per-cluster legs and score; unmatched strong signals.
- `runs` — stage timings.

## Tests

```bash
make test        # or: .venv/bin/python -m pytest -q
```

Covers deduplication and handle hashing, the phrase-rule classifier in four languages, clustering
determinism on a fixture, score arithmetic, an end-to-end run with a fake embedder, the extractors
(geography, figures, horizons), Google News and GDELT URL builders per language, a generated PDF
through chunking and claim storage, and triangulation with reverse hypotheses on a fixture set.

## First digest

`digests/first-digest-2026-09-08.md` was produced by this pipeline from a live collection (30 days
of Hacker News, 7 days of App Store reviews, 1,245 items) with no external model calls. Hacker News
comments are a noisy source for rule-based detection; Reddit, once its free script-app keys are in
`.env`, is the richer one for this tool.
