# Hot Startups

Agreed description, written in plain words. This is the reference for building the program.
Nothing in here is code yet.

## What it is

A weekly web page that shows the 50 startups worldwide with the highest potential, explained
simply, so the reader can see what ideas young, bright minds are chasing right now. The purpose is
inspiration for the reader's own ideas, so the idea behind each startup is the star of every entry.

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

1. **Find candidates.** The program reads the free news feeds of startup news sites, local ones
   included, and searches the internet for startups: funding news, "top startups" lists, accelerator
   batches, launch sites.
2. **Learn about each promising one.** It searches again for the company, reads the company
   website and news about it.
3. **Judge.** Claude writes, for each startup: the idea in one sentence, the twist (what is special),
   why now, who pays, the facts (founded, based in, team size, money raised, growth signal), the
   three scores with reasons, and the list of sources.
4. **Find trends.** Startups chasing the same idea are grouped and the pattern is named in one
   sentence.
5. **Update the page.** Newcomers are marked "New" so the reader only needs to read the fresh part.

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

## The page

Fully online, opened from any laptop, following the approved sketch:

1. **This week in one minute.** Three sentences and three numbers: startups on the list, new this
   week, trends.
2. **Ideas that keep coming up.** The trends, each with one sentence and the startups behind it.
3. **The list.** One line per startup: rank, name, industry, country, the idea in one sentence, the
   score. Click to open the twist, why now, who pays, facts and sources.
4. **Controls.** Show all or new this week; filter by industry; sort by potential, growth or newest.

Scores stay numbers out of 10 for now.

## Where it runs

In the cloud, on a schedule, once a week (default: Monday morning). Nothing runs on a laptop.

## Where the code lives

The folder `hot_startups` in this repository. Fully separate from scout; no shared code.

## What the reader must provide

Nothing. No keys, no accounts. The reader only decides the weekly limits.
