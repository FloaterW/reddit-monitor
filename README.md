# Reddit Digest

A configurable Reddit monitoring pipeline that scrapes subreddits, filters comments by keyword, generates themed daily digests using NLP summarization, and delivers styled HTML emails on a schedule.

Point it at any set of subreddits and keywords — the pipeline handles scraping, deduplication, summarization, and delivery automatically.

## What It Does

1. **Scrapes** recent posts and comments from any configured subreddits (no Reddit API key needed)
2. **Filters** comments matching configurable keywords using regex word-boundary matching
3. **Deduplicates** across multiple comment sort orders (top + new) per post
4. **Summarizes** matched comments into a themed digest — organized by topic with clickable links back to each original Reddit comment
5. **Emails** a professionally styled HTML digest to your inbox on a daily schedule
6. **Saves** both the raw scraped data (JSON) and the final digest (Markdown)

## Example Use Cases

**Credit card deals** — monitor r/churning, r/CreditCards for keywords like "bonus", "retention", "SUB" to get a daily briefing on new offers and data points:

```bash
python daily_digest.py --subreddits churning,CreditCards,awardtravel --keywords "chase,amex,bonus,SUB,retention"
```

**Tech industry news** — track r/technology, r/programming for breaking developments:

```bash
python daily_digest.py --subreddits technology,programming --keywords "layoff,acquisition,open source,funding,launch" --time week
```

**Job market monitoring** — watch hiring-related subreddits for trends:

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

**`reddit_scraper.py`** — Standalone scraper module. Parses old.reddit.com HTML directly — no API key or OAuth credentials needed. Supports search, subreddit posts, single-post comments, and deep comment search across multiple subreddits. Handles rate limiting with retry and backoff.

**`daily_digest.py`** — Orchestrator. Fetches comments across configured subreddits, filters by keyword with word-boundary matching, pipes matched comments through an NLP summarization step, converts the markdown output to a styled HTML email with inline CSS, and sends it via Gmail SMTP.

**`run_digest.bat`** — Windows Task Scheduler wrapper. Runs the digest with timestamped output filenames and logs all output to `digest_run.log` for debugging.

## Prerequisites

- **Python 3.10+**
- **`requests`** and **`markdown`** libraries
- **An LLM CLI tool** for summarization (configured in `daily_digest.py` — see Summarization Engine below)

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run with default config (edit SUBREDDITS and KEYWORDS in daily_digest.py)
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

The digest pipeline uses an LLM to summarize scraped comments into a themed, actionable digest. The summarization step is handled via a CLI subprocess call in the `summarize()` function of `daily_digest.py`. The model and CLI command are configurable at the top of the file — swap in any LLM CLI that accepts piped text input and returns text output.

The prompt instructs the summarizer to:
- Organize by theme, not by subreddit or keyword
- Lead with time-sensitive items
- Attribute every claim with a clickable `[u/username](permalink)` link
- Flag single unconfirmed data points vs. corroborated ones
- Preserve all information without omission

The prompt itself is domain-agnostic in structure — edit `SUMMARY_PROMPT` to match your use case.

## Email Setup

The digest is emailed automatically after each run. To enable:

1. **Generate a Gmail App Password** at https://myaccount.google.com/apppasswords
2. **Save the password** to a file named `.gmail_app_password` in the project directory
3. Set `DIGEST_EMAIL_TO` and `DIGEST_EMAIL_FROM` in your `.env` file (see `.env.example`)

If `.gmail_app_password` doesn't exist or is empty, the email step is silently skipped and the digest is still saved to disk.

**Security:** The `.gmail_app_password` and `.env` files are excluded from version control via `.gitignore`.

## Email Rendering

The email pipeline converts the markdown digest to a styled HTML email that renders correctly across email clients (Gmail, Outlook, Apple Mail):

- **Table-based layout** with a dark gradient header, white card body, and subtle footer
- **Inline CSS injection** via regex — email clients strip `<style>` tags, so all styles are applied directly to HTML elements
- **Markdown preprocessing** inserts blank lines before list blocks to ensure proper `<ul>`/`<ol>` parsing
- **Multipart MIME** — sends both plain text and HTML versions so the recipient's client picks the best format

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

`reddit_scraper.py` works independently for ad-hoc Reddit scraping — no digest pipeline needed:

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

The scraper parses old.reddit.com HTML directly — no Reddit API key, OAuth, or PRAW dependency needed:

- **Post parsing** — Extracts `data-*` attributes from `<div>` elements with `data-type="link"` (score, author, timestamp, permalink)
- **Comment parsing** — Two-pass approach: first pass uses `html.parser.HTMLParser` to walk the DOM and track comment depth via `<div>` nesting; second pass uses regex to extract body text, score, and timestamps from the raw HTML
- **Rate limiting** — 1s between comment fetches, 1.5s between posts, 2s between subreddits, plus automatic retry with backoff on HTTP 429
- **Deduplication** — Uses `(author, body[:80])` tuples as dedup keys across sort orders
- **Graceful degradation** — Network errors, 403s, 404s, and 5xx responses return `None` instead of crashing, so a single failed request doesn't kill the entire run

## Output Files

Each run produces:

- **`digest_YYYYMMDD_HHMM.md`** — The themed digest in Markdown
- **`digest_YYYYMMDD_HHMM.json`** — Raw scraped comments with metadata (when `--save-raw` is used)
- **`digest_run.log`** — Append-only log of all scheduled runs (stdout + stderr)

## Project Structure

```
reddit-digest/
├── reddit_scraper.py       # Standalone Reddit scraper (no API key needed)
├── daily_digest.py         # Digest orchestrator (scrape → summarize → email)
├── run_digest.bat          # Windows Task Scheduler wrapper
├── requirements.txt        # Python dependencies
├── example_digest.md       # Sample digest output
├── .env.example            # Environment variable template
├── .gitignore              # Excludes credentials, outputs, caches
└── README.md               # This file
```

## Known Limitations

- Relies on HTML scraping of old.reddit.com — may break if Reddit changes their markup
- Short keywords (e.g., "ink", "boa") can produce false-positive matches; the summarizer filters most noise
- Windows Task Scheduler requires the machine to be on (though `StartWhenAvailable` catches up on wake)
- Email delivery is Gmail-only via SMTP; other providers would need minor code changes
