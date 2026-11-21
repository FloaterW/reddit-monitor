"""Collection failures must stay distinguishable from quiet days; no live requests."""

import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
import requests

import daily_digest as digest
import notify_failure
import reddit_scraper as scraper


@pytest.mark.parametrize("status", [401, 403, 404, 410, 503])
def test_http_failure_is_preserved_without_retry_or_response_body(status):
    response = Mock(status_code=status, headers={}, content=b"private server diagnostic")
    with scraper.collection_diagnostics() as warnings, patch.object(
        scraper._RSS_SESSION, "get", return_value=response,
    ) as get, patch.object(scraper, "_retry_sleep") as sleep:
        assert scraper.fetch_subreddit_comments_rss("churning") == []
    assert get.call_count == 1
    sleep.assert_not_called()
    assert any(f"HTTP {status}" in w["reason"] for w in warnings)
    assert "private server diagnostic" not in str(warnings)
    if status in (404, 410):
        assert "2026-11-13" in str(warnings)


@pytest.mark.parametrize("content,reason", [
    (b"not xml", "not valid XML"),
    (b"<html><body>Login</body></html>", "not an Atom feed"),
    (b"<error>Denied</error>", "not an Atom feed"),
])
def test_success_status_with_error_document_is_not_a_valid_empty_feed(content, reason):
    response = Mock(status_code=200, headers={}, content=content)
    with scraper.collection_diagnostics() as warnings, patch.object(
        scraper._RSS_SESSION, "get", return_value=response,
    ):
        assert scraper.fetch_subreddit_comments_rss("churning") == []
    assert any(reason in w["reason"] for w in warnings)


@pytest.mark.parametrize("error,reason", [
    (requests.ConnectionError("private URL or proxy data"), "connection failed"),
    (requests.Timeout("private URL or proxy data"), "request timed out"),
])
def test_network_failure_records_cause_after_bounded_retries(error, reason):
    with scraper.collection_diagnostics() as warnings, patch.object(
        scraper._RSS_SESSION, "get", side_effect=error,
    ) as get, patch.object(scraper, "_retry_sleep"):
        assert scraper.fetch_subreddit_comments_rss("churning") == []
    assert get.call_count == 3
    assert reason in str(warnings)
    assert "private URL" not in str(warnings)


def test_rate_limit_exhaustion_has_actionable_reason_and_bounded_attempts():
    response = Mock(status_code=429, headers={"Retry-After": "30"})
    with scraper.collection_diagnostics() as warnings, patch.object(
        scraper._RSS_SESSION, "get", return_value=response,
    ) as get, patch.object(scraper, "_retry_sleep"):
        assert scraper.fetch_subreddit_comments_rss("churning") == []
    assert get.call_count == 3
    assert "HTTP 429 rate limit after 3 attempts" in str(warnings)


def test_standalone_long_server_cooldown_is_not_shortened_or_waited_indefinitely():
    response = Mock(status_code=429, headers={"Retry-After": "900"})
    with patch.object(scraper._RSS_SESSION, "get", return_value=response) as get, patch.object(
        scraper, "_retry_sleep",
    ) as sleep:
        assert scraper._fetch_rss("/r/churning/comments/.rss") is None
    assert get.call_count == 1
    sleep.assert_not_called()


def test_html_availability_is_checked_once_and_each_feed_keeps_its_budget():
    with (
        patch.object(digest, "old_reddit_available", return_value=False) as probe,
        patch.object(digest, "fetch_subreddit_comments_rss", return_value=[]) as rss,
        patch.object(digest, "request_budget", wraps=digest.request_budget) as budget,
    ):
        assert digest._fetch_all_comments(["churning", "CreditCards"], 100, "new", "day", {}) == []
    probe.assert_called_once_with()
    assert [call.args[0] for call in rss.call_args_list] == ["churning", "CreditCards"]
    assert [call.args[0] for call in budget.call_args_list] == [300, 300]


def test_failed_feeds_reach_status_and_sidecar_and_never_reach_ai_or_email(tmp_path):
    response = Mock(status_code=404, headers={}, content=b"server error")
    with (
        patch.object(digest, "old_reddit_available", return_value=False),
        patch.object(scraper._RSS_SESSION, "get", return_value=response),
        patch.object(digest, "summarize") as summarize,
        patch.object(digest, "send_email") as email,
    ):
        result = digest.main([
            "--monitor", "churning", "--subreddits", "churning", "--no-db",
            "--save", str(tmp_path / "digest.md"),
            "--save-raw", str(tmp_path / "raw.json"),
            "--status-file", str(tmp_path / "status.json"),
        ])
    assert result == 1
    summarize.assert_not_called()
    email.assert_not_called()
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["status"] == "failed"
    assert "Reddit collection failed" in status["error"]
    assert "HTTP 404" in status["error"]
    assert "No matching comments" not in status["error"]
    assert "2026-11-13" in status["error"]
    coverage = json.loads((tmp_path / "raw.coverage.json").read_text())
    assert coverage["raw_comment_count"] == 0
    assert "HTTP 404" in str(coverage["collection_warnings"])
    assert json.loads((tmp_path / "raw.json").read_text()) == []
    assert not (tmp_path / "digest.md").exists()


def test_valid_empty_atom_is_not_mislabeled_http_failure():
    response = Mock(status_code=200, headers={}, content=b'<feed xmlns="http://www.w3.org/2005/Atom"/>')
    with scraper.collection_diagnostics() as warnings, patch.object(
        scraper._RSS_SESSION, "get", return_value=response,
    ):
        assert scraper.fetch_subreddit_comments_rss("churning") == []
    assert warnings == []
    assert digest._empty_collection_error({"raw_comment_count": 0}) == (
        "No comments were collected; no digest was produced."
    )


def test_successful_collection_with_no_keyword_match_is_not_a_transport_failure():
    row = {"id": "t1_abc", "body": "unrelated message", "created": "2026-11-17 20:00 UTC"}
    stats = {}
    with patch.object(digest, "_fetch_all_comments", return_value=[row]):
        assert digest.scrape_all(["Chase"], ["churning"], 100, "day", stats=stats,
                                 window_end=datetime(2026, 11, 17, 23, 30, tzinfo=timezone.utc)) == []
    message = digest._empty_collection_error(stats)
    assert "Collected 1 comments" in message
    assert "none matched" in message
    assert "collection failed" not in message


def test_empty_replay_does_not_fetch_or_generate_and_explains_missing_input(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("[]")
    with patch.object(digest, "scrape_all") as scrape, patch.object(
        digest, "summarize",
    ) as summarize, patch.object(digest, "send_email") as email:
        result = digest.main([
            "--from-json", str(source), "--digest-date", "2026-11-17", "--no-db", "--no-email",
            "--save", str(tmp_path / "digest.md"), "--status-file", str(tmp_path / "status.json"),
        ])
    assert result == 1
    for action in (scrape, summarize, email):
        action.assert_not_called()
    assert "Saved input contains no comments" in json.loads((tmp_path / "status.json").read_text())["error"]


def test_failure_alert_gives_collection_guidance_instead_of_model_login():
    guidance = notify_failure.recovery_guidance("ERROR: Reddit collection failed: no comments were retrieved.")
    assert "RSS retirement" in guidance
    assert "empty saved JSON" in guidance
    assert "codex login" not in guidance and "--from-json" not in guidance
    assert "--from-json" in notify_failure.recovery_guidance("ERROR: Digest failed validation after repair")
