# Case study: a daily credit-card rewards newsletter

The churning monitor is the project's real-world application, not a requirement of the core pipeline. It follows credit-card rewards, bank bonuses, eligibility reports and award travel across five subreddits. The configured keywords and title filters live in [churning.json](../config/monitors/churning.json).

The normal Windows wrapper explicitly selects `--monitor churning --source-safe --quality strict`. The profile selects the `churning` editorial preset and enables narrow financial arithmetic checks. The profile refactor preserves the original extraction and writing prompts byte-for-byte, along with collection settings, source gates, and email formatting. It does not install or modify a schedule.

## Engineering lessons

- A successful email does not prove complete collection: expose throttling, time limits and missing reply context.
- A plausible model response is not sufficient: construct citations from known source IDs and validate supported quantities before delivery.
- Preserve useful information without forcing every matched comment into the newsletter.
- Reproduce failures with saved input before changing production behavior.
- Keep scheduling, delivery status and the independent watchdog observable.

The [historical newsletter sample](../example_digest.md) shows the use case's format, not current offers or independently verified financial advice. The [synthetic job-market demo](../examples/job-market/README.md) demonstrates a second topic without exposing personal run data.

## Collection and newsletter behavior

The churning profile includes r/biltrewards and selects up to 100 recent posts
per subreddit. Listings paginate when Reddit returns fewer posts per request.
The RSS fallback requests up to 30 pages of 100 comments, within a five-minute
budget per subreddit, with duplicate-page
detection. Collection logs warn when limits or repeated pages prevent full coverage.

Keywords match comment bodies, thread titles, and available ancestor comment bodies.
Parent sources travel with replies into summarization; older parents are marked as
background, not current news. RSS does not supply parent IDs, so it can use thread
titles but cannot reconstruct reply chains. Hyphen/space variants and explicit
compact forms (clawback, popup, shutdown, signup) match equivalently.

Coverage remains bounded: Reddit may omit comments, RSS can be incomplete, and
the existing Canadian thread-title filter still applies. More posts/comments can
increase scraping time and the number of summarization batches. Standalone post
bodies without comments are not collected by this comment-based pipeline.

The scheduled run uses `--source-safe --quality strict`. Collection has a
five-minute network budget per subreddit and stops RSS paging after a fully
dated page older than the requested window. Budget exhaustion retains collected
comments and logs a coverage warning; it does not promise exhaustive coverage.

The time window is frozen at run start, before collection begins. A daily run
starting at 18:30 selects timestamps from the previous day's 18:30 through
today's 18:30 (source timestamps have minute precision). Slow collection no
longer shifts the lower cutoff or includes comments posted after that endpoint.
Undated comments are excluded from a bounded window; older ancestor comments
may remain explicitly marked as background. A delayed/manual run uses its own
start time, not an assumed scheduled time.

Source-safe summarization uses two workers, five-minute extraction timeouts,
four-minute writing requests, and a shared fifteen-minute AI budget. It extracts substantive notes, then writes
one themed newsletter with emoji headings, concise bullets, useful numbers and
source citations. Jokes, moderation chatter and empty replies are not news.
Writing is split into batches of at most eight extracted notes instead of one
large final request. Claude runs with explicit low reasoning effort, disabled
skills, and no session persistence. A timed-out extraction or writing request is
retried once, only within the same fifteen-minute total AI budget; authentication,
quota and validation errors are not treated as transient timeouts. Logs include
the stage, request size, attempt, timeout and elapsed time. All final source and
quality checks remain mandatory.
Up to two editorial correction passes are allowed for a writing part, also within
that shared budget. Invalid claims are never accepted just to deliver an email.
Successful responses are checkpointed by the exact prompt hash in the output's
`.work` directory; replaying the same input/output path can reuse them. Every
reused response still goes through source/schema validation. Failed generation
never falls back to emailing raw comments.

Codex is the default. `DIGEST_LLM_COMMAND=codex` selects the supported
Codex CLI adapter and its saved ChatGPT login (`codex login status` to inspect).
This consumes the account's Codex allowance, not separate API billing; usage
limits still apply. `DIGEST_CODEX_MODEL` is an optional override; otherwise the
CLI selects its default. Codex uses an isolated directory, read-only sandbox,
disabled integrations/tools, and no email/API secrets in its environment.
If Codex is absent from the background account's PATH, the script resolves the
newest installed desktop-app CLI under LOCALAPPDATA, or the standalone install.
It does not require the desktop app to be open.

`scheduled_digest_test.py` supports a single-use, expiring diagnostic request in
`data/codex-migration/test-request.json`. When explicitly requested, the existing
Windows task uses saved source data, strict validation, no email and no production
database writes, then checks SMTP authentication. It records a separate result in
`data/codex-migration/background-verification.json`. The request is consumed before
testing; absent/expired requests leave the normal daily workflow unchanged.

For an older edition, use `--from-json <saved-raw.json> --digest-date YYYY-MM-DD`,
with separate `--save`, `--status-file`, and `--no-db` so recovery does not replace
the scheduled run's status. Preview with `--no-email` first. The date labels the
edition; it does not reconstruct missing Reddit material or filter the file.
The churning preset preserves the established newsletter style: source-led, compact
bullets, bold specifics, nested offer/timeline details, and brief DP caveats.
The reference range is 1,500–2,800 words (not a quota), with a hard 3,500-word
limit. Up to 36 distinct topics are supported so busy days need not be forced
into fewer sections. The renderer creates citations from source IDs, at the
start or end of a bullet. Numeric headline claims must match sources cited in
that same section; body and nested-bullet numbers still require their own
line-level sources. Unsupported numeric claims trigger one repair attempt.
There is no raw-comment fallback or appended comment archive. If generation or
validation fails, the normal failure path runs instead of emailing raw comments.

Raw author coverage remains visible as a diagnostic, not a delivery requirement:
summaries should select useful information, not reproduce every matched author.
Citation integrity and numeric source checks remain required. These checks do
not independently verify Reddit claims or prove every paraphrase is correct.

The watchdog waits on a running status for up to ninety minutes from the run's
start, checking every thirty seconds. It reports completion, failure, or a stale
run instead of flagging every run still working at the initial check time.
Each watchdog invocation waits at most forty-five minutes so it fits within its
scheduled-task runtime limit. SMTP connections have a thirty-second timeout.
