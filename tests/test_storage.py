"""Tests for storage.py — SQLite run history."""

import sqlite3
from datetime import datetime, timezone

import pytest

from storage import DigestDB


def _sample_comments():
    return [
        {
            "id": "t1_abc",
            "subreddit": "churning",
            "post_title": "Daily Question Thread",
            "post_permalink": "https://reddit.com/r/churning/comments/xyz/daily/",
            "author": "alice",
            "score": 25,
            "created": "2025-06-24 12:00 UTC",
            "depth": 0,
            "parent_id": "",
            "body": "Chase Sapphire $50 bonus",
            "matched_keywords": ["chase", "sapphire", "bonus"],
        },
        {
            "id": "t1_def",
            "subreddit": "churning",
            "post_title": "Daily Question Thread",
            "post_permalink": "https://reddit.com/r/churning/comments/xyz/daily/",
            "author": "bob",
            "score": 10,
            "created": "2025-06-24 13:00 UTC",
            "depth": 1,
            "parent_id": "t1_abc",
            "body": "Can confirm the chase offer",
            "matched_keywords": ["chase"],
        },
    ]


class TestDigestDB:
    def test_creates_database(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = DigestDB(db_path)
        assert db_path.exists()
        db.close()

    def test_creates_parent_dirs(self, tmp_path):
        db_path = tmp_path / "sub" / "dir" / "test.db"
        db = DigestDB(db_path)
        assert db_path.exists()
        db.close()

    def test_save_and_retrieve_run(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        comments = _sample_comments()
        run_id = db.save_run(
            monitor_name="churning",
            time_filter="day",
            posts_per_subreddit=10,
            comments=comments,
            digest_md="# Test Digest",
            digest_path="test.md",
            raw_json_path="test.json",
        )
        assert run_id == 1

        runs = db.get_runs()
        assert len(runs) == 1
        assert runs[0]["monitor_name"] == "churning"
        assert runs[0]["matched_comment_count"] == 2
        assert runs[0]["digest_path"] == "test.md"
        db.close()

    def test_retrieve_comments(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        run_id = db.save_run(
            monitor_name="test",
            time_filter="day",
            posts_per_subreddit=5,
            comments=_sample_comments(),
        )
        comments = db.get_run_comments(run_id)
        assert len(comments) == 2
        authors = {c["author"] for c in comments}
        assert authors == {"alice", "bob"}
        db.close()

    def test_retrieve_digest(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        run_id = db.save_run(
            monitor_name="test",
            time_filter="day",
            posts_per_subreddit=5,
            comments=_sample_comments(),
            digest_md="# My Digest\n\nContent here.",
        )
        md = db.get_run_digest(run_id)
        assert md == "# My Digest\n\nContent here."
        db.close()

    def test_no_digest_returns_none(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        run_id = db.save_run(
            monitor_name="test",
            time_filter="day",
            posts_per_subreddit=5,
            comments=_sample_comments(),
        )
        assert db.get_run_digest(run_id) is None
        db.close()

    def test_keyword_counts(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        run_id = db.save_run(
            monitor_name="test",
            time_filter="day",
            posts_per_subreddit=5,
            comments=_sample_comments(),
        )
        counts = db.get_keyword_counts(run_id)
        assert counts["chase"] == 2
        assert counts["sapphire"] == 1
        assert counts["bonus"] == 1
        db.close()

    def test_multiple_runs(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        db.save_run(
            monitor_name="churning",
            time_filter="day",
            posts_per_subreddit=10,
            comments=_sample_comments(),
        )
        db.save_run(
            monitor_name="jobs",
            time_filter="week",
            posts_per_subreddit=5,
            comments=[{
                "id": "t1_xyz",
                "body": "hiring freeze",
                "author": "charlie",
                "matched_keywords": ["hiring"],
                "score": 5,
            }],
        )
        all_runs = db.get_runs()
        assert len(all_runs) == 2
        assert all_runs[0]["monitor_name"] == "jobs"

        churning_runs = db.get_runs(monitor_name="churning")
        assert len(churning_runs) == 1
        assert churning_runs[0]["monitor_name"] == "churning"
        db.close()

    def test_runs_ordered_newest_first(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        db.save_run("first", "day", 5, _sample_comments())
        db.save_run("second", "day", 5, _sample_comments())
        runs = db.get_runs()
        assert runs[0]["monitor_name"] == "second"
        assert runs[1]["monitor_name"] == "first"
        db.close()

    def test_raw_comment_count(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        db.save_run(
            monitor_name="test",
            time_filter="day",
            posts_per_subreddit=10,
            comments=_sample_comments(),
            raw_comment_count=500,
        )
        runs = db.get_runs()
        assert runs[0]["raw_comment_count"] == 500
        assert runs[0]["matched_comment_count"] == 2
        db.close()

    def test_persists_run_lifecycle_fields(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        started = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 9, 2, 12, 5, tzinfo=timezone.utc)
        db.save_run(
            "test",
            "day",
            5,
            _sample_comments(),
            started_at=started,
            completed_at=completed,
            status="failed",
            email_status="failed",
            quality_status="passed",
            error="SMTP delivery failed",
        )

        run = db.get_runs()[0]
        assert run["started_at"] == started.isoformat()
        assert run["completed_at"] == completed.isoformat()
        assert run["status"] == "failed"
        assert run["email_status"] == "failed"
        assert run["quality_status"] == "passed"
        assert run["error"] == "SMTP delivery failed"
        db.close()

    def test_failed_save_rolls_back_entire_run(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        invalid = _sample_comments()
        invalid[1]["score"] = object()

        with pytest.raises(sqlite3.ProgrammingError):
            db.save_run("bad", "day", 5, invalid)

        good_run_id = db.save_run("good", "day", 5, _sample_comments())
        runs = db.get_runs()
        assert len(runs) == 1
        assert runs[0]["id"] == good_run_id
        assert runs[0]["monitor_name"] == "good"
        assert len(db.get_run_comments(good_run_id)) == 2
        db.close()

    def test_migrates_existing_runs_table(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        connection = sqlite3.connect(db_path)
        connection.execute(
            "CREATE TABLE runs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "monitor_name TEXT NOT NULL, started_at TEXT NOT NULL, "
            "time_filter TEXT NOT NULL, posts_per_subreddit INTEGER NOT NULL, "
            "raw_comment_count INTEGER NOT NULL, "
            "matched_comment_count INTEGER NOT NULL, digest_path TEXT, "
            "raw_json_path TEXT)"
        )
        connection.commit()
        connection.close()

        db = DigestDB(db_path)
        columns = {
            row["name"] for row in db.conn.execute("PRAGMA table_info(runs)")
        }
        assert {
            "completed_at",
            "status",
            "email_status",
            "quality_status",
            "error",
        } <= columns
        db.close()

    def test_empty_comments(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        run_id = db.save_run(
            monitor_name="test",
            time_filter="day",
            posts_per_subreddit=5,
            comments=[],
        )
        assert db.get_run_comments(run_id) == []
        assert db.get_keyword_counts(run_id) == {}
        db.close()

    def test_empty_id_comments_not_lost(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        comments = [
            {"id": "", "body": "first comment", "author": "alice",
             "matched_keywords": ["chase"], "score": 10},
            {"id": "", "body": "second comment", "author": "bob",
             "matched_keywords": ["amex"], "score": 5},
        ]
        run_id = db.save_run("test", "day", 5, comments)
        stored = db.get_run_comments(run_id)
        assert len(stored) == 2
        db.close()

    def test_limit_respected(self, tmp_path):
        db = DigestDB(tmp_path / "test.db")
        for i in range(5):
            db.save_run(f"run-{i}", "day", 5, _sample_comments())
        runs = db.get_runs(limit=3)
        assert len(runs) == 3
        db.close()
