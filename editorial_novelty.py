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
