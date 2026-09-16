# Hot Startups

Agreed description, written in plain words. This is the reference for building the program.
Nothing in here is code yet.

## What it is

A weekly web page that shows the 50 startups worldwide with the highest potential, explained
simply, so the reader can see what ideas young, bright minds are chasing right now.

The goal is not to learn exact market sizes. The goal is to see which ideas work better and which
work worse, and to spark the reader's own ideas. Everything on the page serves that: the idea behind
each startup is the star of every entry, every entry carries a reusable lesson, struggling ideas are
shown next to winning ones, and each week the page points at gaps nobody is filling yet. The scores
are only a rough sorting tool.

## What counts as a startup

- Privately owned, not on the stock market.
- Small: under about 500 people.
- Any age, any country, any industry.

Big, well-known companies are dropped. This page is about startups, not large companies.

## What "potential" means

Three things, counted equally:

1. Potential market size.
2. Unique technology.
3. Growth speed. Faster is better.

Each gets a score from 0 to 10, with a one-line reason and a link to where the fact came from.
The overall score is the average of the three. When growth is unknown, which is common for private
companies, the entry says "growth unknown" and the overall score uses the other two. Unknown is never
scored as low.

## What happens every week

1. **Find candidates.** The program first reads a fixed set of startup list sites (see below), then
   the free news feeds of startup news sites, local ones included, then searches the internet for
   startups: funding news, "top startups" lists, accelerator batches, launch sites.
2. **Learn about each promising one.** It searches again for the company, reads the company
   website and news about it.
3. **Judge.** Claude writes, for each startup: the idea in one sentence, the twist (what is special),
   why now, who pays, the lesson (one sentence on the reusable pattern behind the idea, meant to
   travel into the reader's own thinking), the facts (founded, based in, team size, money raised,
   growth signal), the three scores with reasons, and the list of sources.
4. **Find trends.** Startups chasing the same idea are grouped and the pattern is named in one
   sentence.
5. **Find what is struggling.** A few searches a week for shutdowns, pivots, down rounds and stalls
   among startups on the list and in its trends. Each gets one line on what went wrong.
6. **Point at gaps.** Claude writes three to five sentences on holes in the list: combinations,
   regions or professions nobody on the list serves yet. Clearly marked as suggestions, not facts.
7. **Update the page.** Newcomers are marked "New" so the reader only needs to read the fresh part.

## Startup list sites

Websites that are themselves lists of startups, such as launch sites, accelerator company lists and
startup directories. They live in a simple file, one site per line, that the reader can add to at any
time (`hot_startups/list_sites.txt`). Every weekly run reads them before any searching. Pages are read
directly first; when a site blocks plain automated reading (VivaTech does), Claude's own page reader
is used instead, which gets through. Sites that cannot be read either way or need a login are reported
on the page, never skipped quietly. The reader's own finds go in from the start.

## International, not American only

- The program searches in each region's own language: Chinese, Japanese, Korean, German, French,
  Spanish, Portuguese, Indonesian, Turkish, Arabic, and others. Claude writes the search words in the
  language, reads the results, and writes the page in English.
- Each region has its own list of local startup news sites, and the program searches those directly.
- The weekly search budget is split roughly by how much startup activity each region has, with a
  minimum for every region so nobody is skipped. Starting split: North America 30%, Europe 25%,
  East and South Asia 25%, Latin America 8%, Middle East and Africa 7%, rest of the world 5%.
  A region that keeps producing strong finds gets a bit more the following week.
- No quotas on the list. The 50 are simply the 50 best, wherever they are.
- The page shows where the startups come from, so any tilt is visible.

## Memory

The program keeps everything it has learned in its own database. It never searches the same startup
twice, and it re-checks growth for known startups only once a month. The list gets better week by
week.

## Search and cost: the free setup

No paid search service. Three free ways of finding things, in this order:

1. **News feeds.** Most startup news sites, local ones included, publish a free feed of their latest
   articles. Reading feeds needs no key and costs nothing. This finds most candidates.
2. **Claude's built-in web search**, from the cloud session where the program runs. No key, no extra
   cost; it runs on the reader's Claude subscription. Tested on 13 September 2026 in Portuguese and
   Japanese: works, and returns local news.
3. **Reading company websites directly.** Free.

A paid search service (Tavily or Brave Search) can be plugged in later if more volume is ever wanted.
The design keeps a slot for it. Checked on 13 September 2026: Tavily's free plan is 1,000 searches a
month; Brave gives $5 of free credit a month, about 1,000 searches; Google's search service is closed
to new customers.

## Hard limits, set by the reader

The reader sets three numbers in the settings file, in plain words:

| Setting | Meaning | Starting value | First run |
|---|---|---|---|
| searches per week | internet searches a run may do | 250 | 60 |
| pages per week | web pages a run may read | 200 | 40 |
| startups judged per week | startups Claude may write up and score | 60 | 20 |

- The program enforces the limits, not Claude's good behaviour. It hands out search tickets one at a
  time and counts each one; when they are gone, no more searches happen. Same for pages and write-ups.
- A run also stops itself after a set time (90 minutes to start with), whatever it is doing.
- Every run writes its exact counts at the bottom of the page, for example:
  "This week's run: 212 searches of 250, 180 pages of 200, 48 startups judged of 60, 41 minutes."
- A pause switch stops the weekly runs entirely.
- The program cannot read the reader's subscription usage. So the first run uses the small "first run"
  limits above; afterwards the reader checks the usage page on claude.ai, and the weekly numbers are
  set together from real figures.

## Keeping usage low

Four rules cut the tokens a run spends, roughly halving it:

1. **No hunting while the backlog is big.** New-name searches run only when fewer than 100 found startups are
   waiting to be judged.
2. **Feeds are filtered first.** A feed entry reaches Claude only if it mentions funding, a launch or a new
   company, in any of our languages. Everything else is dropped by the program.
3. **Leaner write-ups.** The rulebook is read once per run, not sent with every write-up, and each dossier
   carries less website text.
4. **Empty search phrases retire.** The program records how many startups each search phrase finds; a phrase
   that found nothing twice is no longer used.

The weekly run can also be set to a lighter model, which uses less of the subscription's limit.

## The page

Fully online, opened from any laptop, following the approved sketch:

1. **This week in one minute.** Three sentences and three numbers: startups on the list, new this
   week, trends.
2. **Ideas that keep coming up.** The trends, each with one sentence and the startups behind it.
3. **Ideas that are struggling.** Shutdowns, pivots and stalls, each with one line on why.
4. **Gaps we noticed.** Three to five idea sparks for the week, marked as suggestions.
5. **The list.** One line per startup: rank, name, industry, country, the idea in one sentence, the
   score. Click to open the twist, why now, who pays, the lesson, facts and sources.
6. **Controls.** Show all or new this week; filter by industry; sort by potential, growth or newest.

Scores stay numbers out of 10 for now.

## Rules that guard against known problems

Built-in rules, always on:

- **Facts.** A fact is shown as fact only when two independent sources agree. With one source it reads
  "reported by X". Every fact carries a date; anything older than a year is marked "old". Official
  pages (company site, official announcements) beat blogs.
- **Too big to be a startup.** Before scoring, the program checks for signs of a stock listing or a
  large team. If found, the company is dropped and the reason logged.
- **Consistent scores.** Claude scores against a written rulebook with examples of what a 9, a 5 and a
  2 mean, the same for every startup. The rulebook is a file in the folder that the reader can read.
  Score history is kept, so a jump is visible.
- **Duplicates.** A startup is identified by its website address, not its name.
- **Dead startups.** Known startups are re-checked monthly. A website that no longer answers, or news
  of a shutdown, removes the entry.
- **Failed runs.** A run saves after every step and resumes where it stopped. The page always shows the
  date of the last successful run. The reader gets an email when a run fails.
- **Thin first weeks.** One small measuring run first, then one bigger catch-up run once the real usage
  is known, then the weekly rhythm.

Reduced and shown honestly, since they cannot be fully avoided:

- **Missing growth numbers.** When no number exists, the program looks for substitutes: open job
  positions, app store rank, a funding round bigger than the last. The entry says which substitute was
  used, or "growth unknown".
- **Blocked or changed websites.** Direct read first, then Claude's page reader. What still fails is
  listed on the page.
- **Under-covered regions.** The page shows the country mix every week. When a region looks thin, local
  list sites for it are added. The reader's own finds help most here.

## Where it runs

In the cloud, on a schedule, once a week (default: Monday morning). Nothing runs on a laptop.

## Where the code lives

The folder `hot_startups` in this repository. Fully separate from scout; no shared code.

## What the reader must provide

Nothing. No keys, no accounts. The reader only decides the weekly limits.
