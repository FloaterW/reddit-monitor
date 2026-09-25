# Reddit Monitor

A configurable Reddit monitoring and email-digest pipeline. Choose communities and keywords, set an audience and editorial priorities, and turn collected discussions into a source-linked newsletter.

Built around a real daily credit-card rewards newsletter, with a separate job-market example showing how the same pipeline supports another topic. Collection is bounded; this project does not promise exhaustive Reddit coverage.

## What it does

- Collects posts/comments through the existing HTML/RSS adapters and filters comments using monitor settings.
- Deduplicates comments, keeps available reply context, and reports collection limitations.
- Uses a configured AI CLI to extract relevant notes and write a themed newsletter.
- Constructs citations from known sources and checks supported numeric claims before delivery in source-safe mode.
- Renders sanitized Markdown and HTML, optionally sends through Gmail, and records run outcomes.
- Supports saved-data replay, SQLite history, bounded retries, and a scheduled-run watchdog.

## Try it without accounts or network access

Requires Python 3.10+ and installed dependencies:

```powershell
git clone https://github.com/FloaterW/reddit-monitor.git
cd reddit-monitor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-lock.txt
python demo_digest.py
```

On macOS/Linux, activate with `source .venv/bin/activate`.

The demo uses **fictional job-market comments and fixture model responses**, not live Reddit or AI. It exercises the real source-safe rendering, validation, run-status, and HTML-preview code. Network, scraping, and email delivery are blocked; no production database or run status is changed.

Open `data/demo-job-market/preview.html`. Markdown, validation results, and a separate run status are saved alongside it. The illustrative Reddit links are not real source posts. [Demo details](examples/job-market/README.md).

## Profiles, not hardcoded topics

| Profile | Example purpose | Editorial behavior |
| --- | --- | --- |
| [Churning](config/monitors/churning.json) | Credit-card rewards, bank bonuses, award travel | Established source-led newsletter style; financial arithmetic checks enabled |
| [Job market](config/monitors/job-market.json) | Hiring, interviews, compensation, workplace policies | Career-focused priorities and descriptive headings; financial arithmetic checks disabled |
| Your own JSON profile | Another topic and audience | General editorial preset with configurable priorities and style |

Profiles define subreddits, keywords, collection settings, audience, editorial priorities, style, and optional domain-specific checks. Core citation, source-grounding, and output-safety checks remain shared. **General-purpose means configurable, not that every domain has been validated.**

See [the profile guide](docs/PROFILES.md) to create a monitor.

## Run with a real model

Install and authenticate a supported CLI first. Codex is the default; Claude remains an optional provider. Credentials stay local. See [setup and operations](docs/OPERATIONS.md) and [security guidance](SECURITY.md).

Preview the synthetic sources using your actual model, without scraping or sending email:

```powershell
python daily_digest.py --monitor job-market --from-json examples/job-market/source.json --digest-date 2026-01-01 --source-safe --quality strict --no-email --no-db --save data/job-preview/newsletter.md --status-file data/job-preview/status.json
```

This command uses your provider allowance. Unlike `demo_digest.py`, it generates a new model response.

For live collection, first confirm you have appropriate access and permission under Reddit's current policies. Then omit `--from-json` and `--digest-date`; keep `--no-email --no-db` and separate output/status paths while testing. Saved-input replay does not reconstruct missing material.

## Architecture

```text
Monitor JSON → Collection and filtering → Structured AI extraction/writing
                       │                              │
                Coverage warnings              Source validation
                                                      │
                                         Markdown + safe HTML
                                                      │
                                      Optional email / SQLite
                                                      │
                                            Status + watchdog
```

The main modules are:

- `reddit_scraper.py`: collection and pagination.
- `monitor_config.py` / `editorial_profiles.py`: validated configuration and editorial presets.
- `daily_digest.py`: orchestration, provider isolation, outputs, and delivery.
- `verified_digest.py`: bounded source-safe extraction, writing, and rendering.
- `evaluate_digest.py`, `editorial_checks.py`, `financial_checks.py`: shared checks and optional financial rules.
- `storage.py`, `check_digest_ran.py`, `notify_failure.py`: persistence and run monitoring.

## Real-world case study: credit-card rewards

The project's daily churning newsletter drove its timeout recovery, source attribution, numeric checks, coverage disclosures, and operational safeguards. It remains an explicitly configured example workflow.

The existing `run_digest.bat` selects the churning profile. The refactor does not change its 6:30 p.m. schedule, keywords, providers, or email destination. No-argument CLI operation retains its historical churning defaults for backward compatibility; select a profile explicitly for new uses.

Read the [case study](docs/CHURNING_CASE_STUDY.md). The included [historical sample](example_digest.md) illustrates formatting, not current offers or verified advice.

## Testing and security

```powershell
python -m pip install -r requirements-dev-lock.txt
python -m pytest -q
python -m ruff check .
python -m pip_audit -r requirements-dev-lock.txt
```

Tests use static inputs and mocked service boundaries. CI checks Windows/Linux with Python 3.10 and 3.12, enforces coverage, and audits dependencies. CodeQL provides additional static analysis. Regression tests verify exact churning-prompt preservation and that other profiles do not inherit its editorial instructions.

Passwords, local environment files, raw runs, databases, logs, and investigation notes are excluded from Git. SMTP uses certificate and hostname verification. See [SECURITY.md](SECURITY.md) for the security model and audit limitations.

## Known limitations

- Reddit throttling, feed changes, page/time budgets, and unavailable parent context can leave gaps. Standalone post bodies without comments are not included in the digest collector.
- Technical endpoint access does not establish permission to collect or reuse data.
- Source checks catch particular errors, not every factual or contextual mistake. Reddit claims are not independently verified.
- The generic profile has an offline demonstration; the churning profile is the established operational use case.
- Real runs require provider authentication and available usage allowance; email requires local Gmail configuration.
- The supplied scheduling wrappers are Windows-specific. No schedules are created by cloning or running the offline demo.

[Operations reference](docs/OPERATIONS.md) · [Profile configuration](docs/PROFILES.md) · [Security](SECURITY.md) · [MIT license](LICENSE)
