"""Regression tests for run status, delivery failures, and atomic output."""

import json
from datetime import datetime
from unittest.mock import patch

from check_digest_ran import (
    check_run_status,
    find_todays_digests,
    parse_due,
)
from check_digest_ran import (
    main as watchdog_main,
)
from daily_digest import atomic_write_text, main
from storage import DigestDB


def _comment():
    return {
        "id": "t1_abc",
        "author": "alice",
        "score": 10,
        "body": "I received a $50 credit",
        "created": "2026-09-02 12:00 UTC",
        "depth": 0,
        "parent_id": "",
        "subreddit": "test",
        "post_title": "Test post",
        "post_permalink": "https://reddit.com/r/test/comments/post/title/",
        "matched_keywords": ["credit"],
    }


def _summary(amount="50"):
    return (
        "# Daily Digest\n\n"
        "[u/alice](https://reddit.com/r/test/comments/post/title/abc/) "
        f"reported a **${amount}** credit."
    )


def _run_args(tmp_path, raw_path, *extra):
    return [
        "--from-json", str(raw_path),
        "--save", str(tmp_path / "digest.md"),
        "--save-raw", str(tmp_path / "raw-copy.json"),
        "--db", str(tmp_path / "monitor.db"),
        "--status-file", str(tmp_path / "status.json"),
        "--quiet-summary",
        *extra,
    ]


class TestAtomicOutputs:
    def test_atomic_write_replaces_existing_file(self, tmp_path):
        destination = tmp_path / "nested" / "output.txt"
        destination.parent.mkdir()
        destination.write_text("old", encoding="utf-8")

        atomic_write_text(destination, "new")

        assert destination.read_text(encoding="utf-8") == "new"
        assert not list(destination.parent.glob("*.tmp"))


class TestDigestRunLifecycle:
    def test_success_records_quality_delivery_and_real_timestamps(self, tmp_path):
        raw_path = tmp_path / "source.json"
        raw_path.write_text(json.dumps([_comment()]), encoding="utf-8")

        with patch("daily_digest.summarize", return_value=_summary()):
            exit_code = main(_run_args(tmp_path, raw_path, "--quality", "strict", "--no-email"))

        assert exit_code == 0
        status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
        assert status["status"] == "completed"
        assert status["exit_code"] == 0
        assert status["email_status"] == "disabled"
        assert status["quality_status"] == "passed"
        assert (tmp_path / "digest.evaluation.json").exists()

        db = DigestDB(tmp_path / "monitor.db")
        run = db.get_runs()[0]
        db.close()
        assert run["raw_comment_count"] == 1
        assert run["status"] == "completed"
        assert run["started_at"] < run["completed_at"]

    def test_strict_quality_failure_blocks_email(self, tmp_path):
        raw_path = tmp_path / "source.json"
        raw_path.write_text(json.dumps([_comment()]), encoding="utf-8")

        with (
            patch("daily_digest.summarize", return_value=_summary("500")),
            patch("daily_digest.send_email") as send_email,
        ):
            exit_code = main(_run_args(tmp_path, raw_path, "--quality", "strict"))

        assert exit_code == 3
        send_email.assert_not_called()
        status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
        assert status["status"] == "failed"
        assert status["quality_status"] == "failed"
        assert status["email_status"] == "blocked_by_quality_gate"

    def test_email_failure_is_a_failed_run(self, tmp_path):
        raw_path = tmp_path / "source.json"
        raw_path.write_text(json.dumps([_comment()]), encoding="utf-8")

        with (
            patch("daily_digest.summarize", return_value=_summary()),
            patch("daily_digest.send_email", return_value="failed"),
        ):
            exit_code = main(_run_args(tmp_path, raw_path, "--quality", "off"))

        assert exit_code == 4
        status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
        assert status["status"] == "failed"
        assert status["email_status"] == "failed"
        db = DigestDB(tmp_path / "monitor.db")
        assert db.get_runs()[0]["status"] == "failed"
        db.close()

    def test_invalid_json_shape_fails_and_records_status(self, tmp_path):
        raw_path = tmp_path / "source.json"
        raw_path.write_text(json.dumps({"unexpected": True}), encoding="utf-8")

        exit_code = main(_run_args(tmp_path, raw_path, "--no-email"))

        assert exit_code == 1
        status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
        assert status["status"] == "failed"
        assert "list of comment objects" in status["error"]


class TestWatchdogStatus:
    def test_due_time_rejects_out_of_range_values(self):
        assert parse_due("99:99") == (19, 30)
        assert parse_due("18:05") == (18, 5)

    def test_success_requires_nonempty_digest(self, tmp_path):
        digest = tmp_path / "digest.md"
        digest.write_text("# Digest", encoding="utf-8")
        status_path = tmp_path / "status.json"
        status_path.write_text(
            json.dumps({
                "date": "2026-09-02",
                "status": "completed",
                "digest_path": str(digest),
                "email_status": "sent",
            }),
            encoding="utf-8",
        )

        state, _ = check_run_status(status_path, datetime(2026, 9, 2, 20, 0))
        assert state == "success"

        digest.write_text("  \n", encoding="utf-8")
        state, _ = check_run_status(status_path, datetime(2026, 9, 2, 20, 0))
        assert state == "failed"

    def test_filename_fallback_ignores_empty_digest(self, tmp_path, monkeypatch):
        import check_digest_ran

        monkeypatch.setattr(check_digest_ran, "PROJECT_DIR", tmp_path)
        (tmp_path / "digest_20260902_1830.md").write_text("", encoding="utf-8")
        assert find_todays_digests(datetime(2026, 9, 2)) == []

    def test_overdue_failed_status_returns_failure_when_alert_fails(self, tmp_path):
        status = tmp_path / "status.json"
        status.write_text(
            json.dumps({
                "date": "2026-09-02",
                "status": "failed",
                "error": "email failed",
            }),
            encoding="utf-8",
        )
        with patch("check_digest_ran.send_alert", return_value=False) as alert:
            exit_code = watchdog_main(
                ["--status-file", str(status), "--due", "19:30"],
                now=datetime(2026, 9, 2, 20, 0),
            )
        assert exit_code == 1
        alert.assert_called_once()

    def test_failure_before_due_does_not_alert(self, tmp_path):
        status = tmp_path / "status.json"
        status.write_text(
            json.dumps({"date": "2026-09-02", "status": "running"}),
            encoding="utf-8",
        )
        with patch("check_digest_ran.send_alert") as alert:
            exit_code = watchdog_main(
                ["--status-file", str(status), "--due", "19:30", "--quiet"],
                now=datetime(2026, 9, 2, 19, 0),
            )
        assert exit_code == 0
        alert.assert_not_called()


class TestFailureNotifier:
    def test_missing_credentials_is_reported_as_notifier_failure(self, monkeypatch):
        import notify_failure

        monkeypatch.setattr(notify_failure, "GMAIL_APP_PASSWORD", "")
        monkeypatch.setattr(notify_failure.sys, "argv", ["notify_failure.py", "4"])
        assert notify_failure.main() == 1

    def test_log_reader_returns_only_requested_tail(self, tmp_path):
        from notify_failure import read_log_tail

        log = tmp_path / "run.log"
        log.write_text("one\ntwo\nthree\n", encoding="utf-8")
        assert read_log_tail(log, lines=2) == "two\nthree"
