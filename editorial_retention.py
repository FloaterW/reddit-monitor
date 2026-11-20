"""Bounded candidates and material-fact retention, not exhaustive news recall.

Candidates are requests for editorial review, never automatically publishable
news. Requirements bind selected facts to their actual supporting source rather
than requiring every number, source or disclaimer to appear in the newsletter.
"""

import re
from decimal import Decimal

_NUMBER = r"\$?\d[\d,]*(?:\.\d+)?[kK]?"
_FIRST_PERSON = re.compile(r"\b(?:I|my|we|our|P[12])\b", re.I)
_AGE = re.compile(
    r"\b(?:last\s+(?:week|month|year)|(?:(?:a\s+)?(?:couple(?:\s+of)?|few|several)|one|two|three|\d+|a|an)\s+"
    r"(?:days?|weeks?|months?|years?)\s+(?:ago|earlier|previously))\b", re.I)
_PC_EVENT = re.compile(r"\b(?:converted|downgraded|upgraded|PC['’]?(?:ed|d)|product[- ]changed)\b", re.I)
_PC_BLOCK = re.compile(r"\bno\s+(?:PC|product[- ]change|conversion)\s+options?\b|"
                       r"\b(?:unable|cannot|couldn['’]t|can['’]t|refused|blocked)\b.{0,40}\b(?:PC|convert|conversion|product[- ]change)\b", re.I)
_NEGATION = re.compile(r"\b(?:not|never|no|without|neither)\b|n['’]t\b", re.I)
_SPEND_AFTER_AMOUNT = re.compile(rf"(?<!\w)({_NUMBER})\s+(?:in\s+)?(?:spend(?:ing)?|purchases?)\b", re.I)
_SPEND_BEFORE_AMOUNT = re.compile(
    rf"\b(?:spend(?:ing)?(?:\s+(?:requirement|threshold|minimum))?|minimum\s+spend(?:ing)?)"
    rf"\s*(?:(?:of|is|was|at least|a total of|spending|spend)\s+)?({_NUMBER})(?!\w)", re.I)
_BENEFITS = {
    "flight credit/certificate": re.compile(r"\b(?:flight|airfare)\s+(?:credit|certificate)s?\b", re.I),
    "companion certificate": re.compile(r"\bcompanion\s+(?:certificate|pass|ticket)s?\b", re.I),
    "hotel credit/certificate": re.compile(r"\bhotel\s+(?:credit|certificate)s?\b|\bfree[- ]night\s+certificate\b", re.I),
    "welcome bonus": re.compile(r"\b(?:welcome|sign[- ]?up|new[- ]cardmember)\s+(?:bonus|offer)\b|\bSUB\b", re.I),
    "credit or bonus": re.compile(r"\b(?:credit|certificate|bonus|cash\s*back)\b", re.I),
}


def _plain(text):
    return str(text).replace("*", "").replace("`", "").replace("’", "'")


def _number(value):
    raw = value.replace("$", "").replace(",", "").lower()
    number = Decimal(raw[:-1]) * 1000 if raw.endswith("k") else Decimal(raw)
    return format(number.normalize(), "f")


def _sentences(text):
    return [part.strip() for part in re.split(r"(?<=[!?])\s*|\n|\.(?:\s|$)", _plain(text)) if part.strip()]


def _spend_matches(text):
    """Yield explicit, non-negated spend quantities with their locations."""
    plain = _plain(text)
    for pattern in (_SPEND_AFTER_AMOUNT, _SPEND_BEFORE_AMOUNT):
        for match in pattern.finditer(plain):
            prefix = re.split(r"[.;!?]|\bbut\b", plain[:match.start()], flags=re.I)[-1]
            if _NEGATION.search(" ".join(prefix.split()[-3:])):
                continue
            if re.match(r"\s+(?:(?:is|was)\s+)?(?:not|never)\s+(?:required|needed)\b", plain[match.end():], re.I):
                continue
            yield match


def _spend_values(text):
    """Only explicit spend quantities, not an arbitrary matching amount."""
    return list(dict.fromkeys((_number(m.group(1)), m.group(1)) for m in _spend_matches(text)))


def _qualification_facts(body):
    facts = []
    for sentence in _sentences(body):
        if sentence.endswith("?") or re.match(r"\s*(?:does|would|could|can|is|are)\b", sentence, re.I):
            continue  # A question cannot establish a qualifying condition.
        if re.search(r"\b(?:not sure|I think|I believe|I guess|maybe|perhaps|might|IIRC)\b", sentence, re.I):
            continue  # Ambiguous conditions stay with editorial/source review.
        # Clause-local links prevent a spend condition for a companion benefit
        # from being attached to an unrelated flight credit in the same comment.
        for clause in re.split(r";|\b(?:but|whereas|while)\b", sentence, flags=re.I):
            benefits = [(name, match) for name, pattern in _BENEFITS.items()
                        for match in pattern.finditer(clause)]
            # Generic 'credit' must not replace the more specific flight credit.
            benefits = [(name, match) for name, match in benefits
                        if name != "credit or bonus" or not any(
                            other != name and specific.start() <= match.start() < specific.end()
                            for other, specific in benefits)]
            for spend in _spend_matches(clause):
                links = []
                for name, benefit in benefits:
                    if spend.end() <= benefit.start():
                        bridge = clause[spend.end():benefit.start()]
                        explicit = re.search(r"\b(?:for|earns?|earned|unlocks?|unlocked|to (?:earn|get|receive|qualify(?: for)?))\b", bridge, re.I)
                    elif benefit.end() <= spend.start():
                        bridge = clause[benefit.end():spend.start()]
                        explicit = re.search(r"\b(?:requires?|needs?|after|for|with|(?:subject|tied|linked) to)\b", bridge, re.I)
                        explicit = explicit or re.match(r"\s+(?:requirement|threshold|minimum)\b", clause[spend.end():], re.I)
                    else:
                        continue
                    # Do not jump across another named benefit to infer a link.
                    crosses = any(other.start() >= min(spend.end(), benefit.end())
                                  and other.end() <= max(spend.start(), benefit.start())
                                  for _, other in benefits if other != benefit)
                    if explicit and len(bridge) <= 80 and not crosses and not _NEGATION.search(bridge):
                        term = benefit.group().casefold()
                        if name == "credit or bonus" and term == "bonus":
                            modifier = re.search(r"\b(retention|upgrade|referral|anniversary)\s+$",
                                                 clause[:benefit.start()], re.I)
                            if modifier:
                                term = modifier.group(1).casefold() + " bonus"
                        links.append((len(bridge), name, term))
                if links:
                    _, benefit, term = min(links, key=lambda link: (link[0], link[1]))
                    original = spend.group(1)
                    fact = {"amount": _number(original), "source_amount": original,
                            "benefit": benefit, "evidence": clause.strip()[:1000]}
                    if benefit == "credit or bonus":
                        fact["benefit_term"] = term
                    facts.append(fact)
    return facts


def _bonus_alias(fact):
    return (fact["benefit"] == "welcome bonus"
            or (fact["benefit"] == "credit or bonus" and fact.get("benefit_term") == "bonus"))


def _same_qualifying_benefit(left, right):
    # Preserve existing benefit categories and matches. Only bridge the known
    # bonus/welcome-bonus mismatch when the generic match was literally bonus,
    # not a credit/certificate or an explicitly different bonus subtype.
    return left["benefit"] == right["benefit"] or (_bonus_alias(left) and _bonus_alias(right))


def _token(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _product_change_facts(body):
    """Extract explicit successful changes contrasted with blocked options."""
    if not _PC_BLOCK.search(body):
        return []
    result = []
    for sentence in _sentences(body):
        event = _PC_EVENT.search(sentence)
        if not event or not _FIRST_PERSON.search(sentence):
            continue
        prefix = sentence[:event.start()]
        if _NEGATION.search(" ".join(prefix.split()[-4:])):
            continue
        destination = re.search(r"\bto\s+(?:(?:a|an|the)\s+)?(.+?)(?=\s+(?:via|with|without|last)\b|$)", sentence[event.end():], re.I)
        if not destination:
            continue
        terms = re.findall(r"[A-Za-z][A-Za-z0-9&'-]*", destination.group(1))
        terms = [term for term in terms if term.casefold() not in {
            "a", "an", "the", "no", "annual", "fee", "card", "credit", "free", "zero", "account",
        }]
        if not terms or len(terms) > 6:
            continue
        excluded = re.search(r"\b(?:not\s+(?:(?:through|from|via)\s+)?|non[- ])([A-Z][A-Za-z']+)\b", sentence)
        origin = re.search(r"^\s+(?:(?:a|an|the)\s+)?([A-Z][A-Za-z]+)", sentence[event.end():])
        age = _AGE.search(sentence)
        result.append({"destination_terms": terms,
                       "excluded_origin": excluded.group(1) if excluded else None,
                       "origin_issuer": origin.group(1) if origin else None,
                       "event_age": age.group() if age else None,
                       "evidence": sentence[:1200]})
    return result


def _reward_currencies(text):
    currencies = []
    for name, pattern in (("cash rewards", r"\bcash\s*back\b|\brewards?\s+cash\b|\bcash\s+rewards?\b|\brewards?\s+(?:remain\w*|stay\w*)\s+cash\b|\bcash\b(?=\s+(?:rather than|instead of|not)\s+(?:miles|points))"),
                          ("miles", r"\bmiles\b"), ("points", r"\bpoints\b|\b(?:MR|UR)\b")):
        if re.search(pattern, text, re.I):
            currencies.append(name)
    return currencies


def _reward_conversion_failure(body):
    if len(_reward_currencies(body)) < 2:
        return False
    failure = re.compile(r"\b(?:not|never|no|didn't|wasn't|weren't|hasn't|haven't|cannot|can't)\s+"
                          r"(?:(?:been|be|being|successfully|automatically|actually|yet|fully|able to|get|getting|a)\s+){0,4}"
                          r"(?:convert\w*|conversion|transfer\w*|become|became|turn\w*|chang\w*)\b|"
                          r"\b(?:cash\s*back|rewards?\s+cash|cash\s+rewards?|miles|points)\b.{0,45}\b(?:remain\w*|kept|stay\w*)\s+separate\b|"
                          r"\bseparate\b.{0,45}\b(?:cash|miles|points)\b|"
                          r"\b(?:remain\w*|stay\w*)\s+(?:as\s+)?cash\b.{0,30}\b(?:rather than|instead of|not)\s+miles\b", re.I)
    # Currency mentions elsewhere in a long product-change report cannot turn
    # an unchanged card number (or another unrelated attribute) into a failed
    # rewards conversion. Require local currency evidence for the predicate.
    return any(_reward_currencies(clause) and failure.search(clause)
               for sentence in _sentences(body)
               for clause in re.split(r";|,|\b(?:but|however|whereas)\b", sentence, flags=re.I))


def _older_offer_facts(body):
    result = []
    for sentence in _sentences(body):
        age = _AGE.search(sentence)
        if not age or not re.search(r"\b(?:offer|bonus|promotion|SUB)\b", sentence, re.I):
            continue
        quantities = {_number(m.group()) for m in re.finditer(rf"(?<!\w){_NUMBER}(?!\w)", sentence)
                      if "$" in m.group() or m.group().lower().endswith("k")}
        result.append({"event_age": age.group(), "quantities": sorted(quantities), "evidence": sentence[:1200]})
    return result


def identify_candidates(comments, limit=24):
    """Return bounded review candidates; speculative matches are not mandates."""
    if limit < 0:
        raise ValueError("Candidate limit must not be negative")
    candidates = []
    seen = set()
    from editorial_selection import priority_leads
    watchlist = {}
    for lead in priority_leads(comments):
        # Mandatory leads already have deterministic recovery and planner/
        # writer recall checks. Spend the omission-review allowance on newly
        # recognized optional leads instead of reviewing those facts twice.
        if not lead.get("review_only"):
            continue
        for sid in lead["source_ids"]:
            watchlist.setdefault(sid, []).append(lead)
    for index, row in enumerate(comments):
        sid, body = row.get("id"), _plain(row.get("body", ""))
        if not isinstance(sid, str) or sid in seen or row.get("context_only"):
            continue
        seen.add(sid)
        reasons, categories, priority = [], [], 4
        if sid in watchlist:
            categories.append("watchlist_offer_or_outcome")
            reasons.append("Concrete Ink/Chase business-card terms or an application outcome. Explicitly assess whether the source adds news, an unresolved question or only a routine recommendation; recognition does not mandate publication.")
            priority = min(priority, 2)
        if manufactured_spending_signal(body):
            categories.append("manufactured_spending_operation")
            reasons.append("Explicit manufactured-spending or buying-group operational signal. Review the actual outcome/terms and distinguish a firsthand event from a question; merchant names alone are not evidence.")
            priority = min(priority, 1)
        if _FIRST_PERSON.search(body) and _reward_conversion_failure(body):
            categories.append("rewards_conversion")
            reasons.append("Firsthand rewards conversion/non-conversion or separate-balance outcome; preserve the observed direction and limits.")
            priority = 0
        if _product_change_facts(body):
            categories.append("contrasting_product_change")
            reasons.append("Successful product change contrasts with unavailable options; preserve which product/origin had each outcome.")
            priority = min(priority, 0)
        if (re.search(r"\b(?:account|transfers?|payments?|reloads?|microdeposits?)\b", body, re.I)
                and re.search(r"\b(?:suspend\w*|frozen|freeze|blocked|failed|stopped|cutoff|unable)\b", body, re.I)
                and (_FIRST_PERSON.search(body) or re.search(r"\bmicrodeposits?\b", body, re.I))):
            categories.append("operational_restriction")
            reasons.append("Concrete account/payment/transfer restriction or operational warning; assess whether this is a new DP rather than general advice.")
            priority = min(priority, 1)
        if _qualification_facts(body):
            categories.append("spend_qualification")
            reasons.append("Explicit qualifying spend attached to a benefit; check whether a selected story lost its condition.")
            priority = min(priority, 2)
        if _older_offer_facts(body):
            categories.append("older_offer")
            reasons.append("Offer report supplies historical age; preserve age instead of presenting it as newly available.")
            priority = min(priority, 3)
        if reasons:
            candidates.append((priority, index, {"source_id": sid, "categories": categories, "reasons": reasons}))
    candidates.sort(key=lambda value: value[:2])
    return [candidate for _, _, candidate in candidates[:limit]]


def manufactured_spending_signal(body):
    """Narrow MS operational leads; Amazon/Prime/Best Buy alone never qualify."""
    explicit = re.search(r"\b(?:manufactured\s+spend(?:ing)?|buying\s+groups?|BFMR|buy\s+for\s+me\s+retail|VGC|MCGC)\b", body, re.I)
    shorthand = re.search(r"\bMS\b", body) and re.search(r"\b(?:gift\s+cards?|liquidat\w*|money\s+orders?|funding|cash\s+advance|reload\w*)\b", body, re.I)
    concrete = re.search(r"\b(?:declin\w*|cancel\w*|refund\w*|paid|payment\w*|payout\w*|frozen|freez\w*|shutdown|shut\s+down|delay\w*|changed?|limits?|fees?|returned?)\b", body, re.I)
    return bool((explicit or shorthand) and concrete)


def selected_note_requirements(notes, comments):
    """Protect narrow material facts only for sources attached to selected notes."""
    by_id = {row.get("id"): row for row in comments if isinstance(row.get("id"), str)}
    requirements = []
    seen = set()
    for note in notes:
        # The planner may retain a clarification as its own note. Tie its
        # condition to the shared story, never to unrelated notes in the batch.
        story = note.get("editorial_story")
        group = [other for other in notes if story and other.get("editorial_story") == story] if story else [note]
        related = list(dict.fromkeys(sid for other in group for sid in other.get("source_ids", []) if sid in by_id))
        note_text = " ".join(_plain(other.get("text", "")) for other in group)
        for sid in related:
            source = by_id[sid]
            if source.get("context_only"):
                continue
            body = _plain(source.get("body", ""))
            facts = []
            for fact in _qualification_facts(body):
                if _BENEFITS[fact["benefit"]].search(note_text):
                    facts.append({"kind": "spend_qualification", **fact})
            if re.search(r"\b(?:PC|product[- ]change|convert\w*|conversion|downgrad\w*)\b", note_text, re.I):
                facts.extend({"kind": "contrasting_product_change", **fact} for fact in _product_change_facts(body))
            if (_FIRST_PERSON.search(body) and _reward_conversion_failure(body)
                    and re.search(r"\b(?:rewards?|cash|miles|points|convert\w*|transfer\w*)\b", note_text, re.I)):
                facts.append({"kind": "rewards_conversion", "currencies": _reward_currencies(body), "evidence": body[:1200]})
            if re.search(r"\b(?:offer|bonus|promotion|SUB)\b", note_text, re.I):
                for fact in _older_offer_facts(body):
                    if not fact["quantities"] or any(value in {_number(m.group()) for m in re.finditer(rf"(?<!\w){_NUMBER}(?!\w)", note_text)} for value in fact["quantities"]):
                        facts.append({"kind": "event_age", **fact})
            for fact in facts:
                key = (sid, fact["kind"], fact.get("amount"), fact.get("benefit"),
                       tuple(fact.get("destination_terms", [])), fact.get("event_age"))
                if key in seen:
                    continue
                seen.add(key)
                requirements.append({"source_id": sid, "related_source_ids": related, **fact})
    return requirements


def _has_age(text):
    return bool(_AGE.search(text) or re.search(r"\b(?:historical|previously|earlier|past offer)\b", text, re.I))


def _contrast_preserved(text, requirement):
    plain = _plain(text)
    words = {_token(word) for word in re.findall(r"[A-Za-z0-9&'-]+", plain)}
    if not all(_token(term) in words for term in requirement["destination_terms"]):
        return False
    if not re.search(r"\b(?:converted|conversion|downgraded|product[- ]change|PC['’]?(?:ed|d)|changed)\b", plain, re.I):
        return False
    # An affirmative event must be in the clause naming the destination. A
    # separate blocked conversion or 'not successfully converted' is not proof.
    successful = False
    for clause in re.split(r"[.;!?]|\b(?:but|whereas|while)\b", plain, flags=re.I):
        if not all(_token(term) in {_token(word) for word in re.findall(r"[A-Za-z0-9&'-]+", clause)}
                   for term in requirement["destination_terms"]):
            continue
        event = re.search(r"\b(?:successful\w*|converted|downgraded|changed|PC['’]?(?:ed|d)|no issues)\b", clause, re.I)
        if event and not _NEGATION.search(" ".join(clause[:event.start()].split()[-5:])):
            successful = True
    if not successful:
        return False
    excluded = requirement.get("excluded_origin")
    if excluded:
        normalized = re.sub(r"[^a-z0-9\s-]", "", plain.casefold())
        issuer = re.escape(_token(excluded))
        exclusion = re.search(rf"\b(?:non[- ]|not\s+(?:(?:through|from|via|a)\s+){{0,2}}){issuer}\b", normalized)
        origin = requirement.get("origin_issuer")
        native = bool(origin and re.search(rf"\b(?:native|original)\s+{re.escape(origin)}\b|\b{re.escape(origin)}[- ]originated\b", plain, re.I))
        if not exclusion and not native:
            return False
    return not requirement.get("event_age") or _has_age(plain)


def retention_issues(document, notes, comments):
    """Check source-specific conditions/outcomes, not arbitrary numeric presence."""
    requirements = selected_note_requirements(notes, comments)
    bullets = [bullet for section in document.get("sections", []) if isinstance(section, dict)
               for bullet in section.get("bullets", []) if isinstance(bullet, dict)]
    issues = []
    for requirement in requirements:
        sid, kind = requirement["source_id"], requirement["kind"]
        source_bullets = [_plain(b.get("text", "")) for b in bullets if sid in b.get("source_ids", [])]
        cited = " ".join(source_bullets)
        related = " ".join(_plain(b.get("text", "")) for b in bullets
                           if set(requirement["related_source_ids"]) & set(b.get("source_ids", [])))
        if kind == "spend_qualification":
            mentioned = _BENEFITS[requirement["benefit"]].search(related)
            if requirement["benefit"] == "credit or bonus" and _bonus_alias(requirement):
                mentioned = re.search(r"\bbonus\b", related, re.I) or _BENEFITS["welcome bonus"].search(related)
            if not mentioned:
                continue  # Omitting the benefit claim does not imply it is unconditional.
            retained = any(fact["amount"] == requirement["amount"] and _same_qualifying_benefit(fact, requirement)
                           for text in source_bullets for fact in _qualification_facts(text))
            # A concise nested qualification may leave its benefit name in the
            # preceding bullet. Accept it only when this source's bullet names
            # no other benefit and explicitly identifies qualifying spending.
            retained = retained or any(
                not any(pattern.search(text) for pattern in _BENEFITS.values())
                and re.search(r"\b(?:qualifying|requires?|required|needed|threshold|minimum)\b", text, re.I)
                and requirement["amount"] in {amount for amount, _ in _spend_values(text)}
                for text in source_bullets)
            if not retained:
                issues.append(f"Preserve {sid}'s {requirement['source_amount']} qualifying spend for the {requirement['benefit']} with that source citation, or omit the associated benefit claim. A matching amount elsewhere is not sufficient.")
        elif kind == "contrasting_product_change":
            if related and not _contrast_preserved(cited, requirement):
                issues.append(f"Preserve {sid}'s contrasting successful product change to {' '.join(requirement['destination_terms'])}, its distinct product/origin and reported historical timing. Do not retain only the blocked outcome or imply the success was current.")
        elif kind == "rewards_conversion":
            if related and not (_reward_conversion_failure(cited) and set(requirement["currencies"]) <= set(_reward_currencies(cited))):
                issues.append(f"Preserve {sid}'s observed rewards non-conversion/separate balances ({', '.join(requirement['currencies'])}) without turning it into a successful conversion or a universal rule.")
        elif kind == "event_age":
            if re.search(r"\b(?:offer|bonus|promotion|SUB)\b", cited, re.I) and not _has_age(cited):
                issues.append(f"Preserve {sid}'s reported offer age ({requirement['event_age']}) or explicitly label it historical; do not present an older offer sighting as current availability.")
    return issues
