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
4. Fetch the memory that travels with the page: with the Artifact tool, `action: read_file`, `url` = the address in
   `config.yaml` under `page.artifact_url`, `path: memory.json`. It is saved into your scratchpad folder; the result
   names the path. Then `python -m hotstartups memory import <that path>`. It restores `data/` only if the bundle
   knows a later run than the repository does, and says so either way. If the file does not exist yet, continue
   with the repository's data.
5. `python -m hotstartups run start`. If it prints `paused`, stop here and say so. It prints the limits for this run.

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

1. `python -m hotstartups run finish` writes the week file, `out/index.html` (a small page shell), `out/entries.js`
   (the page's data) and `out/memory.json` (everything under `data/`, the program's memory), prints the exact
   counts, and tries to commit and push `data/`, `out/` and `config.yaml` to the branch. A failed push is only a
   warning: the memory also travels with the page in `memory.json`.
2. Publish with the Artifact tool. `page.artifact_url` in `config.yaml` is set: first `action: read` with that url
   (the page is small; read the saved file in full as the tool asks), then publish with `file_path: out/index.html`,
   `url` set to that address, and `files: {"entries.js": "out/entries.js", "memory.json": "out/memory.json"}`.
   Omit favicon. Both files are required: without `entries.js` the page is empty, without `memory.json` the next
   run forgets this one. If the address is empty: publish the same way without `url`, with favicon 🔥, then write
   the returned URL into `config.yaml` under `page.artifact_url`.
3. Final message: start with "OK" if everything worked or "FAILED" if anything did not; then the counts line from
   `run finish`, how many startups are on the page, how many are new, what could not be read, whether the push
   and the publish succeeded, and anything that failed. Short, plain words, for a reader who is not a programmer.
