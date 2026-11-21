"""Offline collection/replay integration checks for bounded continuity windows."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import daily_digest as digest

END = datetime(2026, 11, 4, 23, 30, 2, tzinfo=timezone.utc)


class FixedClockMeta(type):
    def __instancecheck__(cls, instance):
        return isinstance(instance, datetime)


class FixedClock(datetime, metaclass=FixedClockMeta):
    @classmethod
    def now(cls, tz=None):
        return END.astimezone(tz) if tz else END.replace(tzinfo=None)


def source(identifier, created):
    return {
        "id": "t1_" + identifier, "author": "reporter", "body": "BFMR changed its purchase cap.",
        "created": created, "subreddit": "churning", "post_title": "Buying group discussion",
        "post_permalink": "https://reddit.com/r/churning/comments/thread/topic/",
    }


def prior_delivery(tmp_path, hours):
    end = END.replace(second=0) - timedelta(hours=hours)
    raw = tmp_path / "prior.json"
    raw.write_text("[]", encoding="utf-8")
    raw.with_suffix(".coverage.json").write_text(json.dumps({
        "window_start": (end - timedelta(hours=24)).isoformat(),
        "window_end": end.isoformat(), "collection_warnings": [], "replay_unknown": False,
    }), encoding="utf-8")
    status = tmp_path / "live-status.json"
    status.write_text(json.dumps({
        "monitor": "credit-card-churning", "status": "completed_with_warnings",
        "email_status": "sent", "raw_json_path": str(raw),
    }), encoding="utf-8")
    return status


def run_arguments(tmp_path, status, *extra, monitor="churning"):
    return [
        "--monitor", monitor, "--subreddits", "churning", "--source-safe",
        "--quality", "off", "--no-email", "--no-db", "--quiet-summary",
        "--save", str(tmp_path / "new.md"), "--save-raw", str(tmp_path / "new.json"),
        "--status-file", str(status), *extra,
    ]


def test_daily_shift_reaches_rss_and_local_filter_before_prior_status_is_replaced(tmp_path):
    status = prior_delivery(tmp_path, 25)
    rows = [
        source("before", "2026-11-03 22:29 UTC"),
        source("boundary", "2026-11-03 22:30 UTC"),
        source("transition", "2026-11-03 23:00 UTC"),
        source("end", "2026-11-04 23:30 UTC"),
        source("future", "2026-11-04 23:31 UTC"),
    ]
    with (
        patch.object(digest, "datetime", FixedClock),
        patch.object(digest, "old_reddit_available", return_value=False),
        patch.object(digest, "fetch_subreddit_comments_rss", return_value=rows) as rss,
        patch.object(digest, "request_budget", wraps=digest.request_budget) as budgets,
        patch.object(digest.time, "sleep"),
        patch.object(digest, "summarize", return_value="# Daily Digest\n\nSource-led summary.") as summarize,
        patch.object(digest, "send_email") as email,
    ):
        result = digest.main(run_arguments(tmp_path, status))

    assert result == 0
    assert rss.call_args.kwargs["cutoff"] == datetime(2026, 11, 3, 22, 30, tzinfo=timezone.utc)
    assert [row["id"] for row in summarize.call_args.args[0]] == [
        "t1_boundary", "t1_transition", "t1_end",
    ]
    assert summarize.call_args.args[1] == "25 hours"
    assert summarize.call_args.kwargs["edition_cutoff"] == "2026-11-04T23:30:00+00:00"
    assert [call.args[0] for call in budgets.call_args_list] == [300]
    email.assert_not_called()
    coverage = json.loads((tmp_path / "new.coverage.json").read_text(encoding="utf-8"))
    assert coverage["window_start"] == "2026-11-03T22:30:00+00:00"
    assert coverage["window_end"] == "2026-11-04T23:30:00+00:00"
    text = (tmp_path / "new.md").read_text(encoding="utf-8")
    assert "Daily collection window extended" in text
    assert "at most 25 hours" in text and "not exhaustive" in text


def test_large_gap_keeps_normal_cutoff_and_discloses_uncollected_interval(tmp_path):
    status = prior_delivery(tmp_path, 26)
    rows = [source("gap", "2026-11-03 23:00 UTC"), source("current", "2026-11-04 10:00 UTC")]
    with (
        patch.object(digest, "datetime", FixedClock),
        patch.object(digest, "old_reddit_available", return_value=False),
        patch.object(digest, "fetch_subreddit_comments_rss", return_value=rows) as rss,
        patch.object(digest.time, "sleep"),
        patch.object(digest, "summarize", return_value="# Daily Digest\n\nSource-led summary.") as summarize,
    ):
        assert digest.main(run_arguments(tmp_path, status)) == 0
    assert rss.call_args.kwargs["cutoff"] == datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc)
    assert [row["id"] for row in summarize.call_args.args[0]] == ["t1_current"]
    text = (tmp_path / "new.md").read_text(encoding="utf-8")
    assert "Coverage continuity gap: 2 hours" in text and "was not collected" in text


@pytest.mark.parametrize("hours", [23.9, 25.1, 48])
def test_invalid_explicit_continuity_window_is_rejected_before_network(hours):
    with patch.object(digest, "_fetch_all_comments") as fetch:
        with pytest.raises(ValueError, match="between 24 and 25"):
            digest.scrape_all(["BFMR"], ["churning"], 100, "day", window_end=END,
                              window_start=END.replace(second=0) - timedelta(hours=hours))
    fetch.assert_not_called()


@pytest.mark.parametrize("reference", ["sidecar", "date_only", "unknown"])
@pytest.mark.parametrize("profile,title", [
    ("churning", "Daily Digest"), ("job-market", "Job Market Digest"),
])
def test_replay_uses_original_reference_without_mutating_inputs_or_live_artifacts(tmp_path, reference, profile, title):
    original = tmp_path / "saved.json"
    original.write_text(json.dumps([source("old", "2026-11-02 12:00 UTC")]), encoding="utf-8")
    sidecar = original.with_suffix(".coverage.json")
    if reference == "sidecar":
        sidecar.write_text(json.dumps({
            "collection_warnings": [], "window_start": "2026-11-01T22:30:00+00:00",
            "window_end": "2026-11-02T22:30:00+00:00", "replay_unknown": False,
        }), encoding="utf-8")
    production_status = prior_delivery(tmp_path, 25)
    ledger = tmp_path / "production-delivery.db"
    ledger.write_bytes(b"This production ledger must never be opened during a no-email preview.")
    history = tmp_path / "history.db"
    with sqlite3.connect(history) as connection:
        connection.executescript("""
            CREATE TABLE runs (id INTEGER, monitor_name TEXT, started_at TEXT, email_status TEXT, status TEXT);
            CREATE TABLE digests (run_id INTEGER, markdown TEXT);
            INSERT INTO runs VALUES (1, 'credit-card-churning', '2026-10-31T22:30:00+00:00', 'sent', 'completed');
            INSERT INTO digests VALUES (1, '# Old edition\n\n- Prior source-attributed report.');
        """)
    protected = [original, production_status, ledger, history]
    if sidecar.exists():
        protected.append(sidecar)
    before = {path: path.read_bytes() for path in protected}
    extra = ["--from-json", str(original), "--history-db", str(history), "--delivery-ledger", str(ledger)]
    if reference == "date_only":
        extra += ["--digest-date", "2026-11-02"]
    with (
        patch.object(digest, "DEFAULT_STATUS_FILE", production_status),
        patch.object(digest, "datetime", FixedClock),
        patch.object(digest, "scrape_all", side_effect=AssertionError("Replay must not collect")),
        patch("coverage_continuity.resolve_daily_window", side_effect=AssertionError("Replay must not shift its window")),
        patch.object(digest, "send_email", side_effect=AssertionError("Replay must not email")),
        patch.object(digest, "summarize", return_value="# Daily Digest\n\nSource-led summary.") as summarize,
    ):
        assert digest.main(run_arguments(tmp_path, tmp_path / "replay-status.json", *extra,
                                         monitor=profile)) == 0
    expected = {"sidecar": "2026-11-02T22:30:00+00:00", "date_only": "2026-11-02", "unknown": None}
    assert summarize.call_args.kwargs["edition_cutoff"] == expected[reference]
    expected_date = "2026-11-04" if reference == "unknown" else "2026-11-02"
    expected_label = "November 04, 2026" if reference == "unknown" else "November 02, 2026"
    assert (tmp_path / "new.md").read_text(encoding="utf-8").startswith(f"# {title} — {expected_label}\n")
    final_status = json.loads((tmp_path / "replay-status.json").read_text(encoding="utf-8"))
    assert final_status["edition_date"] == expected_date
    assert final_status["date"] == "2026-11-04"  # Actual execution day remains available to the watchdog.
    assert {path: path.read_bytes() for path in protected} == before
    assert not (tmp_path / "history.db-wal").exists()
    assert not (tmp_path / "history.db-journal").exists()


def test_source_safe_boundary_passes_cutoff_to_verified_generation():
    cutoff = "2026-11-02T22:30:00+00:00"
    with patch("verified_digest.summarize_verified", return_value="# Digest") as verified:
        assert digest.summarize([source("one", "2026-11-02 12:00 UTC")],
                                source_safe=True, edition_cutoff=cutoff) == "# Digest"
    assert verified.call_args.kwargs["edition_cutoff"] == cutoff


def test_email_replay_without_edition_date_stops_before_touching_delivery_or_status(tmp_path):
    original = tmp_path / "saved.json"
    original.write_text(json.dumps([source("old", "2026-11-02 12:00 UTC")]), encoding="utf-8")
    status = tmp_path / "protected-status.json"
    status.write_text("Preserve the previous run status.", encoding="utf-8")
    arguments = [arg for arg in run_arguments(tmp_path, status, "--from-json", str(original))
                 if arg != "--no-email"]
    with (
        patch.object(digest, "summarize") as summarize,
        patch.object(digest, "send_email") as email,
        pytest.raises(SystemExit) as error,
    ):
        digest.main(arguments)
    assert error.value.code == 2
    assert status.read_text(encoding="utf-8") == "Preserve the previous run status."
    assert not (tmp_path / "delivery.db").exists()
    assert not (tmp_path / "new.md").exists()
    summarize.assert_not_called()
    email.assert_not_called()
