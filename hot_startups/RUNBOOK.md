# Weekly run: what Claude does

This is the complete instruction for the weekly Hot Startups job. It runs in a Claude Code session in the cloud.
The program hands out tickets and counts them; Claude does the searching, reading and judging.

## Setup

1. In the repository, check out the branch named in `hot_startups/config.yaml` under `git.branch` and pull the latest.
2. `pip install -q -r hot_startups/requirements.txt`
3. `cd hot_startups && python -m hotstartups init`
4. `python -m hotstartups run start`. If it prints `paused`, stop here and say so. It prints the limits for this run.
5. Read `RULEBOOK.md` once now. Every `judge` ticket relies on it; the ticket itself no longer repeats it.

## The loop

Repeat until `next` prints `"done": true`:

1. `python -m hotstartups next --batch 5 --out work/next.json` and read `work/next.json`.
2. For each task in `tasks`, do exactly what its `instructions` say, using the task's own fields:
   - `search` and `research_search`: run the WebSearch tool with `query` (several in parallel is fine). Write the JSON result file.
   - `extract`: read the `text` in the task and write the candidates file. No web access needed.
   - `fetch_extract` and `fetch`: use the WebFetch tool on `url` with a prompt as the instructions say. Write the result file.
   - `judge`: read `schema` and `dossier` in the task, apply the rulebook you read at the start, write the entry file following the schema exactly.
   - `struggling`, `trends`, `gaps`, `brief`: read the task payload, write the result file.
3. Save each result as `work/<ticket>.result.json` and run the command in the task's `submit_with`.
   If submit prints `rejected`, fix the file and submit again.
4. Never run a search or read a page that is not in a ticket. The tickets are the budget.

## Closing

1. `python -m hotstartups run finish` writes `data/weeks/latest.json` and `out/index.html` and prints the exact counts.
2. Publish the page with the Artifact tool. If `page.artifact_url` in `config.yaml` is set: first `action: read` with that url, then publish `out/index.html` with `url` set to it (omit favicon). If it is empty: publish `out/index.html` as a new artifact with favicon 🔥, then write the returned URL into `config.yaml` under `page.artifact_url`.
3. `git add data out config.yaml && git commit -m "Hot Startups weekly run <run id>"` and `git push -u origin <branch>`. The data folder is the program's memory; without the push the next run forgets everything.
4. Final message: the counts line from `run finish`, how many startups are on the page, how many are new, what could not be read, and anything that failed. If anything failed, say "FAILED" in the first line so the notification is sent.
