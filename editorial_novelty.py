"""Conservative checks for old numeric background leaking through parent evidence.

These diagnostics do not decide which news is useful and never modify a document.
They require both an explicit planner repeat decision and matching delivered
history. Ambiguous overlap and clauses describing a new outcome are left alone.
"""

import re
from decimal import Decimal

_STOP = set("a an and are as at be been being by for from had has have in into is it its of on or that the their them they this to was were which with would reported reports report says say notes noted lists list source commenter user reddit describes describe offering k m d x".split())
_GENERIC_TOPIC = _STOP | set("card cards business personal rewards reward welcome offer offers bonus points point miles mile annual fee spend spending terms refreshed refresh new old prior previous changes change already delivered bank america visa mastercard targeted public legacy separate link links availability application applications approval approvals denial denials outcome outcomes".split())
_NUMERIC = re.compile(r"(?<![\w])\$?(\d[\d,]*(?:\.\d+)?)([kKmM]?)(?![\w])")
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")
_UPDATE = re.compile(
    r"\b(?:approved|approvals?|denied|denials?|declined|pending|applied|reconsideration|recon|"
    r"payouts?|posted|credited|received|refunded|clawbacks?|clawed|closed|shutdown|shut down|"
    r"rejected|failed|stopped|unavailable|expired|expires|ends|ending|deadline|"
    r"increased|decreased|raised|lowered|no longer|returned|reappeared|extended|"
    r"conflicts?|contradicts?|disputes?|contrary|versus|vs|compared|rather than|instead of)\b", re.I)
_QUALITATIVE_CHANGE = re.compile(
    r"\b(?:now|newly|formerly|previously|first[- ]time|first time|became|becomes|"
    r"expanded|narrowed|restricted to|limited to|opened to|available to|eligible for|"
    r"eligible to|ineligible|requires?|must|except|excluding|not available|not eligible)\b", re.I)
_SCOPE_FEATURES = tuple(re.compile(pattern, re.I) for pattern in (
    r"\b(?:public|publicly|everyone|anyone|all applicants)\b",
    r"\b(?:targeted|invitation[- ]only|invite[- ]only|by invitation)\b",
    r"\breferrals?\b", r"\b(?:in[- ]branch|branch[- ]only|branches)\b",
    r"\b(?:online|web[- ]only|website)\b", r"\b(?:mailers?|mailed|postal)\b",
    r"\b(?:existing|current) (?:customers?|cardholders?|accounts?)\b",
    r"\b(?:new customers?|new cardholders?|first[- ]time)\b",
    r"\b(?:sole[- ]prop|sole proprietors?|incorporated|corporations?|llcs?)\b",
    r"\b(?:eligible|eligibility|qualif(?:y|ies|ied))\b",
))
_SPLIT = re.compile(r"(?<=[.!?;])\s+|[\r\n]+")


def _words(text):
    return set(_WORD.findall(text.casefold())) - _STOP


def _number_sequence(text):
    values = []
    # Multipliers and compact day suffixes remain numeric anchors as well.
    text = re.sub(r"(?<=\d)(?=[xXdD]\b)", " ", text)
    for match in _NUMERIC.finditer(text):
        value = Decimal(match[1].replace(",", ""))
        value *= {"k": 1000, "m": 1000000}.get(match[2].lower(), 1)
        values.append(format(value.normalize(), "f"))
    return values


def _numbers(text):
    return set(_number_sequence(text))


def _same_order(candidate, previous):
    """Do not mistake swapped bonus/spend quantities for unchanged terms."""
    remaining = iter(previous)
    return all(any(value == old for old in remaining) for value in candidate)


def _plain(text):
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", str(text))
    return text.replace("*", "").replace("_", "")


def _overlap(candidate, other, *, numeric_count=2):
    shared = candidate & other
    # Three distinct matching quantities are stronger anchors than a single
    # price/spend pair; allow more wording variation, not different quantities.
    minimum, ratio = (4, 0.60) if numeric_count >= 3 else (6, 0.70)
    return len(shared) >= minimum and len(shared) / max(1, len(candidate)) >= ratio


def _same_topic(anchors, words):
    # Two shared family words (e.g. "Chase Ink") cannot establish that Cash,
    # Preferred and Unlimited are the same product. Ambiguous/abbreviated
    # matches may retain some background; they must not remove valid new news.
    return bool(anchors) and anchors <= words


def _card_scopes(text, anchors):
    """Scope beside the product name, not a personal/business account ratio."""
    words = [word for word in _WORD.findall(text.casefold()) if word not in _STOP]
    product = set(anchors) - {"business", "personal"}
    return {word for index, word in enumerate(words) if word in {"business", "personal"}
            and set(words[max(0, index-1):index] + words[index+1:index+2]) & product}


def _scope_change(candidate, previous, *, anchors=()):
    """Changed audience/channel is material even when every number is unchanged.

    New qualifiers are ambiguous, not proof of a repeat. Prefer leaving them for
    the normal factual/retention checks over forcing a destructive correction.
    """
    return bool(_card_scopes(candidate, anchors) - _card_scopes(previous, anchors)
                or _QUALITATIVE_CHANGE.search(candidate) or any(
        pattern.search(candidate) and not pattern.search(previous) for pattern in _SCOPE_FEATURES))


def repeat_fact_context(plan, payload, history, *, maximum=80):
    """Return bounded prior numeric facts explicitly excluded by the planner.

    History is used only as evidence of prior publication, never as evidence of
    current offer truth. Low-value decisions and compact history indexes cannot
    establish a repeat. No matching full story means no blocking fact.
    """
    if not plan or not payload or not history:
        return []
    notes = {n["note_id"]: n for n in payload.get("notes", [])}
    selected = [notes[n] for story in plan.get("stories", [])
                if story.get("status") in {"new", "update", "deadline"}
                for n in story.get("note_ids", []) if n in notes]
    prior = [(_words(_plain(item)), _number_sequence(_plain(item)), edition.get("date"), _plain(item))
             for edition in history for item in edition.get("items", []) if isinstance(item, str)]
    facts, seen = [], set()
    for story in plan.get("stories", []):
        if story.get("status") != "repeat":
            continue
        for identifier in story.get("note_ids", []):
            note = notes.get(identifier, {})
            anchors = _words(str(note.get("topic", ""))) - (_GENERIC_TOPIC - {"business", "personal"})
            for clause in _SPLIT.split(_plain(note.get("text", ""))):
                words, numbers = _words(clause), _numbers(clause)
                # Single prices/ratios are too weak to identify a repeated fact.
                if len(numbers) < 2 or len(words) < 5 or _UPDATE.search(clause):
                    continue
                match = next((date for previous, values, date, previous_text in prior
                              if numbers <= set(values) and _same_order(_number_sequence(clause), values)
                              and _same_topic(anchors, previous)
                              and not _scope_change(clause, previous_text, anchors=anchors)
                              and _overlap(words, previous, numeric_count=len(numbers))), False)
                if match is False:
                    continue
                fingerprint = (tuple(sorted(words)), tuple(_number_sequence(clause)))
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                # A selected note may establish a changed scope in a separate
                # sentence from its still-identical terms. Preserve those terms
                # when the writer cites that explicit update; parent evidence
                # alone does not receive this exemption.
                material_sources = sorted({source_id for current in selected
                    if _same_topic(anchors, _words(str(current.get("topic", "")) + " " + current.get("text", "")))
                    and _QUALITATIVE_CHANGE.search(current.get("text", ""))
                    and (not _numbers(current.get("text", "")) or numbers <= _numbers(current["text"]))
                    for source_id in current.get("source_ids", [])})
                facts.append({"note_id": identifier, "text": clause.strip(),
                              "topic": note.get("topic", ""), "source_ids": note.get("source_ids", []),
                              "previous_edition": match, "anchors": sorted(anchors),
                              "material_update_source_ids": material_sources})
                if len(facts) >= maximum:
                    return facts
    return facts


def repeated_background(document, facts):
    """Locate high-confidence repeated clauses; do not remove or rewrite them."""
    findings = []
    for section_index, section in enumerate(document.get("sections", [])):
        for bullet_index, bullet in enumerate(section.get("bullets", [])):
            for clause in _SPLIT.split(_plain(bullet.get("text", ""))):
                words, numbers = _words(clause), _numbers(clause)
                if len(numbers) < 2 or len(words) < 5 or _UPDATE.search(clause):
                    continue
                heading_words = _words(str(section.get("title", "")))
                for fact in facts:
                    anchors = set(fact["anchors"])
                    # A heading may supply an omitted product name, but cannot
                    # turn a clause explicitly naming another family member
                    # into this old product's claim.
                    identity = words if anchors & words else heading_words | words
                    # A numeric coincidence without a recognisable topic is not
                    # a safe reason to alter an otherwise supported statement.
                    if not _same_topic(anchors, identity):
                        continue
                    if (_scope_change(str(section.get("title", "")) + " " + _plain(bullet.get("text", "")),
                                      str(fact.get("topic", "")) + " " + fact["text"], anchors=anchors)
                            or set(bullet.get("source_ids", [])) & set(fact.get("material_update_source_ids", []))):
                        continue
                    if (numbers <= _numbers(fact["text"])
                            and _same_order(_number_sequence(clause), _number_sequence(fact["text"]))
                            and _overlap(words, _words(fact["text"]), numeric_count=len(numbers))):
                        findings.append({"section_index": section_index, "bullet_index": bullet_index,
                                         "note_id": fact["note_id"], "clause": clause.strip(),
                                         "previous_edition": fact["previous_edition"]})
                        break
    return findings


def novelty_issues(document, facts):
    """Bound correction text while preserving exact clause locations for repair."""
    findings = repeated_background(document, facts)
    return [
        f"Section {f['section_index'] + 1}, bullet {f['bullet_index'] + 1}: "
        f"unchanged background from planner-repeat note {f['note_id']} was already published. "
        f"Remove this repeated clause, not the current update: {f['clause'][:600]} "
        "Preserve current outcomes, contradictions, deadlines and materially necessary offer details with their sources."
        for f in findings[:12]
    ]


_IDENTITY_GENERIC = _GENERIC_TOPIC | set(
    "the a an this these those targeted holiday shopping holders cardholders can lets earn earns "
    "after within everyday purchases purchase statement credit credits redemption redemptions "
    "get gets up more value standard depending each per days months year first reported reports "
    "bonus points spending offer program promotion promo terms its their same offers "
    "january february march april may june july august september october november december "
    "jan feb mar apr jun jul aug sep sept oct nov dec".split())
_PARAPHRASE_NOISE = set("holders cardholders card cards can lets holiday shopping offer offers spending spend".split())


def _clause_identity(text):
    # Require an explicit name in BOTH clauses. Titles alone cannot turn a
    # generic reward statement about a different card into the same product.
    # Missing/ambiguous names deliberately produce no blocking diagnosis.
    names = {word.casefold() for word in re.findall(r"\b[A-Z][A-Za-z]+\b", text)} - _IDENTITY_GENERIC
    if not names:
        return set()
    # Preserve lower-case card-family modifiers and personal/business scope.
    modifiers = set("preferred unlimited reserve cash obsidian palladium blue ascent summit platinum gold premier signature infinite flex savor venture aspire surpass altitude autograph freedom strata custom double".split())
    return names | (_words(text) & modifiers) | _card_scopes(text, names)


def same_edition_repeats(document, *, maximum=24):
    """Flag only strong, shared-evidence numeric repeats across distinct stories.

    Exact source sets preserve independent corroborating reports. Matching
    ordered quantities, explicit product names and qualitative scope avoid
    conflating different cards/offers. No text is removed by this diagnostic.
    """
    previous, findings = [], []
    for section_index, section in enumerate(document.get("sections", [])):
        heading = _plain(section.get("title", "")).casefold()
        bullets = section.get("bullets", [])
        for bullet_index, bullet in enumerate(bullets):
            # A parent can be necessary context for a unique nested qualifier.
            # Removing it would silently attach its children to a different
            # parent (or leave an orphan), even if their text stayed exact.
            if (bullet.get("indent", 0) == 0 and bullet_index + 1 < len(bullets)
                    and bullets[bullet_index + 1].get("indent", 0) == 1):
                continue
            body = _plain(bullet.get("text", ""))
            sources = frozenset(bullet.get("source_ids", []))
            if not sources or _UPDATE.search(body) or _QUALITATIVE_CHANGE.search(body):
                continue
            for clause in _SPLIT.split(body):
                words, quantities = _words(clause), _number_sequence(clause)
                identity = _clause_identity(clause)
                # A generic cardholder clause may inherit ONE unambiguous
                # product identity from its own heading. Multi-product/family
                # headings and clauses explicitly naming another product do
                # not receive this fallback.
                if not identity and re.search(r"\bcardholders?\b", clause, re.I):
                    heading_identity = _clause_identity(_plain(section.get("title", "")))
                    if len(heading_identity) == 1:
                        identity = heading_identity
                words = {"earn" if word in {"earns", "awards"} else word for word in words} | identity
                if len(set(quantities)) < 2 or len(words) < 6 or not identity:
                    continue
                money_slots = tuple(match.group().startswith("$") for match in _NUMERIC.finditer(
                    re.sub(r"(?<=\d)(?=[xXdD]\b)", " ", clause)))
                current = {"section_index": section_index, "bullet_index": bullet_index,
                           "clause": clause.strip(), "source_ids": sorted(sources)}
                for old, old_heading, old_sources, old_words, old_quantities, old_identity, old_body, old_money in previous:
                    if (old["section_index"] == section_index or heading == old_heading
                            or sources != old_sources or quantities != old_quantities or money_slots != old_money
                            or identity != old_identity):
                        continue
                    if _scope_change(body, old_body, anchors=identity) or _scope_change(old_body, body, anchors=identity):
                        continue
                    shared = words & old_words
                    if (not (words ^ old_words) - _PARAPHRASE_NOISE
                            and len(shared) >= 6 and len(shared) / min(len(words), len(old_words)) >= 0.8):
                        findings.append({**current, "repeated_from_section": old["section_index"],
                                         "repeated_from_bullet": old["bullet_index"],
                                         "retained_clause": old["clause"]})
                        break
                previous.append((current, heading, sources, words, quantities, identity, body, money_slots))
                if len(findings) >= maximum:
                    return findings
    return findings


def same_edition_issues(document):
    return [
        f"Section {f['section_index'] + 1}, bullet {f['bullet_index'] + 1}: same-edition shared-source "
        f"numeric clause repeats section {f['repeated_from_section'] + 1}, bullet {f['repeated_from_bullet'] + 1}: "
        f"{f['clause'][:600]} Keep the complete fact in its own relevant story once. "
        "Remove only the redundant clause from the other story; retain every distinct outcome, qualification, "
        "deadline, source attribution and material selected detail. Do not delete an entire mixed bullet."
        for f in same_edition_repeats(document)
    ]


def shared_fact_retention_issues(document, findings):
    """A scoped duplicate repair must keep at least one original supported fact."""
    bullets = [bullet for section in document.get("sections", []) for bullet in section.get("bullets", [])]
    return ["Scoped repetition repair lost every original occurrence of this supported fact: "
            + finding["retained_clause"][:600] + " Retain the original occurrence and its source IDs in one story."
            for finding in findings if not any(
                set(bullet.get("source_ids", [])) == set(finding["source_ids"])
                and finding["retained_clause"] in _plain(bullet.get("text", "")) for bullet in bullets)]


def scoped_repeat_repair_issues(original, candidate, findings):
    """Permit duplicate-clause deletion only, preserving all other source text.

    The original has already passed the ordinary part validation. Unaffected
    bullets remain exact, and a mixed bullet must retain its other clauses and
    source IDs. Moving a leading citation to the end is allowed only after its
    first clause is removed, so the remaining complete sentence stays grammatical.
    This validates a model response; it never edits or deletes text itself.
    """
    before, after = original.get("sections", []), candidate.get("sections", [])
    if len(before) != len(after) or [s.get("title") for s in before] != [s.get("title") for s in after]:
        return ["Scoped repetition repair must preserve every original section and its title."]
    flagged = {}
    for finding in findings:
        flagged.setdefault((finding["section_index"], finding["bullet_index"]), set()).add(finding["clause"])
    issues = []
    for section_index, (old_section, new_section) in enumerate(zip(before, after)):
        proposed = new_section.get("bullets", [])
        cursor = 0
        for bullet_index, old in enumerate(old_section.get("bullets", [])):
            removed = flagged.get((section_index, bullet_index), set())
            new = proposed[cursor] if cursor < len(proposed) else None
            if not removed:
                if new != old:
                    issues.append(f"Section {section_index + 1}, original bullet {bullet_index + 1}: preserve this unaffected bullet exactly, including text, sources and order.")
                cursor += 1
                continue
            clauses = [clause.strip() for clause in _SPLIT.split(old["text"]) if clause.strip()]
            remaining = [clause for clause in clauses if _plain(clause).strip() not in removed]
            if not remaining:
                # An entirely duplicate bullet may be removed. An unchanged
                # occurrence is still detected by the ordinary duplicate gate.
                if new == old:
                    cursor += 1
                continue
            old_meta = {key: value for key, value in old.items() if key != "text"}
            new_meta = {key: value for key, value in (new or {}).items() if key != "text"}
            first_removed = _plain(clauses[0]).strip() in removed
            if first_removed and old_meta.get("source_position") == "start" and new_meta.get("source_position") == "end":
                new_meta["source_position"] = "start"
            retained_text = " ".join(remaining)
            if (not new or old_meta != new_meta
                    or new.get("text", "").strip() not in {old["text"].strip(), retained_text}):
                issues.append(f"Section {section_index + 1}, original bullet {bullet_index + 1}: remove only the flagged duplicate clause; retain every other clause verbatim, its source IDs and nesting. Required remaining text: {retained_text}")
            cursor += 1
        if cursor != len(proposed):
            issues.append(f"Section {section_index + 1}: scoped repetition repair may not add, reorder or replace other bullets.")
    return issues
