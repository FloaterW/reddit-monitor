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
_SPEND_AFTER_AMOUNT = re.compile(rf"(?<!\w)({_NUMBER})\s+(?:in\s+)?(?:spend(?:ing)?|(?:(?:everyday|eligible|qualifying)\s+)?purchases?)\b", re.I)
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


def _generic_benefit_term(clause, match):
    term = match.group().casefold()
    if term == "bonus":
        modifier = re.search(r"\b(retention|upgrade|referral|anniversary|transfer|redemption)\s+$",
                             clause[:match.start()], re.I)
        if modifier:
            return modifier.group(1).casefold() + " bonus"
        if re.search(r"\d+(?:\.\d+)?\s*%\s+$", clause[:match.start()]):
            return "percentage bonus"
    return term


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
                        term = _generic_benefit_term(clause, benefit) if name == "credit or bonus" else benefit.group().casefold()
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
    if _bonus_alias(left) and _bonus_alias(right):
        return True
    if left["benefit"] != right["benefit"]:
        return False
    if left["benefit"] == "credit or bonus":
        return re.sub(r"\s", "", left.get("benefit_term", "")) == re.sub(r"\s", "", right.get("benefit_term", ""))
    return True


def _benefit_mentioned(text, fact):
    """The generic category is not permission to substitute a different benefit."""
    if fact["benefit"] != "credit or bonus" and not _bonus_alias(fact):
        return bool(_BENEFITS[fact["benefit"]].search(text))
    pattern = (_BENEFITS["welcome bonus"].pattern + "|" + _BENEFITS["credit or bonus"].pattern
               if _bonus_alias(fact) else _BENEFITS["credit or bonus"].pattern)
    for clause in _sentences(text):
        for match in re.finditer(pattern, clause, re.I):
            if re.search(r"\b(?:no|without|neither)\s+$", clause[:match.start()], re.I):
                continue
            if re.match(r"\s+(?:(?:is|was|are|were)\s+)?(?:not|never)\s+(?:available|offered|included)\b", clause[match.end():], re.I):
                continue
            term = _generic_benefit_term(clause, match)
            mentioned = {"benefit": "welcome bonus"} if _BENEFITS["welcome bonus"].fullmatch(match.group()) else {
                "benefit": "credit or bonus", "benefit_term": term}
            if _same_qualifying_benefit(mentioned, fact):
                return True
    return False


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


_DIRECT_DEPOSIT = re.compile(r"(?i:\bdirect[- ]deposits?\b)|\bDDs?\b")
_BONUS_PAYOUT = re.compile(r"\b(?:bonus|bonuses|payout)\b", re.I)
_UNCERTAIN_CONDITION = re.compile(r"\b(?:unclear|unknown|unspecified|whether|maybe|perhaps|might|could|not sure|not established|not stated|not reported|does not establish|check|verify)\b", re.I)


def _cash_values(text):
    return {_number(m.group()) for m in re.finditer(rf"(?<!\w){_NUMBER}(?!\w)", text)
            if "$" in m.group() or m.group().lower().endswith("k")}


def _account_names(text):
    return {m.group(1).casefold() for m in re.finditer(
        r"\b([A-Z][A-Za-z0-9]+)\s+(?:Checking|checking|Savings|savings)\b", text)}


def _direct_deposit_facts(text, *, bonus_context=False):
    """Preserve an explicit DD requirement or reported payout path, not bank policy."""
    facts = []
    for sentence in _sentences(text):
        if sentence.endswith("?"):
            continue
        for clause in re.split(r";|\b(?:but|whereas|while)\b", sentence, flags=re.I):
            if _UNCERTAIN_CONDITION.search(clause):
                continue
            deposit, bonus = _DIRECT_DEPOSIT.search(clause), _BONUS_PAYOUT.search(clause) or bonus_context
            if not deposit or not bonus:
                continue
            prefix = " ".join(clause[:deposit.start()].split()[-4:])
            if (_NEGATION.search(prefix) or re.search(r"\bbefore\b", prefix, re.I)
                    or re.match(r"\s+(?:(?:is|was|are|were)\s+)?not\s+(?:required|needed|necessary)", clause[deposit.end():], re.I)):
                continue
            if not re.search(r"\b(?:requires?|required|needs?|after|following|followed|once|upon|within|until|when|met|meeting|qualifying)\b", clause, re.I):
                continue
            required = bool(re.search(r"\b(?:requires?|needs?)\s+(?:a\s+|qualifying\s+)?(?:direct[- ]deposits?|DDs?)\b|"
                                      r"\b(?:direct[- ]deposits?|DDs?)\s+(?:(?:is|are)\s+)?required\b", clause, re.I))
            facts.append({"relationship": "required" if required else "reported_payout_path",
                          "cash_values": sorted(_cash_values(clause)), "account_names": sorted(_account_names(clause)),
                          "evidence": clause[:1200]})
    return facts


def _ordinary_transfer_statements(text, *, include_uncertain=False):
    # Both "qualifies" and "does not qualify" would overstate an unspecified
    # funding source. A question or explicit uncertainty makes no such claim.
    return [sentence for sentence in _sentences(text)
            if (include_uncertain or (not sentence.endswith("?") and not _UNCERTAIN_CONDITION.search(sentence)))
            and re.search(r"\b(?:ordinary|regular|external|ACH|bank)\s+(?:bank\s+)?transfers?\b", sentence, re.I)
            and re.search(r"\b(?:qualif\w*|counts?|suffic\w*|satisf\w*|meets?|works?|eligible)\b", sentence, re.I)]


def _ordinary_transfer_claim(text):
    return bool(_ordinary_transfer_statements(text))


def _clause_key(text):
    return " ".join(re.findall(r"\w+", _plain(text).casefold()))


def _selected_deposit_facts(source_facts, note_facts):
    """Choose only unique source clauses; missing anchors are not a wildcard."""
    selected = []
    for note in note_facts:
        exact = [fact for fact in source_facts if _clause_key(fact["evidence"]) == _clause_key(note["evidence"])]
        matches = exact or [fact for fact in source_facts
                            if fact["account_names"] and note["account_names"]
                            and set(fact["account_names"]) & set(note["account_names"])
                            and (not fact["cash_values"] or not note["cash_values"]
                                 or set(fact["cash_values"]) & set(note["cash_values"]))]
        if len(matches) > 1:
            matches = [fact for fact in matches if fact["relationship"] == note["relationship"]]
        if len(matches) == 1 and matches[0] not in selected:
            selected.append(matches[0])
    return selected


def _associated_transfer_statements(body, fact):
    """An immediate 'this bonus' statement can qualify the preceding DD report."""
    direct = _ordinary_transfer_statements(fact["evidence"])
    if direct:
        return direct
    sentences = _sentences(body)
    for index, sentence in enumerate(sentences[:-1]):
        if _clause_key(fact["evidence"]) not in _clause_key(sentence):
            continue
        following = sentences[index+1]
        if _ordinary_transfer_claim(following) and re.fullmatch(
                r"(?:ordinary|regular|external|ACH|bank)\s+(?:bank\s+)?transfers?\s+"
                r"(?:(?:also|do|does|not|don't|can|will)\s+)*(?:qualify|count|work|suffice|are eligible)\s+"
                r"(?:for|towards?)\s+(?:this|that|the same)\s+(?:bonus|payout|offer)"
                r"(?:\s+(?:only|if|when|after|provided|subject to|unless)\b[^.!?]*)?[.!]?", following, re.I):
            return [following]
    return []


_INK_VARIANT_GROUP = re.compile(
    r"\b(?:Chase\s+)?Ink\s+(?:Business\s+)?(?:Cash|Unlimited|Preferred)"
    r"(?:\s*(?:,\s*)?(?:and|&|/)\s*(?:(?:Chase\s+)?Ink\s+(?:Business\s+)?)?(?:Cash|Unlimited|Preferred))+\b|"
    r"\b(?:CIC|CIU|CIP)(?:\s*(?:,\s*)?(?:and|&|/)\s*(?:CIC|CIU|CIP))+\b", re.I)
_REWARD_AMOUNT = re.compile(rf"({_NUMBER})\s*(?:cash\s*back|back|points?|miles|bonus|offers?|SUB)\b", re.I)


def _shared_offer_facts(text):
    """Only explicitly coordinated Ink variants sharing one adjacent offer."""
    names = {"cash": "Ink Cash", "cic": "Ink Cash", "unlimited": "Ink Unlimited",
             "ciu": "Ink Unlimited", "preferred": "Ink Preferred", "cip": "Ink Preferred"}
    facts = []
    for sentence in _sentences(text):
        if sentence.endswith("?"):
            continue
        for clause in re.split(r";|\b(?:but|whereas|while)\b", sentence, flags=re.I):
            if _UNCERTAIN_CONDITION.search(clause):
                continue
            for group in _INK_VARIANT_GROUP.finditer(clause):
                if _NEGATION.search(" ".join(clause[:group.start()].split()[-4:])):
                    continue
                cards = sorted({names[token.casefold()] for token in re.findall(r"\b(?:Cash|CIC|Unlimited|CIU|Preferred|CIP)\b", group.group(), re.I)})
                if len(cards) < 2:
                    continue
                for reward in _REWARD_AMOUNT.finditer(clause):
                    if reward.start() >= group.end():
                        bridge = clause[group.end():reward.start()].strip(" :,-")
                        allowed = r"(?:both|each|also|have|has|had|show|shows|offer|offers|offering|a|an|the|same|targeted|in-app|welcome|new|for|of|at|with|is|are|was|were|currently|now|earn)"
                    elif reward.end() <= group.start():
                        bridge = clause[reward.end():group.start()].strip(" :,-")
                        allowed = r"(?:offer|offers|bonus|bonuses|on|for|both|each|the|same|across|of|these|cards)"
                    else:
                        continue
                    if len(bridge) > 70 or (bridge and not re.fullmatch(rf"{allowed}(?:\s+{allowed})*", bridge, re.I)):
                        continue
                    facts.append({"cards": cards, "amount": _number(reward.group(1)),
                                  "source_amount": reward.group(1), "evidence": clause[:1200]})
    return facts


def selected_note_requirements(notes, comments):
    """Protect narrow material facts only for sources attached to selected notes."""
    by_id = {row.get("id"): row for row in comments if isinstance(row.get("id"), str)}
    requirements = []
    seen = {}
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
            # These facts must survive extraction AND be selected. An omitted
            # prior condition or another product mention is not a new mandate.
            selected_text = " ".join(_plain(other.get("text", "")) for other in group if sid in other.get("source_ids", []))
            deposit_notes, shared_notes = _direct_deposit_facts(selected_text), _shared_offer_facts(selected_text)
            selected_qualifications = _qualification_facts(selected_text)
            # A selected reply may explicitly retain an offer from its supplied
            # parent. Preserve that selected fact, without promoting other old
            # parent material or an independently excluded extraction note.
            for evidence in [source, *[p for p in source.get("parent_context", []) if isinstance(p, dict)]]:
                evidence_id = evidence.get("id")
                if not isinstance(evidence_id, str):
                    continue
                evidence_body = _plain(evidence.get("body", ""))
                binding = {"evidence_source_id": evidence_id, "evidence_source_ids": list(dict.fromkeys([evidence_id, sid]))}
                # Funding-path protection is intentionally limited to the
                # selected source itself; an older parent's condition must not
                # become a fresh requirement for a later payout report.
                deposit_sources = _direct_deposit_facts(evidence_body) if evidence_id == sid else []
                for fact in _selected_deposit_facts(deposit_sources, deposit_notes):
                    facts.append({"kind": "direct_deposit", **fact, **binding,
                                  "source_deposit_fact_count": len(deposit_sources),
                                  "transfer_qualification_statements": _associated_transfer_statements(evidence_body, fact)})
                for fact in _shared_offer_facts(evidence_body):
                    if any(fact["cards"] == n["cards"] and fact["amount"] == n["amount"] for n in shared_notes):
                        facts.append({"kind": "shared_offer_variants", **fact, **binding})
                if evidence_id != sid:
                    # Parent spending conditions are selected only when this
                    # reply's note explicitly retains the same spend/benefit
                    # relationship. A nearby redemption or status-match note
                    # must not promote an unselected parent's shopping offer.
                    for fact in _qualification_facts(evidence_body):
                        if any(fact["amount"] == n["amount"] and _same_qualifying_benefit(fact, n)
                               for n in selected_qualifications):
                            facts.append({"kind": "spend_qualification", **fact, **binding})
            for fact in _qualification_facts(body):
                if _benefit_mentioned(note_text, fact):
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
                evidence_id = fact.get("evidence_source_id", sid)
                key = (evidence_id, fact["kind"], fact.get("amount"), fact.get("benefit"), fact.get("benefit_term"),
                       tuple(fact.get("destination_terms", [])), fact.get("event_age"),
                       tuple(fact.get("cards", [])), tuple(fact.get("account_names", [])), tuple(fact.get("cash_values", [])),
                       fact.get("relationship"))
                if key in seen:
                    if fact.get("evidence_source_ids"):
                        previous = seen[key]
                        previous["evidence_source_ids"] = list(dict.fromkeys(
                            [*previous.get("evidence_source_ids", []), *fact["evidence_source_ids"]]))
                    continue
                requirement = {"source_id": evidence_id, "related_source_ids": related, **fact}
                seen[key] = requirement
                requirements.append(requirement)
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
        evidence_ids = set(requirement.get("evidence_source_ids", [sid]))
        source_bullets = [_plain(b.get("text", "")) for b in bullets if evidence_ids & set(b.get("source_ids", []))]
        cited = " ".join(source_bullets)
        related = " ".join(_plain(b.get("text", "")) for b in bullets
                           if set(requirement["related_source_ids"]) & set(b.get("source_ids", [])))
        if kind == "spend_qualification":
            # The writer may cite the explicitly selected parent rather than
            # its reply. Both are bound evidence for this same condition.
            mentioned = _benefit_mentioned(" ".join([related, cited]), requirement)
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
        elif kind == "direct_deposit":
            names = requirement["account_names"]
            claims = []
            for section in document.get("sections", []):
                if not isinstance(section, dict):
                    continue
                text = " ".join(_plain(b.get("text", "")) for b in section.get("bullets", [])
                                if isinstance(b, dict) and evidence_ids & set(b.get("source_ids", [])))
                if not text:
                    continue
                heading = _plain(section.get("title", ""))
                account_names = _account_names(text) or _account_names(heading)
                if names and account_names and not set(names) & account_names:
                    continue
                # A heading supplies claim TYPE only for its own cited bullet.
                # It cannot supply the missing DD evidence or rescue a claim in
                # a separate section. Incidental outgoing-transfer prose is not
                # a payout claim merely because a broad heading mentions bonuses.
                heading_claim = _BONUS_PAYOUT.search(heading) and re.search(
                    r"\b(?:paid|pays?|receiv\w*|arriv\w*|credited|posted|earned|earns?|requires?|required|qualifying|eligible)\b", text, re.I)
                if _BONUS_PAYOUT.search(text) or heading_claim:
                    claims.append((text, heading))
            retained, checked_claims = True, []
            for text, heading in claims:
                facts = [fact for fact in _direct_deposit_facts(text, bonus_context=True)
                         if not names or not fact["account_names"] or set(names) & set(fact["account_names"])]
                if requirement.get("source_deposit_fact_count", 1) > 1:
                    # A multi-account source cannot supply unqualified context
                    # to every same-citation payout. With no explicit account
                    # match, only the exact selected clause is safely bound.
                    bound = [fact for fact in facts
                             if (names and fact["account_names"] and set(names) & set(fact["account_names"]))
                             or _clause_key(fact["evidence"]) == _clause_key(requirement["evidence"])]
                    if not bound:
                        continue
                    facts = bound
                checked_claims.append(text)
                kept = any(fact["relationship"] == requirement["relationship"] for fact in facts)
                if requirement["relationship"] == "reported_payout_path":
                    # Observing DD before payout does not establish a universal
                    # eligibility rule, even if the inferred rule is plausible.
                    kept = kept and not any(fact["relationship"] == "required"
                                            for fact in [*facts, *_direct_deposit_facts(heading)])
                retained = retained and kept
            if claims and not retained:
                issues.append(f"Preserve {sid}'s direct-deposit qualification/reported payout path with its bonus claim and source citation. Keep this a reported funding path unless the source explicitly states a requirement; do not imply an ordinary transfer qualifies.")
            supplied = requirement["transfer_qualification_statements"]
            if supplied:
                supplied_keys = {_clause_key(statement) for statement in supplied}
                if any(_clause_key(statement) not in supplied_keys for text in checked_claims
                       for statement in _ordinary_transfer_statements(text, include_uncertain=True)):
                    issues.append(f"Preserve {sid}'s explicit associated transfer-eligibility statement without changing its direction or conditions. The rendered eligibility claim does not match the supplied source clause.")
            elif any(_ordinary_transfer_claim(text) for text in checked_claims):
                issues.append(f"Do not infer whether ordinary bank transfers qualify for {sid}'s bonus. The selected source reports direct deposits without establishing transfer eligibility.")
        elif kind == "shared_offer_variants":
            from editorial_selection import CARD_PATTERNS
            matching_claim = any(
                any(re.search(CARD_PATTERNS[card], clause, re.I) for card in requirement["cards"])
                and any(_number(m.group(1)) == requirement["amount"] for m in _REWARD_AMOUNT.finditer(clause))
                for sentence in _sentences(cited)
                for clause in re.split(r";|\b(?:but|whereas|while)\b", sentence, flags=re.I))
            if not matching_claim:
                continue  # Do not require card mentions when the offer itself is omitted.
            retained = any(fact["cards"] == requirement["cards"] and fact["amount"] == requirement["amount"]
                           for fact in _shared_offer_facts(cited))
            if not retained:
                issues.append(f"Preserve all explicitly selected variants ({', '.join(requirement['cards'])}) for {sid}'s shared {requirement['source_amount']} reported offer, with that citation. Do not turn a preapproval banner or question into an actual approval.")
    return issues
