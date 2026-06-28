# Reddit Digest

A Reddit monitoring pipeline that scrapes subreddits, filters comments by keyword, summarizes them with an LLM, and emails you a daily digest.

Point it at any set of subreddits and keywords and it handles scraping, deduplication, summarization, and delivery.

## What It Does

1. **Scrapes** recent posts and comments from configured subreddits (no API key needed)
2. **Filters** comments by keyword using regex word-boundary matching
3. **Deduplicates** across multiple comment sort orders (top + new) per post
4. **Summarizes** matched comments into a digest organized by topic, with links back to each original comment
5. **Emails** a styled HTML digest on a daily schedule
6. **Saves** raw scraped data (JSON) and the final digest (Markdown)

## Example Use Cases

**Credit card deals** - monitor r/churning, r/CreditCards for new offers and data points:

```bash
python daily_digest.py --subreddits churning,CreditCards,awardtravel --keywords "chase,amex,bonus,SUB,retention"
```

**Tech industry news** - track r/technology, r/programming for developments:

```bash
python daily_digest.py --subreddits technology,programming --keywords "layoff,acquisition,open source,funding,launch" --time week
```

**Job market monitoring** - watch hiring-related subreddits:

```bash
python daily_digest.py --subreddits cscareerquestions,experienceddevs --keywords "hiring,freeze,remote,return to office,compensation"
```

A sample digest output is included in [`example_digest.md`](example_digest.md).

## Architecture

```
┌─────────────────────┐     ┌──────────────────────┐     ┌──────────────────┐
│  reddit_scraper.py  │────>│  daily_digest.py     │────>│  NLP Summarizer  │
│  (HTTP + HTML parse) │     │  (orchestration)     │     │  (themed digest) │
└─────────────────────┘     └──────────┬───────────┘     └────────┬─────────┘
                                       │                           │
                              ┌────────▼──────────┐      ┌────────▼────────┐
                              │  Gmail SMTP       │      │  digest_*.md    │
                              │  (HTML email)     │      │  digest_*.json  │
                              └───────────────────┘      └─────────────────┘
```

**`reddit_scraper.py`** - Standalone scraper. Parses old.reddit.com HTML directly, no API key needed. Supports search, subreddit posts, single-post comments, and deep comment search. Handles rate limiting with retry and backoff.

**`daily_digest.py`** - Orchestrator. Fetches comments across configured subreddits, filters by keyword, pipes matches through an LLM summarization step, converts the output to a styled HTML email, and sends it via Gmail SMTP.

**`run_digest.bat`** - Windows Task Scheduler wrapper. Runs the digest with timestamped output filenames and logs everything to `digest_run.log`.

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

All defaults are at the top of `daily_digest.py` and can be overridden via CLI arguments:

| Setting | Default | Description |
|---------|---------|-------------|
| `SUBREDDITS` | *(your list)* | Subreddits to monitor |
| `KEYWORDS` | *(your list)* | Terms to match in comments (word-boundary regex) |
| `POSTS_PER_SUB` | 10 | Posts to scan per subreddit |
| `POST_SORT` | "new" | How to sort posts when fetching |
| `TIME_FILTER` | "day" | Time window for posts |
| `POST_TITLE_FILTERS` | regex dict | Optional per-subreddit title filters (e.g., only scrape weekly megathreads) |

### CLI Arguments

```
python daily_digest.py [OPTIONS]

  --posts N          Posts to scan per subreddit (default: 10)
  --time WINDOW      hour | day | week | month | year | all (default: day)
  --save FILE        Save digest to a specific markdown file
  --save-raw FILE    Also save raw scraped comments to JSON
  --subreddits LIST  Override subreddits (comma-separated)
  --keywords LIST    Override keywords (comma-separated)
```

## Summarization Engine

The digest uses an LLM to summarize scraped comments into a themed digest. It shells out to a CLI tool (default: `claude`) as a subprocess. The CLI must accept the pattern `<command> -p --model <model>`, reading the prompt from stdin and writing the summary to stdout.

**Configuration** via environment variables or `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `DIGEST_LLM_COMMAND` | `claude` | CLI executable for summarization |
| `DIGEST_LLM_MODEL` | `claude-sonnet-4-6` | Model name passed via `--model` |

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

Tests cover HTML parsing, keyword matching, dedup logic, time-window filtering, and markdown email preprocessing. All tests use static fixtures with no network calls.

## Project Structure

```
reddit-digest/
├── reddit_scraper.py           # Standalone Reddit scraper (no API key needed)
├── daily_digest.py             # Digest orchestrator (scrape → summarize → email)
├── run_digest.bat              # Windows Task Scheduler wrapper
├── requirements.txt            # Python dependencies
├── requirements-dev.txt        # Dev dependencies (pytest, ruff)
├── pyproject.toml              # Project config (pytest, ruff settings)
├── example_digest.md           # Sample digest output
├── .env.example                # Environment variable template
├── .github/workflows/ci.yml    # GitHub Actions CI (lint + test)
├── .gitignore                  # Excludes credentials, outputs, caches
├── tests/                      # pytest test suite
└── README.md                   # This file
```

## Design Tradeoffs / Limitations

**Why HTML scraping instead of the Reddit API?** The project parses `old.reddit.com` HTML directly with regex and `html.parser` to avoid OAuth credentials and API key management. The tradeoff is **markup fragility**: if Reddit changes their HTML, the parsers need updating. A future version could switch to PRAW or the official API.

**Rate limiting.** The scraper adds 1-2 second delays between requests and handles HTTP 429 with backoff. Heavy usage (many subreddits, high `--posts` counts) may still trigger Reddit's rate limiter, which slows the run but doesn't crash it.

**Keyword false positives.** Short keywords like "ink" or "boa" use word-boundary matching (`\b`) to avoid substring hits (e.g., "thinking"), but edge cases remain. The LLM summarizer usually filters these out, but the raw JSON may contain false matches.

**Email delivery.** Gmail-only via SMTP with app passwords. Other providers would need changes to the SMTP host/port config.

**Scheduling.** `run_digest.bat` is Windows-specific. On macOS/Linux, use `cron` instead. The machine must be on at the scheduled time (though `StartWhenAvailable` catches up on wake).
