"""Bounded local filtering diagnostics; never fetch or interpret source content."""

import re
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit

MAX_RECORDS = 5_000
MAX_EXCERPT_CHARS = 600
MAX_MATCHES_PER_ORIGIN = 64
MAX_WARNINGS = 100


def _text(value, maximum):
    return "".join(char if char.isprintable() else " " for char in str(value or ""))[:maximum]


def _identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) else None


def _permalink(value):
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme not in {"https", "http"} or url.hostname not in
                {"reddit.com", "www.reddit.com", "old.reddit.com"}
                or url.username or url.password or url.port not in {None, 80, 443}
                or not re.fullmatch(r"/r/[A-Za-z0-9_]+/comments/[A-Za-z0-9]+(?:/[A-Za-z0-9_%.-]+)*/?", url.path)):
            return None
        return "https://reddit.com" + url.path
    except ValueError:
        return None


def _timestamp(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def set_audit_warnings(payload, warnings):
    payload["collection_warning_count"] = len(warnings)
    payload["collection_warnings_truncated"] = len(warnings) > MAX_WARNINGS
    payload["collection_warnings"] = [
        {"subreddit": _text(item.get("subreddit"), 128), "reason": _text(item.get("reason"), 1_000)}
        for item in warnings[:MAX_WARNINGS]
    ]


def build_collection_audit(raw, decisions, *, window_start=None, window_end=None,
                           warnings=(), max_records=MAX_RECORDS,
                           excerpt_chars=MAX_EXCERPT_CHARS):
    """Summarize decisions already made by the collector, without changing them.

    Indices refer to the post-title-filter raw list; duplicates remain separate
    observations. Aggregate counts include every observation, even when bounded
    detailed records or excerpts are omitted. No author names are added.
    """
    max_records = min(MAX_RECORDS, max(0, max_records))
    excerpt_chars = min(MAX_EXCERPT_CHARS, max(0, excerpt_chars))
    counts, feeds, records = Counter(), {}, []
    for index, source in enumerate(raw):
        decision = decisions[index]
        reason, retained = decision["reason"], decision["retained"]
        counts[reason] += 1
        feed = _text(source.get("subreddit"), 128) or "unknown"
        aggregate = feeds.setdefault(feed, {
            "observed_count": 0, "retained_count": 0, "reason_counts": {},
            "earliest_observed_utc": None, "latest_observed_utc": None,
            "pre_thread_filter_count": None,
        })
        aggregate["observed_count"] += 1
        aggregate["retained_count"] += int(retained)
        aggregate["reason_counts"][reason] = aggregate["reason_counts"].get(reason, 0) + 1
        created = _timestamp(source.get("created"))
        if created:
            aggregate["earliest_observed_utc"] = min(filter(None, [aggregate["earliest_observed_utc"], created]))
            aggregate["latest_observed_utc"] = max(filter(None, [aggregate["latest_observed_utc"], created]))
        if len(records) >= max_records:
            continue
        origins = decision.get("keyword_matches", {})
        record = {
            "observation_index": index,
            "source_id": _identifier(source.get("id")), "subreddit": feed,
            "created": _text(source.get("created"), 128), "created_utc": created,
            "post_permalink": _permalink(source.get("post_permalink")),
            "retained": retained, "reason": reason,
            "keyword_matches": {origin: [_text(term, 128) for term in origins.get(origin, [])[:MAX_MATCHES_PER_ORIGIN]]
                                for origin in ("body", "title", "parent")},
            "keyword_matches_truncated": any(len(origins.get(origin, [])) > MAX_MATCHES_PER_ORIGIN
                                             or any(len(str(term)) > 128 for term in origins.get(origin, []))
                                             for origin in ("body", "title", "parent")),
        }
        if not retained:
            body = source.get("body", "")
            record.update(post_title=_text(source.get("post_title"), 240),
                          post_title_truncated=len(str(source.get("post_title") or "")) > 240,
                          body_excerpt=_text(body, excerpt_chars),
                          body_excerpt_truncated=len(str(body or "")) > excerpt_chars)
        records.append(record)
    retained_count = sum(feed["retained_count"] for feed in feeds.values())
    result = {
        "version": 1, "mode": "live_collection", "filtering_observed": True,
        "scope": "after_thread_title_filter_and_collector_deduplication",
        "window_start": window_start, "window_end": window_end,
        "observed_count": len(raw), "retained_count": retained_count,
        "rejected_count": len(raw) - retained_count, "reason_counts": dict(counts),
        "feeds": feeds, "records": records,
        "records_saved": len(records), "records_omitted": len(raw) - len(records),
        "records_truncated": len(records) < len(raw),
        "limits": {"max_records": max_records, "max_rejected_excerpt_chars": excerpt_chars,
                   "max_keyword_matches_per_origin": MAX_MATCHES_PER_ORIGIN,
                   "max_collection_warnings": MAX_WARNINGS},
        "limitations": [
            "Upstream thread-title exclusions and collector-level duplicates are not observed here; their counts are unknown.",
            ("Includes fetched submission bodies and comments; unfetched Reddit material remains unknown."
             if any(s.get("source_kind") == "submission" for s in raw) else
             "Unfetched Reddit material and standalone post bodies are absent; RSS records also lack parent replies."),
            "Retention is a filtering decision, not a judgment that a source is useful or factually correct.",
            "Rejected excerpts are bounded and may omit context; aggregate counts include omitted audit records.",
        ],
    }
    set_audit_warnings(result, warnings)
    return result


def replay_audit_manifest(comments, source_artifact, original_audit=None):
    """A saved-input copy must not pretend it observed the original filtering."""
    return {
        "version": 1, "mode": "saved_input_replay", "filtering_observed": False,
        "input_record_count": len(comments), "source_artifact": str(source_artifact),
        "original_collection_audit": str(original_audit) if original_audit else None,
        "limitations": ["No collection or filtering was repeated. Rejected or unfetched original records cannot be reconstructed from this saved input."],
    }
