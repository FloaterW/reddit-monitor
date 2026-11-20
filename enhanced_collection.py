"""Opt-in post/comment collection and bounded cache reuse; no model or email calls."""

import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from delivery_guard import edition_lock
from intelligence_store import IntelligenceStore, scope_key
from reddit_api import RedditAPIClient
from reddit_scraper import collection_diagnostics, collection_warning, request_budget
from source_records import source_time, unavailable


@contextmanager
def _store(path, warnings):
    try:
        store = IntelligenceStore(path)
    except (sqlite3.DatabaseError, OSError):
        # Optional cached analysis must not take down fresh collection. Keep a
        # corrupt store untouched; a later repair can inspect it separately.
        warnings.append({"subreddit": "", "reason": "API cache unavailable: collected a fresh full window without persistent cache reuse."})
        store = IntelligenceStore(":memory:")
    with store:
        yield store


def _needed(rows, start, end):
    """Current sources plus available ancestry, excluding unrelated old padding."""
    by_id = {s["id"]: s for s in rows}
    wanted = {sid for sid, row in by_id.items() if (when := source_time(row)) and start <= when <= end}
    for sid in list(wanted):
        visited = {sid}
        child = by_id[sid]
        parent = child.get("parent_id")
        for _ in range(64):
            if not parent or parent not in by_id or parent in visited:
                break
            row = by_id[parent]
            if row.get("post_permalink") != child.get("post_permalink") or not (when := source_time(row)) or when > end:
                break
            wanted.add(parent)
            visited.add(parent)
            parent = row.get("parent_id")
    return [row for sid, row in by_id.items() if sid in wanted]


def collect_enhanced(monitor, store_path, *, window_end, window_start=None,
                     stats=None, collection_audit=None, now=None):
    """Share one rate-limited API client and protect the new store across runs."""
    import daily_digest as digest

    if digest._collection_source() != "api":
        raise ValueError("Enhanced collection requires DIGEST_REDDIT_SOURCE=api")
    if monitor.get("time_filter", "day") != "day":
        raise ValueError("Enhanced collection currently supports daily windows only")
    if window_end.tzinfo is None:
        raise ValueError("A timezone-aware collection cutoff is required")
    end = window_end.astimezone(timezone.utc).replace(second=0, microsecond=0)
    start = window_start or end - timedelta(days=1)
    if start.tzinfo is None or not 24 <= (end-start).total_seconds()/3600 <= 25:
        raise ValueError("Invalid enhanced daily window")
    now = now or datetime.now(timezone.utc)
    if end > now:
        raise ValueError("Cannot collect a future window")
    scope = scope_key(monitor)
    stats = stats if stats is not None else {}
    cache_warnings = []
    # A fixed key is intentional: different monitors sharing the same store
    # directory must not run overlapping collectors against one OAuth client.
    with edition_lock(store_path, monitor="reddit-api-collection", edition_date="2000-01-01",
                      destination="collection@local.invalid"), _store(store_path, cache_warnings) as store:
        expired = store.prune(now)
        cached = store.cached(scope, now)
        fresh, refreshed = {}, {}
        removed, unverified = set(), set()
        checkpoints = []
        with collection_diagnostics() as warnings, RedditAPIClient.from_environment() as client:
            for sub in monitor["subreddits"]:
                since = store.fetch_start(scope, sub, start, end)
                before = len(warnings)
                title_filter = monitor.get("title_filters", {}).get(sub)
                print(f"  API posts/comments r/{sub}: {since.isoformat()} to {end.isoformat()}", flush=True)
                with request_budget(300):
                    rows = client.fetch_submissions(sub, since, end, title_filter=title_filter)
                    rows += client.fetch_subreddit(sub, since, end, title_filter=title_filter, include_submissions=True)
                fresh.update({r["id"]: r for r in _needed(rows, since, end)})
                checkpoints.append((sub, since, not warnings[before:]))
            # Cached bodies are refreshed before reuse, so deletions and edits
            # cannot be silently resurrected from the midday snapshot.
            candidates = [r for r in _needed([*cached, *fresh.values()], start, end) if r["id"] not in fresh]
            if candidates:
                with request_budget(120):
                    refreshed, removed, unverified = client.refresh_sources(candidates, max_batches=15)
                if unverified:
                    collection_warning("", f"API cache refresh incomplete: {len(unverified)} cached sources excluded; coverage may have gaps.")
            api_requests = client.request_count
        raw_map = {**refreshed, **fresh}
        warnings.extend(cache_warnings)
        removed.update(sid for sid, row in raw_map.items() if unavailable(row))
        store.remove(scope, removed)
        raw = _needed([row for sid, row in raw_map.items() if sid not in removed], start, end)
        for row in raw:
            if row.get("source_kind") == "comment":
                issue, _ = RedditAPIClient._ancestry_problem(row, {r["id"]: r for r in raw})
                row["context_status"] = "partial" if issue else (
                    "available" if row.get("parent_id", "").startswith("t1_") else "top_level")
        # Retain the full source only once in the cache. Old ancestors remain
        # context; their creation time is never relabelled as today's event.
        stored_rows = [{**r, "context_only": not start <= source_time(r) <= end} for r in raw]
        result = store.ingest(scope, stored_rows, now)
        if result["invalid_sources"] or result["capacity_evictions"]:
            warnings.append({"subreddit": "", "reason": "API cache validation or capacity limit excluded stored evidence; coverage may have gaps."})
        for sub, since, complete in checkpoints:
            # A failed cache refresh must not advance a watermark over the gap.
            store.record_window(scope, sub, since, end, complete and not unverified
                                and not result["invalid_sources"] and not result["capacity_evictions"])
        # Disclose the scope even when the bounded listings report no failures.
        warnings.append({"subreddit": "", "reason": "API source scope: bounded submission and comment listings with available parent context; not exhaustive Reddit coverage."})
        comments = digest.scrape_all(monitor["keywords"], monitor["subreddits"], monitor["posts_per_subreddit"], "day",
                                    post_sort=monitor["post_sort"], title_filters=monitor.get("title_filters", {}),
                                    stats=stats, window_start=start, window_end=end, collection_audit=collection_audit,
                                    raw_sources=raw, raw_warnings=warnings)
        stats.update(source_scope="posts_and_comments", api_requests=api_requests,
                     cached_sources_reused=len(refreshed), cache_sources_excluded=len(unverified),
                     removed_sources=len(removed), expired_cache_sources=expired,
                     source_kind_counts=dict(Counter(r.get("source_kind", "comment") for r in comments if not r.get("context_only"))))
        metrics = {key: stats.get(key) for key in ("window_start", "window_end", "raw_comment_count", "matched_comment_count",
                   "api_requests", "cached_sources_reused", "cache_sources_excluded", "removed_sources", "expired_cache_sources", "source_kind_counts")}
        metrics["warning_count"] = len(warnings)
        metrics["by_subreddit"] = dict(Counter(r["subreddit"] for r in comments if not r.get("context_only")))
        metrics["fact_observations"] = dict(Counter(e["observation"] for e in result["events"]))
        store.record_metrics(scope, now, metrics)
        return comments, store.context(scope, comments), store.report(scope, now, verified_ids=set(raw_map)-removed)
