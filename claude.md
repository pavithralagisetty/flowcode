# CLAUDE.md

## What we are building

A research agent for Flowcode. Flowcode makes branded QR codes and landing pages that connect real world places (packaging, TV, events, stores, signs) to online experiences, and it tracks every scan.

The agent takes a company name or website, researches what that company is doing right now (campaigns, product launches, sponsorships, events, packaging, retail), and returns a ranked outline of where Flowcode could help. Every opportunity says **what is happening**, **why Flowcode fits**, and **where the agent found it**, with links and dates.

It must work well for one company, and be designed so the same steps can run across hundreds or thousands of companies on a schedule, only redoing work when something new appears.

## Ground rules (read these first)

1. **No fact without a source.** Every claim in the output must point to a saved evidence record with a URL, a date, and the exact sentence from the page that backs it up. If a claim cannot be backed up, drop it.
2. **Search summaries are not evidence.** A search provider (including Gemini) may return a written answer. Only use the links it returns. Always fetch the real page ourselves and quote from it.
3. **Fresh only.** Ignore evidence older than `recency_days` in config (default 180), unless the page shows the thing is still ongoing or upcoming.
4. **Never use flowcode.com as evidence about the target company.** Flowcode pages are only used in the playbook and the existing customer check.
5. **The AI judges, the code does the math.** The AI gives 1 to 5 ratings with a one line reason. Python computes every weighted score. Weights live in config and are never hardcoded.
6. **Every provider is swappable.** Search and the AI model sit behind small interfaces, so switching providers means changing config and adding one file, nothing else.
7. **Keep it simple.** Plain Python, no agent frameworks. Small typed functions that are easy to read.

## Tech stack

- Python 3.11 or newer
- **Search (for now):** Gemini API with Grounding with Google Search, through the `google-genai` package. We will switch later (Perplexity Search API, Tavily, or Claude web search), so this must sit behind the `SearchProvider` interface.
- **Reasoning:** Claude through the `anthropic` package by default, with the option to use Gemini instead, chosen in config.
- **Page fetching:** `httpx` (follow redirects) and `trafilatura` to pull the main text and publish date.
- **Storage:** SQLite through the standard `sqlite3` module.
- **Data models:** `pydantic`
- **Config:** `config.yaml` for settings, `.env` for keys (`python-dotenv`)
- **CLI:** `typer`
- **Tests:** `pytest`, using a fake search provider and saved pages so tests never touch the network.

**Important note on Google:** the old Google Custom Search JSON API is closed to new customers and shuts down on January 1, 2027. Do not use it. Use Gemini grounding with Google Search. Gemini 3 grounding needs a Google project with billing turned on.

## Project layout

```
flowcode_agent/
  CLAUDE.md
  README.md
  config.yaml
  .env.example
  playbook.yaml
  existing_customers.yaml
  companies.csv                 # sample batch list
  prompts/
    resolve.md
    moments.md
    match.md
    verify.md
    score.md
  flowcode_agent/
    __init__.py
    __main__.py
    cli.py
    config.py
    models.py                   # pydantic models
    db.py                       # SQLite setup and queries
    fetch.py                    # page fetching and text extraction
    providers/
      search_base.py            # SearchProvider interface and SearchResult
      search_gemini.py          # Gemini grounding provider
      search_fake.py            # for tests
      llm_base.py               # LLM interface
      llm_anthropic.py
      llm_gemini.py
    steps/
      resolve.py                # step 1
      gather.py                 # step 2
      moments.py                # step 3
      match.py                  # step 4
      verify.py                 # step 5
      score.py                  # step 6
      report.py                 # step 7
    pipeline.py                 # runs all steps for one company
    batch.py                    # runs many companies, plus refresh
    evaluate.py
  outputs/                      # reports go here
  tests/
```

## Interfaces

```python
class SearchResult(BaseModel):
    url: str
    title: str | None = None
    snippet: str | None = None
    query: str
    provider: str

class SearchProvider(Protocol):
    name: str
    def search(self, query: str, max_results: int = 10) -> list[SearchResult]: ...

class LLM(Protocol):
    def complete_json(self, system: str, user: str, schema: type[BaseModel], model: str) -> BaseModel: ...
```

`get_search_provider(config)` and `get_llm(config)` pick the class by the name set in config.

For every AI call that returns structured data: ask for JSON matching the pydantic schema, validate it, and if validation fails retry once with the error message included. Log token usage for every call.

### Gemini grounding provider notes

- Send the query to the Gemini model from config with the Google Search tool turned on, asking it to search and list the most recent relevant pages.
- Read the sources from the response's grounding metadata (`grounding_chunks`, each with a web URI and title). Save `web_search_queries` too, for debugging.
- **Ignore the written answer text.** It is never evidence.
- Source links may come back as Google redirect links. Follow the redirect with `httpx` to get the real page URL before saving.
- Grounding is billed per request after a monthly free allowance, so cache every result in SQLite by (provider, query, day) and never repeat the same query on the same day.
- The model writes its own search queries, so hints like `site:` are best effort. That is fine.
- Check the current Gemini model list in Google's docs before setting the model name in config.

## The pipeline for one company

### Step 1: Resolve the company (`resolve.py`)

Input is a name or a URL. Search for the official site, then have the AI return the official name, official domain, industry, main brands or product lines, and a confidence from 0 to 1.

If confidence is low because the name could mean several companies, stop and print the candidates so the user can choose. `companies.csv` can include a `domain` column to skip this.

Check `existing_customers.yaml` and mark the company if it is already a Flowcode customer.

### Step 2: Gather evidence (`gather.py`)

Run this fixed set of searches for the company (and for each main brand if results are thin):

```
{brand} campaign
{brand} new ad
{brand} commercial site:ispot.tv
{brand} sponsorship
{brand} partnership
{brand} tour OR festival OR pop up
{brand} new packaging
{brand} limited edition
{brand} loyalty program OR sweepstakes
{brand} QR code
{brand} site:prnewswire.com OR site:businesswire.com
```

Also find and read the company newsroom: search `{brand} newsroom press releases`, then try common paths on the official domain like `/news`, `/newsroom`, `/press`.

For every result URL:

- Skip `blocked_domains` from config (flowcode.com is always blocked).
- Fetch with a timeout and a normal browser user agent. Log and skip failures.
- Pull the main text, title, publisher, and publish date with trafilatura. If there is no date, mark it as unknown.
- Save to the `evidence` table with a hash of the final URL and a hash of the text, so the same story copied across many sites is stored once.
- Drop pages older than `recency_days`. Keep pages with unknown dates, but mark them.
- Stop at `max_pages_per_company`.

### Step 3: Find moments (`moments.py`)

Send evidence to the AI in batches (the cheap model is fine here). For each concrete thing that is happening, return a `Moment`:

- `title`
- `type`: one of `product_launch`, `packaging`, `tv_ad`, `digital_campaign`, `sponsorship`, `live_event`, `retail_expansion`, `loyalty_or_contest`, `other`
- `summary`: what is happening, in 2 to 3 sentences
- `timing`: a date or date range, plus a status of `upcoming`, `ongoing`, or `past`
- `physical_touchpoints`: where a code could physically go (package, screen, ticket, sign, product tag, and so on)
- `evidence`: a list of `{evidence_id, exact_quote}`, where `exact_quote` is copied word for word from the page text

Merge moments that describe the same thing.

### Step 4: Match to the Flowcode playbook (`match.py`)

Load `playbook.yaml`. For each moment, the AI picks the best fitting play (or none) and writes:

- `why_flowcode_fits`: 2 to 3 sentences, specific to this moment
- `flowcode_products`: which products to use
- `similar_story`: taken from the play
- `suggested_first_pitch`: one or two sentences a sales rep could actually say

Drop moments with no physical touchpoint and no fitting play.

### Step 5: Verify (`verify.py`)

For each opportunity:

1. **Code check:** every `exact_quote` must appear in the saved page text. Normalize spaces and quote marks, and allow a fuzzy match above `quote_match_threshold`. If it fails, remove that evidence link.
2. **AI check:** given the claim and its quotes, does the evidence actually support the claim? Answer `yes`, `partly`, or `no`. On `no`, drop the claim. On `partly`, rewrite the claim to say only what is supported.
3. An opportunity with no verified evidence left is dropped.

Save the verification result on each opportunity.

### Step 6: Score (`score.py`)

Before scoring, remove anything unverified and any event that is already over.

The AI rates each opportunity 1 to 5 on each factor with a one line reason, using this rubric. Python computes the weighted score.

**Opportunity score**

| Factor | Weight | 5 means | 1 means |
|---|---|---|---|
| `timing` | 0.25 | Happening now or in the next 1 to 3 months | Already over |
| `playbook_fit` | 0.25 | Close match to a Flowcode customer story | No place to put a code |
| `reach` | 0.20 | National TV, nationwide packaging, millions of people | One small local event |
| `evidence_strength` | 0.15 | Company's own announcement plus news, last 30 days | One weak or older source |
| `openness` | 0.15 | Already uses plain QR codes or asks people to scan or sign up | Already tied to a competitor |

**Company score**

| Factor | Weight | How to rate it |
|---|---|---|
| `best_opportunity` | 0.40 | Score of its single best opportunity |
| `strong_count` | 0.20 | Opportunities scoring at least `strong_threshold` (3.5): none = 1, one = 2, two = 3, three = 4, four or more = 5 |
| `company_size` | 0.20 | AI rating from the evidence: national brand with big ad budgets = 5, small local business = 1 |
| `industry_fit` | 0.10 | 5 if the industry is in `strong_industries` in config, otherwise 3 |
| `new_activity` | 0.10 | Newest strong opportunity found in the last 30 days = 5, 31 to 60 days = 4, 61 to 90 days = 3, older = 2 |

Check at startup that each set of weights adds up to 1. Existing customers are scored, but listed separately in batch results.

### Step 7: Report (`report.py`)

Write `outputs/{company_slug}/report.md` and `outputs/{company_slug}/report.json`. Keep every quote in the report short (under 20 words). The Markdown follows this shape:

```
# {Company}: where Flowcode could help
Run date · Company score · Existing Flowcode customer: yes or no

## Snapshot
Industry, main brands, and 2 to 3 sentences on what they are doing right now.

## Top opportunities
### 1. {title} (score 4.35)
**What's happening:** ...
**Why Flowcode fits:** ...
**Similar Flowcode story:** {name} ({url})
**Suggested first pitch:** ...
**Score breakdown:** timing 5 (reason), playbook_fit 5 (reason), ...
**Sources:**
- {publisher}, {date}: "{short quote}" {url}

## Other opportunities
One line each, with score.

## New since last run
Only filled in refresh mode.

## Gaps
Channels where nothing was found (TV, social, email, events, packaging, retail).
```

## Batch and refresh (running at scale)

- **`batch`** reads `companies.csv` (columns `name`, and optionally `domain`), runs the pipeline for each company with the concurrency limit from config, and writes `outputs/ranking.md` and `outputs/ranking.csv` sorted by company score. Existing customers go in their own section.
- **Resume safely.** Every step saves its results to SQLite by company and run, so a crash can pick up where it left off without redoing finished steps.
- **`refresh`** reruns step 2 for each stored company, compares URL and text hashes with what is already saved, and only runs steps 3 to 7 when new evidence shows up. The report then fills in "New since last run". Running refresh twice in a row should find nothing new.
- **Usage logging.** Count search requests and AI tokens per company per run, save them in the `usage` table, and print totals at the end.
- **Retries.** Retry with backoff on rate limit and server errors.
- **Do not build a scheduler.** Instead, explain in the README how this would run for thousands of companies: a scheduled refresh, checking big accounts and companies with upcoming moments more often, a marketing calendar (Super Bowl, holidays, back to school, March Madness), and feedback from sales reps used to tune the weights.

## Database tables

```
companies(id, name, domain, industry, brands_json, is_existing_customer, created_at)
runs(id, company_id, mode, status, started_at, finished_at)
search_cache(provider, query, day, results_json)
evidence(id, company_id, run_id, url, final_url, url_hash, text_hash, title,
         publisher, published_date, date_known, fetched_at, text)
moments(id, company_id, run_id, json)
opportunities(id, company_id, run_id, moment_id, play_id, json, verified,
              score, factor_scores_json)
company_scores(company_id, run_id, score, factors_json)
usage(run_id, company_id, search_requests, llm_input_tokens, llm_output_tokens)
```

## Commands

```
python -m flowcode_agent run "Eddie Bauer"
python -m flowcode_agent run https://www.example.com
python -m flowcode_agent batch companies.csv
python -m flowcode_agent refresh
python -m flowcode_agent eval
```

All other options live in `config.yaml`, so commands stay short.

## Config

`config.yaml`:

```yaml
search:
  provider: gemini              # later: perplexity, tavily, claude
  gemini_model: "SET_ME"        # a current Gemini 3 model; check Google's model list
  max_results_per_query: 10

llm:
  provider: anthropic           # or gemini
  main_model: claude-sonnet-5-5
  cheap_model: claude-haiku-5-5

research:
  recency_days: 180
  max_pages_per_company: 60
  fetch_timeout_seconds: 15
  blocked_domains: [flowcode.com]

verify:
  quote_match_threshold: 0.9

scoring:
  strong_threshold: 3.5
  strong_industries: [retail and consumer goods, sports and entertainment, media]
  opportunity_weights:
    timing: 0.25
    playbook_fit: 0.25
    reach: 0.20
    evidence_strength: 0.15
    openness: 0.15
  company_weights:
    best_opportunity: 0.40
    strong_count: 0.20
    company_size: 0.20
    industry_fit: 0.10
    new_activity: 0.10

batch:
  concurrency: 4
```

`.env.example`:

```
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
```

If `llm.provider` is `gemini`, only the Gemini key is needed.

## Playbook (`playbook.yaml`)

Seed it with these plays, taken from Flowcode's published customer stories. Each play has an id, a name, signals to look for, the Flowcode idea, products, and a proof story with its link and result.

```yaml
products:
  - Flowcodes (branded QR codes)
  - FlowURLs
  - FlowHubs
  - FlowID (customer profiles built from scans)
  - Analytics and Integrations
  # Confirm each product's description on flowcode.com before relying on it.

plays:
  - id: packaging_signup
    name: Package to customer list
    signals: [new product, new flavor, packaging redesign, limited edition]
    idea: A code on the package that leads to a giveaway, contest, or sign up
    proof:
      name: LOVE CORN
      url: https://www.flowcode.com/case-study/love-corn
      result: 43,000 scans and 27,000 new contacts

  - id: tv_to_purchase
    name: TV ad to purchase
    signals: [TV ad, big game ad, broadcast sponsorship, streaming ad]
    idea: A code on screen that goes straight to buying
    proof:
      name: NFL Shop
      url: https://www.flowcode.com/case-study/nfl-shop
      result: $25,000 in sales and 10,000 scans from one airing

  - id: live_event
    name: Live events and tours
    signals: [tour, festival, concert, stadium, game day, pop up]
    idea: Codes on tickets, merch, and signs for sign ups and sales
    proof:
      name: Ceremony of Roses (Zach Bryan tour)
      url: https://www.flowcode.com/case-study/ceremony-of-roses
      result: 152,000 fan emails and $1.8M in extra merch sales

  - id: product_tags
    name: A code on every product
    signals: [many products in stores, new collection, apparel, gear]
    idea: A unique code on each product tag with details, reviews, and stock, with the link set after printing
    proof:
      name: Eddie Bauer
      url: https://www.flowcode.com/case-study/eddie-bauer
      result: 167,000 scans and 183% growth year over year

  - id: many_locations
    name: Codes across many locations
    signals: [stores, airports, kiosks, signs, national rollout]
    idea: Codes on signs at every location, tracking which spots work best
    proof:
      name: CLEAR
      url: https://www.flowcode.com/case-study/clear
      result: over 7,000 codes and over 1.2M scans

  - id: contest_entry
    name: Contest or loyalty entry
    signals: [sweepstakes, contest, loyalty program, rewards]
    idea: The code is how people enter
    proof:
      name: Capri Sun
      url: https://www.flowcode.com/case-study/capri-sun
      result: over 28,000 contest entries
```

## Existing customers (`existing_customers.yaml`)

```yaml
# Brands with a published Flowcode customer story
case_study_customers:
  - LOVE CORN
  - Waterpik
  - CLEAR
  - NFL Shop
  - Capri Sun
  - Care Connections
  - Feld Entertainment
  - Pittsburgh Penguins
  - PIM Brands
  - Eddie Bauer
  - Ceremony of Roses
  - Buoyant Aero
  - Jonas Brothers
  - MoonPay
  - GoBowling
  - Welch's
  - Lunchables
  - Planters

# Brands shown in Flowcode's logo strip or design gallery (likely customers, lower confidence)
likely_customers:
  - ESPN
  - Ford
  - Paramount
  - Pfizer
  - SoFi
  - Uber Eats
  - LEGO
  - Hoka
  - Audi
  - NASCAR
  - Chewy
  - Airbnb
  - Disney+
  - Poppi
```

Matching should ignore case and punctuation.

## Prompts

Keep each prompt in `prompts/` as its own Markdown file so it is easy to tune. Every prompt must:

- say the output must be JSON matching the given schema, with nothing else
- say to use only the evidence provided and never outside knowledge for facts about the company
- say to copy quotes word for word
- say to return an empty list rather than guess

## Evaluation (`eval` command)

1. **Golden test:** run on Eddie Bauer and LOVE CORN with flowcode.com blocked. Pass if at least one top opportunity uses the same play as their real Flowcode story (`product_tags` for Eddie Bauer, `packaging_signup` for LOVE CORN).
2. **Quality numbers for every run:** share of claims that passed verification, share of sources from the last 90 days, share of source links that still load, number of opportunities, and which channels were covered.
3. Write the results to `outputs/eval.md`.

## Build order

Build one step at a time. After each step, run the tests and do one small real run before moving on.

1. Config, models, database, fake providers, and the test setup
2. Gemini search provider, page fetching, and the gather step (run on one company and look at the saved evidence)
3. Resolve step
4. Moments step and match step
5. Verify step
6. Score step
7. Report step
8. Batch and refresh
9. Evaluation
10. README covering the design, the choices made, the scoring weights, and the plan for running at scale

## Done means

- `run "Eddie Bauer"` produces a report where every source link loads and every quote is found on its page.
- `batch companies.csv` with 5 to 10 companies produces a ranking.
- `refresh` run twice in a row reports nothing new the second time.
- All tests pass with no network access.
- The demo uses a brand that is not in `existing_customers.yaml` and has a lot going on in packaging, events, or TV.

## Coding style

- Type hints everywhere, short docstrings
- Use `logging`, and only print in the CLI
- Keep functions short and focused
- No keys or secrets in code