"""Detect bounded issuer/card identities supplied only by discussion titles.

A thread label can identify where a comment appeared, not which unnamed product
the commenter personally used. This is a curated identity check, not entity
resolution or a guarantee that every unsupported identity will be detected.
"""

import re

# Longer names precede nested issuer names so one unsupported phrase yields one
# issue. Deliberately avoid arbitrary title n-grams and ambiguous aliases such as
# Gold, Platinum, MS, or generic 'business'.
_IDENTITIES = (
    ("Fidelity Visa", r"\bfidelity(?:\s+(?:rewards?|signature)){0,2}\s+visa(?:\s+signature)?\b"),
    ("Fidelity", r"\bfidelity\b"),
    ("Barclays", r"\bbarclay(?:s|'s)\b"),
    ("Capital One", r"\b(?:capital\s*one|cap\s*1|c1)\b"),
    ("Bilt Palladium", r"\b(?:bilt\s+)?palladium\b"),
    ("Bilt Obsidian", r"\b(?:bilt\s+)?obsidian\b"),
    ("Wells Fargo", r"\bwells\s+fargo\b"),
    ("American Express", r"\b(?:american\s+express|amex)\b"),
    ("Citi", r"\b(?:citi|citibank)\b"),
    ("Bank of America", r"\b(?:bank\s+of\s+america|bofa|boa)\b"),
)


def _plain(value):
    return str(value or "").replace("*", "").replace("`", "").replace("’", "'")


def _discussion_label(text, start, end):
    """Require attribution local to this mention, not a blanket disclaimer."""
    before = text[max(0, start - 90):start]
    after = text[end:end + 90]
    # 'In a Fidelity Visa discussion', 'Palladium thread: ...', and similar
    # explicit noun labels. Restrict intervening words and punctuation so an
    # unrelated later mention of 'discussion' does not excuse an assertion.
    suffix = re.match(
        r"(?:\s+(?:credit|card|visa|rewards|signature|offer|verification)){0,3}"
        r"\s+(?:discussion|thread|conversation|post)\b", after, re.I)
    if suffix:
        return True
    # 'Discussion: Fidelity Visa', 'a thread about Capital One', or 'the thread
    # is about Barclays'. The phrase must directly precede this named identity.
    prefix = re.search(
        r"\b(?:discussion|thread|conversation|post)"
        r"(?:(?:\s+(?:is|was))?\s+(?:about|on|regarding|titled)|\s*:)?"
        r"\s+(?:(?:a|an|the)\s+)?$", before, re.I)
    # 'Discussion: Fidelity Visa offers...' turns the named product into the
    # subject of a new assertion; 'Discussion: Fidelity Visa — reported terms'
    # remains an explicit topic label.
    direct_assertion = re.match(
        r"\s+(?:offers?|has|have|provides?|gives?|allows?|requires?|pays?|"
        r"approved|denied|changed|stopped|restored|issued|charges?)\b", after, re.I)
    return bool(prefix and not direct_assertion)


def topic_attribution_issues(text, sources):
    """Flag title-only identities asserted without explicit discussion framing.

    ``sources`` are cited raw comments. Only their bodies and supplied parent
    bodies support an identity. Titles can supply a discussion label, including
    in a heading, but cannot establish that an unnamed reply concerns that card.
    Every named mention is checked independently; one safe label does not make
    later unsupported assertions safe.
    """
    plain = _plain(text)
    sources = list(sources)
    titles = " ".join(_plain(source.get("post_title") or source.get("thread")
                             or source.get("discussion_topic_hint")) for source in sources)
    bodies = []
    for source in sources:
        bodies.append(_plain(source.get("body")))
        for parent in source.get("parent_context") or []:
            if isinstance(parent, dict):
                bodies.append(_plain(parent.get("body")))
    evidence = " ".join(bodies)
    issues = []
    reported_spans = []
    for name, pattern in _IDENTITIES:
        if not re.search(pattern, titles, re.I) or re.search(pattern, evidence, re.I):
            continue
        unsupported = []
        for match in re.finditer(pattern, plain, re.I):
            if any(start <= match.start() and match.end() <= end for start, end in reported_spans):
                continue
            if not _discussion_label(plain, match.start(), match.end()):
                unsupported.append(match.span())
        if unsupported:
            reported_spans.extend(unsupported)
            issues.append(
                f"{name} is identified only by the discussion title, not the cited body or supplied parents. "
                "Remove the unsupported issuer/card identity or explicitly attribute this mention to its "
                "discussion (for example, 'In a [named] discussion, a commenter reports...'). "
                "Do not present the thread label as verified product applicability."
            )
    return issues
