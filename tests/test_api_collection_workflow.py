"""API routing keeps collection independent of the newsletter/delivery pipeline."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import daily_digest as digest
from notify_failure import recovery_guidance


def api_client(rows):
    client = MagicMock()
    client.__enter__.return_value = client
    client.fetch_subreddit.return_value = rows
    return client


def test_api_routing_uses_one_client_and_same_window_without_legacy(monkeypatch):
    monkeypatch.setenv("DIGEST_REDDIT_SOURCE", "api")
    end = datetime(2026, 11, 18, 23, 30, tzinfo=timezone.utc)
    start = end - timedelta(hours=25)
    client = api_client([])
    with (
        patch("reddit_api.RedditAPIClient.from_environment", return_value=client) as factory,
        patch.object(digest, "old_reddit_available") as legacy,
        patch.object(digest, "request_budget", wraps=digest.request_budget) as budget,
        digest.collection_diagnostics() as warnings,
    ):
        assert digest._fetch_all_comments(
            ["one", "two"], 100, "new", "day", {"two": "weekly"},
            window_end=end, window_start=start,
        ) == []
    factory.assert_called_once_with()
    legacy.assert_not_called()
    assert budget.call_count == 2
    assert all(call.args == (300,) for call in budget.call_args_list)
    assert [call.args[0] for call in client.fetch_subreddit.call_args_list] == ["one", "two"]
    assert all(call.kwargs["cutoff"] == start for call in client.fetch_subreddit.call_args_list)
    assert all(call.kwargs["window_end"] == end for call in client.fetch_subreddit.call_args_list)
    assert client.fetch_subreddit.call_args_list[1].kwargs["title_filter"] == "weekly"
    assert all("API source scope" in warning["reason"] for warning in warnings)
    client.__exit__.assert_called_once()


def test_invalid_collector_fails_explicitly_without_legacy(monkeypatch):
    monkeypatch.setenv("DIGEST_REDDIT_SOURCE", "typo")
    with patch.object(digest, "old_reddit_available") as legacy:
        with pytest.raises(ValueError, match="DIGEST_REDDIT_SOURCE"):
            digest._fetch_all_comments(["one"], 100, "new", "day")
    legacy.assert_not_called()


def test_api_configuration_failure_never_falls_back(monkeypatch):
    monkeypatch.setenv("DIGEST_REDDIT_SOURCE", "api")
    with (
        patch("reddit_api.RedditAPIClient.from_environment", side_effect=ValueError("REDDIT_CLIENT_ID missing")),
        patch.object(digest, "old_reddit_available") as legacy,
    ):
        with pytest.raises(ValueError, match="REDDIT_CLIENT_ID"):
            digest._fetch_all_comments(["one"], 100, "new", "day")
    legacy.assert_not_called()


def test_api_reply_inherits_old_parent_and_preserves_direct_citation(monkeypatch):
    monkeypatch.setenv("DIGEST_REDDIT_SOURCE", "api")
    post = "https://reddit.com/r/churning/comments/abc/topic/"
    parent = {"id": "t1_parent", "parent_id": "t3_abc", "body": "Chase Ink welcome offer",
              "created": "2026-11-15 10:00 UTC", "post_title": "Discussion Thread",
              "author": "parent_author", "subreddit": "churning", "post_permalink": post,
              "comment_permalink": post + "parent/"}
    reply = {"id": "t1_reply", "parent_id": "t1_parent", "body": "This ends tomorrow",
             "created": "2026-11-18 12:00 UTC", "post_title": "Discussion Thread",
             "author": "reply_author", "subreddit": "churning", "post_permalink": post,
             "comment_permalink": post + "reply/"}
    future = {**reply, "id": "t1_future", "created": "2026-11-19 01:00 UTC"}
    stats = {}
    client = api_client([reply, parent, future])
    with patch("reddit_api.RedditAPIClient.from_environment", return_value=client):
        comments = digest.scrape_all(
            ["Chase"], ["churning"], 100, "day", stats=stats,
            window_end=datetime(2026, 11, 18, 23, 30, tzinfo=timezone.utc),
        )
    assert [row["id"] for row in comments] == ["t1_reply", "t1_parent"]
    assert comments[1]["context_only"] is True
    assert comments[0]["parent_context"] == [parent]
    assert stats["collector"] == "api"
    from evaluate_digest import _comment_url_key
    assert _comment_url_key(comments[0]).endswith("/reply")
    prompt = digest.format_comments_for_prompt(comments)
    assert "BACKGROUND ONLY" in prompt
    assert "Chase Ink welcome offer" in prompt


def test_api_coverage_note_uses_existing_footer_layout():
    note = digest.collection_notice([
        {"subreddit": "churning", "reason": "API source scope: standalone post bodies are not collected."},
        {"subreddit": "churning", "reason": "API parent context unavailable."},
        {"subreddit": "churning", "reason": "API request failed: HTTP 403; coverage is incomplete."},
    ])
    assert note.startswith("\n\n---\n\n**Coverage note:**")
    assert "standalone post bodies are not collected" in note
    assert "Some parent replies could not be retrieved" in note
    assert "Some Reddit API requests failed" in note
    assert "This digest is not exhaustive." in note
    assert "RSS" not in note


def test_api_failure_guidance_identifies_collection_not_model():
    detail = digest._empty_collection_error({"raw_comment_count": 0, "collection_warnings": [
        {"subreddit": "churning", "reason": "API request failed: HTTP 403; coverage is incomplete."},
    ]})
    assert "HTTP 403" in detail
    guidance = recovery_guidance(detail)
    assert "Reddit API collection" in guidance
    assert "codex login" not in guidance


@pytest.mark.parametrize("detail", [
    "API collection budget or cooldown limit reached",
    "API source scope: standalone post bodies are not collected. Total comments fetched: 0",
    "DIGEST_REDDIT_SOURCE must be 'legacy' or 'api'",
])
def test_api_empty_or_configuration_failure_has_api_guidance(detail):
    guidance = recovery_guidance(detail)
    assert "Reddit API collection" in guidance
    assert "codex login" not in guidance
    assert "RSS retirement" not in guidance


def test_legacy_remains_default_when_api_is_unconfigured(monkeypatch):
    monkeypatch.delenv("DIGEST_REDDIT_SOURCE", raising=False)
    assert digest._collection_source() == "legacy"


def test_empty_api_run_persists_failure_without_ai_or_smtp(monkeypatch, tmp_path):
    monkeypatch.setenv("DIGEST_REDDIT_SOURCE", "api")
    client = api_client([])

    def unavailable(*args, **kwargs):
        digest.collection_warning("churning", "API request failed: HTTP 403; coverage is incomplete.")
        return []

    client.fetch_subreddit.side_effect = unavailable
    with (
        patch("reddit_api.RedditAPIClient.from_environment", return_value=client),
        patch.object(digest, "summarize") as summarize,
        patch.object(digest, "send_email") as email,
        patch.object(digest, "old_reddit_available") as legacy,
    ):
        result = digest.main([
            "--monitor", "churning", "--subreddits", "churning", "--source-safe",
            "--quality", "strict", "--no-email", "--no-db",
            "--save", str(tmp_path / "digest.md"),
            "--save-raw", str(tmp_path / "comments.json"),
            "--status-file", str(tmp_path / "status.json"),
        ])
    assert result == 1
    summarize.assert_not_called()
    email.assert_not_called()
    legacy.assert_not_called()
    assert json.loads((tmp_path / "comments.json").read_text()) == []
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["status"] == "failed"
    assert "HTTP 403" in status["error"]
    coverage = json.loads((tmp_path / "comments.coverage.json").read_text())
    assert coverage["collector"] == "api"


@pytest.mark.parametrize("body", ["[removed]", " [deleted]\n"])
def test_deleted_current_body_cannot_promote_parent_as_news(body):
    post = "https://reddit.com/r/churning/comments/abc/topic/"
    parent = {"id": "t1_parent", "parent_id": "t3_abc", "body": "Chase offer expired",
              "created": "2026-11-15 10:00 UTC", "post_permalink": post}
    deleted = {"id": "t1_deleted", "parent_id": "t1_parent", "body": body,
               "created": "2026-11-18 12:00 UTC", "post_title": "Chase offer",
               "post_permalink": post}
    with patch.object(digest, "_fetch_all_comments", return_value=[deleted, parent]):
        assert digest.scrape_all(["chase"], ["churning"], 100, "day", window_end=
                                 datetime(2026, 11, 18, 23, 30, tzinfo=timezone.utc)) == []


def test_deleted_parent_can_remain_background_for_real_reply():
    post = "https://reddit.com/r/churning/comments/abc/topic/"
    records = [
        {"id": "t1_reply", "parent_id": "t1_deleted", "body": "Chase confirmed this ended.",
         "created": "2026-11-18 12:00 UTC", "post_permalink": post},
        {"id": "t1_deleted", "parent_id": "t3_abc", "body": "[removed]",
         "created": "2026-11-18 11:00 UTC", "post_permalink": post},
    ]
    with patch.object(digest, "_fetch_all_comments", return_value=records):
        comments = digest.scrape_all(["chase"], ["churning"], 100, "day", window_end=
                                     datetime(2026, 11, 18, 23, 30, tzinfo=timezone.utc))
    assert comments[0]["id"] == "t1_reply"
    assert comments[1]["context_only"] is True
    assert comments[1]["id"] == "t1_deleted"
