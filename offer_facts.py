"""Conservative, inspectable extraction of reported facts, never verified offers.

Each value retains the exact source span. Unknown terms stay unknown. The model
still reads the original evidence; these records cannot establish eligibility or
an approval probability from a self-selected Reddit sample.
"""

import hashlib
import json
import re

from editorial_selection import CARD_PATTERNS

ENTITIES = {
    **CARD_PATTERNS,
    "Sapphire Preferred": r"\b(?:CSP|(?:Chase\s+)?Sapphire\s+Preferred)\b",
    "Sapphire Reserve": r"\b(?:CSR(?!\s+(?:business|biz))|Sapphire\s+Reserve(?!\s+(?:for\s+)?(?:Business|Biz)))\b",
    "Venture X": r"\bVenture\s+X\b",
    "Venture": r"\bVenture(?!\s+X)\b",
    "Bilt Palladium": r"\b(?:Bilt\s+)?Palladium\b",
    "Bilt Obsidian": r"\b(?:Bilt\s+)?Obsidian\b",
    "IHG Premier Select": r"\b(?:IHG\s+)?Premier\s+Select\b",
    "IHG Premier": r"\bIHG\s+Premier(?!\s+Select)\b",
    "Marriott Boundless": r"\b(?:Marriott\s+)?Boundless\b",
    "Atmos Summit": r"\b(?:Atmos\s+)?Summit\b",
    "Amex Business Platinum": r"\b(?:Amex\s+)?(?:Business|Biz)\s+Plat(?:inum)?\b",
    "Amex Business Gold": r"\b(?:Amex\s+)?(?:Business|Biz)\s+Gold\b",
    "Blue Business Plus": r"\b(?:BBP|Blue\s+Business\s+Plus)\b",
    "Citi Strata Elite": r"\b(?:Citi\s+)?Strata\s+Elite\b",
    "United Explorer": r"\b(?:United\s+)?Explorer\b",
}
NUMBER = r"\d[\d,]*(?:\.\d+)?[kK]?"
POINT_SHORTHAND = r"\d[\d,]*(?:\.\d+)?[kK]"
OFFER = re.compile(r"\b(?:offer|bonus|SUB|welcome|sign[- ]?up|retention)\b", re.I)
QUESTION = re.compile(r"^\s*(?:can|could|should|would|does|do|is|are|will|has|have|anyone|how|what|when)\b", re.I)
UNCERTAIN = re.compile(r"\b(?:if|whether|might|could|may|would|should|will|not|never|unconfirmed)\b|n['’]t\b", re.I)
PERSONAL = re.compile(r"\b(?:I|we|my|our|P[123])\b", re.I)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _number(raw):
    value = raw.replace(",", "").lstrip("$").lower()
    number = float(value.rstrip("k")) * (1000 if value.endswith("k") else 1)
    return int(number) if number.is_integer() else number


def _span(match, group=0, *, numeric=False):
    raw = match.group(group)
    return {"value": _number(raw) if numeric else raw, "text": raw,
            "start": match.start(group), "end": match.end(group)}


def _sentences(body):
    start = 0
    for boundary in re.finditer(r"[.!?](?=\s|$)|\n+", body):
        yield start, body[start:boundary.end()]
        start = boundary.end()
    if start < len(body):
        yield start, body[start:]


def extract_facts(source, entities=None):
    """One unambiguous product per source; ambiguous multi-product text is skipped.

    This deliberately sacrifices recall to keep terms bound to the right card.
    Skipped sources remain available to the normal newsletter model.
    """
    if source.get("context_only"):
        return []
    body = str(source.get("body") or "")
    if not body.strip() or body.strip().casefold() in {"[removed]", "[deleted]"}:
        return []
    found = [name for name, pattern in (entities or ENTITIES).items() if re.search(pattern, body, re.I)]
    if len(found) != 1:
        return []
    entity = found[0]
    facts = []
    # Extract terms from the same sentence/line as an offer signal, never an
    # unrelated credit, account balance or another comment's numbers.
    for offset, text in _sentences(body):
        if ("?" in text or QUESTION.search(text) or not OFFER.search(text)
                or re.search(r"\b(?:if|might|could|would|not|rumou?r|hypothetical)\b|n['’]t\b", text, re.I)):
            continue
        fields = {}
        patterns = {
            "bonus_points": rf"(?<![\w$])({NUMBER})\s*(?:MR|UR|points?|miles?)\s+(?:(?:welcome|sign[- ]?up)\s+)?bonus\b",
            "spend_amount": rf"\bafter\s+(?:spending\s+)?\$({NUMBER})|\$({NUMBER})\s+(?:of\s+)?spend(?:ing)?\b",
            "spend_period": r"\b(?:in|within|over)\s+(\d+\s*(?:months?|mos?|days?))\b",
            "cash_bonus": rf"\$({NUMBER})\s+(?:welcome\s+)?bonus\b",
            "deadline": r"\b(?:expires?|ends?|through|until)\s+((?:\d{1,2}/\d{1,2}(?:/\d{2,4})?)|(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}(?:,?\s+\d{4})?))\b",
        }
        # Common explicit offer shorthand: "100k offer" or "100k bonus".
        point_matches = [m for pattern in (
            patterns["bonus_points"],
            rf"(?<![\w$])({POINT_SHORTHAND})\s+(?:welcome\s+)?(?:offer|bonus|SUB)\b",
            rf"\b(?:offer|bonus|SUB)\s*(?:of|is|:)?\s*({POINT_SHORTHAND})(?![\w,])",
            rf"\b(?:offer|bonus|SUB)\s*(?:of|is|:)?\s*({NUMBER})\s*(?:MR|UR|points?|miles?)\b")
            for m in re.finditer(pattern, text, re.I)]
        if len({_number(m[1]) for m in point_matches}) > 1 or re.search(r"\b(?:addition to|excluding|apart from)\b", text, re.I):
            continue  # Comparisons do not establish a single offer's terms.
        if point_matches:
            fields["bonus_points"] = _span(point_matches[0], 1, numeric=True)
        for key, pattern in patterns.items():
            if key == "bonus_points":
                continue
            matches = list(re.finditer(pattern, text, re.I))
            # Conflicting values are not silently collapsed into one offer.
            if len(matches) == 1:
                match = matches[0]
                group = next(i for i, value in enumerate(match.groups(), 1) if value is not None)
                fields[key] = _span(match, group, numeric=key in {"spend_amount", "cash_bonus"})
        availability = re.search(r"\b(targeted|public)\b", text, re.I)
        if len(set(m.casefold() for m in re.findall(r"\b(?:targeted|public)\b", text, re.I))) > 1:
            continue
        if availability:
            fields["availability"] = _span(availability)
            fields["availability"]["value"] = availability.group().lower()
        channel = re.search(r"\b(in[- ]branch|referral|in[- ]app|email|mail(?:er)?)\b", text, re.I)
        if channel:
            fields["channel"] = _span(channel)
            fields["channel"]["value"] = channel.group().lower().replace(" ", "-")
        if not any(k in fields for k in ("bonus_points", "cash_bonus", "spend_amount")):
            continue
        for value in fields.values():
            value["start"] += offset
            value["end"] += offset
        facts.append(_event(source, entity, "offer_report", fields, body))
    for offset, text in _sentences(body):
        if "?" in text or not PERSONAL.search(text) or not re.search((entities or ENTITIES)[entity], text, re.I):
            continue
        if re.search(r"\b(?:price[- ]match|claim|CLI|credit[- ]limit|refund|chargeback|customer service)\b", text, re.I):
            continue
        for kind, pattern in (
            ("bonus_paid", r"\b(?:received|got|earned)\s+(?:my\s+|the\s+)?(?:(?:\$?" + NUMBER + r")\s+)?(?:welcome\s+)?bonus\b(?!\s+offer)"),
            ("approved", r"\bapproved\b|\binstant\s+approval\b"),
            ("denied", r"\bdenied\b|\bdeclined\b"),
            ("pending", r"\b(?:application|decision)\s+(?:is\s+|still\s+)?pending\b"),
        ):
            for match in re.finditer(pattern, text, re.I):
                prefix = re.split(r"[,;]|\b(?:but|although|however)\b", text[:match.start()], flags=re.I)[-1]
                if (not PERSONAL.search(prefix) or UNCERTAIN.search(prefix)
                        or re.search(r"\b(?:pre[- ]?approved|preapproval|friend|someone|anyone|heard|you|they|he|she)\b", text, re.I)):
                    continue
                fields = {"outcome": _span(match)}
                actor = re.search(r"\bP[123]\b", text, re.I)
                if actor:
                    fields["actor"] = _span(actor)
                    fields["actor"]["value"] = actor.group().upper()
                ratio = re.search(r"\b(\d{1,2}/24)\b", text)
                if ratio:
                    fields["account_ratio"] = _span(ratio, 1)
                for value in fields.values():
                    value["start"] += offset
                    value["end"] += offset
                facts.append(_event(source, entity, kind, fields, body))
                break
    unique = {event["event_key"]: event for event in facts}
    return list(unique.values())


def _event(source, entity, kind, fields, body):
    for value in fields.values():
        assert body[value["start"]:value["end"]] == value["text"]
    values = {key: value["value"] for key, value in fields.items() if key != "outcome"}
    identity = {"entity": entity, "kind": kind,
                "availability": values.get("availability", "unknown"),
                "channel": values.get("channel", "unknown")}
    return {"entity": entity, "kind": kind, "fields": fields, "values": values,
            "source_id": source["id"], "author": source.get("author", ""),
            "evidence_status": "reddit_report_unverified", "story_key": fingerprint(identity),
            "variant_key": fingerprint(values),
            "event_key": fingerprint([source["id"], identity, values])}
