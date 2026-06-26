# Community Intelligence Roadmap

This project should be framed as more than a Reddit scraper. The stronger long-term idea is a configurable community intelligence system:

```text
source communities -> collect posts/comments -> normalize data -> filter signals -> dedupe -> summarize -> deliver digest
```

That pipeline is already present in the current app. The next steps should grow the project around that pipeline without turning it into a half-built platform.

## Current Positioning

Use this positioning in the README, resume, and interviews:

> A configurable community intelligence pipeline that monitors public discussion sources, detects high-signal conversations, ranks and deduplicates findings, summarizes them with an LLM, and delivers scheduled cited digests.

For the current version, be precise:

> A Python Reddit monitoring pipeline that scans selected communities for high-signal public discussions, filters and deduplicates relevant comments, summarizes them with an LLM, and delivers a scheduled email digest.

## Immediate Finish Line: Portfolio-Ready V1

Before expanding scope, finish the hardening pass currently in progress.

### Must Do Before Commit

1. Confirm `.env` is ignored and not tracked.
2. Confirm `.gmail_app_password` is ignored and not tracked.
3. Confirm `.claude/` is ignored or deleted locally and not tracked.
4. Confirm generated files are ignored:
   - `digest_*.md`
   - `digest_*.json`
   - `reddit_*.json`
   - `deep_*.json`
   - `*_results.json`
   - `*_comments.json`
   - `digest_run.log`
5. Fix dedup fallback so missing comment IDs do not collapse multiple comments from the same post.
6. Update README clone instructions to use:

```bash
git clone https://github.com/FloaterW/reddit-monitor.git
cd reddit-monitor
```

7. Make sure README LLM wording does not claim any arbitrary LLM CLI works unless the CLI supports the expected `-p --model` argument pattern.

### Final Verification Commands

Run these before committing:

```bash
python -m compileall -q .
python -m pytest -q
python -m ruff check .
python reddit_scraper.py --help
python daily_digest.py --help
git status --short
```

Expected result:

- Compile passes.
- Tests pass.
- Ruff passes.
- Both help commands work.
- No secrets or generated outputs are staged.
- Only source, tests, docs, config, and CI files are staged.

### Commit Message

Use:

```text
Polish Reddit monitor for portfolio release
```

## Expansion Strategy

The best expansion direction is to turn the app into a configurable signal-monitoring platform. Keep the core workflow the same, but allow users to define multiple monitors.

Example monitor config:

```yaml
name: credit-card-churning
subreddits:
  - churning
  - CreditCards
keywords:
  - chase
  - amex
  - bonus
  - 5/24
delivery:
  email: true
  schedule: daily
```

Future profiles could include:

- `credit-card-churning`
- `job-market-watch`
- `internship-opportunities`
- `tech-layoffs`
- `ai-tools-watch`
- `local-housing-market`
- `gaming-deals`
- `travel-awards`

This makes the app feel much larger without changing its soul.

## Recommended Roadmap

Build in this order:

1. Config profiles
2. SQLite history
3. Signal scoring and trend detection
4. Ask-your-digests search
5. Optional new source adapters

Each phase below includes exact implementation steps and acceptance criteria.

## Phase 1: Config Profiles

Goal: Replace hardcoded monitor settings with explicit config files while preserving the current default behavior.

### Why This Matters

Right now, the app is useful but hardcoded around churning. Config profiles make it feel like a reusable monitoring system instead of a one-off script.

### Files To Add Or Change

- Add `config/monitors/churning.yml`
- Add `config/monitors/job-market.yml`
- Add `config/monitors/tech-news.yml`
- Add config loading helpers, either in:
  - `daily_digest.py` for a small implementation, or
  - a new `monitor_config.py` if the logic grows
- Update `README.md`
- Add tests for config loading

### Exact Tasks

1. Create a config directory:

```text
config/
  monitors/
    churning.yml
    job-market.yml
    tech-news.yml
```

2. Define the monitor schema:

```yaml
name: credit-card-churning
description: Tracks credit-card rewards, churning data points, bank bonuses, and award travel.
subreddits:
  - churning
  - CreditCards
  - awardtravel
  - churningcanada
keywords:
  - chase
  - amex
  - citi
  - capital one
  - sapphire
  - ink
  - 5/24
post_sort: new
time_filter: day
posts_per_subreddit: 10
title_filters:
  churningcanada: "(?i)data\\s*point\\s*weekly|US\\s*churning\\s*discussion"
digest:
  title: Churning Digest
  audience: credit card churning and award travel enthusiasts
delivery:
  email: true
```

3. Add a CLI argument:

```bash
python daily_digest.py --monitor churning
```

4. Keep old CLI overrides working:

```bash
python daily_digest.py --monitor churning --posts 5 --time week
python daily_digest.py --subreddits python,django --keywords "deployment,database"
```

5. Define precedence:

```text
CLI arguments override monitor config.
Monitor config overrides code defaults.
Code defaults remain available as fallback.
```

6. Add tests:

- Loads a valid monitor file.
- Rejects missing required fields.
- CLI overrides config values.
- Existing default behavior still works when no monitor is passed.

### Acceptance Criteria

- Existing commands still work.
- `python daily_digest.py --monitor churning --help` or equivalent usage is documented.
- Tests pass without network access.
- README shows at least one monitor example.

## Phase 2: SQLite History

Goal: Store runs, comments, matches, and generated summaries in a local database.

### Why This Matters

Without history, every run is isolated. SQLite turns the project into a system that can answer questions over time.

### Files To Add Or Change

- Add `storage.py`
- Add `schema.sql` or inline schema creation
- Add `data/` to `.gitignore`
- Update `daily_digest.py` to optionally write to SQLite
- Add tests for database writes and reads

### Suggested Tables

```sql
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    time_filter TEXT NOT NULL,
    posts_per_subreddit INTEGER NOT NULL,
    raw_comment_count INTEGER NOT NULL,
    matched_comment_count INTEGER NOT NULL,
    digest_path TEXT,
    raw_json_path TEXT
);

CREATE TABLE comments (
    id TEXT PRIMARY KEY,
    run_id INTEGER NOT NULL,
    subreddit TEXT,
    post_title TEXT,
    post_permalink TEXT,
    author TEXT,
    score INTEGER,
    created TEXT,
    depth INTEGER,
    parent_id TEXT,
    body TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    comment_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id),
    FOREIGN KEY (comment_id) REFERENCES comments(id)
);

CREATE TABLE digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    markdown TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
```

### Exact Tasks

1. Add an env var:

```text
DIGEST_DB_PATH=data/reddit_monitor.db
```

2. Add CLI flags:

```bash
python daily_digest.py --db data/reddit_monitor.db
python daily_digest.py --no-db
```

3. Save each run:

- monitor name
- subreddits
- keywords
- raw comment count
- matched comment count
- output paths
- digest markdown

4. Add a simple inspect command later:

```bash
python daily_digest.py history --limit 10
```

Only add this if it fits cleanly. Do not overbuild.

### Acceptance Criteria

- Database file is created locally.
- Database file is ignored by Git.
- Tests use temporary database files.
- A run can be stored and read back.
- Existing file outputs still work.

## Phase 3: Signal Scoring

Goal: Rank comments and topics by importance instead of only filtering by keyword.

### Why This Matters

Filtering finds relevant comments. Scoring finds important comments.

### Scoring Inputs

Use a transparent scoring function:

```text
score =
  keyword_score
  + reddit_score_weight
  + recency_weight
  + thread_popularity_weight
  + repeated_topic_weight
  + author_repetition_weight
```

### Exact Tasks

1. Add `signal_score.py`.
2. Implement a pure function:

```python
def score_comment(comment: dict, keyword_hits: list[str], now: datetime) -> dict:
    ...
```

Return:

```python
{
    "score": 87,
    "reasons": [
        "matched 3 keywords",
        "posted within 2 hours",
        "comment has 25 Reddit points"
    ]
}
```

3. Add score data to matched comments:

```json
{
  "signal_score": 87,
  "signal_reasons": [...]
}
```

4. Sort matched comments by signal score before summarization.
5. Add top-signal section to raw JSON output.
6. Add tests for scoring.

### Suggested Weights

Start simple:

```text
+10 per keyword match
+0 to +20 based on Reddit score
+0 to +20 based on recency
+10 if post has high comment count
+15 if similar topic appears in multiple comments
```

Keep weights easy to explain in interviews.

### Acceptance Criteria

- Signal score is deterministic.
- Tests cover low, medium, and high signal comments.
- Digest prompt receives comments in ranked order.
- README explains scoring at a high level.

## Phase 4: Trend Detection

Goal: Detect when a topic is increasing compared to prior runs.

### Why This Matters

This is where the project starts to feel like an intelligence tool:

> Bilt mentions are up 240% versus yesterday.

### Exact Tasks

1. Require SQLite history from Phase 2.
2. Add `trends.py`.
3. Track keyword counts per run.
4. Compare current run to prior windows:

```text
current 24h vs previous 24h
current 7d vs previous 7d
```

5. Add trend output:

```json
{
  "keyword": "bilt",
  "current_count": 17,
  "previous_count": 5,
  "change_percent": 240,
  "label": "spiking"
}
```

6. Include trends in the summarizer prompt:

```text
TREND SIGNALS:
- bilt: 17 mentions, up 240% vs previous period
- chase ink: 9 mentions, up 80% vs previous period
```

### Acceptance Criteria

- Trend calculations are tested.
- Zero-division cases are handled.
- Small counts do not produce misleading huge spikes.
- Digest includes trend context when available.

## Phase 5: Confidence Labels

Goal: Teach the digest to distinguish rumor, single data point, repeated report, conflict, and confirmed source.

### Labels

Use these:

```text
single_data_point
repeated_report
conflicting_reports
confirmed_official
unknown
```

### Exact Tasks

1. Add a confidence classification step before summarization.
2. Use simple rules first:

```text
1 matching comment -> single_data_point
3+ similar comments -> repeated_report
mentions "official", "announced", or links to official source -> possible confirmed_official
opposing terms like "works" and "does not work" in same topic -> conflicting_reports
```

3. Add confidence labels to prompt entries.
4. Add prompt instruction:

```text
Preserve confidence labels. Do not present single data points as confirmed facts.
```

5. Add tests for classification.

### Acceptance Criteria

- Single comments are clearly marked as single data points.
- Repeated topics are identified.
- Contradictory examples are identified.
- Summary prompt includes confidence labels.

## Phase 6: Ask-Your-Digests Search

Goal: Let the user query past digests and matched comments.

### Example Questions

```text
What changed with Chase Ink offers this month?
What were the strongest Bilt data points this week?
Show me all negative Amex clawback reports.
What travel transfer bonuses were mentioned recently?
```

### Simple Version First

Start with keyword search over SQLite:

```bash
python digest_query.py "Chase Ink" --since 30d
```

Return:

- matching comments
- matching digests
- dates
- links
- source usernames

### Later Version

Add an LLM synthesis mode:

```bash
python digest_query.py "What changed with Chase Ink offers this month?" --summarize
```

### Files To Add

- `digest_query.py`
- `query.py`
- tests for search and date filtering

### Acceptance Criteria

- Query works offline.
- Query uses stored local data only.
- Results include citations/permalinks.
- Summarized mode is optional and fails gracefully if LLM CLI is missing.

## Phase 7: Multi-Channel Delivery

Goal: Add delivery outputs beyond Gmail while keeping email as the default.

### Channels To Consider

- Local Markdown archive
- RSS feed
- Discord webhook
- Slack webhook
- Telegram bot

### Recommended Order

1. Local Markdown archive
2. RSS feed
3. Discord webhook
4. Slack webhook

### Exact Tasks

1. Create `delivery/` package:

```text
delivery/
  __init__.py
  email.py
  markdown_archive.py
  rss.py
  discord.py
```

2. Define a common interface:

```python
class DeliveryChannel:
    def send(self, subject: str, body_md: str) -> bool:
        ...
```

3. Move existing Gmail logic into `delivery/email.py`.
4. Keep `send_email()` wrapper for backward compatibility.
5. Add config:

```yaml
delivery:
  email: true
  markdown_archive: true
  rss: false
  discord: false
```

### Acceptance Criteria

- Email behavior remains unchanged.
- Markdown archive writes a dated file.
- RSS feed generation is deterministic and tested.
- Webhook integrations are optional and skipped if not configured.

## Phase 8: Source Adapters

Goal: Support sources beyond Reddit without rewriting the pipeline.

### Why This Matters

Once the collector is abstracted, the project becomes a general intelligence pipeline.

### Potential Sources

- Reddit
- RSS feeds
- Hacker News
- GitHub releases/issues
- job boards
- official bank/travel promo pages

### Adapter Interface

```python
class SourceAdapter:
    name: str

    def collect(self, config: dict) -> list[dict]:
        ...
```

All adapters should output normalized records:

```json
{
  "source": "reddit",
  "community": "churning",
  "title": "Question Thread",
  "author": "username",
  "body": "comment text",
  "url": "https://...",
  "created": "2026-06-26 18:00 UTC",
  "score": 12,
  "metadata": {}
}
```

### Exact Tasks

1. Rename Reddit-specific collection logic internally as a Reddit adapter.
2. Create `sources/reddit.py`.
3. Add `sources/rss.py` for RSS feeds.
4. Keep existing CLI behavior intact.
5. Add tests for adapter normalization.

### Acceptance Criteria

- Reddit still works.
- RSS fixture can be parsed without network.
- Pipeline consumes normalized records, not Reddit-only comments.
- README explains adapter architecture.

## Phase 9: Digest Quality Evaluation

Goal: Add evaluation checks for AI-generated summaries.

### Why This Matters

This is a strong portfolio differentiator. It shows you understand that LLM features need quality control.

### Evaluations To Add

1. Citation coverage:
   - Every major claim should include a source link.
2. No unsupported claims:
   - Summary should not introduce card names, dates, or amounts absent from source comments.
3. Completeness:
   - Important source comments should be represented.
4. Confidence preservation:
   - Single data points should not be written as confirmed facts.
5. Contradiction handling:
   - Conflicting comments should be called out.

### Simple Non-LLM Checks

Start with deterministic checks:

- Count source comment links in summary.
- Check for missing usernames.
- Check that all dollar amounts in summary exist in source text.
- Check that all dates in summary exist in source text.

### Optional LLM Judge Later

Add an optional evaluator:

```bash
python evaluate_digest.py digest.md raw_comments.json
```

### Acceptance Criteria

- Evaluation runs offline for deterministic checks.
- Failing evaluation gives actionable messages.
- Evaluation is optional and does not block normal digest generation.

## What Not To Build Yet

Do not build these until the pipeline is mature:

- Full web dashboard
- Authentication
- User accounts
- Payments
- Multi-user SaaS hosting
- Large complex database schema
- Real-time streaming
- Browser extension
- Mobile app

These can distract from the strongest part of the project: the pipeline.

## Suggested Future README Tagline

Use something like:

```text
Reddit Monitor is a configurable community intelligence pipeline that tracks public discussion sources, detects high-signal conversations, summarizes them with an LLM, and delivers scheduled cited digests.
```

## Suggested Resume Bullets

Use one of these:

```text
Built a Python Reddit monitoring pipeline that scans configurable communities, deduplicates comments across sort orders, summarizes relevant discussions with an LLM, and sends scheduled HTML email digests with raw JSON/Markdown artifacts.
```

```text
Developed a configurable community intelligence pipeline with HTML collection, keyword filtering, deduplication, LLM summarization, email delivery, pytest coverage, CI, and secret-safe configuration.
```

```text
Designed an automated signal-monitoring tool that ranks public discussion data, preserves source citations, and generates daily AI-assisted digests for high-volume Reddit communities.
```

## Interview Talking Points

Be ready to explain:

1. Why you used `old.reddit.com` HTML instead of the API.
2. How rate limiting and graceful failure work.
3. Why deduplication matters across `top` and `new` comment sorts.
4. How you prevent secrets from entering Git.
5. How tests avoid live Reddit, Gmail, and LLM calls.
6. What you would change for production:
   - official API or licensed data source
   - stronger storage layer
   - monitoring and retries
   - background job scheduler
   - source adapters
   - evaluation for LLM summaries

## Best Next Prompt For Claude

After the current hardening pass is committed, use this prompt for Phase 1:

```text
Please implement Phase 1 from COMMUNITY_INTELLIGENCE_ROADMAP.md: config profiles.

Guardrails:
- Do not rewrite the scraper.
- Do not add a web UI.
- Do not change default behavior.
- Do not touch secrets or generated output files.
- Preserve existing CLI commands.

Required:
- Add monitor config files under config/monitors/.
- Add --monitor support to daily_digest.py.
- CLI args must override monitor config.
- Add tests for config loading and overrides.
- Update README with concise monitor usage.
- Run compileall, pytest, ruff, and CLI help checks.
```

## Final Recommendation

Finish portfolio-ready V1 first. Then build Phase 1 and Phase 2. Those two phases alone would make the project feel much more serious:

1. Config profiles make it reusable.
2. SQLite history makes it persistent and queryable.

After that, signal scoring and trend detection are the most impressive technical additions.
