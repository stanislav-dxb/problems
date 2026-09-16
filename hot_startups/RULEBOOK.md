# Rulebook for judging a startup

Claude follows this when it writes an entry. Same rules for every startup, every week.

## Style

- Plain English. Short sentences. No jargon: say "software that does the accounts", not "AI-native fintech platform".
- Write for a curious reader who is not in the industry.
- The idea is the star. The twist and the lesson matter more than the money.

## Is it a startup?

- Privately owned. A company listed on a stock market is out.
- Small: team of about 500 people or fewer. Larger is out.
- Any age, any country, any industry.
- If unsure about size, keep it and mark team size unknown. If clearly over the limit, set `is_startup: false` and say why.

## Facts

- Every fact gets the URL it came from. Two independent URLs (different websites) make it confirmed.
- Never invent a number. Unknown is `null`.
- Prefer the company's own site and official announcements over blogs and reposts.
- A source older than a year is still usable, but say the date.

## Scores, 0 to 10

Each score has a one-sentence reason and, where possible, a source URL.

**Market: how big could this get?**
- 9 to 10: almost everyone, or every business, in a large part of the world could use it. Payments, energy, housing, health for all.
- 7 to 8: a large industry or a whole profession, worldwide.
- 5 to 6: a real market, but one country or one niche.
- 2 to 3: a hobby-sized or very local market.
- 0 to 1: no paying customers imaginable.

**Tech: how unique and hard to copy is what they built?**
- 9 to 10: something new that others cannot copy in years: new science, new hardware, a rare dataset.
- 7 to 8: a clever approach with a real edge that would take a strong team a year or two to match.
- 5 to 6: solid engineering, but a good team could copy it in months.
- 2 to 3: a thin layer on top of tools anyone can buy.
- 0 to 1: nothing built yet, or a plain copy.

**Growth: how fast is it growing?**
- 9 to 10: doubling users, revenue or customers every few months, with numbers to show it.
- 7 to 8: clear, fast growth in the last year with at least one number.
- 5 to 6: growing, but slowly or with weak evidence.
- 2 to 3: flat, or numbers only from the company itself with nothing to back them.
- `null`: no numbers at all. Then look for a substitute and name it in `basis`: open job positions, app store rank, a funding round bigger than the last, customer logos. If nothing, `basis: unknown`. Unknown is never scored low.

The overall score is the average of the known scores. It is a rough sorting tool, nothing more.

## Status

`status_signal` is `active` unless the dossier shows the company shut down, pivoted to a different business, or stalled with no news and no growth for a year or more.
