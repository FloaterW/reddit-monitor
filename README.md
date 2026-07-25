# Reddit Digest

A Reddit monitoring pipeline that scrapes subreddits, filters comments by keyword, summarizes them with an LLM, and emails you a daily digest.

Point it at any set of subreddits and keywords and it handles scraping, deduplication, summarization, and delivery.

## What It Does

1. **Scrapes** recent posts and comments from configured subreddits (no API key needed)
2. **Filters** comments by keyword using regex word-boundary matching
3. **Deduplicates** across multiple comment sort orders (top + new) per post
4. **Summarizes** matched comments into a digest organized by topic, with links back to each original comment
5. **Evaluates** digest quality — verifies citations, dollar amounts, numeric claims, and completeness against source data
6. **Emails** a styled HTML digest on a daily schedule
7. **Stores** run history in SQLite for querying past digests and tracking trends
8. **Saves** raw scraped data (JSON) and the final digest (Markdown)

## Example Use Cases

**Credit card deals** — use the built-in `churning` monitor profile:

```bash
python daily_digest.py --monitor churning --db data/monitor.db
```

**Job market monitoring** — use the built-in `job-market` profile:

```bash
python daily_digest.py --monitor job-market --db data/monitor.db
```

**Custom one-off run** — pass subreddits and keywords directly:

```bash
python daily_digest.py --subreddits technology,programming --keywords "layoff,acquisition,open source" --time week
```

A sample digest output is included in [`example_digest.md`](example_digest.md).

## Architecture

```
                           ┌──────────────────┐
                           │ monitor_config.py│
                           │ (JSON profiles)  │
                           └────────┬─────────┘
                                    │
┌─────────────────────┐     ┌───────▼──────────────┐     ┌──────────────────┐
│  reddit_scraper.py  │────>│  daily_digest.py     │────>│  NLP Summarizer  │
│  (HTTP + HTML parse) │     │  (orchestration)     │     │  (themed digest) │
└─────────────────────┘     └──────────┬───────────┘     └────────┬─────────┘
                                       │                           │
                     ┌─────────────────┼──────────────┐            │
                     │                 │              │            │
            ┌────────▼──────┐  ┌───────▼───────┐  ┌──▼────────────▼────┐
            │  Gmail SMTP   │  │  storage.py   │  │  digest_*.md/json  │
            │  (HTML email) │  │  (SQLite DB)  │  │  (file output)     │
            └───────────────┘  └───────────────┘  └──────────┬─────────┘
                                                             │
                                                  ┌──────────▼─────────┐
                                                  │ evaluate_digest.py │
                                                  │ (quality checks)   │
                                                  └────────────────────┘
```

**`reddit_scraper.py`** - Standalone scraper. Parses old.reddit.com HTML directly, no API key needed. Supports search, subreddit posts, single-post comments, and deep comment search. Handles rate limiting with retry and backoff.

**`daily_digest.py`** - Orchestrator. Fetches comments across configured subreddits, filters by keyword, pipes matches through an LLM summarization step, converts the output to a styled HTML email, and sends it via Gmail SMTP. Supports monitor profiles and SQLite history.

**`monitor_config.py`** - Loads JSON monitor profiles from `config/monitors/`. Validates required fields, applies defaults, and supports the CLI > config > default priority chain.

**`storage.py`** - SQLite storage for run history. Stores metadata, matched comments, keyword match counts, and generated digests with WAL mode for concurrent access.

**`evaluate_digest.py`** - Standalone digest quality checker. Verifies citation coverage, dollar amounts, numeric claims, and completeness against source data.

**`run_digest.bat`** - Windows Task Scheduler wrapper. Runs the digest with timestamped output filenames and logs everything to `digest_run.log`. If the run exits non-zero, it calls `notify_failure.py` so the failure reaches your inbox instead of sitting silently in the log.

**`notify_failure.py`** - Emails a failure alert with the tail of `digest_run.log`, the exit code, and recovery steps. Never changes the run's exit code, even if the alert itself cannot be sent.

## Prerequisites

- **Python 3.10+**
- **`requests`** and **`markdown`** libraries
- **An LLM CLI tool** for summarization (see Summarization Engine below)

## Quick Start

```bash
# Clone and install
git clone https://github.com/FloaterW/reddit-monitor.git
cd reddit-monitor
pip install -r requirements.txt

# Copy and edit .env (optional — see .env.example)
cp .env.example .env

# Run with default config (credit card / churning keywords)
python daily_digest.py

# Run with custom subreddits and keywords
python daily_digest.py --subreddits python,django --keywords "deployment,database,migration,security"

# Run with options
python daily_digest.py --posts 15 --time week --save my_digest.md --save-raw raw_data.json
```

## Configuration

### Monitor Profiles

Instead of passing subreddits and keywords every time, define a JSON config in `config/monitors/`:

```bash
# List available profiles
python daily_digest.py --list-monitors

# Run with a profile
python daily_digest.py --monitor churning

# CLI args override profile values
python daily_digest.py --monitor churning --posts 20 --time week
```

A profile is a JSON file with subreddits, keywords, title filters, and digest metadata. See `config/monitors/churning.json` for the full format. Create your own by adding a `.json` file to `config/monitors/`.

**Settings priority:** CLI args > monitor config > code defaults.

### CLI Arguments

```
python daily_digest.py [OPTIONS]

  --monitor NAME     Load a monitor profile from config/monitors/
  --posts N          Posts to scan per subreddit (default: 10)
  --time WINDOW      hour | day | week | month | year | all (default: day)
  --save FILE        Save digest to a specific markdown file
  --save-raw FILE    Also save raw scraped comments to JSON
  --from-json FILE   Resume from saved raw JSON instead of re-scraping
  --subreddits LIST  Override subreddits (comma-separated)
  --keywords LIST    Override keywords (comma-separated)
  --db PATH          Save run history to a SQLite database
  --no-db            Skip database storage
  --history [N]      Show recent runs from the database (default: 10)
  --list-monitors    List available monitor profiles and exit
```

## Summarization Engine

The digest uses an LLM to summarize scraped comments into a themed digest. It shells out to a CLI tool (default: `claude`) as a subprocess. The CLI must accept the pattern `<command> -p --model <model>`, reading the prompt from stdin and writing the summary to stdout.

**Configuration** via environment variables or `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `DIGEST_LLM_COMMAND` | `claude` | CLI executable for summarization |
| `DIGEST_LLM_MODEL` | `claude-sonnet-4-6` | Model name passed via `--model` |
| `DIGEST_LLM_TIMEOUT` | `1200` | Seconds to wait for summarization |

Any CLI that accepts `-p --model <name>` with stdin/stdout works as a drop-in replacement. If the CLI is missing, the pipeline gives a clear error instead of a traceback.

The prompt tells the summarizer to:
- Organize by theme, not by subreddit or keyword
- Lead with time-sensitive items
- Attribute every claim with a clickable `[u/username](permalink)` link
- Flag single data points vs. corroborated ones

Edit `SUMMARY_PROMPT` in `daily_digest.py` to match your use case.

## Email Setup

The digest is emailed automatically after each run. To enable:

1. **Generate a Gmail App Password** at https://myaccount.google.com/apppasswords
2. **Save the password** to a file named `.gmail_app_password` in the project directory
3. Set `DIGEST_EMAIL_TO` and `DIGEST_EMAIL_FROM` in your `.env` file (see `.env.example`)

If `.gmail_app_password` doesn't exist or is empty, the email step is silently skipped and the digest is still saved to disk.

**Security:** The `.gmail_app_password` and `.env` files are excluded from version control via `.gitignore`.

## Email Rendering

The markdown digest is converted to a styled HTML email that renders across Gmail, Outlook, and Apple Mail:

- **Table-based layout** with a dark gradient header, white card body, and footer
- **Inline CSS** - email clients strip `<style>` tags, so styles are applied directly to elements
- **Markdown preprocessing** inserts blank lines before list blocks so the parser generates correct `<ul>`/`<ol>` tags
- **Multipart MIME** - sends both plain text and HTML so the recipient's client picks the best format

## Run History (SQLite)

Pass `--db` to store every run's metadata, matched comments, keyword counts, and generated digest in a SQLite database:

```bash
# Run and save to database
python daily_digest.py --monitor churning --db data/monitor.db

# View recent runs
python daily_digest.py --history --db data/monitor.db

# Filter history by monitor
python daily_digest.py --history --monitor churning --db data/monitor.db
```

The database uses WAL mode for safe concurrent reads (e.g., querying history while a run is in progress).

## Digest Quality Evaluation

`evaluate_digest.py` checks a generated digest against its source comments to catch LLM hallucinations and omissions:

```bash
# Evaluate a digest
python evaluate_digest.py digest_20260628_1830.md digest_20260628_1830.json

# JSON output for programmatic use
python evaluate_digest.py digest.md raw.json --json
```

**Checks performed:**

| Check | What it catches |
|-------|-----------------|
| Citation coverage | Authors whose comments were used but not attributed |
| Dollar amounts | Dollar figures in the digest not found in source comments |
| Numeric claims | Percentages, multipliers, and point/mile amounts the LLM invented |
| Completeness | High-scoring comments that the digest ignored entirely |

## Automated Daily Scheduling (Windows)

Set up a Windows Task Scheduler task to run the digest on a daily schedule:

```powershell
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument '/c "path\to\run_digest.bat"'

$trigger = New-ScheduledTaskTrigger -Daily -At "6:30PM"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "RedditDailyDigest" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Daily Reddit digest"
```

**To test manually:**
```powershell
Start-ScheduledTask -TaskName "RedditDailyDigest"
```

Logs are written to `digest_run.log` in the project directory.

## Standalone Scraper Usage

`reddit_scraper.py` also works on its own for ad-hoc scraping:

```bash
# Search all of Reddit
python reddit_scraper.py search "machine learning frameworks"

# Search within a subreddit
python reddit_scraper.py search "deployment strategies" --subreddit devops --sort top --time week

# Get subreddit posts
python reddit_scraper.py posts python --sort new --limit 20

# Get comments from a specific post
python reddit_scraper.py comments https://www.reddit.com/r/python/comments/xyz/ --sort top

# Deep search: scan comments inside posts for a keyword
python reddit_scraper.py deep-search "FastAPI" --subreddit python,webdev --posts 10
```

## How the Scraper Works

The scraper parses old.reddit.com HTML directly. No API key, OAuth, or PRAW needed.

- **Post parsing** - extracts `data-*` attributes from `<div>` elements with `data-type="link"` (score, author, timestamp, permalink)
- **Comment parsing** - two-pass: first pass uses `html.parser.HTMLParser` to walk the DOM and track depth via `<div>` nesting; second pass uses regex to pull body text, score, and timestamps
- **Rate limiting** - 1s between comment fetches, 1.5s between posts, 2s between subreddits, plus retry with backoff on HTTP 429
- **Deduplication** - uses comment ID as the primary key, falling back to a composite hash of author + body + timestamp + post permalink when IDs are missing
- **Graceful degradation** - network errors and 4xx/5xx responses return `None` instead of crashing, so one failed request doesn't kill the run

## Output Files

Each run produces:

- **`digest_YYYYMMDD_HHMM.md`** - the digest in Markdown
- **`digest_YYYYMMDD_HHMM.json`** - raw scraped comments with metadata (when `--save-raw` is used)
- **`digest_run.log`** - append-only log of all scheduled runs (stdout + stderr)

## Testing

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
python -m pytest -q

# Lint
python -m ruff check .
```

115 tests covering HTML parsing, keyword matching, dedup logic, time-window filtering, markdown email preprocessing, config loading/validation, SQLite storage, and digest quality evaluation. All tests use static fixtures with no network calls.

## Project Structure

```
reddit-digest/
├── reddit_scraper.py           # Standalone Reddit scraper (no API key needed)
├── daily_digest.py             # Digest orchestrator (scrape → summarize → email)
├── evaluate_digest.py          # Digest quality evaluation (citations, facts, completeness)
├── monitor_config.py           # Monitor profile loader (JSON configs)
├── storage.py                  # SQLite run history storage
├── config/monitors/            # Monitor profiles
│   ├── churning.json           # Credit card churning monitor
│   └── job-market.json         # CS job market monitor
├── run_digest.bat              # Windows Task Scheduler wrapper
├── notify_failure.py           # Emails an alert when a scheduled run fails
├── requirements.txt            # Python dependencies
├── requirements-dev.txt        # Dev dependencies (pytest, ruff)
├── pyproject.toml              # Project config (pytest, ruff settings)
├── example_digest.md           # Sample digest output
├── .env.example                # Environment variable template
├── .github/workflows/ci.yml    # GitHub Actions CI (lint + test)
├── .gitignore                  # Excludes credentials, outputs, caches
├── tests/                      # pytest test suite (115 tests)
└── README.md                   # This file
```

## Design Tradeoffs / Limitations

**Why HTML scraping instead of the Reddit API?** The project parses `old.reddit.com` HTML directly with regex and `html.parser` to avoid OAuth credentials and API key management. The tradeoff is **markup fragility**: if Reddit changes their HTML, the parsers need updating. A future version could switch to PRAW or the official API.

**Rate limiting.** The scraper adds 1-2 second delays between requests and handles HTTP 429 with backoff. Heavy usage (many subreddits, high `--posts` counts) may still trigger Reddit's rate limiter, which slows the run but doesn't crash it.

**Keyword false positives.** Short keywords like "ink" or "boa" use word-boundary matching (`\b`) to avoid substring hits (e.g., "thinking"), but edge cases remain. The LLM summarizer usually filters these out, but the raw JSON may contain false matches.

**Email delivery.** Gmail-only via SMTP with app passwords. Other providers would need changes to the SMTP host/port config.

**Scheduling.** `run_digest.bat` is Windows-specific. On macOS/Linux, use `cron` instead. The machine must be on at the scheduled time (though `StartWhenAvailable` catches up on wake).
