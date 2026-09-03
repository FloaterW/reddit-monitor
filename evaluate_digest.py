"""
Digest Quality Evaluation — deterministic checks for LLM-generated summaries.

Verifies that a digest accurately reflects its source comments by checking
citation coverage, dollar amounts, dates, and completeness. No LLM needed.

Usage:
  python evaluate_digest.py digest.md raw_comments.json
  python evaluate_digest.py digest.md raw_comments.json --verbose
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------
_DOLLAR_RE = re.compile(r"\$[\d,]+(?:\.\d{1,2})?")
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?%")
_MULTIPLIER_RE = re.compile(r"\b(\d+)x\b", re.IGNORECASE)
_POINTS_K_RE = re.compile(r"\b(\d+)k\b", re.IGNORECASE)
_POINTS_WORD_RE = re.compile(r"\b([\d,]{3,})\s*(?:points?\b|miles?\b)", re.IGNORECASE)
_CITATION_RE = re.compile(r"\[u/([^\]]+)\]\(([^)]+)\)")
_REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com"}


def _normalize_dollar(s):
    """'$1,000.50' -> '1000.50', '$50' -> '50'"""
    return s.replace("$", "").replace(",", "")


def extract_dollar_amounts(text):
    """Return set of normalized dollar amounts found in text."""
    return {_normalize_dollar(m) for m in _DOLLAR_RE.findall(text)}


def extract_percentages(text):
    """Return set of percentage strings like '20%'."""
    return set(_PERCENT_RE.findall(text))


def extract_multipliers(text):
    """Return set of multiplier strings like '5x'."""
    return {f"{m}x" for m in _MULTIPLIER_RE.findall(text)}


def extract_point_amounts(text):
    """Return set of normalized point/mile amounts.

    '100k' -> '100000', '80,000 points' -> '80000', '250k miles' -> '250000'

    Requires either the 'k' suffix or a number with 3+ digits followed by
    'points'/'miles' to avoid matching Reddit scores like '89 pts'.
    """
    amounts = set()
    for m in _POINTS_K_RE.finditer(text):
        raw = m.group(1)
        try:
            amounts.add(str(int(raw) * 1000))
        except ValueError:
            amounts.add(raw)
    for m in _POINTS_WORD_RE.finditer(text):
        raw = m.group(1).replace(",", "")
        amounts.add(raw)
    return amounts


def extract_citations(digest_text):
    """Return list of (username, permalink) tuples from [u/name](link) patterns."""
    return _CITATION_RE.findall(digest_text)


def _normalize_reddit_url(value):
    """Return a host-independent Reddit URL key, or None for unsafe URLs."""
    try:
        parsed = urlparse(value.strip())
    except (AttributeError, ValueError):
        return None
    if parsed.scheme != "https" or parsed.hostname not in _REDDIT_HOSTS:
        return None
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    return path.lower() if path else None


def _comment_url_key(comment):
    direct = comment.get("comment_permalink") or comment.get("permalink")
    if direct:
        normalized = _normalize_reddit_url(direct)
        if normalized:
            return normalized

    post_permalink = comment.get("post_permalink", "")
    comment_id = str(comment.get("id", "")).removeprefix("t1_")
    if not post_permalink or not comment_id:
        return None
    return _normalize_reddit_url(f"{post_permalink.rstrip('/')}/{comment_id}/")


def _numeric_values(text):
    """Extract the numeric claim types this evaluator can verify."""
    return {
        "dollar_amounts": extract_dollar_amounts(text),
        "percentages": extract_percentages(text),
        "multipliers": extract_multipliers(text),
        "point_amounts": extract_point_amounts(text),
    }


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def check_citation_coverage(digest_text, comments):
    """Check what fraction of source comment authors are cited in the digest.

    Returns dict with cited_authors, uncited_authors, total, coverage ratio,
    and a pass/fail flag (fails if < 50% of authors are cited).
    """
    citations = extract_citations(digest_text)
    cited_usernames = {username.lower() for username, _ in citations}

    source_authors = set()
    for c in comments:
        author = c.get("author", "")
        if author and author != "[deleted]":
            source_authors.add(author.lower())

    cited = source_authors & cited_usernames
    uncited = source_authors - cited_usernames

    total = len(source_authors)
    coverage = len(cited) / total if total > 0 else 1.0

    return {
        "cited_count": len(cited),
        "uncited_count": len(uncited),
        "total_authors": total,
        "coverage": coverage,
        "cited_authors": sorted(cited),
        "uncited_authors": sorted(uncited),
        "passed": coverage >= 0.5,
    }


def check_citation_integrity(digest_text, comments):
    """Require citations to identify the correct source author and comment URL."""
    source_citations = {
        (str(c.get("author", "")).lower(), _comment_url_key(c))
        for c in comments
        if c.get("author") and c.get("author") != "[deleted]"
        and _comment_url_key(c)
    }
    valid = []
    invalid = []
    for username, url in extract_citations(digest_text):
        url_key = _normalize_reddit_url(url)
        citation = (username.lower(), url_key)
        if url_key and citation in source_citations:
            valid.append({"author": username, "url": url})
        else:
            invalid.append({"author": username, "url": url})

    return {
        "total": len(valid) + len(invalid),
        "valid_count": len(valid),
        "invalid_count": len(invalid),
        "valid": valid,
        "invalid": invalid,
        "passed": len(invalid) == 0,
    }


def check_dollar_amounts(digest_text, comments):
    """Check that every dollar amount in the digest exists in at least one source comment.

    Returns dict with verified amounts, unverified amounts, and pass/fail.
    """
    digest_amounts = extract_dollar_amounts(digest_text)

    source_text = " ".join(c.get("body", "") for c in comments)
    source_amounts = extract_dollar_amounts(source_text)

    verified = digest_amounts & source_amounts
    unverified = digest_amounts - source_amounts

    total = len(digest_amounts)
    return {
        "verified_count": len(verified),
        "unverified_count": len(unverified),
        "total": total,
        "verified": sorted(verified),
        "unverified": sorted(unverified),
        "passed": len(unverified) == 0,
    }


def check_numeric_claims(digest_text, comments):
    """Check percentages, multipliers, and point amounts in the digest against source.

    Returns dict with verified/unverified for each category.
    """
    source_text = " ".join(c.get("body", "") for c in comments)

    digest_pcts = extract_percentages(digest_text)
    source_pcts = extract_percentages(source_text)
    unverified_pcts = digest_pcts - source_pcts

    digest_mults = extract_multipliers(digest_text)
    source_mults = extract_multipliers(source_text)
    unverified_mults = digest_mults - source_mults

    digest_pts = extract_point_amounts(digest_text)
    source_pts = extract_point_amounts(source_text)
    unverified_pts = digest_pts - source_pts

    all_unverified = unverified_pcts | unverified_mults | unverified_pts
    return {
        "percentages": {
            "total": len(digest_pcts),
            "unverified": sorted(unverified_pcts),
        },
        "multipliers": {
            "total": len(digest_mults),
            "unverified": sorted(unverified_mults),
        },
        "point_amounts": {
            "total": len(digest_pts),
            "unverified": sorted(unverified_pts),
        },
        "passed": len(all_unverified) == 0,
    }


def check_claim_grounding(digest_text, comments):
    """Verify each numeric claim against source comments cited on the same line."""
    source_by_citation = {}
    for comment in comments:
        author = str(comment.get("author", "")).lower()
        url_key = _comment_url_key(comment)
        if author and url_key:
            source_by_citation[(author, url_key)] = comment.get("body", "")

    checked_count = 0
    ungrounded = []
    for line_number, line in enumerate(digest_text.splitlines(), start=1):
        claims = _numeric_values(line)
        if not any(claims.values()):
            continue

        cited_bodies = []
        for username, url in extract_citations(line):
            body = source_by_citation.get(
                (username.lower(), _normalize_reddit_url(url))
            )
            if body is not None:
                cited_bodies.append(body)

        grounded_values = _numeric_values(" ".join(cited_bodies))
        for category, values in claims.items():
            for value in values:
                checked_count += 1
                if value not in grounded_values[category]:
                    ungrounded.append({
                        "line": line_number,
                        "category": category,
                        "value": value,
                    })

    return {
        "checked_count": checked_count,
        "ungrounded_count": len(ungrounded),
        "ungrounded": ungrounded,
        "passed": len(ungrounded) == 0,
    }


def check_completeness(digest_text, comments, top_fraction=0.25):
    """Check that high-score source comments are represented in the digest.

    Looks at the top 25% of comments by score and checks whether
    each author appears in the digest (as a citation or plain mention).

    Returns dict with covered/missing high-signal comments and pass/fail.
    """
    if not comments:
        return {"top_count": 0, "covered": [], "missing": [], "passed": True}

    scored = sorted(comments, key=lambda c: c.get("score", 0), reverse=True)
    cutoff_idx = max(1, int(len(scored) * top_fraction))
    top_comments = scored[:cutoff_idx]

    cited_authors = {author.lower() for author, _ in extract_citations(digest_text)}
    covered = []
    missing = []

    for c in top_comments:
        author = c.get("author", "")
        if not author or author == "[deleted]":
            continue
        author_pattern = re.compile(
            rf"(?<![A-Za-z0-9_-])u/{re.escape(author)}(?![A-Za-z0-9_-])",
            re.IGNORECASE,
        )
        if author.lower() in cited_authors or author_pattern.search(digest_text):
            covered.append({"author": author, "score": c.get("score", 0)})
        else:
            missing.append({
                "author": author,
                "score": c.get("score", 0),
                "body_preview": c.get("body", "")[:80],
            })

    total = len(covered) + len(missing)
    return {
        "top_count": total,
        "covered_count": len(covered),
        "missing_count": len(missing),
        "covered": covered,
        "missing": missing,
        "passed": len(missing) == 0,
    }


def evaluation_passed(results):
    """Return True only when every registered quality check passes."""
    return all(result["passed"] for result in results.values())


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------
def evaluate(digest_text, comments):
    """Run all checks and return a combined report dict."""
    return {
        "citation_coverage": check_citation_coverage(digest_text, comments),
        "citation_integrity": check_citation_integrity(digest_text, comments),
        "dollar_amounts": check_dollar_amounts(digest_text, comments),
        "numeric_claims": check_numeric_claims(digest_text, comments),
        "claim_grounding": check_claim_grounding(digest_text, comments),
        "completeness": check_completeness(digest_text, comments),
    }


def format_report(results):
    """Format evaluation results as a human-readable string."""
    lines = []

    cit = results["citation_coverage"]
    status = "PASS" if cit["passed"] else "FAIL"
    lines.append(f"Citation coverage [{status}]: {cit['cited_count']}/{cit['total_authors']}"
                 f" authors cited ({cit['coverage']:.0%})")
    if cit["uncited_authors"]:
        for a in cit["uncited_authors"][:10]:
            lines.append(f"  - u/{a} not cited")
        if len(cit["uncited_authors"]) > 10:
            lines.append(f"  ... and {len(cit['uncited_authors']) - 10} more")

    integrity = results["citation_integrity"]
    status = "PASS" if integrity["passed"] else "FAIL"
    lines.append(
        f"Citation integrity [{status}]: {integrity['valid_count']}/"
        f"{integrity['total']} citations match a source comment"
    )
    for citation in integrity["invalid"][:5]:
        lines.append(
            f"  - u/{citation['author']} has an invalid or mismatched URL: "
            f"{citation['url']}"
        )

    dol = results["dollar_amounts"]
    status = "PASS" if dol["passed"] else "FAIL"
    lines.append(f"Dollar amounts [{status}]: {dol['verified_count']}/{dol['total']} verified")
    if dol["unverified"]:
        for a in dol["unverified"]:
            lines.append(f"  - ${a} not found in source comments")

    num = results["numeric_claims"]
    status = "PASS" if num["passed"] else "FAIL"
    parts = []
    for key in ("percentages", "multipliers", "point_amounts"):
        cat = num[key]
        if cat["unverified"]:
            parts.append(f"{key}: {', '.join(cat['unverified'])}")
    lines.append(f"Numeric claims [{status}]: "
                 + ("; ".join(parts) if parts else "all verified"))

    grounding = results["claim_grounding"]
    status = "PASS" if grounding["passed"] else "FAIL"
    lines.append(
        f"Claim grounding [{status}]: "
        f"{grounding['checked_count'] - grounding['ungrounded_count']}/"
        f"{grounding['checked_count']} line-level numeric claims verified"
    )
    for claim in grounding["ungrounded"][:5]:
        lines.append(
            f"  - line {claim['line']}: {claim['category']} "
            f"value {claim['value']} is not in the cited source"
        )

    comp = results["completeness"]
    status = "PASS" if comp["passed"] else "FAIL"
    lines.append(f"Completeness [{status}]: {comp['covered_count']}/{comp['top_count']}"
                 f" top-scored comments represented")
    if comp["missing"]:
        for m in comp["missing"][:5]:
            lines.append(f"  - u/{m['author']} ({m['score']} pts): {m['body_preview']}")
        if len(comp["missing"]) > 5:
            lines.append(f"  ... and {len(comp['missing']) - 5} more")

    passed = evaluation_passed(results)
    lines.append(f"\nOverall: {'PASS' if passed else 'FAIL'}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Evaluate digest quality against source comments"
    )
    parser.add_argument("digest", type=str, help="Path to digest markdown file")
    parser.add_argument("comments", type=str, help="Path to raw comments JSON file")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--verbose", action="store_true", help="Show detailed results")
    args = parser.parse_args()

    digest_path = Path(args.digest)
    comments_path = Path(args.comments)

    if not digest_path.exists():
        print(f"ERROR: Digest file not found: {digest_path}")
        sys.exit(1)
    if not comments_path.exists():
        print(f"ERROR: Comments file not found: {comments_path}")
        sys.exit(1)

    digest_text = digest_path.read_text(encoding="utf-8")
    raw = json.loads(comments_path.read_text(encoding="utf-8"))

    if isinstance(raw, list):
        comments = raw
    elif isinstance(raw, dict) and "results" in raw:
        comments = raw["results"]
    else:
        print("ERROR: Comments JSON must be a list or have a 'results' key")
        sys.exit(1)

    results = evaluate(digest_text, comments)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_report(results))

    passed = evaluation_passed(results)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
