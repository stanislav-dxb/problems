# Hot Startups

A weekly web page of the world's highest-potential startups, explained in plain words, built to spark ideas.
What it does and why is written in `DESCRIPTION.md`. This file says how to use it.

## Files you may edit

| File | What it is |
|---|---|
| `config.yaml` | the weekly limits, the pause switch, the page size |
| `list_sites.txt` | websites that list startups, one per line; the run reads them first |
| `sources.yaml` | news feeds and search words per region and language |
| `RULEBOOK.md` | how startups are judged and scored |

Everything else is code or memory.

## How a run works

The program (`python -m hotstartups`) hands out tickets: a search to run, a page to read, a startup to judge.
Claude, in a cloud session, does each ticket and hands the result back. The program counts every ticket against
the limits in `config.yaml`, so a run can never do more than allowed. `RUNBOOK.md` is the full instruction Claude follows.

Commands:

```
python -m hotstartups run start      # start (or resume) this week's run; prints the limits
python -m hotstartups next --batch 5 # hand out the next tickets
python -m hotstartups submit t007 work/t007.result.json
python -m hotstartups status         # counts against limits
python -m hotstartups run finish     # write the week file and the page (out/index.html)
python -m hotstartups check-sources  # test that every feed and list site can be read
python -m hotstartups show <id>      # one startup's record
```

## Memory

`data/startups/` holds one file per startup the program has ever seen, `data/runs/` one file per run, and
`data/weeks/latest.json` what the page shows. After every run the program bundles all of it into
`out/memory.json`, which is published next to the page, and also tries to commit it to git. The next run
fetches the bundle from the page first (`memory import`), so the memory survives even when a cloud session
cannot push to git. `work/` and `cache/` are scratch and not committed.

The page itself is two files: `out/index.html`, a small shell, and `out/entries.js`, the data it shows. Keeping
the shell small keeps republishing cheap.

## Tests

```
pip install -r requirements.txt
python -m pytest -q
```
