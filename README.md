# Flowcode research agent

Give it a company name or website. It researches what the company is doing right now (campaigns, launches, packaging, TV, sponsorships, events, retail, loyalty), and writes a ranked list of places Flowcode could help. Every opportunity says what is happening, why Flowcode fits, and where the agent found it, with a link, a date, and a quote.

## Setup

```bash
python3.11 -m venv .venv          # 3.11 or newer
.venv/bin/pip install google-genai anthropic httpx trafilatura pydantic typer pytest python-dotenv pyyaml
cp .env.example .env              # fill in GEMINI_API_KEY and ANTHROPIC_API_KEY
```

Gemini grounding with Google Search needs a Google Cloud project with billing turned on. If `llm.provider` is `gemini`, only `GEMINI_API_KEY` is needed. The old Google Custom Search JSON API is not used. It is closed to new customers and shuts down on January 1, 2027.

## Commands

```bash
python -m flowcode_agent run "Liquid Death"
python -m flowcode_agent run https://www.example.com
python -m flowcode_agent batch companies.csv
python -m flowcode_agent refresh
python -m flowcode_agent eval
.venv/bin/python -m pytest        # offline, no keys needed
```

Reports go to `outputs/{company}/report.md` and `report.json`. Batch writes `outputs/ranking.md` and `outputs/ranking.csv`. Eval writes `outputs/eval.md`. All settings live in `config.yaml`.

## How it works

| Step | File | What it does | Model |
|---|---|---|---|
| 1. Resolve | `steps/resolve.py` | Searches for the official site and has the AI return the name, domain, industry, brands, and a confidence. If confidence is low, it stops and prints the candidates. Checks `existing_customers.yaml`, ignoring case and punctuation. | cheap |
| 2. Gather | `steps/gather.py` | Reads the newsroom (found by search plus `/news`, `/newsroom`, `/press`, `/press-releases`), then runs the 11 fixed queries. Brand queries run too when results are thin. Fetches each page, extracts it with trafilatura, and dedupes by URL hash and text hash. | none |
| 3. Moments | `steps/moments.py` | Sends evidence in batches. The AI lists moments with word for word quotes tied to evidence ids. Duplicates are merged. | cheap |
| 4. Match | `steps/match.py` | Picks a play from `playbook.yaml` for each moment and writes the fit and the pitch. `similar_story` is copied from the play in code, not written by the AI. | main |
| 5. Verify | `steps/verify.py` | Code checks that every quote is on its saved page (normalized, fuzzy ≥ `quote_match_threshold`). Then the AI judges `yes`, `partly` (claim rewritten), or `no` (dropped). | main |
| 6. Score | `steps/score.py` | The AI gives 1 to 5 ratings with reasons. Python computes every weighted score. | main |
| 7. Report | `steps/report.py` | Markdown and JSON. Quotes are cut to under 20 words. | none |

`pipeline.py` saves to SQLite after every step and records the stage reached in `runs.status`. If a run crashes, the next `run` for that company resumes from the last finished stage.

### Ground rules and how the code enforces them

- **No fact without a source.** Moments must carry `{evidence_id, exact_quote}`. Quotes that are not on the saved page are removed, and an opportunity with no verified quote left is dropped.
- **Search summaries are not evidence.** `GeminiSearchProvider` reads only `grounding_metadata.grounding_chunks` (URI and title) and ignores the answer text. It follows Google redirect links to the real URL, and we fetch every page ourselves.
- **Fresh only.** Pages older than `recency_days` are dropped unless they mention this year or next year together with ongoing or upcoming wording ("through", "until", "upcoming"...). Pages with unknown dates are kept, with `date_known = 0`.
- **flowcode.com is never evidence.** It is added to `blocked_domains` even if the config removes it, and it is checked both before and after redirects.
- **The AI judges, the code does the math.** Weights live in `config.yaml` and are checked to add up to 1 at startup.
- **Swappable providers.** `providers/search_base.py` and `providers/llm_base.py` define the interfaces, and `providers/__init__.py` picks the class by config name. To add Tavily, write `search_tavily.py` and add one branch there.
- **Structured AI output.** Every call asks for JSON matching the pydantic schema and validates it. If validation fails, it retries once with the error included (`BaseLLM.complete_json`). Token usage is logged for every call and saved to the `usage` table.

## Choices made

- **Models.** Claude `claude-sonnet-5-5` is the main model. The cheap model is `claude-haiku-4-5`, because the spec's `claude-haiku-5-5` does not exist. For grounded search I use `gemini-3.8-flash`, a stable model that supports Google Search grounding (checked on ai.google.dev, Nov 2026). `gemini-4-hydrogen` is the newer fast option and also supports grounding.
- **Refusal fallback.** Sonnet 5.5 calls send the server-side `fallbacks: "default"` option. If the model declines a request, Anthropic retries it on a fallback model inside the same call. Remove it in `llm_anthropic.py` if you don't want it.
- **Retries.** The Anthropic SDK retries 429, 5xx, and connection errors (`max_retries=5`). Gemini calls and page fetches use `retry.with_backoff`.
- **Search cache.** Results are cached by `(provider, query, day)`, so a query is never paid for twice on the same day.
- **Page text sent to the AI** is capped at `max_chars_per_page`. The full text is saved and used for quote checks.
- **Dropping moments.** Following the spec literally, a moment is dropped only when it has neither a physical touchpoint nor a fitting play.
- **Unverified opportunities stay in the database** with `verified = 0`, so eval can report the verification rate. They never reach scoring or the report.
- **`best_opportunity`** uses the best opportunity's exact score, which is already on the 1 to 5 scale. **`new_activity`** uses the newest known publish date among the strong opportunities' sources: 1 if there are no strong opportunities, 2 if no dates are known.
- **Gaps** map moment types to channels: TV ← `tv_ad`; social ← `digital_campaign`; email ← `loyalty_or_contest`; events ← `live_event`, `sponsorship`; packaging ← `packaging`, `product_launch`; retail ← `retail_expansion`.

## Scoring weights

Opportunity: timing 0.25, playbook_fit 0.25, reach 0.20, evidence_strength 0.15, openness 0.15.
Company: best_opportunity 0.40, strong_count 0.20, company_size 0.20, industry_fit 0.10, new_activity 0.10.

Timing and playbook fit carry the most weight because a sales rep can only act on something happening now that has a clear place for a code. Reach comes next because it decides deal size. Evidence strength and openness adjust the score at the edges. Tune all of these in `config.yaml` (see sales feedback below).

## Running at scale (thousands of companies)

No scheduler is built in. This is how the pieces would be used:

1. **Scheduled refresh.** A cron job or cloud scheduler runs `refresh` nightly over a slice of the account list. Refresh only re-gathers. Steps 3 to 7 (the expensive AI part) run only when new URL or text hashes appear, so a quiet company costs about 12 cached-or-cheap searches and no tokens.
2. **Tiered cadence.** Check big accounts and companies with `upcoming` moments daily or weekly. Check the long tail monthly. Sort the queue by last company score, timing status, and days since the last run.
3. **Marketing calendar.** Before tentpoles (Super Bowl, March Madness, back to school, Halloween, the holidays), raise the cadence for the industries that buy against them and add seasonal queries (for example `{brand} Super Bowl ad`).
4. **Sales feedback.** Let reps mark opportunities as useful or not, and refit `opportunity_weights` and `company_weights` from that signal. Weights are config only, so a tuning change is a one-line edit.
5. **Cost controls.** The `usage` table shows search requests and tokens per company per run. Use it to set budgets per tier. Concurrency is `batch.concurrency`, and each worker has its own SQLite connection (WAL mode). For very large fleets, move to Postgres and a work queue. The step functions don't change.

## Tests

`tests/` uses a fake search provider, saved HTML pages in `tests/fixtures/`, and a scripted fake LLM. A fixture blocks all socket connections, so tests never touch the network. They cover: config validation, customer matching, gather (dedupe, blocking, recency, the page cap, the search cache), quote matching, the scoring math, the JSON retry, a full pipeline run, refresh finding nothing new twice in a row, resuming after a crash, eval numbers, and batch ranking.

## Web app (React)

A React frontend (in `web/`) styled after the Flowcode app, backed by a small JSON API (`api.py`).

```
cd web && npm install && npm run build      # build once
.venv/bin/uvicorn api:app --port 8600       # serves the API and the built app
# open http://localhost:8600
```

For live-reload development, run the API as above and `cd web && npm run dev` (http://localhost:5173 proxies `/api` to port 8600).

Pages: Home (start research, live run log), Reports (ranked companies; click one for its scored opportunities with sources) and Manage (providers, weights, playbook).

## Storage: SQLite or PostgreSQL

All data the app shows lives in one database: companies, runs, saved pages, moments, opportunities, scores,
the search cache, reports, the per-call cost log and the runs started from the web app. Pick the engine in
`config.yaml`:

```yaml
storage:
  backend: postgres      # or sqlite (the local file in paths.db)
  url_env: DATABASE_URL  # environment variable with the postgres:// connection string
```

Report files are still written to `outputs/` as a convenience, but the app reads reports from the database.

To move existing local data into Postgres (the target must be empty):

```
.venv/bin/python -m flowcode_agent migrate             # copies paths.db, plus old report files and logs/api_calls.jsonl
```

Tests run on SQLite by default. To run them against Postgres, point this at an empty throwaway database:

```
FLOWCODE_TEST_DATABASE_URL=postgresql://localhost/flowcode_test .venv/bin/python -m pytest
```

## Deploying (Render, free plan)

The repo has a `Dockerfile` (builds the React app, then runs the API) and a `render.yaml` Blueprint.

1. In Supabase, click **Connect** and copy the **Session pooler** string (port 5432), then add `?sslmode=require`.
   Do not use the Direct connection (`db.<ref>.supabase.co`): it is IPv6-only and Render connects over IPv4.
2. In Render: **New > Blueprint**, pick this GitHub repo. It creates one free web service from `render.yaml`.
3. Fill in the environment variables it asks for:
   - `DATABASE_URL`: the Session pooler string.
   - `ANTHROPIC_API_KEY`: optional. Without it the deployed app shows reports, usage and PDFs but cannot run new
     research, so it can never spend API credits. Research can still be run locally in Claude Code mode, and it
     shows up in the deployed app because both use the same database.
   - `FLOWCODE_MODE=api` is already set; Claude Code mode needs the `claude` command logged in locally.
4. Deploy. Render checks `/api/health` to know the service is up.

On the free plan the service sleeps after 15 minutes without visits; the next visit wakes it in about a minute.
A free uptime monitor pinging `/api/health` every 10 to 14 minutes keeps it awake. Nothing is stored on the
server itself, so sleeping, restarts and redeploys lose no data. A research run cut off by a restart is marked
"interrupted"; running it again resumes from the last finished step.

To test the image locally: `docker build -t flowcode-research .` then
`docker run -p 8000:8000 -e DATABASE_URL=... flowcode-research`.
