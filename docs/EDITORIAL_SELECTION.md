# Editorial selection and replay

The churning preset uses the established newsletter format with an additional
selection step. General-purpose profiles retain their existing pipeline.

1. Extract substantive notes across issuers, with changed offers and application
   reports ahead of routine discussions. Churning batches contain at most 80
   sources as well as the existing character limit, so a busy batch does not
   squeeze hundreds of comments into the 15-note response limit.
2. Detect concrete offer leads for Ink Cash/CIC, Ink Unlimited/CIU, Ink
   Preferred/CIP and Sapphire Reserve for Business/CSRB. Protect first-person
   approval and denial reports too. If extraction loses one of these leads,
   restore it as an internal source excerpt for the writer to summarize.
3. Review up to 24 heuristic omission candidates in batches of at most 12.
   Candidates include concrete rewards-conversion outcomes, contrasting product
   changes, operational restrictions, benefit-spending conditions and older
   offer sightings. Review sources already cited in notes too: a citation does
   not prove their material details survived. Each reviewed source gets a
   structured decision and reason; useful missing details become additional
   notes. Natural offer phrasing such as “100K SUB for $8K spend” and watchlist
   operational signals can qualify for review. A recommendation question does
   not automatically become a new-offer announcement. Generic shopping words
   alone do not establish a manufactured-spending development. Unreviewed
   candidates are counted explicitly. This shares the workload-based AI budget,
   with a 90-second per-call review limit and bounded retries.
4. Plan stories across all batches. Account for each note as new, updated,
   deadline-related, repetitive or low-value. Group related reports together;
   retain contradictions and material updates. Volume alone should not decide
   newsletter space. There is no hard Bilt quota.
5. Write bounded parts without splitting an assigned story between parts.
   Keep the planner's ordering after mandatory watchlist leads move first;
   an update is not automatically less important than a new item.
   Preserve source attribution, financial and arithmetic checks, numeric
   grounding, bounded repairs and the overall AI time budget.
6. Check priority recall and narrow material-fact requirements again in each
   written part and the merged newsletter. Citing a source while
   omitting its card and concrete offer amounts does not satisfy the check.
   Persistent failures stop delivery instead of silently losing protected news.
7. Check the final draft for narrowly identifiable facts already delivered in
   earlier editions. A matching planner repeat decision, full prior story,
   topic identity, numeric agreement and substantial wording overlap are all
   required. Detected repetitions enter the existing bounded writer-repair
   cycle; the checker never deletes a bullet automatically. Changed amounts,
   outcomes, deadlines, offer channels, eligibility or ambiguous scope are
   conservatively preserved. Remaining validation failures stop delivery.

Material-retention checks bind qualifying spending, contrasted product-change
outcomes, rewards non-conversion and older offer age to their actual source and
planned story. They do not require every number or repeated disclaimer. A
spending condition for a benefit must remain when that benefit is reported;
unrelated spending elsewhere does not satisfy it. Headings must preserve a
latest expected release date as “by/on or before,” rather than asserting a hold
will last through that date. Discussion-title hints may name the conversation,
but cannot establish a reply's missing card identity, terms or parent context.
Bounded checks also reject detected hypothetical shipment/restoration/payment statements
rewritten as completed outcomes and unsupported claims that a mentioned large bill
alone met a spending threshold. Known issuer/card names present only in a thread
title must be framed as discussion context or omitted, not asserted as the reply's
verified product identity. These checks cover explicit patterns and aliases;
they are not a general semantic fact checker.

Reported offers are not verified public terms. Questions stay questions;
preapproval marketing is not an application outcome or a paid bonus. When
an offer includes a spending period, it must be framed as an unverified report
or accompanied by a short reminder to check the actual offer terms. When
detected reports disagree on a spending period, the writer must flag it as
unverified and point to actual offer terms, without inventing a replacement.
The program does not automatically verify offers on issuer websites.

## Delivered history

When `--db` is used, the churning source-safe workflow reads up to seven earlier
delivered editions for the same monitor. Only successful runs with
`email_status=sent` qualify. Preview, failed, unrelated and future runs are
excluded, including recoveries delivered after the replay cutoff. Reads use a
read-only SQLite connection. Up to seven distinct edition dates are selected
within a 14-day lookback; edition gaps are disclosed. Complete headed stories
keep their parent and nested bullets together, with author/source-ID provenance.
Text is bounded to 12,000 characters per edition and the full serialized history
to 60,000 characters. With current sources available, a compact story index
provides partial topic/number hints, while relevant complete stories retain
their qualifications and provenance. Each edition gets an opportunity to
contribute a full story before the remaining budget is allocated by relevance.
The hints are discovery aids, never evidence or a substitute for full stories.
Omitted stories, index coverage and budget limits are explicit. This remains
partial editorial memory, not seven guaranteed complete newsletters.

History guides novelty only. It is never a source for new claims or citations.
New terms, meaningful corroboration/contradictions and useful deadline reminders
may justify repeating a story. Identical detected targeted offers can be treated
as optional, but a changed or conflicting spending period remains protected.
Without usable history, the run continues using current sources only.

## Preview with a real provider

Use saved sources and separate output paths. This uses the configured provider's
allowance but does not collect Reddit data or send email:

```powershell
python daily_digest.py --monitor churning --from-json data/saved-comments.json --digest-date 2026-10-29 --source-safe --quality strict --no-email --no-db --history-db data/monitor.db --save data/editorial-preview/newsletter.md --status-file data/editorial-preview/status.json
```

`--history-db` is read-only and does not override `--no-db`. With saved sources,
the coverage sidecar's window end is the history cutoff. If only an edition date
is known, the cutoff is conservatively midnight UTC on that date; without either,
history is not used. Never point a preview at production output/status paths.

The `.work` directory contains `editorial-plan.json`, `editorial-report.json`,
`extraction-review.json`, `source-selection.json`, model checkpoints and the
source-validated document. Source selection records extraction references,
planner decisions, candidate-review reasons and final candidate-document
citations. It explicitly distinguishes document validation from email delivery;
an included source ID is not proof every fact was retained. Unreviewed extraction
omissions have no invented explanation.

The evaluation report includes detected priority leads, bounded history counts,
omission-review counts and source provenance. Bilt body-word mentions, heading
context and citations to r/biltrewards are separate diagnostics: a subreddit
citation is not necessarily a Bilt story. Score-based completeness is unavailable
when all scores are missing/zero, as with RSS, rather than pretending tied zero
scores rank meaningful community interest.
These local artifacts contain source data and stay in ignored output directories.
Collection diagnostics separate current sources from older background context,
count communities and partial context, and show extraction-batch and watchlist
coverage. They describe the collected input, not all Reddit activity. These
diagnostics do not add tables or sections to the delivered newsletter.

## Limits

Recall checks recognize specific card aliases and common offer formats; they do
not prove exhaustive coverage. Candidate heuristics and material-fact patterns
also cover specific cases rather than every possible omission. The model still judges relevance, semantic
repetition and novelty. Every selected note need not become a separate bullet.
The selection changes do not add sources, restore missing parent context, fetch
standalone post bodies or change Reddit request budgets. Existing collection
warnings remain visible. The schedule, keywords, provider and email destination
remain in place. The additional `CSRB` keyword catches standalone shorthand
reports that would not match the existing whole-word `CSR` keyword.
