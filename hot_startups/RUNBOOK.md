# Weekly run: what Claude does

This is the complete instruction for the weekly Hot Startups job. It runs in a Claude Code session in the cloud.
The program hands out tickets and counts them; Claude does the searching, reading and judging.

The main session must stay small. It never reads a ticket itself: every batch of tickets is worked by a
short-lived helper (the Agent tool), which starts with an empty memory, does the batch, submits, and reports
one line per ticket. This keeps the run cheap: a long session that re-reads a growing history on every step
costs many times more than the work itself.

## Setup (main session)

1. In the repository, check out the branch named in `hot_startups/config.yaml` under `git.branch` and pull the latest.
2. `pip install -q -r hot_startups/requirements.txt`
3. `cd hot_startups && python -m hotstartups init`
4. `python -m hotstartups run start`. If it prints `paused`, stop here and say so. It prints the limits for this run.

## The loop (main session)

Repeat until `next` prints `"done": true`:

1. `python -m hotstartups next --batch 8 --out work/next.json`. Do not read `work/next.json` yourself.
2. Launch one helper with the Agent tool (general-purpose), with this task, verbatim, plus the run's folder path:

   > Work the tickets in `hot_startups/work/next.json` (read that file first; the run folder is `hot_startups/`).
   > For each task in `tasks`, do exactly what its `instructions` say, using only the task's own fields:
   > `search` and `research_search`: run the WebSearch tool with `query`, several in parallel. `extract`: read
   > the `text` in the task, no web access. `fetch_extract` and `fetch`: use the WebFetch tool on `url`.
   > `judge`: read `hot_startups/RULEBOOK.md` once, then write the entry following `schema` and `dossier` exactly.
   > `struggling`, `trends`, `gaps`, `brief`: read the task payload and write the result.
   > Save each result as `hot_startups/work/<ticket>.result.json`, then run the command in the task's
   > `submit_with` from inside `hot_startups/`. If a submit prints `rejected`, fix the file and submit again.
   > Never run a web search or read a web page that is not in a ticket. When every ticket is submitted,
   > reply with one line per ticket: ticket id, type, and the submit summary. Nothing else.

3. Read the helper's one-line-per-ticket reply. If a ticket is missing from it, run `python -m hotstartups status`;
   tickets still `issued` come back on the next `next` call, so just continue the loop.

## Closing (main session)

1. `python -m hotstartups run finish` writes `data/weeks/latest.json` and `out/index.html`, prints the exact counts,
   and then commits and pushes `data/`, `out/` and `config.yaml` to the branch by itself. Check its `git` field:
   `"pushed": true` is required. If it says `"pushed": false`, run `git status` and `git push -u origin <branch>`
   yourself and say what happened. The data folder is the program's memory; without the push the next run forgets
   everything.
2. Publish the page with the Artifact tool. `page.artifact_url` in `config.yaml` is set: first `action: read` with
   that url, then publish `out/index.html` with `url` set to it (omit favicon). If it is empty: publish
   `out/index.html` as a new artifact with favicon 🔥, write the returned URL into `config.yaml` under
   `page.artifact_url`, commit and push again.
3. Final message: start with "OK" if everything worked or "FAILED" if anything did not; then the counts line from
   `run finish`, how many startups are on the page, how many are new, what could not be read, whether the push
   and the publish succeeded, and anything that failed. Short, plain words, for a reader who is not a programmer.
