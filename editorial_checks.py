"""Narrow grammar and wait-duration safeguards, not a general language checker."""

import re

_AMOUNT = r"\d+(?:\.\d+)?(?:\s*(?:[-–—]|to)\s*\d+(?:\.\d+)?)?"
_UNIT = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|hr|days?|weeks?|months?|years?)"
_DURATION = re.compile(rf"(?<![\w$.,])({_AMOUNT})[\s-]+({_UNIT})\b", re.I)
_BARE_BEFORE_WAIT = re.compile(rf"(?<![\w$.,])({_AMOUNT})\s+(?:wait|waiting time|on hold)\b", re.I)
_WAIT_BEFORE_AMOUNT = re.compile(
    rf"\b(?:wait(?:ed|ing)?|hold time|queue time)\s+"
    rf"(?:(?:was|is|of|for|about|roughly|around|approximately|nearly|up to|over|a|an)\s+)*"
    rf"({_AMOUNT})(?![\d.,])", re.I)
_UNCLEAR = re.compile(r"(?:units? (?:are |were |was )?(?:unspecified|unclear|unknown|not (?:given|specified|provided)))", re.I)


def _plain(text):
    return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text).replace("*", "").replace("`", "")


def _durations(text):
    result = set()
    for amount, unit in _DURATION.findall(_plain(text)):
        amount = re.sub(r"\s*(?:[-–—]|to)\s*", "-", amount)
        unit = unit.lower().rstrip("s")
        unit = {"sec": "second", "min": "minute", "hr": "hour"}.get(unit, unit)
        result.add((amount, unit))
    return result


def editorial_issues(text, *, leading_source=False, source_bodies=None):
    plain = _plain(text).strip()
    issues = []
    # Preserve noun phrases such as 'Reports of ...' and ordinary imperatives.
    if (not leading_source and re.match(r"^(?:reports|notes|confirms|asks)\b", plain, re.I)
            and not re.match(r"^(?:reports|notes)\s+(?:of|on|from|about)\b", plain, re.I)):
        issues.append("End-cited bullet lacks a grammatical subject. Write a complete sentence, or use one leading source with source_position=start; do not just move the citations.")

    supported = set().union(*(_durations(body) for body in source_bodies)) if source_bodies else set()
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", plain):
        if not re.search(r"\b(?:wait|waited|waiting|hold|queue)\b", sentence, re.I):
            continue
        unclear = bool(_UNCLEAR.search(sentence))
        bare = bool(_BARE_BEFORE_WAIT.search(sentence))
        for match in _WAIT_BEFORE_AMOUNT.finditer(sentence):
            tail = sentence[match.end():].lstrip()
            if re.match(rf"[-\s]*{_UNIT}\b", tail, re.I):
                continue
            # 'Waited for the 3-4 offer' is not a duration. Limit detection to
            # duration-like clauses rather than guessing from every number.
            if not tail or re.match(r"[.,;:!?)]|\b(?:for|before|after|until|to|and|but)\b", tail, re.I):
                bare = True
        if bare and not unclear:
            issues.append("Wait duration has no time unit. Omit the amount or explicitly say 'units unspecified'; never guess hours/minutes from an incomplete source.")
        if source_bodies is not None:
            for amount, unit in sorted(_durations(sentence) - supported):
                issues.append(f"Wait-related duration {amount} {unit} is not stated with that unit in the cited sources. Omit it or describe the duration as unspecified; do not infer missing units.")
    return issues
