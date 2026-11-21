"""Bounded local source cache and attributed observations, separate from delivery.

Stored evidence expires after 48 hours without a successful refresh. Explicit
removals cascade through derived observations. A failed refresh cannot silently
make stale evidence current. Counts describe observed reports, not population odds.
"""

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from offer_facts import extract_facts, fingerprint
from source_records import source_time, source_url_key, unavailable

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
 scope TEXT NOT NULL, id TEXT NOT NULL, subreddit TEXT NOT NULL, created TEXT NOT NULL,
 observed TEXT NOT NULL, first_seen TEXT NOT NULL, content_hash TEXT NOT NULL,
 payload TEXT NOT NULL, PRIMARY KEY(scope,id));
CREATE TABLE IF NOT EXISTS events (
 scope TEXT NOT NULL, event_key TEXT NOT NULL, source_id TEXT NOT NULL,
 story_key TEXT NOT NULL, variant_key TEXT NOT NULL, entity TEXT NOT NULL, kind TEXT NOT NULL,
 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(scope,event_key), FOREIGN KEY(scope,source_id) REFERENCES sources(scope,id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS windows (
 scope TEXT NOT NULL, subreddit TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
 complete INTEGER NOT NULL, PRIMARY KEY(scope,subreddit));
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, observed TEXT NOT NULL,
 metrics TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_story ON events(scope,story_key);
CREATE INDEX IF NOT EXISTS sources_observed ON sources(observed);
"""


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("An aware UTC observation time is required")
    return value.astimezone(timezone.utc)


def scope_key(monitor):
    return fingerprint({k: monitor.get(k) for k in ("name", "subreddits", "keywords", "title_filters")})


class IntelligenceStore:
    def __init__(self, path, *, max_sources=10000):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=10)
        try:
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA secure_delete=ON")
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
        except sqlite3.Error:
            self.conn.close()
            raise
        self.max_sources = max_sources

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.conn.close()

    def prune(self, now):
        cutoff = (utc(now) - timedelta(hours=48)).isoformat()
        with self.conn:
            removed = self.conn.execute("DELETE FROM sources WHERE observed <= ?", (cutoff,)).rowcount
            # Old window metadata cannot justify skipping an expired cache.
            self.conn.execute("DELETE FROM windows WHERE end <= ?", (cutoff,))
            self.conn.execute("DELETE FROM runs WHERE observed < ?", ((utc(now)-timedelta(days=30)).isoformat(),))
        if removed:
            with self.conn:
                self.conn.execute("UPDATE windows SET complete=0")
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return removed

    def remove(self, scope, identifiers):
        identifiers = set(identifiers)
        with self.conn:
            self.conn.executemany("DELETE FROM sources WHERE scope=? AND id=?", [(scope, sid) for sid in identifiers])
            if identifiers:
                # A source removed from a cached interval makes its old
                # completeness claim unusable for future incremental fetches.
                self.conn.execute("UPDATE windows SET complete=0 WHERE scope=?", (scope,))
        if identifiers:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def cached(self, scope, now):
        self.prune(now)
        return [json.loads(row[0]) for row in self.conn.execute(
            "SELECT payload FROM sources WHERE scope=? ORDER BY created,id", (scope,))]

    def fetch_start(self, scope, subreddit, start, end):
        row = self.conn.execute("SELECT * FROM windows WHERE scope=? AND subreddit=?", (scope, subreddit)).fetchone()
        if row and row["complete"] and utc(row["start"]) <= start <= utc(row["end"]) < end:
            return max(start, utc(row["end"]) - timedelta(minutes=5))
        return start

    def record_window(self, scope, subreddit, start, end, complete):
        previous = self.conn.execute("SELECT * FROM windows WHERE scope=? AND subreddit=?", (scope, subreddit)).fetchone()
        if previous and utc(previous["end"]) > end:
            return  # Replays cannot move a live watermark backward.
        if previous and previous["complete"] and complete and utc(previous["end"]) >= start:
            start = min(utc(previous["start"]), start)
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO windows VALUES (?,?,?,?,?)",
                              (scope, subreddit, start.isoformat(), end.isoformat(), int(complete)))

    def ingest(self, scope, sources, now, *, entities=None):
        stamp = utc(now).isoformat()
        decisions, removed, invalid = [], [], 0
        with self.conn:
            for source in sources:
                row = {k: v for k, v in source.items() if k not in {"parent_context", "matched_keywords", "analysis_context"}}
                if not row.get("source_kind"):
                    row["source_kind"] = "submission" if str(row.get("id", "")).startswith("t3_") else "comment"
                when = source_time(row)
                if not source_url_key(row) or when is None or when > utc(now):
                    invalid += 1
                    continue
                sid = row["id"]
                if unavailable(row):
                    self.conn.execute("DELETE FROM sources WHERE scope=? AND id=?", (scope, sid))
                    self.conn.execute("UPDATE windows SET complete=0 WHERE scope=?", (scope,))
                    removed.append(sid)
                    continue
                if row.get("author") in {None, "[deleted]"}:
                    # Replace the entire old record; no old name or excerpts
                    # survive in event payloads after account deletion.
                    row["author"] = "[deleted]"
                old = self.conn.execute("SELECT first_seen,content_hash FROM sources WHERE scope=? AND id=?", (scope, sid)).fetchone()
                content_hash = fingerprint({k: row.get(k) for k in ("body", "author", "post_title", "edited_utc")})
                first_seen = old["first_seen"] if old else stamp
                old_events = {r["event_key"]: dict(r) for r in self.conn.execute(
                    "SELECT * FROM events WHERE scope=? AND source_id=?", (scope, sid))}
                # REPLACE would cascade even for unchanged data, losing the
                # event's first observation. Use an actual upsert instead.
                self.conn.execute("""INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(scope,id) DO UPDATE SET subreddit=excluded.subreddit,created=excluded.created,
                    observed=excluded.observed,content_hash=excluded.content_hash,payload=excluded.payload""",
                    (scope, sid, row["subreddit"], when.isoformat(), stamp, first_seen, content_hash, json.dumps(row)))
                self.conn.execute("DELETE FROM events WHERE scope=? AND source_id=?", (scope, sid))
                events = extract_facts(row, entities=entities) if row["author"] != "[deleted]" else []
                for event in events:
                    prior = self.conn.execute("SELECT variant_key FROM events WHERE scope=? AND story_key=? AND first_seen <= ?",
                                              (scope, event["story_key"], stamp)).fetchall()
                    variants = {r[0] for r in prior}
                    existing = old_events.get(event["event_key"])
                    label = ("same_observation" if existing else "source_edited" if old and old["content_hash"] != content_hash
                             else "matching_report" if event["variant_key"] in variants
                             else "different_reported_terms" if variants else "new_report")
                    # Prior collected reports are not proof of prior publication.
                    event.update(observation=label, observed_at=stamp, prior_variant_count=len(variants),
                                 comparison_scope="previous_collection_not_publication")
                    self.conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)",
                                      (scope, event["event_key"], sid, event["story_key"], event["variant_key"],
                                       event["entity"], event["kind"], existing["first_seen"] if existing else stamp,
                                       stamp, json.dumps(event)))
                    decisions.append(event)
            excess = self.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] - self.max_sources
            if excess > 0:
                self.conn.execute("DELETE FROM sources WHERE rowid IN (SELECT rowid FROM sources ORDER BY observed LIMIT ?)", (excess,))
                self.conn.execute("UPDATE windows SET complete=0")
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {"events": decisions, "removed": removed, "invalid_sources": invalid,
                "capacity_evictions": max(0, excess)}

    def context(self, scope, sources, *, maximum=80):
        allowed = {s["id"] for s in sources if not s.get("context_only")}
        events = [json.loads(r[0]) for r in self.conn.execute(
            "SELECT payload FROM events WHERE scope=? ORDER BY last_seen DESC,event_key", (scope,))]
        events = [e for e in events if e["source_id"] in allowed]
        events.sort(key=lambda e: (e["observation"] == "same_observation", e["entity"], e["event_key"]))
        return {"meaning": "Unverified source-derived observations; prior collection is not prior publication. Different terms may reflect targeting, not a changed public offer.",
                "events": [{k: e[k] for k in ("source_id", "entity", "kind", "values", "observation", "evidence_status")} for e in events[:maximum]],
                "omitted_event_count": max(0, len(events)-maximum)}

    def record_metrics(self, scope, now, metrics):
        # Only aggregate operational counters are retained longer than sources.
        with self.conn:
            self.conn.execute("INSERT INTO runs(scope,observed,metrics) VALUES (?,?,?)", (scope, utc(now).isoformat(), json.dumps(metrics)))

    def report(self, scope, now, *, verified_ids=None):
        self.prune(now)
        sources = {r["id"]: json.loads(r["payload"]) for r in self.conn.execute("SELECT id,payload FROM sources WHERE scope=?", (scope,))}
        events = [json.loads(r[0]) for r in self.conn.execute("SELECT payload FROM events WHERE scope=? ORDER BY entity,kind", (scope,))]
        if verified_ids is not None:
            events = [e for e in events if e["source_id"] in verified_ids]
        grouped = {}
        for e in events:
            group = grouped.setdefault((e["entity"], e["kind"], e["variant_key"]), {"entity": e["entity"], "kind": e["kind"],
                "values": e["values"], "authors": set(), "sources": set(), "observations": set()})
            if e["author"] and e["author"] != "[deleted]":
                group["authors"].add(e["author"].casefold())
            group["sources"].add(e["source_id"])
            group["observations"].add(e["observation"])
        facts = []
        for group in grouped.values():
            facts.append({"entity": group["entity"], "kind": group["kind"], "values": group["values"],
                          "distinct_reporters": len(group["authors"]), "report_count": len(group["sources"]),
                          "observations": sorted(group["observations"]),
                          "last_observed": min(e["observed_at"] for e in events if e["source_id"] in group["sources"]),
                          "links": ["https://reddit.com" + source_url_key(sources[sid]) + "/" for sid in sorted(group["sources"])],
                          "evidence_status": "reddit_report_unverified"})
        return {"generated_at": utc(now).isoformat(), "source_count": len(sources), "event_count": len(events),
                "by_subreddit": dict(Counter(s["subreddit"] for s in sources.values())), "facts": facts,
                "runs": [dict(observed=r[0], **json.loads(r[1])) for r in self.conn.execute(
                    "SELECT observed,metrics FROM runs WHERE scope=? ORDER BY id DESC LIMIT 30", (scope,))],
                "limitations": ["Counts describe observed reports, not approval odds or exhaustive coverage.",
                                "One person can post more than once; distinct usernames are not guaranteed independent people.",
                                "Unknown terms remain unknown. Different reports do not establish an issuer-wide change.",
                                "Sources and derived facts expire after 48 hours without successful refresh."]}
