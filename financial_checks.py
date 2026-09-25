"""Conservative arithmetic checks, not a claim of general factual verification.

Only explicit, unambiguous relationships are checked. A model must flag an
inconsistent source or omit its calculation, never invent corrected offer terms.
"""

import re
from decimal import Decimal

NUMBER = r"\d[\d,]*(?:\.\d+)?[kK]?"


def number(value):
    value = value.replace(",", "")
    return Decimal(value[:-1]) * 1000 if value.lower().endswith("k") else Decimal(value)


def arithmetic_issues(text):
    text = re.sub(r"\[[^\]]+\]\([^)]*\)", "", text).replace("*", "")
    issues = []
    # Check sentences separately so a later, unrelated booking or balance does
    # not change which quantities are compared. Decimal points are preserved.
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text):
        if re.search(r"inconsistent|does not add up|do not add up|arithmetic (?:error|unclear)|calculation (?:is )?(?:incorrect|unverified)", sentence, re.I):
            continue
        percents = re.findall(r"(\d+(?:\.\d+)?)%", sentence)
        if re.search(r"(?:transfer.{0,12}bonus|bonus.{0,12}transfer)", sentence, re.I) and len(percents) == 1:
            # Exclude dollar costs, dates, percentages, existing balances, and
            # non-unit base ratios. These require contextual/manual reasoning.
            amounts = [number(m.group()) for m in re.finditer(
                rf"(?<![\w$.,/]){NUMBER}(?![\w%,/])", sentence)
                if number(m.group()) >= 1000]
            if (len(amounts) == 2 and not re.search(r"\d\s*:\s*\d|existing|already had|balance|top.up", sentence, re.I)):
                low, high = sorted(amounts)
                expected = low * (1 + Decimal(percents[0]) / 100)
                # Allow normal whole-thousand transfer rounding, not a discount
                # incorrectly applied as if it were a transfer bonus.
                tolerance = Decimal(1000) * (1 + Decimal(percents[0]) / 100)
                if abs(expected - high) > tolerance:
                    issues.append("Transfer-bonus amounts are arithmetically inconsistent; flag the source discrepancy or omit the calculation.")

        combined = re.search(
            rf"\$({NUMBER})\s*(?:SUB|bonus|credit)\s*(?:plus|\+)\s*\$({NUMBER})\s*(?:[A-Za-z]+\s+){{0,3}}(?:bonus|credit)\s*[:=—,-]\s*\$({NUMBER})\s*(?:return|total|combined)",
            sentence, re.I)
        if combined and number(combined[1]) + number(combined[2]) != number(combined[3]):
            issues.append("Combined bonus/credit total is inconsistent; flag or omit the total.")

        net = re.search(r"(?:paid|cost|price|purchase)\b[^$]*\$(" + NUMBER + r").*?\b(?:after|minus|less)\b(.*?)\bnet(?: cost)?(?: was| is)?\s*(?:of|=|:)?\s*\$(" + NUMBER + r")", sentence, re.I)
        if net and re.search(r"credit|rebate|cash", net[2], re.I) and not re.search(r"\bor\b|versus|\bvs\b", net[2], re.I):
            credits = [number(v) for v in re.findall(r"\$(" + NUMBER + r")", net[2])]
            if 1 <= len(credits) <= 3 and number(net[1]) - sum(credits) != number(net[3]):
                issues.append("Net cost after credits is inconsistent; flag or omit the net calculation.")

        spending = re.search(r"\$(" + NUMBER + r")\s*(?:mortgage|rent|housing payment).*?requires\s*\$(" + NUMBER + r").*?(\d+(?:\.\d+)?)%", sentence, re.I)
        if spending and abs(number(spending[1]) * Decimal(spending[3]) / 100 - number(spending[2])) > Decimal("0.01"):
            issues.append("Percentage-based spending requirement is inconsistent; flag or omit the calculation.")
    return issues


def check_arithmetic_consistency(text):
    issues = [{"line": n, "issue": issue} for n, line in enumerate(text.splitlines(), 1)
              for issue in arithmetic_issues(line)]
    return {"passed": not issues, "issues": issues}
