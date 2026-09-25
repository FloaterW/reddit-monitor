"""
SQLite storage for digest run history.

Stores runs, matched comments, keyword matches, and generated digests
so past data can be queried and trends tracked over time.

Usage:
    db = DigestDB("data/reddit_monitor.db")
    run_id = db.save_run(monitor_name="churning", time_filter="day",
                         posts_per_subreddit=10, comments=matched,
                         digest_md=summary, digest_path="digest.md",
                         raw_json_path="raw.json")
    runs = db.get_runs(limit=10)
    db.close()
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    email_status TEXT,
    quality_status TEXT,
    error TEXT,
    time_filter TEXT NOT NULL,
    posts_per_subreddit INTEGER NOT NULL,
    raw_comment_count INTEGER NOT NULL,
    matched_comment_count INTEGER NOT NULL,
    digest_path TEXT,
    raw_json_path TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    id TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    subreddit TEXT,
    post_title TEXT,
    post_permalink TEXT,
    author TEXT,
    score INTEGER,
    created TEXT,
    depth INTEGER,
    parent_id TEXT,
    body TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id),
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    comment_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    markdown TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_matches_run ON matches(run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_unique
    ON matches(run_id, comment_id, keyword);
CREATE UNIQUE INDEX IF NOT EXISTS idx_digests_run ON digests(run_id);
"""

_RUN_COLUMN_MIGRATIONS = {
    "completed_at": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'completed'",
    "email_status": "TEXT",
    "quality_status": "TEXT",
    "error": "TEXT",
}


class DigestDB:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self):
        with self.conn:
            self.conn.executescript(_SCHEMA)
            existing = {
                row["name"] for row in self.conn.execute("PRAGMA table_info(runs)")
            }
            for column, definition in _RUN_COLUMN_MIGRATIONS.items():
                if column not in existing:
                    self.conn.execute(
                        f"ALTER TABLE runs ADD COLUMN {column} {definition}"
                    )

    def save_run(self, monitor_name, time_filter, posts_per_subreddit,
                 comments, digest_md=None, digest_path=None, raw_json_path=None,
                 raw_comment_count=None, started_at=None, completed_at=None,
                 status="completed", email_status=None, quality_status=None,
                 error=None):
        """Save a complete run: metadata, comments, keyword matches, and digest.

        Args:
            monitor_name: Name of the monitor profile used.
            time_filter: Time window used for scraping.
            posts_per_subreddit: Number of posts scanned per subreddit.
            comments: List of matched comment dicts (with 'matched_keywords').
            digest_md: The generated digest markdown text.
            digest_path: Path where the digest was saved.
            raw_json_path: Path where raw JSON was saved.
            raw_comment_count: Total comments before keyword filtering.
            started_at: UTC datetime or ISO string captured before scraping began.
            completed_at: UTC datetime or ISO string captured after processing ended.
            status: Overall run outcome (for example, ``completed`` or ``failed``).
            email_status: Delivery outcome, if email was requested.
            quality_status: Digest evaluation outcome, if evaluation was enabled.
            error: Sanitized failure detail for unsuccessful runs.

        Returns:
            The run ID.
        """
        def isoformat(value, default=None):
            if value is None:
                return default
            return value.isoformat() if hasattr(value, "isoformat") else str(value)

        now = datetime.now(timezone.utc)
        started = isoformat(started_at, now.isoformat())
        completed = isoformat(completed_at, now.isoformat())
        if raw_comment_count is None:
            raw_comment_count = len(comments)

        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO runs (monitor_name, started_at, completed_at, status, "
                "email_status, quality_status, error, time_filter, "
                "posts_per_subreddit, raw_comment_count, matched_comment_count, "
                "digest_path, raw_json_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    monitor_name, started, completed, status, email_status,
                    quality_status, error, time_filter, posts_per_subreddit,
                    raw_comment_count, len(comments), digest_path, raw_json_path,
                ),
            )
            run_id = cursor.lastrowid

            for idx, c in enumerate(comments):
                comment_id = c.get("id", "")
                if not comment_id:
                    comment_id = f"_no_id_{run_id}_{idx}"
                self.conn.execute(
                    "INSERT OR IGNORE INTO comments "
                    "(id, run_id, subreddit, post_title, post_permalink, "
                    "author, score, created, depth, parent_id, body) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (comment_id, run_id, c.get("subreddit"),
                     c.get("post_title"), c.get("post_permalink"),
                     c.get("author"), c.get("score", 0), c.get("created"),
                     c.get("depth", 0), c.get("parent_id"), c.get("body", "")),
                )

                for kw in c.get("matched_keywords", []):
                    self.conn.execute(
                        "INSERT OR IGNORE INTO matches (run_id, comment_id, keyword) "
                        "VALUES (?, ?, ?)",
                        (run_id, comment_id, kw),
                    )

            if digest_md is not None:
                self.conn.execute(
                    "INSERT INTO digests (run_id, markdown, created_at) "
                    "VALUES (?, ?, ?)",
                    (run_id, digest_md, completed),
                )
        return run_id

    def get_runs(self, limit=10, monitor_name=None):
        """Return recent runs, newest first."""
        if monitor_name:
            rows = self.conn.execute(
                "SELECT * FROM runs WHERE monitor_name = ? "
                "ORDER BY id DESC LIMIT ?",
                (monitor_name, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_run_comments(self, run_id):
        """Return all comments for a specific run."""
        rows = self.conn.execute(
            "SELECT * FROM comments WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_run_digest(self, run_id):
        """Return the digest markdown for a specific run, or None."""
        row = self.conn.execute(
            "SELECT markdown FROM digests WHERE run_id = ?", (run_id,)
        ).fetchone()
        return row["markdown"] if row else None

    def get_keyword_counts(self, run_id):
        """Return keyword match counts for a specific run."""
        rows = self.conn.execute(
            "SELECT keyword, COUNT(*) as count FROM matches "
            "WHERE run_id = ? GROUP BY keyword ORDER BY count DESC",
            (run_id,),
        ).fetchall()
        return {r["keyword"]: r["count"] for r in rows}

    def close(self):
        self.conn.close()
