# API collection and observed-fact analysis

This opt-in path adds submission titles/self text to the existing comment digest.
The same source-checked writer, newsletter format, HTML renderer, email sender,
quality gate and duplicate-delivery ledger remain in use.

```powershell
python daily_digest.py --monitor churning --source-safe --intelligence-store data/api-analysis/cache.db --no-email --quality strict --save data/preview.md
python collect_snapshot.py --monitor churning --store data/api-analysis/cache.db
```

The second command collects only. It cannot call the summarizer or email sender.
On Windows, `run_collection.bat` is intended for 12:30; `run_digest.bat` still runs
at 18:30. `scripts/install_collection_task.ps1 -Root <checkout>` creates only the
midday task, refuses to replace an existing task, and copies the evening task's
logon type. It does not change the evening trigger or credentials. Missed midday
runs do not start late on resume. A missing snapshot causes a normal full-window
evening collection. Its status/log are separate from the evening watchdog.

## Collection and evidence

One OAuth client shares pacing, response-header cooldowns and request budgets.
Each subreddit has the existing 300-second collection budget, up to 5 submission
pages and 30 comment pages. Cache refresh has a 120-second budget and at most
15 batches of 100 IDs. There is no alternate transport after denied API access.

A complete cached interval can shorten the next listing pass, with a five-minute
overlap. IDs deduplicate that overlap. Known cached sources are refreshed through
`/api/info` before reuse; failed refreshes are excluded and disclosed, not assumed
deleted. Deleted bodies are removed; deleted usernames are replaced and their
derived author facts removed. Ancestors older than the edition window are background
only. Current creation timestamps are never reassigned to old posts or replies.
Fetching a post now cannot reconstruct its exact text before a historical edit.

Submission links and comment links have different validated identities. Standalone
post bodies are evidence; incidental thread titles on a reply remain context.
Parent bodies are shared once within each enhanced model payload, with explicit
references for repeated context. Full bodies remain available; none are trimmed
to fit these extraction batches. A single oversized source can exceed the target
batch size. The AI allowance scales with extraction workload: the greater of 25
minutes or `900 + 80 * ceil(batches / 2)` seconds, capped at 40 minutes and the
remaining time before 50 minutes from job start. A 30-batch run receives 35 minutes.
The absolute synthesis deadline includes elapsed collection and preprocessing;
later synthesis stages cannot restart that clock. Final validation, artifact
writes, SMTP and bookkeeping may add time beyond this allowance. Two workers
still share one deadline. Extraction requests time out after two
minutes, with at most one transient retry. Extraction reserves 20 minutes for later
stages; omission review reserves 17 minutes, and planning reserves 10 minutes for
writing. Planning/writing requests are capped at four minutes. These are maximum
allowances, not delays; successful requests proceed immediately. No source is
removed to meet a timeout. Exhausted stages fail safely without emailing raw or
partial output. Successful checkpoints remain reusable. The `.work/ai-budget.json`
sidecar records phase timing, retries and checkpoint reuse. External service or
quota failures can still prevent delivery; this policy cannot guarantee uptime.
The Windows wrapper uses `digest_recovery.py` for at most one whole-synthesis
resume after an explicit model timeout. Saved sources and coverage are hash-pinned,
the edition/window/configuration/history remain fixed, and successful checkpoints
are reused. Both attempts share the original synthesis deadline measured 50 minutes
from job start. The controller
does not retry quota, authentication, quality or SMTP failures, and does not
recollect sources. It needs at least two minutes remaining to attempt a resume.
Editorial-plan corrections identify the exact missing or duplicated note IDs.
Every extracted note must still receive an explicit selection decision; accounting
errors cannot be waived to send an incomplete plan.

## Analysis and local report

`data/api-analysis/analysis.html` and its JSON companion show observed offer terms,
reported application outcomes, distinct usernames, source links, collection counts
and newsletter selection decisions. The report is local and is not added to email.
Midday collection rebuilds it without newsletter decisions; the evening writer
adds its candidate selection decisions. Those decisions do not prove delivery.

Deterministic extraction requires one recognized product, explicit numeric terms
and attributable statements. Every extracted value retains exact source offsets.
Ambiguous products, questions, negations, non-application denials and ambiguous
currency/points are excluded from this structured layer. Unknown fields stay
unknown. Excluded structured facts do **not** remove the original source from
newsletter analysis. A small number of structured records is expected.

Repeated observations, matching reports, different reported terms and source edits
are labelled separately. A previously collected item was not necessarily published;
the existing delivered-edition history remains the basis for editorial repeats.
Different terms may represent targeting or channels, not an issuer-wide change.
Distinct usernames are not necessarily independent people and are never converted
to approval probabilities. Counts describe observed sources, not exhaustive coverage.

## Retention and recovery

The new SQLite store is separate from run history and delivery. It retains at most
10,000 raw sources across configurations; observations expire after 48 hours without
successful refresh and are purged on the next collection/cleanup. Removed sources
cascade to derived facts, with SQLite secure deletion and WAL checkpointing.
Aggregate collection metrics last 30 days. Configuration changes isolate cached
intervals. These rules do not retroactively delete existing digest archives, old
run-history databases, mailbox copies or explicitly saved preview artifacts.

```powershell
python collect_snapshot.py --monitor churning --store data/api-analysis/cache.db --cleanup-only
```

Cleanup makes no API/model/email calls and rewrites the local report from surviving
cache records. Nothing runs while the machine is off. A failed refresh is not proof
of deletion; report timestamps indicate local ingestion, not independent verification.

To revert enhanced evening collection, remove only `--intelligence-store ...` from
the local wrapper and disable the collection task. Keep the delivery ledger intact.
The guarded release utility can activate a pinned release only after its protected
evening edition succeeds, within its explicit next-day time window. It refuses
changed files/settings or active digest processes, backs up targets and restores
them if installation fails. It never runs or emails a digest.
