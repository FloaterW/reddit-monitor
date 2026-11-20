"""Bounded churning-specific evidence checks, not general semantic verification.

Only comment bodies and supplied parent bodies are evidence. Thread titles,
posting dates and model notes cannot supply a missing card identity or payout.
"""

import re
from datetime import date, datetime, timedelta, timezone

from editorial_selection import CARD_PATTERNS

_NAMED_CARDS = {
    **CARD_PATTERNS,
    "Southwest Priority": r"\b(?:Southwest|SW)(?:\s+Rapid\s+Rewards)?\s+Priority\b",
    "Southwest Premier": r"\b(?:Southwest|SW)(?:\s+Rapid\s+Rewards)?\s+Premier\b",
    "Southwest Plus": r"\b(?:Southwest|SW)(?:\s+Rapid\s+Rewards)?\s+Plus\b",
}
_NUMBER = r"\d[\d,]*(?:\.\d+)?[kK]?"
_CREDIT = (rf"(?:(?:a|an|the|my|our|their|his|her)\s+)?(?:promised\s+)?"
           rf"(?:\${_NUMBER}\s+)?(?:(?:statement|retention|annual[- ]fee|fee)\s+)?"
           rf"(?:credit|refund|rebate)\b(?!\s+(?:card|offer)\b)")
_CREDIT_RECEIPT = re.compile(
    rf"\b(?:received|receiving|got|obtained|awarded)\s+{_CREDIT}"
    rf"|\b{_CREDIT}\s+(?:(?:of|for)\s+\${_NUMBER}\s+)?"
    rf"(?:(?:was|has|had|is|been|already|actually)\s+){{0,3}}(?:posted|paid|arrived|received|credited|issued)\b"
    rf"|\bcredited\s+(?:with\s+)?\${_NUMBER}", re.I)
_NEGATION = re.compile(r"\b(?:not|never|no|neither|without|whether|if|would|could|may|might)\b|n['’]t\b", re.I)
_FUTURE_DAYS = re.compile(r"\b(?:in|for|another|about|around|next)\s+(?:(?:about|around|approximately)\s+)?(\d+)\s+days?\b", re.I)
_DATE = (r"(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|(?:January|February|March|April|May|June|July|August|"
         r"September|October|November|December)\s+\d{1,2}(?:,?\s+\d{4})?)")
_UPPER_RELEASE = re.compile(r"\b(?:on or before|no later than|by)\s+(" + _DATE + r")", re.I)
_FIXED_HOLD = re.compile(r"\b(?:hold|held|unavailable|blocked|restricted)\b[^.!?\n]{0,65}?\b(?:through|until)\s+(" + _DATE + r")", re.I)
_RESTORATION = re.compile(r"\b(?:was|were|has been|have been|had been|got)\s+(?:eventually\s+)?(?:restored|reinstated|reactivated|re-?enabled)\b", re.I)
_PROSPECTIVE_RESTORATION = re.compile(r"\b(?:will|would|can|could|may|might)\b[^.!?\n]{0,100}\b(?:restore|reinstate|reactivate|re-?enable)\b", re.I)
_CARD_OBJECT = r"(?:(?:a|an|the|my|our|their|his|her|any)\s+)?(?:(?:new|replacement|physical)\s+){0,2}cards?\b"
_NO_SHIP_AUX = (r"(?:(?:had|has|have|did|does|do|was|were|is|are)\s+(?:not|never)|"
                r"(?:hadn|hasn|haven|didn|doesn|don|wasn|weren|isn|aren)['’]t|never)")
_NO_CARD_SHIPMENT = re.compile(
    rf"\b{_NO_SHIP_AUX}\s+(?:been\s+)?(?:ship(?:ped)?|sen[td]|mail(?:ed)?)\s+{_CARD_OBJECT}"
    rf"|\b{_CARD_OBJECT}\s+{_NO_SHIP_AUX}\s+(?:been\s+)?(?:shipped|sent|mailed)\b"
    rf"|\bno\s+(?:(?:new|replacement|physical)\s+){{0,2}}cards?\s+"
    rf"(?:(?:was|were|had|has|have|been|having|being|ever|yet)\s+){{0,4}}(?:shipped|sent|mailed)\b", re.I)
_SHIPMENT_UNCERTAINTY = re.compile(
    r"\b(?:if|whether|suppose|supposing|hypothetical(?:ly)?|counterfactual|would|could|might|may|unclear|unknown|unconfirmed|denies|denied|rejects|rejected)\b", re.I)
_LARGE_BILL = r"(?:(?:a|one|the|my|their)\s+)?(?:(?:pretty|very)\s+)?(?:large|big|huge|substantial|sizeable|sizable)\s+bill\b"
_BILL_COMPLETION = re.compile(
    rf"\b(?:met|hit|covered|satisfied|fulfilled|completed)\b[^.!?\n]{{0,75}}\b(?:with|using|through|via)\s+{_LARGE_BILL}"
    rf"|\b{_LARGE_BILL}[^.!?\n]{{0,60}}\b(?:met|hit|covered|satisfied|fulfilled|completed)\b", re.I)
_SPEND_REQUIREMENT = re.compile(r"\b(?:spend(?:ing)?|MSR|requirement|threshold)\b", re.I)
_BILL_AMOUNT = re.compile(rf"\$?{_NUMBER}\s+(?:(?:large|big)\s+)?bill\b|\bbill\s+(?:of|for)\s+\$?{_NUMBER}\b", re.I)
_OUTCOME_UNCERTAINTY = re.compile(r"\b(?:no|not|never|if|whether|should|would|could|can|may|might|will|unconfirmed)\b|n['’]t\b", re.I)
_PAYMENT_CONTEXT = re.compile(r"\bgift[ -]?cards?\b|\b(?:scan(?:ning|ned)?|barcode)\b|\bpayment screen\b", re.I)
_PAYMENT_MODAL = re.compile(r"\b(?:should|would|could|may|might)\b[^.!?\n]{0,100}\b(?:pay|payment|work|use|redeem)\b", re.I)
_PAYMENT_SUCCESS = re.compile(
    r"\bsuccessful(?:ly)?\b|\bworked\b|\bdid\s+work\b|\bsucceeded\b|\bwent\s+through\b"
    r"|\benabled\s+(?:the\s+)?payment\b|\bwas\s+accepted\b"
    r"|\b(?:I|we)\s+(?:paid|redeemed)\b"
    r"|\b(?:I|we)\s+had\s+to\s+wait\b[^.!?\n]{0,100}\bpayment screen\b[^.!?\n]{0,50}\bfor\s+it\s+to\s+work\b", re.I)
_REACTIVE_REPLY = re.compile(
    r"^\s*(?:(?:oh|wow|ah|well)[,.!\s]+)?(?:I\s+(?:did\s+not|didn['’]t)\s+know\b"
    r"|congrat(?:s|ulations)\b|thank(?:s|\s+you)\b)", re.I)
_APPROVAL_EVENT = re.compile(
    r"\bapproved\b|\b(?:instant|instantaneous|immediate)\s+approval\b"
    r"|\b(?:got|received|obtained|secured|reports?|reported)\s+(?:an?\s+)?"
    r"(?:(?:instant|instantaneous|immediate|second)\s+)?approval\b", re.I)
_APPROVAL_ADVICE = re.compile(
    r"\b(?:advice|tips?|odds|chances|possible|guaranteed|guarantees|hoping|hope|try|needs?)\b"
    r"|\bhow\s+to\b", re.I)


def _plain(text):
    return re.sub(r"\[[^]]*\]\([^)]*\)", "", str(text)).replace("*", "").replace("`", "")


def evidence_bodies(sources):
    """Parent context can establish a referent, but titles never do."""
    return [str(row.get("body", "")) for source in sources
            for row in [source, *source.get("parent_context", [])] if isinstance(row, dict)]


def _affirmative_matches(pattern, text):
    for match in pattern.finditer(text):
        # Exclude explicitly uncertain/negative claims, including 'not received'.
        prefix = re.split(r"[.;!?]|\b(?:but|however)\b", text[:match.start()], flags=re.I)[-1]
        prefix = " ".join(prefix.split()[-5:])
        suffix = text[match.end():]
        if _NEGATION.search(prefix) or re.match(r"\s+(?:was|is|has been)?\s*(?:not|never)\b", suffix, re.I):
            continue
        yield match


def parse_edition_cutoff(value):
    """Preserve date-only or timezone-unknown references without inventing a time."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError("Edition cutoff must be a date or ISO timestamp")
    try:
        return date.fromisoformat(value) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Edition cutoff must be a date or ISO timestamp") from exc


def edition_prompt_context(value):
    """Trusted temporal reference shared by extraction, planning and writing."""
    cutoff = parse_edition_cutoff(value)
    if cutoff is None:
        reference = "Edition date and cutoff are unknown."
    elif isinstance(cutoff, datetime):
        suffix = " (timezone unknown)" if cutoff.tzinfo is None else ""
        reference = f"Intended edition cutoff: {cutoff.isoformat()}{suffix}."
    else:
        reference = f"Intended edition date: {cutoff.isoformat()}; cutoff time and timezone are unknown."
    return ("\nEDITION REFERENCE (trusted application context):\n" + reference + "\n"
            "Assess recency only as of this intended edition, never the execution date or another wall clock. "
            "Do not label a reported offer stale, expired or currently available from wall-clock knowledge. "
            "If the edition time or source deadline is uncertain, retain the attributed deadline and uncertainty.\n")


def _premature_expiry(text, sources, edition_cutoff):
    if not list(_affirmative_matches(re.compile(r"\b(?:stale|expired)\b", re.I), text)):
        return False
    evidence = " ".join(evidence_bodies(sources))
    if re.search(r"\b(?:stale|expired|ended)\b", evidence, re.I):
        return False
    cutoff = parse_edition_cutoff(edition_cutoff)
    for source in sources:
        body = _plain(source.get("body", ""))
        durations = [int(n) for n in _FUTURE_DAYS.findall(body)]
        if not durations or not re.search(r"\boffer|deadline|expires?\b", body, re.I):
            continue
        # With no known reference, expiry is unsupported. Date-only references
        # use calendar days solely to reject an unsupported elapsed-time claim;
        # this does not certify the precise local deadline or live availability.
        if cutoff is None:
            return True
        try:
            created = datetime.strptime(source.get("created", ""), "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return True
        if isinstance(cutoff, datetime) and cutoff.tzinfo is not None:
            elapsed = cutoff.astimezone(timezone.utc) - created
            # Leave a full day of slack for approximate wording and timezone
            # uncertainty. A same-day 'around 2 days' lead is not proven stale.
            if elapsed < timedelta(days=max(0, min(durations) - 1)):
                return True
        else:
            edition_date = cutoff.date() if isinstance(cutoff, datetime) else cutoff
            if (edition_date - created.date()).days < max(0, min(durations) - 1):
                return True
    return False


def _date_key(value):
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m/%d", "%B %d, %Y", "%B %d %Y", "%B %d"):
        try:
            parsed = datetime.strptime(value.title(), fmt)
            return parsed.month, parsed.day
        except ValueError:
            continue
    return None


def _strengthened_hold(text, bodies):
    upper = {_date_key(m.group(1)) for body in bodies for m in _UPPER_RELEASE.finditer(body)} - {None}
    explicit = {_date_key(m.group(1)) for body in bodies for m in _affirmative_matches(_FIXED_HOLD, body)}
    return any(_date_key(m.group(1)) in upper - explicit for m in _affirmative_matches(_FIXED_HOLD, text))


def _nonshipment_claims(text):
    """Identify narrow card-shipment statements and their local uncertainty.

    'Even if they hadn't shipped a new card' is counterfactual, not an observed
    failure to ship. Avoid using negative-claim helpers that discard the very
    'not/no' whose scope we need to distinguish here.
    """
    for match in _NO_CARD_SHIPMENT.finditer(text):
        prefix = re.split(r"[,.;!?\n]|\b(?:but|however)\b", text[:match.start()], flags=re.I)[-1]
        yield bool(_SHIPMENT_UNCERTAINTY.search(prefix[-200:]))


def _asserted_counterfactual_nonshipment(text, bodies):
    claims = list(_nonshipment_claims(text))
    if not claims or all(claims):
        return False
    evidence = [conditional for body in bodies for conditional in _nonshipment_claims(body)]
    # This guard requires a specific source counterfactual; it is not a general
    # shipment fact checker. Explicit observed nonshipment in a cited body or
    # supplied parent can support the assertion, despite another hypothetical.
    return bool(evidence) and all(evidence)


def _asserted_outcomes(pattern, text):
    for match in pattern.finditer(text):
        prefix = re.split(r"[,.;!?\n]|\b(?:but|however)\b", text[:match.start()], flags=re.I)[-1]
        prefix = " ".join(prefix.split()[-5:])
        if not _OUTCOME_UNCERTAINTY.search(prefix + " " + match.group()):
            yield match


def _unsupported_bill_completion(text, bodies):
    if not (_SPEND_REQUIREMENT.search(text) and list(_asserted_outcomes(_BILL_COMPLETION, text))):
        return False
    bill_sources = [body for body in bodies if re.search(_LARGE_BILL, body, re.I)]
    # Deliberately limited to unquantified bill anecdotes. Explicit amounts or
    # a reported completion are outside this guard, not independently certified.
    return bool(bill_sources) and not any(
        _BILL_AMOUNT.search(body) or list(_asserted_outcomes(_BILL_COMPLETION, body)) for body in bill_sources)


def _unsupported_payment_success(text, sources):
    if not _PAYMENT_CONTEXT.search(text):
        return False
    claims = list(_asserted_outcomes(_PAYMENT_SUCCESS, text))
    if not claims:
        return False
    relevant = [source for source in sources if _PAYMENT_CONTEXT.search(_plain(source.get("body", "")))]
    if not any(_PAYMENT_MODAL.search(_plain(source.get("body", ""))) for source in relevant):
        return False
    successful_authors = set()
    for index, source in enumerate(relevant):
        if list(_asserted_outcomes(_PAYMENT_SUCCESS, _plain(source.get("body", "")))):
            # A parent may explain an operation, but its outcome cannot become
            # another commenter's personal success or an independent report.
            successful_authors.add(str(source.get("author") or source.get("id") or index).casefold())
    required = 1
    number_words = {"two": 2, "three": 3, "four": 4, "five": 5, "both": 2}
    for claim in claims:
        prefix = re.split(r"[.;!?\n]", text[:claim.start()])[-1][-160:]
        counted = re.search(r"\b(two|three|four|five|both|[2-9])\s+(?:commenters|users|people|reports)\b", prefix, re.I)
        if counted:
            value = counted.group(1).lower()
            required = max(required, int(value) if value.isdigit() else number_words[value])
        elif re.search(r"\bboth\s+(?:(?:report|reported|say|said|reportedly)\s+)?$", prefix, re.I):
            required = max(required, 2)
    return len(successful_authors) < required


def _approval_assertions(text):
    """Narrow positive approval events; advice, questions and negatives are not DPs."""
    for sentence in re.finditer(r"[^.!?\n]+[.!?]?", text):
        value = sentence.group()
        if value.rstrip().endswith("?"):
            continue
        for clause in re.split(r"[,;]|\b(?:but|however|although|though|whereas)\b", value, flags=re.I):
            if (_APPROVAL_EVENT.search(clause) and not _OUTCOME_UNCERTAINTY.search(clause)
                    and not _APPROVAL_ADVICE.search(clause)):
                yield clause


def _direct_approval_support(body):
    claims = list(_approval_assertions(body))
    if not _REACTIVE_REPLY.search(body):
        return bool(claims)
    for clause in claims:
        # This is a reaction-only guard, not an ownership grammar. An
        # affirmative child statement is sufficient unless it explicitly
        # addresses someone else's approval; ambiguity must not force a
        # genuine child DP onto a parent's citation.
        other_person = re.search(
            r"\b(?:you|OP)\s+(?:(?:was|were|got|are|is|have|has|been|just|also|both|actually|instantly|immediately|already|finally|successfully|automatically)\s+){0,6}approved\b"
            r"|\b(?:you|OP)\s+(?:got|received|obtained|secured)\s+(?:an?\s+)?(?:(?:instant|immediate|second)\s+){0,3}approval\b"
            r"|\b(?:your|OP['’]s|the\s+parent['’]s)\s+(?:(?:instant|immediate|second|card|application)\s+){0,4}approval\b",
            clause, re.I,
        )
        if not other_person:
            return True
    return False


def _reactive_parent_approval(text, sources):
    """A reaction cannot inherit a different author's personal approval outcome."""
    if not list(_approval_assertions(text)):
        return []
    # A directly cited affirmative source can establish the approval. This
    # deliberately permits parent-plus-child citations and normal direct DPs.
    if any(_direct_approval_support(_plain(source.get("body", ""))) for source in sources):
        return []
    parent_ids = []
    for source in sources:
        body = _plain(source.get("body", ""))
        if not _REACTIVE_REPLY.search(body):
            continue
        author = str(source.get("author") or "").casefold()
        if not author or author == "[deleted]":
            continue
        for parent in source.get("parent_context", []):
            if not isinstance(parent, dict):
                continue
            parent_author = str(parent.get("author") or "").casefold()
            parent_body = _plain(parent.get("body", ""))
            if (not parent_author or parent_author in {author, "[deleted]"}
                    or not re.search(r"\b(?:I|we|my|our)\b", parent_body, re.I)
                    or not list(_approval_assertions(parent_body))):
                continue
            identifier = parent.get("id") or parent.get("source_id")
            if isinstance(identifier, str) and re.fullmatch(r"t1_[A-Za-z0-9]+", identifier):
                parent_ids.append(identifier)
    return list(dict.fromkeys(parent_ids))


def factual_issues(text, sources, *, edition_cutoff=None):
    """Reject specific unsupported identities, credit receipts and expiry claims."""
    plain = _plain(text)
    bodies = [_plain(body) for body in evidence_bodies(sources)]
    evidence = " ".join(bodies)
    issues = []
    for name, pattern in _NAMED_CARDS.items():
        if re.search(pattern, plain, re.I) and not re.search(pattern, evidence, re.I):
            issues.append(f"{name} is not identified in the cited body or supplied parents. Remove the unsupported card identity; thread titles and model notes are not evidence.")
    if (list(_affirmative_matches(_CREDIT_RECEIPT, plain))
            and not any(list(_affirmative_matches(_CREDIT_RECEIPT, body)) for body in bodies)):
        issues.append("The cited bodies do not establish receipt/posting of this credit. Preserve offered or promised status; an annual-fee posting is not a credit payout.")
    if _premature_expiry(plain, sources, edition_cutoff):
        issues.append("The source's approximate future deadline is not established as stale/expired for this edition. Preserve the attributed deadline and uncertainty; do not use the replay execution date.")
    if _strengthened_hold(plain, bodies):
        issues.append("The source gives an on-or-before/latest release date, not a hold guaranteed through that date. Preserve expected release by/on-or-before wording.")
    if (list(_affirmative_matches(_RESTORATION, plain))
            and any(_PROSPECTIVE_RESTORATION.search(body) for body in bodies)
            and not any(list(_affirmative_matches(_RESTORATION, body)) for body in bodies)):
        issues.append("The source describes possible/future restoration after contacting support, not a completed personal restoration. Present it as the commenter's conditional warning or advice; do not claim transfers/access were restored in an observed case.")
    if _asserted_counterfactual_nonshipment(plain, bodies):
        issues.append("The source discusses nonshipment of a card conditionally (for example, 'even if'), not as an observed fact. Preserve that hypothetical or omit the shipment detail; do not assert that no replacement card shipped.")
    if _unsupported_bill_completion(plain, bodies):
        issues.append("The source mentions a large bill without its amount or an explicit claim that this bill met the spending requirement. Keep the requirement and bill observation separate; do not infer that the bill alone satisfied the threshold.")
    if _unsupported_payment_success(plain, sources):
        issues.append("The cited payment/scan guidance includes a conditional suggestion, not enough observed successes to support this claim. Attribute any actual working-payment report separately and keep should/could/may guidance conditional; do not turn it into a completed payment or an additional successful commenter.")
    parent_ids = _reactive_parent_approval(plain, sources)
    if parent_ids:
        issues.append("The cited reactive reply does not report its author's personal approval; that outcome belongs to a different-author supplied parent. Cite the actual parent source_id "
                      + ", ".join(parent_ids)
                      + " directly instead of transferring its experience to the replying commenter.")
    return issues
