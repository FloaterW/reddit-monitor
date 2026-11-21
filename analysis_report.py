"""Local escaped HTML/JSON diagnostics; the newsletter template is unchanged."""

import html
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit


def collection_diagnostics(comments, *, extraction_batches=None):
    """Describe observed input only; these are counts, never recall percentages."""
    import re

    from editorial_retention import manufactured_spending_signal
    from editorial_selection import CARD_PATTERNS, priority_leads

    current = [c for c in comments if not c.get("context_only")]
    background = [c for c in comments if c.get("context_only")]
    communities = {}
    for source in comments:
        sub = str(source.get("subreddit") or "unknown").casefold().removeprefix("r/")
        counts = communities.setdefault(sub, {"current_comments": 0, "current_submissions": 0, "background_sources": 0})
        field = ("background_sources" if source.get("context_only") else
                 "current_submissions" if source.get("source_kind") == "submission" else "current_comments")
        counts[field] += 1
    context = Counter(str(c.get("context_status") or "unknown") for c in current if c.get("source_kind") != "submission")
    watchlist = {card: sum(bool(re.search(pattern, str(c.get("body") or ""), re.I)) for c in current)
                 for card, pattern in CARD_PATTERNS.items()}
    watchlist["manufactured_spending_operational_signals"] = sum(manufactured_spending_signal(str(c.get("body") or "")) for c in current)
    leads = priority_leads(current)
    result = {"source_count": len(comments), "current_source_count": len(current), "background_source_count": len(background),
              "by_subreddit": dict(sorted(communities.items())), "current_comment_context": dict(sorted(context.items())),
              "watchlist_current_body_mentions": watchlist, "watchlist_offer_outcome_leads": len(leads),
              "watchlist_review_only_leads": sum(lead.get("review_only", False) for lead in leads),
              "meaning": "Counts describe collected input, not all Reddit news. Watchlist mentions can be questions or repeated background; leads are unverified. Missing/partial context is not filled from a thread title."}
    if extraction_batches is not None:
        from verified_digest import _source_records
        sizes = [len(json.dumps(_source_records(batch), ensure_ascii=False)) for batch in extraction_batches]
        result["extraction"] = {"batches": len(sizes), "sources_per_batch": [len(b) for b in extraction_batches],
                                "source_payload_characters": sizes, "largest_source_payload_characters": max(sizes, default=0),
                                "meaning": "Shared-context source JSON only; instructions and repair text add prompt overhead. Oversized sources are retained intact."}
    return result


def write_report(path, report, *, editorial=None):
    from daily_digest import atomic_write_json, atomic_write_text

    path = Path(path)
    payload = {**report, "editorial": editorial or {}}
    atomic_write_json(path.with_suffix(".json"), payload)
    def esc(value):
        return html.escape(str(value), quote=True)
    facts = []
    for row in report.get("facts", []):
        links = []
        for link in row.get("links", []):
            parsed = urlsplit(link)
            if parsed.scheme == "https" and parsed.hostname == "reddit.com" and not parsed.username and not parsed.password:
                links.append(f'<a href="{esc(link)}" rel="noopener noreferrer">source</a>')
        facts.append("<tr>" + "".join(f"<td>{esc(value)}</td>" for value in (
            row["entity"], row["kind"], json.dumps(row["values"], ensure_ascii=False),
            row["distinct_reporters"], ", ".join(row["observations"]), row.get("last_observed", "unknown"))) + "<td>" + " · ".join(links) + "</td></tr>")
    runs = []
    for row in report.get("runs", []):
        runs.append("<tr>" + "".join(f"<td>{esc(row.get(key, 'unknown'))}</td>" for key in (
            "observed", "raw_comment_count", "matched_comment_count", "api_requests", "cached_sources_reused", "warning_count")) + "</tr>")
    sections = (editorial or {}).get("stories", [])
    reasons = "".join(f"<li><strong>{esc(r.get('topic', ''))}</strong> — {esc(r.get('status', ''))}: {esc(r.get('reason', ''))}</li>" for r in sections)
    document = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Digest analysis</title>
<style>body{font:15px/1.6 system-ui;margin:32px auto;padding:0 20px;max-width:1200px;color:#172033;background:#f7f9fc}h1,h2{line-height:1.25}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:10px;border-bottom:1px solid #dbe2ec;vertical-align:top}th{background:#eaf0f8}a{color:#2159ad}.table{overflow:auto}.note{padding:16px;background:#fff5d9;border-radius:8px}</style></head><body>
<h1>Digest coverage and reported facts</h1>"""
    document += f"<p>Updated {esc(report['generated_at'])}. Sources in the current cache: {esc(report['source_count'])}.</p>"
    document += '<div class="note">' + "<br>".join(esc(s) for s in report.get("limitations", [])) + "</div>"
    document += "<h2>Reported offers and outcomes</h2><p>Unknown fields are omitted. Reports are not verified issuer terms. Counts are not approval probabilities. Observation times describe local ingestion; offline replays do not check current availability.</p><div class=table><table><tr><th>Card/topic</th><th>Report type</th><th>Reported fields</th><th>Distinct reporters</th><th>Observed differences</th><th>Last observed</th><th>Evidence</th></tr>" + "".join(facts) + "</table></div>"
    document += "<p>Cached records by subreddit: " + esc(json.dumps(report.get("by_subreddit", {}))) + ". These are observed counts, not coverage percentages.</p>"
    diagnostics = (editorial or {}).get("collection_diagnostics") or report.get("collection_diagnostics")
    if diagnostics:
        document += "<h2>Current edition coverage diagnostics</h2><p>" + esc(diagnostics.get("meaning", "")) + "</p>"
        document += f"<p>Current sources: {esc(diagnostics.get('current_source_count', 0))}; background context: {esc(diagnostics.get('background_source_count', 0))}.</p>"
        community_rows = "".join("<tr>" + "".join(f"<td>{esc(value)}</td>" for value in (
            sub, counts.get("current_comments", 0), counts.get("current_submissions", 0), counts.get("background_sources", 0))) + "</tr>"
            for sub, counts in diagnostics.get("by_subreddit", {}).items())
        document += "<div class=table><table><tr><th>Community</th><th>Current comments</th><th>Current posts</th><th>Background context</th></tr>" + community_rows + "</table></div>"
        document += "<p>Current comment context: " + esc(json.dumps(diagnostics.get("current_comment_context", {}))) + ".</p>"
        document += "<p>Watchlist body mentions: " + esc(json.dumps(diagnostics.get("watchlist_current_body_mentions", {}))) + ". Mentions are not verified news.</p>"
        extraction = diagnostics.get("extraction")
        if extraction:
            document += f"<p>Extraction batches: {esc(extraction['batches'])}; largest source payload: {esc(extraction['largest_source_payload_characters'])} characters. {esc(extraction.get('meaning', ''))}</p>"
    document += "<h2>Collection trend</h2><div class=table><table><tr><th>Observed</th><th>Fetched</th><th>Retained</th><th>API requests</th><th>Refreshed cache</th><th>Warnings</th></tr>" + "".join(runs) + "</table></div>"
    document += "<h2>Editorial selection</h2><ul>" + (reasons or "<li>No newsletter selection ran for this collection-only pass.</li>") + "</ul></body></html>"
    atomic_write_text(path, document)
