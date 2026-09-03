# Reddit Digest

A Reddit monitoring pipeline that scrapes subreddits, filters comments by keyword, summarizes them with an LLM, and emails you a daily digest.

Point it at any set of subreddits and keywords and it handles scraping, deduplication, summarization, and delivery.

## What It Does

1. **Scrapes** recent posts and comments from configured subreddits (no API key needed)
2. **Filters** comments by keyword using regex word-boundary matching
3. **Deduplicates** across multiple comment sort orders (top + new) per post
4. **Summarizes** matched comments into a digest organized by topic, with links back to each original comment
5. **Evaluates** every digest — verifies citation targets, line-level numeric claims, and completeness against source data
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

**`daily_digest.py`** - Orchestrator. Fetches comments, filters by keyword, summarizes in isolated batches, evaluates and sanitizes the result, writes outputs atomically, and then sends it via Gmail SMTP. It records the complete run, quality, and delivery outcome.

**`monitor_config.py`** - Loads JSON monitor profiles from `config/monitors/`. Validates required fields, applies defaults, and supports the CLI > config > default priority chain.

**`storage.py`** - SQLite storage for run history. Stores metadata, matched comments, keyword match counts, and generated digests with WAL mode for concurrent access.

**`evaluate_digest.py`** - Standalone and integrated quality checker. It verifies citation coverage and integrity, global numeric consistency, line-level claim grounding, and high-signal comment coverage.

**`run_digest.bat`** - Windows Task Scheduler wrapper. Runs the digest with timestamped output filenames and logs everything to `digest_run.log`. If the run exits non-zero, it calls `notify_failure.py` so the failure reaches your inbox instead of sitting silently in the log.

**`check_digest_ran.py`** - Independent watchdog. It validates the dated run-status record and the referenced nonempty digest, so a failed email or incomplete write cannot masquerade as success.

**`notify_failure.py`** - Emails a failure alert with a bounded tail of `digest_run.log`, the exit code, and recovery steps. It reports its own delivery failure with a nonzero exit code.

## Prerequisites

- **Python 3.10+**
- Runtime dependencies from `requirements.txt`
- **An LLM CLI tool** for summarization (see Summarization Engine below)

## Quick Start

```bash
# Clone, create an isolated environment, and install
git clone https://github.com/FloaterW/reddit-monitor.git
cd reddit-monitor
python -m venv .venv

# PowerShell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-lock.txt

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
  --no-email         Disable email delivery for this run
  --quality MODE     off | warn | strict (default: warn)
  --evaluation-report FILE
                      Override the JSON quality-report path
  --status-file FILE Atomic status record used by the watchdog
  --quiet-summary    Keep the full generated digest out of logs
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
| `DIGEST_LLM_MAX_INPUT_CHARS` | `80000` | Approximate source size per isolated batch |
| `DIGEST_PROMPT_BODY_LIMIT` | `4000` | Per-comment prompt cap; stored source remains complete |

Any CLI that accepts `-p --model <name>` with stdin/stdout can be used. The child process runs in a temporary directory with a minimal environment. When the Claude CLI is selected, project/user settings and tools are explicitly disabled. Reddit text is untrusted input, and generated Markdown is stripped of raw HTML, images, and non-Reddit links before storage or email rendering.

The prompt tells the summarizer to:
- Organize by theme, not by subreddit or keyword
- Lead with time-sensitive items
- Attribute every claim with a clickable `[u/username](permalink)` link
- Flag single data points vs. corroborated ones

Edit `SUMMARY_PROMPT` in `daily_digest.py` to match your use case.

## Email Setup

The digest is emailed automatically after each run. To enable:

1. **Generate a Gmail App Password** at https://myaccount.google.com/apppasswords
2. **Store it outside the repository and synced folders.** On Windows the default location is `%APPDATA%\reddit-digest\gmail_app_password`. You can instead set `DIGEST_GMAIL_PASSWORD_FILE` to another external path or inject `GMAIL_APP_PASSWORD` into the process environment.
3. Set `DIGEST_EMAIL_TO` and `DIGEST_EMAIL_FROM` in `.env` (see `.env.example`). Do not place the password in `.env`.

If no password is configured, email is explicitly recorded as skipped and the digest is still saved. An attempted SMTP delivery that fails marks the whole run failed and returns a nonzero exit code.

Project-local credential files and `GMAIL_APP_PASSWORD` entries in `.env` are deliberately ignored. This prevents an ignored file in a cloned or synced working tree from becoming the active secret source.

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

Quality evaluation runs automatically after summarization. `warn` mode records failures and still delivers the digest; `strict` mode blocks email and exits nonzero. `off` disables evaluation. The standalone command remains available:

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
| Citation integrity | Links that do not resolve to the cited author's source comment |
| Dollar amounts | Dollar figures in the digest not found in source comments |
| Numeric claims | Percentages, multipliers, and point/mile amounts the LLM invented |
| Claim grounding | Numeric claims not present in a source cited on the same line |
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

The batch wrapper prefers `.venv`, rotates `digest_run.log` at 5 MiB, keeps full digest text out of the log, and writes `data/last_run_status.json`. Schedule `run_watchdog.bat` separately after the expected completion time; it alerts if the run did not finish successfully, the email failed, or the digest is missing/empty.

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

The scraper first tries old.reddit.com HTML and falls back to Reddit's RSS feeds when login is required. No API key, OAuth, or PRAW is needed.

- **Post parsing** - extracts `data-*` attributes from `<div>` elements with `data-type="link"` (score, author, timestamp, permalink)
- **Comment parsing** - a structured `HTMLParser` walk extracts complete bodies, score, timestamps, nesting depth, and parent IDs
- **Rate limiting** - 1s between comment fetches, 1.5s between posts, 2s between subreddits, plus retry with backoff on HTTP 429
- **Deduplication** - uses comment ID as the primary key, falling back to a composite hash of author + body + timestamp + post permalink when IDs are missing
- **RSS fallback** - honors post count, sort, time window, and title filters before selecting comments
- **Graceful degradation** - retries transient failures and falls back from HTML to RSS; a run with no usable matches is recorded as failed rather than silently succeeding

## Output Files

Each run produces:

- **`digest_YYYYMMDD_HHMM.md`** - the digest in Markdown
- **`digest_YYYYMMDD_HHMM.json`** - raw scraped comments with metadata (when `--save-raw` is used)
- **`digest_YYYYMMDD_HHMM.evaluation.json`** - deterministic quality results
- **`data/last_run_status.json`** - atomic machine-readable run, quality, and email outcome
- **`digest_run.log`** - scheduled-run output, rotated to `digest_run.previous.log` at 5 MiB

## Testing

```bash
# Install dev dependencies
python -m pip install -r requirements-dev-lock.txt

# Run tests
python -m pytest -q

# Coverage gate used by CI
python -m pytest --cov=. --cov-fail-under=60

# Lint
python -m ruff check .
```

Tests cover parsing, matching, RSS selection, retries, LLM isolation, email outcomes, atomic run lifecycle, watchdog behavior, configuration, transactional SQLite storage, and digest evaluation. Tests use static fixtures and mocks rather than live network calls. CI runs on Linux and Windows with Python 3.10 and 3.12, audits dependencies, and enforces the coverage floor. CodeQL scans the default branch and pull requests.

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
├── check_digest_ran.py         # Independent successful-run watchdog
├── run_watchdog.bat            # Windows wrapper for the watchdog
├── requirements.txt            # Python dependencies
├── requirements-dev.txt        # Dev dependencies (pytest, ruff)
├── requirements-lock.txt       # Reproducible resolved runtime versions
├── requirements-dev-lock.txt   # Reproducible resolved CI/dev versions
├── pyproject.toml              # Project config (pytest, ruff settings)
├── example_digest.md           # Sample digest output
├── .env.example                # Environment variable template
├── .github/workflows/          # Cross-platform CI and CodeQL scanning
├── .github/dependabot.yml      # Automated dependency update configuration
├── LICENSE                     # MIT license
├── .gitignore                  # Excludes credentials, outputs, caches
├── tests/                      # Offline pytest regression suite
└── README.md                   # This file
```

## Design Tradeoffs / Limitations

**Why HTML/RSS scraping instead of the Reddit API?** The project parses old.reddit.com HTML and uses RSS as a fallback to avoid OAuth credentials and API key management. The tradeoff is **markup/feed fragility and reduced RSS metadata**: Reddit changes may require parser updates, and RSS comments commonly report a score of zero. A future version could switch to PRAW or the official API.

**Rate limiting.** The scraper adds 1-2 second delays between requests and handles HTTP 429 with backoff. Heavy usage (many subreddits, high `--posts` counts) may still trigger Reddit's rate limiter, which slows the run but doesn't crash it.

**Keyword false positives.** Short keywords like "ink" or "boa" use word-boundary matching (`\b`) to avoid substring hits (e.g., "thinking"), but edge cases remain. The LLM summarizer usually filters these out, but the raw JSON may contain false matches.

**Email delivery.** Gmail-only via SMTP with app passwords. Other providers would need changes to the SMTP host/port config.

**Scheduling.** `run_digest.bat` is Windows-specific. On macOS/Linux, use `cron` instead. The machine must be on at the scheduled time (though `StartWhenAvailable` catches up on wake).
