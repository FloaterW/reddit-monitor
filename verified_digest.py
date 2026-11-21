"""Bounded editorial synthesis with canonical citations; never email raw dumps."""

import hashlib
import itertools
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from editorial_checks import editorial_issues
from editorial_novelty import (
    novelty_issues,
    repeat_fact_context,
    repeated_background,
    same_edition_issues,
    same_edition_repeats,
    scoped_repeat_repair_issues,
    shared_fact_retention_issues,
)
from editorial_profiles import resolve_editorial
from editorial_retention import identify_candidates, retention_issues, selected_note_requirements
from editorial_selection import (
    CARD_PATTERNS,
    PLAN_RULES,
    amounts,
    plan_batches,
    plan_payload,
    plan_repair_details,
    priority_issues,
    priority_leads,
    protect_notes,
    selection_report,
    title_key,
    validate_plan,
)
from editorial_trace import build_source_trace
from evaluate_digest import _comment_url_key, check_claim_grounding
from factual_checks import edition_prompt_context, factual_issues, parse_edition_cutoff
from financial_checks import arithmetic_issues
from omission_review import REVIEW_RULES, validate_review
from synthesis_budget import SynthesisBudget
from topic_attribution import topic_attribution_issues

AI_BUDGET_SECONDS = 2400
AI_BATCH_TIMEOUT = 120
AI_SYNTHESIS_TIMEOUT = 600
MAX_WRITING_NOTES = 8
MAX_EXTRACTION_SOURCES = 80
MAX_OMISSION_SOURCES = 24
OMISSION_BATCH_SOURCES = 12
MAX_DIGEST_WORDS = 3500
SOURCE_TIME_RULES = """
Source created timestamps are INTERNAL chronology context, not newsletter boilerplate.
Do not routinely print comment posting dates, times or UTC timestamps. A posting
timestamp is not proof of an event's exact local date. Do not convert 'today' or
'yesterday' into a calendar event date without sufficient source evidence. Use
source timing to avoid presenting a stale relative deadline as current; when its
exact date or timezone is unknown, retain a brief attributed uncertainty instead
of inventing a date. Preserve explicit source deadlines and timezone labels.
Sources marked background_only are older context, not independent current news.
Keep a source's explicit older event age when relevant to availability or a
comparison: 'a couple of weeks ago' is not a newly launched offer today.
discussion_topic_hint only names the discussion; it cannot establish the identity,
terms or outcome of a context-free reply. Never use it as missing parent evidence.
Each published section must cite at least one current source; older parents may
provide supporting details for that current development.
"""

def parse_reply(reply):
    text = reply.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        result = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Digest model did not return valid structured output") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Digest model output must be an object")
    return result


def _source_records(comments):
    records = [{**({"source_kind": "submission", "title_is_source_evidence": True}
                if c.get("source_kind") == "submission" else {}),
             "source_id": c.get("id"), "author": c.get("author"),
             # Missing parents cannot be replaced by the thread title. Expose
             # the title only as a labelled discussion hint for these replies;
             # it is not evidence of their card identity or claim.
             "thread": (c.get("post_title") if c.get("parent_context") or c.get("context_status") in {"available", "top_level"} else None),
             "discussion_topic_hint": c.get("post_title", ""),
             "subreddit": c.get("subreddit"),
             "created": c.get("created", ""),
             "body": c.get("body", ""), "background_only": c.get("context_only", False),
             "parent_id": c.get("parent_id", ""),
             "context_status": c.get("context_status", "available" if c.get("parent_context") else "unknown"),
             "source_consistency_warnings": arithmetic_issues(c.get("body", "")) +
                 editorial_issues(c.get("body", ""), leading_source=True),
             "parent_context": [{"source_id": p.get("id"), "body": p.get("body", ""),
                                 "created": p.get("created", ""),
                                 "background_only": True}
                                for p in c.get("parent_context", [])]} for c in comments]
    if any(c.get("source_kind") == "submission" or any(p.get("source_kind") == "submission"
           for p in c.get("parent_context", [])) for c in comments):
        # Full shared context appears once per payload. References retain IDs;
        # bodies are neither truncated nor silently omitted from the payload.
        seen = {r["source_id"] for r in records}
        for record, source in zip(records, comments):
            for parent, original in zip(record["parent_context"], source.get("parent_context", [])):
                if parent["source_id"] in seen:
                    parent.pop("body")
                    parent["body_reference"] = "Full body supplied elsewhere in this payload under this source_id"
                else:
                    parent["author"] = original.get("author")
                    seen.add(parent["source_id"])
    return records


def _plain_markdown(value):
    return (isinstance(value, str) and bool(value.strip())
            and not re.search(r"https?://|\[[^\]]*\]\(|\[u/|<[^>]+>|```|[\r\n]", value))


def _safe_auxiliary_shape(document):
    """Only run semantic helpers after iterable/string fields are validated."""
    sections = document.get("sections")
    return isinstance(sections, list) and all(
        isinstance(section, dict) and isinstance(section.get("bullets"), list)
        and all(isinstance(b, dict) and isinstance(b.get("text"), str)
                and isinstance(b.get("source_ids"), list)
                and all(isinstance(sid, str) for sid in b["source_ids"])
                for b in section["bullets"])
        for section in sections)


def render_checked(document, comments, *, minimum_sections=None, digest_date=None,
                   financial_checks=True, factual_checks=False, edition_cutoff=None):
    """Gate included claims and newsletter structure, not raw-author coverage."""
    sources = {c.get("id"): c for c in comments if c.get("id")}
    sections = document.get("sections")
    minimum = minimum_sections if minimum_sections is not None else (3 if len(comments) >= 50 else 1)
    if not isinstance(sections, list) or not minimum <= len(sections) <= 36:
        return None, [f"Use between {minimum} and 36 distinct themed sections."]
    errors, titles = [], set()
    lines = [f"# Daily Digest — {digest_date or datetime.now():%B %d, %Y}"]
    for section in sections:
        if not isinstance(section, dict):
            errors.append("Each section must be an object.")
            continue
        title = section.get("title", "")
        if not _plain_markdown(title) or title.lstrip().startswith((">", "#")):
            errors.append("Use plain thematic titles without links or markup headings.")
            continue
        if re.match(r"^(?:📅\s*)?(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}", title):
            errors.append("Do not add a date section or prefix; the application supplies the edition date.")
            continue
        if title.casefold() in titles:
            errors.append("Merge repeated section titles.")
        titles.add(title.casefold())
        lines.extend(["", "---", "", f"## {title}", ""])
        bullets = section.get("bullets")
        if not isinstance(bullets, list) or not 1 <= len(bullets) <= 24:
            errors.append(f"{title}: use one to 24 concise bullets, including nested details.")
            continue
        has_top_level = False
        has_current_source = False
        section_sources = {}
        for item in bullets:
            if not isinstance(item, dict):
                errors.append(f"{title}: malformed bullet.")
                continue
            text, ids = item.get("text"), item.get("source_ids")
            indent = item.get("indent", 0)
            position = item.get("source_position", "end")
            if (type(indent) is not int or indent not in (0, 1)
                    or (indent == 1 and not has_top_level)
                    or position not in ("start", "end")):
                errors.append(f"{title}: invalid bullet nesting or attribution position.")
                continue
            has_top_level = has_top_level or indent == 0
            if (not _plain_markdown(text) or text.lstrip().startswith((">", "#"))
                    or len(text.split()) > 100):
                errors.append(f"{title}: use a summarized bullet under 100 words, not a raw quote.")
                continue
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 8
                    or any(not isinstance(i, str) or i not in sources for i in ids)):
                errors.append(f"{title}: bullet has unknown or missing source_ids.")
                continue
            cited = [sources[i] for i in dict.fromkeys(ids)]
            has_current_source = has_current_source or any(not c.get("context_only") for c in cited)
            section_sources.update({c["id"]: c for c in cited})
            if position == "start" and len(cited) != 1:
                errors.append(f"Bullet {text!r}: leading attribution requires exactly one source; split distinct reports or use a standalone sentence with end citations.")
                continue
            authors = [c.get("author", "").casefold() for c in cited]
            if position == "start":
                # Replace only an exact redundant subject before a reporting
                # verb. The canonical link already supplies that same subject.
                author = str(cited[0].get("author", ""))
                if author:
                    text = re.sub(rf"^(?:u/)?{re.escape(author)}\s+(?=(?:reports|notes|says|saw|found|clarifies|quotes|advises|confirms|estimates|lists|booked|claims)\b)",
                                  "", text, count=1, flags=re.I)
            if len(authors) != len(set(authors)):
                errors.append(f"Bullet {text!r}: repeated author citations; choose one sufficient source per author or split distinct facts into separate bullets. These are not independent reports.")
                continue
            if financial_checks:
                errors.extend(f"Bullet {text!r}: {issue}" for issue in arithmetic_issues(text))
            if factual_checks:
                errors.extend(f"Bullet {text!r}: {issue}" for issue in factual_issues(
                    text, cited, edition_cutoff=edition_cutoff))
                errors.extend(f"Bullet {text!r}: {issue}" for issue in topic_attribution_issues(text, cited))
            errors.extend(f"Bullet {text!r}: {issue}" for issue in editorial_issues(
                text, leading_source=position == "start", source_bodies=[c.get("body", "") for c in cited]))
            if re.search(r"\b(?:two|three|four|five|\d+)\s+additional\s+(?:users|reports|cardholders|DPs)\b", text, re.I):
                detail = (" and distinguish repeated comments and unconfirmed enrollment from payout."
                          if financial_checks else "; distinguish independent people from repeated comments.")
                errors.append(f"Bullet {text!r}: avoid counted 'additional' reporters; identify the actual independent reporter" + detail)
            links = []
            for source in cited:
                author, url = source.get("author", ""), _comment_url_key(source)
                if not url or not re.fullmatch(r"[A-Za-z0-9_-]+", author):
                    errors.append(f"Cannot cite source {source.get('id')} safely; use another source.")
                    continue
                # Escape Markdown punctuation in usernames without changing the
                # displayed name or the canonical citation URL.
                escaped_author = author.replace("_", r"\_")
                links.append(f"[u/{escaped_author}](https://reddit.com{url}/)")
            attribution = " and ".join(links) if position == "start" else "; ".join(links)
            content = (f"{attribution} {text.strip()}" if position == "start"
                       else f"{text.strip()} {attribution}")
            line = f"{'  ' * indent}- {content}"
            grounding = check_claim_grounding(line, cited)
            if not grounding["passed"]:
                values = ", ".join(f"{e['category']}={e['value']}" for e in grounding["ungrounded"])
                errors.append(f"Bullet {text!r}, sources {ids}: unsupported numbers: {values}. Copy source formats exactly or remove the unsupported claim.")
            lines.append(line)
        if not has_current_source:
            errors.append(f"{title}: cite at least one current source; background-only parents cannot establish a current news section.")
        if factual_checks:
            errors.extend(f"Heading {title!r}: {issue}" for issue in factual_issues(
                title, list(section_sources.values()), edition_cutoff=edition_cutoff))
            errors.extend(f"Heading {title!r}: {issue}" for issue in topic_attribution_issues(
                title, list(section_sources.values())))
    result = "\n".join(lines) + "\n"
    # Headline numbers must be supported by sources actually cited within that
    # section. Body bullets still require their own line-level citations.
    grounding = check_claim_grounding(result, comments)
    if not grounding["passed"]:
        errors.append("Unsupported headline or bullet numbers: " + json.dumps(grounding["ungrounded"]))
    if len(result.split()) > MAX_DIGEST_WORDS:
        errors.append(f"Digest exceeds {MAX_DIGEST_WORDS} words; consolidate repeated material.")
    return (None, errors) if errors else (result, [])


def summarize_verified(comments, chunker, invoke, artifact_dir=None, recovered_notes=None,
                       editorial_profile=None, recent_editions=None, editorial_report=None,
                       edition_cutoff=None, analysis_context=None, available_ai_seconds=None,
                       job_deadline=None):
    """Extract useful notes, synthesize one themed digest, repair once if needed."""
    profile = editorial_profile or resolve_editorial()
    recent_editions = recent_editions or []
    edition_context = edition_prompt_context(edition_cutoff)
    edition_date = parse_edition_cutoff(edition_cutoff)
    if isinstance(edition_date, datetime):
        edition_date = edition_date.date()
    leads = priority_leads(comments, recent_editions) if profile.preset == "churning" else []
    chunks = chunker(comments) if recovered_notes is None else []
    if profile.preset == "churning":
        chunks = [chunk[offset:offset + MAX_EXTRACTION_SOURCES] for chunk in chunks
                  for offset in range(0, len(chunk), MAX_EXTRACTION_SOURCES)]
    budget = SynthesisBudget(len(chunks), maximum=AI_BUDGET_SECONDS, available=available_ai_seconds,
                             deadline=job_deadline,
                             editorial=profile.preset == "churning", clock=time.monotonic)
    call_numbers = itertools.count(1)

    def save_budget():
        if artifact_dir is not None:
            from daily_digest import atomic_write_json
            # Writes happen only at phase boundaries, once all workers have
            # joined, so a late worker cannot overwrite newer diagnostics.
            atomic_write_json(Path(artifact_dir) / "ai-budget.json", budget.snapshot())

    def phase(name):
        budget.phase = name
        save_budget()

    phase("extraction")
    print(f"  AI allowance: {budget.seconds:g}s for {len(chunks)} extraction batches; planning/writing time reserved.", flush=True)

    def call(prompt, limit=AI_BATCH_TIMEOUT):
        cache_path = None
        if artifact_dir is not None:
            fingerprint = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            cache_path = Path(artifact_dir) / f"checkpoint-{fingerprint}.json"
            if cache_path.exists():
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                    if cached.get("prompt_sha256") == fingerprint and isinstance(cached.get("reply"), dict):
                        budget.record("checkpoint", "reused")
                        return cached["reply"]
                except (ValueError, OSError):
                    pass  # Corrupt checkpoints must never block a fresh attempt.
        from digest_schema import response_schema
        stage = response_schema(prompt)["required"][0]
        for attempt in range(2):
            timeout = budget.timeout(limit)
            started = time.monotonic()
            print(f"  AI {stage}: {len(prompt):,} characters, attempt {attempt + 1}, "
                  f"limit {timeout:.0f}s.", flush=True)
            try:
                reply = parse_reply(invoke(prompt, timeout=timeout, structured=True))
                budget.record(stage, "completed", time.monotonic()-started)
                print(f"  AI {stage} completed in {time.monotonic() - started:.1f}s.", flush=True)
                break
            except (TimeoutError, ConnectionError) as exc:
                budget.record(stage, "timeout" if isinstance(exc, TimeoutError) else "temporary_failure", time.monotonic()-started)
                reason = "timed out" if isinstance(exc, TimeoutError) else "had a temporary provider/session failure"
                print(f"  AI {stage} {reason} after {time.monotonic() - started:.1f}s.", flush=True)
                if attempt or budget.deadline - time.monotonic() - budget.reserves.get(budget.phase, 0) <= 0:
                    raise
                print("  Retrying this request once within the remaining AI budget.", flush=True)
        if artifact_dir is not None:
            from daily_digest import atomic_write_json
            atomic_write_json(Path(artifact_dir) / f"model-{next(call_numbers):03d}.json", reply)
            atomic_write_json(cache_path, {"prompt_sha256": fingerprint, "reply": reply})
        return reply

    def extract(chunk):
        prompt = profile.rules + SOURCE_TIME_RULES + edition_context + profile.extraction + json.dumps(_source_records(chunk), ensure_ascii=False)
        known = {c.get("id") for c in chunk}
        aliases = {i.removeprefix("t1_"): i for i in known if isinstance(i, str)}
        for attempt in range(2):
            notes = call(prompt).get("notes")
            valid = isinstance(notes, list) and len(notes) <= 15
            if valid:
                for note in notes:
                    if (not isinstance(note, dict) or not isinstance(note.get("text"), str)
                            or not note["text"].strip() or len(note["text"]) > 2000
                            or not isinstance(note.get("source_ids"), list) or not note["source_ids"]):
                        valid = False
                        break
                    # Internal notes may contain whitespace/formatting. Only the
                    # final newsletter has strict markup requirements.
                    note["source_ids"] = [aliases.get(i, i) if isinstance(i, str) else i for i in note["source_ids"]]
                    if any(not isinstance(i, str) or i not in known for i in note["source_ids"]):
                        valid = False
                        break
            if valid:
                return notes
            if attempt == 0:
                print("  Repairing extraction structure/source IDs...")
                prompt += "\nThe previous reply had malformed notes or invalid source_ids. Return at most 15 notes with nonempty text and exact source_id values from SOURCE COMMENTS. Remove unsupported notes. Previous reply:\n" + json.dumps(notes, ensure_ascii=False)
        raise RuntimeError("Digest extraction still contains invalid source references after repair")

    if recovered_notes is None:
        print(f"  Editorial digest: extracting useful information from {len(chunks)} batches.")
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                notes = [note for batch in executor.map(extract, chunks) for note in batch]
        finally:
            save_budget()
    else:
        known = {c.get("id") for c in comments}
        notes = recovered_notes
        if not isinstance(notes, list) or any(
            not isinstance(n, dict) or not isinstance(n.get("text"), str)
            or not isinstance(n.get("source_ids"), list) or not n["source_ids"]
            or any(not isinstance(i, str) or i not in known for i in n["source_ids"])
            for n in notes
        ):
            raise RuntimeError("Recovered notes contain invalid source references")
        print(f"  Reusing {len(notes)} extracted notes, checked against original source IDs.")
    phase("review")
    review = {"enabled": profile.preset == "churning", "candidate_count": 0,
              "reviewed_count": 0, "unreviewed_count": 0, "decisions": []}
    if profile.preset == "churning":
        candidates = identify_candidates(comments, limit=len(comments))
        reviewed = candidates[:MAX_OMISSION_SOURCES]
        review.update(candidate_count=len(candidates), reviewed_count=len(reviewed),
                      unreviewed_count=max(0, len(candidates) - len(reviewed)), candidates=reviewed)
        source_map = {c.get("id"): c for c in comments}

        def review_batch(group):
            source_ids = {c["source_id"] for c in group}
            existing = [n for n in notes if source_ids & set(n["source_ids"])]
            payload = {"sources": _source_records([source_map[c["source_id"]] for c in group]),
                       "candidate_signals": group, "existing_notes": existing}
            prompt = profile.rules + SOURCE_TIME_RULES + edition_context + REVIEW_RULES + json.dumps(payload, ensure_ascii=False)
            for attempt in range(2):
                reply = call(prompt, 90)
                issues = validate_review(reply, source_ids, existing)
                if not issues:
                    return reply
                if attempt:
                    raise RuntimeError("Omission review failed validation: " + "; ".join(issues[:3]))
                prompt += "\nREPAIR REVIEW:\n" + json.dumps(reply, ensure_ascii=False) + "\nISSUES:\n" + json.dumps(issues)

        if reviewed:
            print(f"  Reviewing {len(reviewed)} potential extraction omissions within the existing AI budget.")
            groups = [reviewed[offset:offset + OMISSION_BATCH_SOURCES]
                      for offset in range(0, len(reviewed), OMISSION_BATCH_SOURCES)]
            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    replies = list(executor.map(review_batch, groups))
            finally:
                save_budget()
            for reply in replies:
                notes.extend(reply["notes"])
                review["decisions"].extend(reply["decisions"])
        if artifact_dir is not None:
            from daily_digest import atomic_write_json
            atomic_write_json(Path(artifact_dir) / "extraction-review.json", review)
    notes = protect_notes(notes, leads, comments)
    if not notes:
        raise RuntimeError("No substantive items found; no raw-comment email will be sent")
    phase("planning")
    plan = None
    if profile.preset == "churning" and (len(notes) > MAX_WRITING_NOTES or (recent_editions and len(notes) > 1)):
        payload = plan_payload(notes, leads, recent_editions)
        if analysis_context:
            payload["observed_facts"] = analysis_context
            payload["observed_facts_rules"] = (
                "Unverified diagnostics only. Always use current sources as evidence. "
                "A previously collected fact was not necessarily published. Never drop an item just because it was cached. "
                "Different reported terms can be targeted variants, not verified issuer-wide changes. "
                "Use these hints to compare substance and preserve contradictions, not to invent claims or alter newsletter style.")
        prompt = edition_context + PLAN_RULES + json.dumps(payload, ensure_ascii=False)
        for attempt in range(2):
            try:
                plan = call(prompt, 240)
            finally:
                save_budget()
            issues = validate_plan(plan, payload)
            if not issues:
                break
            if attempt:
                raise RuntimeError("Editorial selection failed validation: " + "; ".join(issues))
            details = plan_repair_details(plan, payload)
            print(f"  Repairing editorial plan: {details['missing_count']} missing and {details['duplicate_count']} duplicated note IDs.", flush=True)
            prompt += ("\nREPAIR PLAN:\n" + json.dumps(plan, ensure_ascii=False)
                       + "\nISSUES:\n" + json.dumps(issues)
                       + "\nEXACT NOTE ACCOUNTING ERRORS:\n" + json.dumps(details)
                       + "\nReturn the complete corrected plan. Assign each missing note to an appropriate story "
                       "with an explicit editorial status; keep every input note exactly once. Preserve unaffected "
                       "assignments. A missing note must not be silently discarded or automatically classified as low_value.")
        if artifact_dir is not None:
            from daily_digest import atomic_write_json
            atomic_write_json(Path(artifact_dir) / "editorial-plan.json", {"plan": plan, "inputs": payload})
    # Cluster specific discussion threads and overlapping topic names before
    # splitting. Otherwise each writing part repeats the same Bilt/Paze story.
    # General news/help threads are not single topics and must not glue together
    # an entire day's material into the old unbounded request.
    sources_by_id = {c.get("id"): c for c in comments}
    clusters = []
    stop = {"the", "a", "and", "or", "to", "of", "for", "in", "on", "with",
            "dp", "dps", "data", "point", "points", "reported", "reports"}
    for note in notes:
        tokens = set(re.findall(r"[a-z0-9]+", str(note.get("topic", "")).lower())) - stop
        threads = set()
        for sid in note["source_ids"]:
            source = sources_by_id[sid]
            title = source.get("post_title", "")
            if title and not re.search(r"weekly|news and updates|question thread|help thread|discussion thread", title, re.I):
                threads.add(source.get("post_permalink") or title.casefold())
        matches = []
        for index, cluster in enumerate(clusters):
            shared = tokens & cluster["tokens"]
            similar = len(shared) >= 3 and len(shared) / max(1, min(len(tokens), len(cluster["tokens"]))) >= 0.6
            if threads & cluster["threads"] or similar:
                matches.append(index)
        merged = {"tokens": tokens, "threads": threads, "notes": [note]}
        for index in reversed(matches):
            existing = clusters.pop(index)
            merged["tokens"] |= existing["tokens"]
            merged["threads"] |= existing["threads"]
            merged["notes"] = existing["notes"] + merged["notes"]
        if matches:
            clusters.insert(min(matches), merged)
        else:
            clusters.append(merged)
    batches, current = [], []
    for cluster in clusters:
        # Even a very large single discussion must not restore the original
        # monolithic bottleneck. Preserve every note across bounded slices.
        for offset in range(0, len(cluster["notes"]), MAX_WRITING_NOTES):
            group = cluster["notes"][offset:offset + MAX_WRITING_NOTES]
            if current and len(current) + len(group) > MAX_WRITING_NOTES:
                batches.append(current)
                current = []
            current.extend(group)
    if current:
        batches.append(current)
    if plan is not None:
        batches = plan_batches(plan, payload, MAX_WRITING_NOTES)

    phase("writing")
    repeated_facts = repeat_fact_context(plan, payload if plan else None, recent_editions)
    novelty_attempts = []
    shared_source_attempts = []

    def save_novelty_progress():
        if artifact_dir is not None:
            from daily_digest import atomic_write_json
            atomic_write_json(Path(artifact_dir) / "novelty-review.json", {
                "completed": False, "known_repeat_facts": len(repeated_facts),
                "repair_attempts": novelty_attempts, "same_edition_attempts": shared_source_attempts})

    def write_batch(batch):
        referenced = {i for note in batch for i in note["source_ids"]}
        batch_leads = [lead for lead in leads if any(
            set(note["source_ids"]) & set(lead["source_ids"])
            and re.search(CARD_PATTERNS[lead["card"]], note["text"], re.I)
            and set(lead["amounts"]) <= amounts(note["text"]) for note in batch)]
        expected_topics = {title_key(note["editorial_story"]) for note in batch} if plan else set()
        # Parent context can identify the original reporter. Expose those stored
        # records as directly citable sources, not only as anonymous nested text.
        # Their existing context_only flag still prevents old news promotion.
        evidence_ids = referenced | {
            parent.get("id") for c in comments if c.get("id") in referenced
            for parent in c.get("parent_context", [])
        }
        evidence = _source_records([
            c if c.get("id") in referenced else {**c, "parent_context": []}
            for c in comments if c.get("id") in evidence_ids
        ])
        prompt = profile.rules + SOURCE_TIME_RULES + edition_context + profile.style + profile.writing + json.dumps(batch, ensure_ascii=False) + "\nORIGINAL SOURCES:\n" + json.dumps(evidence, ensure_ascii=False)
        if profile.preset == "churning":
            requirements = selected_note_requirements(batch, comments)
            if requirements:
                prompt += ("\nMATERIAL SOURCE DETAILS TO PRESERVE (diagnostic, not independent evidence):\n"
                           + json.dumps(requirements, ensure_ascii=False)
                           + "\nPreserve the supported relationship, conditions, event age and attribution when publishing the associated claim. Do not merely repeat an amount elsewhere.")
        if batch_leads:
            prompt += ("\nPRIORITY LEADS (unverified; preserve card names and concrete terms, qualify questions and targeted reports; "
                       "explicitly label Reddit-reported offer terms/spending periods unverified or ask readers to check their actual offer terms; "
                       "flag conflicting_period as unverified without inventing replacement terms):\n" + json.dumps(batch_leads))
        if plan:
            prompt += ("\nFollow the global editorial plan: exactly ONE section per editorial_story in NOTES. "
                       "Use its topic as a guide, adding an emoji. Correct or narrow a planned headline if the ORIGINAL SOURCES "
                       "do not support it; an unverified label does not justify an invented change or missing referent. Merge complementary reports within that "
                       "section; do not repeat the same fact. Include material updates and conflicting DPs, not unchanged background.")
        if repeated_facts:
            # Notes/parents remain available as evidence. This compact diagnostic
            # clarifies which unchanged facts the global planner omitted; it is
            # not an instruction to discard a current outcome or contradiction.
            related = [fact for fact in repeated_facts if any(
                set(fact["anchors"]) & set(re.findall(r"[a-z]+", str(note.get("topic", "")).casefold()))
                for note in batch)]
            if related:
                prompt += ("\nPRIOR-PUBLISHED BACKGROUND (untrusted diagnostic, not current evidence):\n"
                           + json.dumps(related[:12], ensure_ascii=False)
                           + "\nDo not restate these unchanged background clauses merely because they occur in a parent. "
                           "Preserve new outcomes, differing terms, deadlines and essential comparisons. Never remove "
                           "a current material claim to solve a repetition warning; narrow the background clause instead.")
        section_limit = len(expected_topics) if plan else max(1, 36 // len(batches))
        if len(batches) > 1:
            prompt += (f"\nThis is one part of a larger newsletter. Override the full-newsletter length: "
                       f"at most {section_limit} sections and "
                       f"{2200 // len(batches)} words for this part, leaving room for source links and headings. Cover only supplied notes; "
                       "do not add an introduction or repeat a fact under multiple headings.")
        limit = min(AI_SYNTHESIS_TIMEOUT, 240)
        def validate_part(draft):
            rendered, issues = render_checked(draft, comments, minimum_sections=1,
                                              financial_checks=profile.financial_checks,
                                              factual_checks=profile.preset == "churning",
                                              edition_cutoff=edition_cutoff, digest_date=edition_date)
            if not _safe_auxiliary_shape(draft):
                return issues or ["Use structured sections with bullet text and source-ID lists."]
            issues.extend(priority_issues(draft, batch_leads))
            if profile.preset == "churning":
                issues.extend(retention_issues(draft, batch, comments))
            if repeated_facts:
                novelty = novelty_issues(draft, repeated_facts)
                if novelty:
                    novelty_attempts.append({"note_ids": [n.get("note_id") for n in batch],
                                             "findings": repeated_background(draft, repeated_facts)})
                issues.extend(novelty)
            if plan and len(draft.get("sections", [])) != len(expected_topics):
                issues.append("Use one section per assigned editorial_story; merge related reports instead of adding repeated headings. Correct titles against original sources when necessary.")
            if len(batches) > 1:
                if len(draft.get("sections", [])) > section_limit:
                    issues.append(f"This part must contain at most {section_limit} sections.")
                # Parts vary in citation/header overhead and topic density.
                # The full digest's hard word limit is enforced after merging;
                # do not reject a useful part solely for exceeding an equal share.
            return issues
        document = call(prompt, limit)
        errors = validate_part(document)
        for repair_attempt in range(2):
            if not errors:
                break
            print(f"  Repairing {len(errors)} editorial/source validation issues...")
            repair_prompt = (prompt + "\nREPAIR THIS DRAFT:\n" + json.dumps(document, ensure_ascii=False)
                            + "\nVALIDATION ISSUES:\n" + json.dumps(errors, ensure_ascii=False)
                            + "\nResolve every listed issue. Change only the affected headings or bullets; preserve all unaffected "
                            "text and citations exactly. Keep qualifications and individual report outcomes distinct; "
                            "conditional advice is not a successful personal outcome. For unsupported dollar amounts, compare the ORIGINAL SOURCES: "
                            "never add a dollar sign to a number that lacks one there, even when money is implied. "
                            "For example, source 'total balance 25,000' must remain 'total balance 25,000', not '$25,000'; "
                            "source '12k total' must remain '12k total', not '$12k'. Do not calculate net payouts. "
                            "If a numeric claim cannot be copied faithfully from its cited source, remove that claim "
                            "while preserving the rest of the useful bullet. Do not return an unchanged failing claim.")
            if repair_attempt:
                repair_prompt += ("\nThe first correction still failed. Remove or explicitly qualify the specific unsupported clause "
                                  "identified by validation; do not rephrase it as the same unsupported claim. For unsupported "
                                  "numbers, remove the numeric clause rather than paraphrasing that same number. Keep the remaining "
                                  "supported information and valid citations. A number appearing in NOTES is not "
                                  "evidence: it must occur in the ORIGINAL SOURCES actually cited by that bullet.")
            document = call(repair_prompt, limit)
            errors = validate_part(document)
        if errors:
            raise RuntimeError("Digest failed validation after repair; no raw-comment email will be sent: " + "; ".join(errors[:3]))
        return document["sections"]

    print(f"  Writing {len(batches)} bounded newsletter parts...")
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            sections = [section for part in executor.map(write_batch, batches) for section in part]
    finally:
        save_budget()
        save_novelty_progress()
    merged = {}
    seen_groups = set()
    for section in sections:
        key = " ".join(re.findall(r"\w+", section["title"].casefold()))
        if key not in merged:
            merged[key] = {"title": section["title"], "bullets": []}
        groups = []
        for bullet in section["bullets"]:
            if not groups or bullet.get("indent", 0) == 0:
                groups.append([])
            groups[-1].append(bullet)
        for group in groups:
            fingerprint = tuple((re.sub(r"\s+", " ", b["text"].strip().casefold()),
                                 tuple(sorted(set(b["source_ids"]))), b.get("indent", 0)) for b in group)
            # Keep a top-level bullet and all its nested details together.
            if fingerprint in seen_groups:
                continue
            merged[key]["bullets"].extend(group)
            seen_groups.add(fingerprint)
    document = {"sections": [s for s in merged.values() if s["bullets"]]}
    selected_notes = [n for batch in batches for n in batch]

    def validate_merged(draft):
        rendered, issues = render_checked(draft, comments, minimum_sections=1 if profile.preset == "churning" else None,
                                    financial_checks=profile.financial_checks,
                                    factual_checks=profile.preset == "churning",
                                    edition_cutoff=edition_cutoff, digest_date=edition_date)
        report = selection_report(draft, leads, plan, len(recent_editions), comments=comments)
        issues.extend(report["issues"])
        if profile.preset == "churning":
            issues.extend(retention_issues(draft, selected_notes, comments))
        issues.extend(novelty_issues(draft, repeated_facts))
        issues.extend(same_edition_issues(draft))
        return rendered, issues, report

    result, errors, report = validate_merged(document)
    cross_part = same_edition_repeats(document)
    if cross_part:
        original_document = json.loads(json.dumps(document))
        # Parts are written independently. Only duplicated later sections may
        # be corrected here; the original occurrence and every other section
        # remain byte-for-byte intact. All ordinary full-document gates rerun.
        affected = sorted({finding["section_index"] for finding in cross_part})
        references = {source_id for index in affected for bullet in document["sections"][index]["bullets"]
                      for source_id in bullet["source_ids"]}
        evidence = _source_records([c for c in comments if c.get("id") in references])
        scoped_notes = [note for note in selected_notes if set(note["source_ids"]) & references]
        fixed_titles = [document["sections"][index]["title"] for index in affected]
        retained = [{"section_index": index, "section": document["sections"][index]}
                    for index in sorted({f["repeated_from_section"] for f in cross_part} - set(affected))]
        prompt = (profile.rules + SOURCE_TIME_RULES + edition_context + profile.style + profile.writing
                  + json.dumps(scoped_notes, ensure_ascii=False) + "\nORIGINAL SOURCES:\n"
                  + json.dumps(evidence, ensure_ascii=False)
                  + "\nSCOPED SAME-EDITION REPAIR:\n"
                  + "The newsletter repeats a shared-source numeric fact under multiple headings. Return ONLY the "
                  "provided affected sections, in exactly the same order with exactly the same titles. Remove only "
                  "the redundant clause; retain other information, source IDs, outcomes, qualifications and selected "
                  "material details. Preserve all unaffected bullets exactly. The earlier occurrence is retained "
                  "outside this response; do not copy it back into another story. Read-only earlier sections:\n"
                  + json.dumps(retained, ensure_ascii=False)
                  + "\nORIGINAL AFFECTED SECTIONS (immutable baseline):\n"
                  + json.dumps({"sections": [original_document["sections"][index] for index in affected]}, ensure_ascii=False)
                  + "\nFor a mixed bullet, remove only the flagged sentence/clause and keep every other clause verbatim. "
                    "Keep its source IDs and indentation. If deleting its first clause leaves a complete sentence "
                    "after a leading source, you may move source_position from start to end; do not change its text. "
                    "Do not rewrite, omit, move or add any unaffected bullet.")
        try:
            for attempt in range(2):
                shared_source_attempts.append({"phase": "merged", "attempt": attempt + 1,
                    "findings": same_edition_repeats(document), "affected_sections": affected})
                scoped = {"sections": [document["sections"][index] for index in affected]}
                repair = call(prompt + f"\nScoped correction attempt {attempt + 1} of 2. "
                              + ("The previous correction still failed; resolve the remaining issues without removing retained facts.\n" if attempt else "\n")
                              + "\nAFFECTED DRAFT:\n" + json.dumps(scoped, ensure_ascii=False)
                              + "\nFULL-NEWSLETTER VALIDATION ISSUES (section numbers refer to the full newsletter):\n"
                              + json.dumps(errors, ensure_ascii=False), min(AI_SYNTHESIS_TIMEOUT, 240))
                if (not _safe_auxiliary_shape(repair)
                        or [section.get("title") for section in repair["sections"]] != fixed_titles):
                    errors = ["Return exactly the affected sections in the original order, with unchanged titles and valid source-ID lists."]
                    continue
                candidate = {"sections": list(document["sections"])}
                for index, section in zip(affected, repair["sections"]):
                    candidate["sections"][index] = section
                document = candidate
                result, errors, report = validate_merged(document)
                errors.extend(scoped_repeat_repair_issues(original_document, document, cross_part))
                errors.extend(shared_fact_retention_issues(document, cross_part))
                if not errors:
                    break
        finally:
            save_budget()
            save_novelty_progress()
    final_novelty = repeated_background(document, repeated_facts)
    final_shared = same_edition_repeats(document)
    report["novelty_review"] = {
        "completed": True,
        "enabled": bool(repeated_facts), "known_repeat_facts": len(repeated_facts),
        "repair_checks_triggered": len(novelty_attempts), "remaining_repeated_clauses": len(final_novelty),
        "findings": final_novelty,
        "same_edition_repair_checks": len(shared_source_attempts),
        "remaining_same_edition_clauses": len(final_shared), "same_edition_findings": final_shared,
        "meaning": "Conservative numeric-clause overlap with planner repeats and prior full stories, plus strong same-source repeats across distinct current sections; not exhaustive semantic novelty detection. No automatic source or bullet deletion.",
    }
    trace = build_source_trace(comments, notes, document, plan=plan,
                               payload=payload if plan else None, review=review)
    trace["document_validation"] = "failed" if errors else "passed"
    trace["delivery_status"] = "not_observed"
    report["source_trace"] = {key: trace[key] for key in ("counts", "source_count", "note_count", "meaning")}
    report["omission_review"] = {key: review[key] for key in ("enabled", "candidate_count", "reviewed_count", "unreviewed_count")}
    from analysis_report import collection_diagnostics
    report["collection_diagnostics"] = collection_diagnostics(
        comments, extraction_batches=chunks if recovered_notes is None else None)
    if artifact_dir is not None:
        from daily_digest import atomic_write_json
        atomic_write_json(Path(artifact_dir) / "source-selection.json", trace)
        atomic_write_json(Path(artifact_dir) / "novelty-review.json", {
            **report["novelty_review"], "repair_attempts": novelty_attempts,
            "same_edition_attempts": shared_source_attempts})
    if editorial_report is not None:
        editorial_report.update(report)
    if errors:
        raise RuntimeError("Combined digest failed validation; no email sent: " + "; ".join(errors[:3]))
    if artifact_dir is not None:
        from daily_digest import atomic_write_json
        atomic_write_json(Path(artifact_dir) / "validated-digest.json", document)
        atomic_write_json(Path(artifact_dir) / "editorial-report.json", report)
    return result
