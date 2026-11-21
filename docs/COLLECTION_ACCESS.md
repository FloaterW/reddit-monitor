# Reddit collection access

Verified November 18, 2026.

Reddit announced that RSS support would end on November 13, 2026. Its migration guidance offers a moderator-installed Discord Relay app, but no direct RSS replacement for communities you do not moderate. New public API access requests also closed October 31, 2026. See [Reddit's official changelog](https://support.reddithelp.com/hc/en-us/articles/54353370049684-Changelog-October-8-2026).

The existing RSS adapter is therefore a retired dependency, not a reliable production source. An HTTP 404/410 identifies an unavailable feed; by itself it cannot distinguish retirement from a nonexistent community. HTTP 429 identifies rate limiting. HTML access may also be unavailable without authentication. Longer retries or changing the AI provider cannot restore a retired endpoint.

## Current behavior

- Daily digests explicitly select `DIGEST_REDDIT_SOURCE=api` or `legacy` (the compatibility default). API mode requires an existing working installed-app OAuth client. Failed API requests never silently switch to HTML/RSS.
- API mode retrieves recent comments, real thread titles and available parent comments, including replies on older threads. It preserves full bodies, stable source links, the fixed window, configured title filters and existing editorial/HTML rendering. Standalone submission bodies remain outside scope.
- API tokens are temporary, stored only in memory, and revoked on client close when no long server cooldown prevents cleanup. Installed clients have no client secret; the adapter needs no Reddit password, browser cookies or private account permissions.
- The client shares a two-second minimum request spacing, obeys reported cooldowns, and uses bounded retries. Each subreddit retains its 300-second budget, a 30-page listing ceiling and bounded ancestor retrieval. Reddit may stop pagination sooner; reaching a configured limit does not establish full coverage.
- Failed RSS requests retain HTTP status or a sanitized connection/parsing reason in the log and coverage warnings. Response bodies and exception details are not logged.
- HTML availability is checked once per collection pass; each community retains its existing five-minute budget and shared RSS pacing.
- A zero-comment collection fails before AI generation or digest delivery. It is distinguished from a successful collection with no matching keywords, and from an empty saved input file.
- Existing saved, nonempty comments can still be previewed with `--from-json`, strict validation, separate output/status paths, `--no-email` and `--no-db`. An empty file cannot recover a missed edition.
- The scheduled task and email provider are not automatically changed or disabled. A configured schedule alone does not establish that live collection works.

## Configure an existing installed API client

Use only a client you control with appropriate access. A Developer Platform registration badge is not an external API credential. Do not assume that this setup can create new API access.

In the ignored local `.env` file:

```dotenv
DIGEST_REDDIT_SOURCE=api
REDDIT_CLIENT_ID=your_existing_installed_app_client_id
REDDIT_API_USER_AGENT=windows:reddit-digest:v1.0 (by /u/your_reddit_username)
```

The client ID identifies a public installed app, not a password. Keep deployment-specific account configuration local. Do not configure a web/script client secret in its place. The legacy `REDDIT_USER_AGENT` browser string is not used by API mode.

Test the API selection in a process environment before editing the production `.env`. Then run the full source-safe preview with separate outputs, status and evaluation files:

```powershell
python daily_digest.py --monitor churning --source-safe --quality strict --no-email --no-db --history-db data/monitor.db --save data/api-preview/digest.md --save-raw data/api-preview/comments.json --status-file data/api-preview/status.json --evaluation-report data/api-preview/evaluation.json --quiet-summary
```

`--no-email` is required to prevent SMTP and delivery-ledger writes. `--no-db` prevents run-history writes, while `--history-db` preserves read-only comparison with previous delivered editions. Inspect the complete newsletter and its coverage/evaluation reports before enabling the same settings for a scheduled run. A successful preview does not deliver an email or verify SMTP availability.

Coverage sidecars record the selected collector. Missing parents, rate limits, unavailable pages and pagination stopping before the requested boundary remain disclosed through the existing coverage footer. Empty collection fails before model generation or email. API failures identify API access/configuration in failure guidance.

Reddit's [November migration announcement](https://www.reddit.com/r/redditdev/comments/1xulk8k/data_api_migration_reminder_register_your_apps/) says remaining public API access ends in March 2027. Working access today is not a long-term guarantee; track the platform's migration requirements.

## Other replacement candidate to validate

[IFTTT says its Reddit integration is unaffected by the RSS shutdown](https://help.ifttt.com/hc/en-us/articles/58270373595163-Important-update-to-Applets-using-a-Reddit-RSS-feed). Its [Reddit service](https://ifttt.com/reddit) lists a new-comment-in-subreddit trigger. This is a candidate, not an implemented or tested replacement in this repository.

The [documented trigger fields](https://ifttt.com/reddit/triggers/new_comment_in_subreddit) include comment text/HTML, comment URL, author, subreddit, post title/URL, and an ISO 8601 posting time. These cover the basic import requirements on paper; live completeness, truncation and event delivery still require a pilot. A comment ID may be derived from a validated comment permalink, but a parent comment ID is not documented.

A migration needs the owner's IFTTT/Reddit account connection, a small collection-only pilot, and validation of full comment bodies, authors, timestamps, stable source IDs/permalinks, community identity, polling gaps, and usage limits. Parent context and post bodies must not be assumed available. Collect only public community events needed for the monitor, not private saved items or account activity. Preserve the existing newsletter generation and delivery guards when importing validated events.

Do not assume a free plan will handle all communities or that polling recovers missed historical comments. Check [current plan limits and credit usage](https://ifttt.com/plans) before enabling a sustained feed. Test source coverage and cost before changing the production collector; do not purchase or connect accounts automatically.

If existing approved API access is available, evaluate it against Reddit's current migration requirements. New ordinary API credentials are not a confirmed available fallback. A community-installed developer app requires appropriate moderator access.
