"""Offline checks for the OAuth collector; no credentials or live Reddit calls."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
import requests

import reddit_api as api
import reddit_scraper as scraper

END = datetime(2026, 11, 18, 15, 30, tzinfo=timezone.utc)
CUTOFF = END - timedelta(days=1)


def response(payload=None, status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = payload
    return result


def token(value="temporary-secret-token", expires=3600):
    return response({"access_token": value, "token_type": "bearer", "expires_in": expires})


def listing(items=(), after=None):
    return response({"kind": "Listing", "data": {"children": list(items), "after": after}})


def comment(identifier="a", parent="t3_post", *, created=END - timedelta(hours=1),
            post="post", sub="test", body="Chase offer details", title="Chase thread"):
    return {"kind": "t1", "data": {
        "id": identifier, "name": "t1_" + identifier, "body": body,
        "author": "person_1", "score": 5, "created_utc": created.timestamp(),
        "parent_id": parent, "link_id": "t3_" + post, "subreddit": sub,
        "permalink": f"/r/{sub}/comments/{post}/thread/{identifier}/", "link_title": title,
    }}


def post(identifier="post", title="Actual Chase title", sub="test"):
    return {"kind": "t3", "data": {
        "id": identifier, "name": "t3_" + identifier, "subreddit": sub,
        "title": title, "permalink": f"/r/{sub}/comments/{identifier}/thread/",
    }}


@pytest.fixture
def client():
    clock = [1000.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    session = Mock()
    instance = api.RedditAPIClient("public-client-id", "windows:test-digest:v1 (by /u/test)",
                                   session=session, clock=lambda: clock[0], sleeper=sleep)
    instance.test_clock, instance.test_sleeps = clock, sleeps
    return instance


def test_configuration_requires_explicit_real_application_identification(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_API_USER_AGENT", raising=False)
    with pytest.raises(ValueError, match="REDDIT_CLIENT_ID"):
        api.RedditAPIClient.from_environment()
    monkeypatch.setenv("REDDIT_CLIENT_ID", "public-client-id")
    monkeypatch.setenv("REDDIT_API_USER_AGENT", "Mozilla/5.0")
    with pytest.raises(ValueError, match="REDDIT_API_USER_AGENT"):
        api.RedditAPIClient.from_environment()
    monkeypatch.setenv("REDDIT_API_USER_AGENT", "windows:test-digest:v1")
    with patch.object(api.requests, "Session"):
        assert isinstance(api.RedditAPIClient.from_environment(), api.RedditAPIClient)


def test_installed_grant_caches_token_and_never_sends_browser_credentials(client):
    client._session.request.side_effect = [token(), listing(), listing()]
    assert client._get("/r/test/comments", "test", {"limit": 1}) == {"children": [], "after": None}
    client._get("/r/test/comments", "test", {"limit": 1})
    calls = client._session.request.call_args_list
    assert [c.args[0] for c in calls] == ["POST", "GET", "GET"]
    assert calls[0].kwargs["auth"] == ("public-client-id", "")
    assert calls[0].kwargs["data"] == {
        "grant_type": "https://oauth.reddit.com/grants/installed_client",
        "device_id": "DO_NOT_TRACK_THIS_DEVICE",
    }
    assert all(c.kwargs["allow_redirects"] is False for c in calls)
    assert all(0 < c.kwargs["timeout"] <= 20 for c in calls)
    assert client.test_sleeps == [2, 2]


def test_expired_token_is_refreshed(client):
    client._session.request.side_effect = [token("first", 100), token("second")]
    assert client._get_token("test") == "first"
    client.test_clock[0] += 101
    assert client._get_token("test") == "second"


def test_401_refresh_is_attempted_only_once(client):
    client._session.request.side_effect = [token("first"), response(status=401),
                                            token("second"), response(status=401)]
    with scraper.collection_diagnostics() as warnings:
        assert client._get("/r/test/comments", "test", {}) is None
    assert client._session.request.call_count == 4
    assert "HTTP 401" in str(warnings)


@pytest.mark.parametrize("status", [302, 403, 404])
def test_nonretryable_denial_is_not_bypassed_or_retried(client, status, capsys):
    client._session.request.side_effect = [token(), response({"private": "never-log-me"}, status)]
    with scraper.collection_diagnostics() as warnings:
        assert client._get("/r/test/comments", "test", {}) is None
    assert client._session.request.call_count == 2
    assert f"API request failed: HTTP {status}" in str(warnings)
    output = capsys.readouterr().out
    assert "temporary-secret-token" not in output and "never-log-me" not in output


def test_authentication_denial_is_not_repeated_for_every_subreddit(client):
    client._session.request.return_value = response(status=403)
    assert client._get_token("one") is None
    assert client._get_token("two") is None
    assert client._session.request.call_count == 1


def test_429_honors_full_server_cooldown(client):
    client._session.request.side_effect = [token(), response(status=429, headers={"Retry-After": "30"}), listing()]
    assert client._get("/r/test/comments", "test", {}) is not None
    assert client.test_sleeps == [2, 30]


def test_long_cooldown_stops_without_retrying_early(client):
    client._session.request.side_effect = [token(), response(status=429, headers={"Retry-After": "900"})]
    with scraper.collection_diagnostics() as warnings:
        assert client.fetch_subreddit("test", CUTOFF, END) == []
    assert client._session.request.call_count == 2
    assert client.test_sleeps == [2]
    assert "cooldown limit" in str(warnings)


def test_zero_remaining_honors_rate_reset(client):
    first = listing()
    first.headers = {"X-Ratelimit-Remaining": "0", "X-Ratelimit-Reset": "12"}
    client._session.request.side_effect = [token(), first, listing()]
    client._get("/r/test/comments", "test", {})
    client._get("/r/test/comments", "test", {})
    assert client.test_sleeps == [2, 13]


def test_transient_retry_is_bounded_and_sanitizes_exception(client, capsys):
    client._session.request.side_effect = [requests.ConnectionError("credential-must-not-leak")] * 3
    with scraper.collection_diagnostics() as warnings:
        assert client._get_token("test") is None
    assert client._session.request.call_count == 3
    assert "API request failed:" in str(warnings)
    assert "credential-must-not-leak" not in capsys.readouterr().out


def test_invalid_json_or_shape_is_a_collection_failure(client):
    malformed = response()
    malformed.json.side_effect = ValueError("private-body")
    client._session.request.side_effect = [token(), malformed]
    with scraper.collection_diagnostics() as warnings:
        assert client._get("/r/test/comments", "test", {}) is None
    assert "invalid listing document" in str(warnings)


def test_pagination_reaches_boundary_and_retains_full_body_and_metadata(client):
    body = "Long paragraph.\n\n" + "word " * 1500
    client._session.request.side_effect = [token(), listing([comment(body=body)], "t1_a"),
        listing([comment("old", created=CUTOFF - timedelta(minutes=1))], "t1_old"),
        listing([post()])]
    with scraper.collection_diagnostics() as warnings:
        rows = client.fetch_subreddit("test", CUTOFF, END)
    assert warnings == []
    assert rows[0]["body"] == body
    assert rows[0]["id"] == "t1_a" and rows[0]["parent_id"] == "t3_post"
    assert rows[0]["post_title"] == "Actual Chase title"
    assert rows[0]["comment_permalink"] == "https://reddit.com/r/test/comments/post/thread/a/"
    assert client._session.request.call_args_list[2].kwargs["params"]["after"] == "t1_a"


def test_repeated_page_preserves_unique_rows_and_warns(client):
    client._session.request.side_effect = [token(), listing([comment()], "t1_a"),
                                            listing([comment()], "t1_a"), listing([post()])]
    with scraper.collection_diagnostics() as warnings:
        rows = client.fetch_subreddit("test", CUTOFF, END)
    assert len(rows) == 1
    assert "repeated a page" in str(warnings)


@pytest.mark.parametrize("tail", [listing(), listing([comment()])])
def test_end_before_boundary_is_disclosed(client, tail):
    client._session.request.side_effect = [token(), tail, listing([post()])]
    with scraper.collection_diagnostics() as warnings:
        client.fetch_subreddit("test", CUTOFF, END)
    assert "time boundary" in str(warnings)


def test_page_limit_is_disclosed(client):
    client._session.request.side_effect = [token(), listing([comment()], "t1_a"), listing([post()])]
    with scraper.collection_diagnostics() as warnings:
        client.fetch_subreddit("test", CUTOFF, END, max_pages=1)
    assert "1-page limit" in str(warnings)


def test_missing_old_ancestors_are_fetched_and_share_canonical_thread(client):
    old = CUTOFF - timedelta(days=2)
    child = comment("child", parent="t1_parent")
    parent = comment("parent", parent="t1_grand", created=old, title="")
    grand = comment("grand", created=old, body="Chase offer background", title="")
    client._session.request.side_effect = [token(), listing([child]), listing([post()]),
                                            listing([parent]), listing([grand])]
    rows = client.fetch_subreddit("test", CUTOFF, END)
    assert [c["id"] for c in rows] == ["t1_child", "t1_parent", "t1_grand"]
    assert len({c["post_permalink"] for c in rows}) == 1
    assert rows[1]["created"] == old.strftime("%Y-%m-%d %H:%M UTC")
    assert all(c["post_title"] == "Actual Chase title" for c in rows)


def test_cross_thread_parent_cannot_be_attached(client):
    client._session.request.side_effect = [token(), listing([comment("child", "t1_parent")]),
        listing([post()]), listing([comment("parent", "t3_other", post="other")])]
    with scraper.collection_diagnostics() as warnings:
        rows = client.fetch_subreddit("test", CUTOFF, END)
    assert [c["id"] for c in rows] == ["t1_child"]
    assert "invalid identity or thread" in str(warnings)
    assert "parent context unavailable" in str(warnings)


def test_title_filter_uses_actual_submission_title(client):
    client._session.request.side_effect = [token(), listing([comment(title="wrong title")]),
                                            listing([post(title="US Churning Discussion")])]
    assert len(client.fetch_subreddit("test", CUTOFF, END, title_filter="US.*Discussion")) == 1


@pytest.mark.parametrize("field,value", [("subreddit", "elsewhere"), ("created_utc", float("nan")),
                                        ("id", "wrong"), ("body", None), ("parent_id", "t3_wrong")])
def test_malformed_comment_is_skipped_and_disclosed(client, field, value):
    item = comment()
    item["data"][field] = value
    client._session.request.side_effect = [token(), listing([item])]
    with scraper.collection_diagnostics() as warnings:
        assert client.fetch_subreddit("test", CUTOFF, END) == []
    assert "invalid comment" in str(warnings)


def test_partial_primary_collection_survives_budget_expiry(client):
    client._session.request.side_effect = [token(), listing([comment()], "t1_a")]
    with patch.object(client, "_wait", side_effect=[None, None, TimeoutError]), scraper.collection_diagnostics() as warnings:
        rows = client.fetch_subreddit("test", CUTOFF, END)
    assert [c["id"] for c in rows] == ["t1_a"]
    assert "budget" in str(warnings)


def test_default_sleeper_respects_existing_deadline():
    session = Mock()
    instance = api.RedditAPIClient("public-client-id", "windows:test:v1", session=session)
    instance._next_request = instance._clock() + 30
    with scraper.request_budget(0.01), scraper.collection_diagnostics() as warnings:
        assert instance.fetch_subreddit("test", CUTOFF, END) == []
    session.request.assert_not_called()
    assert "budget" in str(warnings)


def test_close_revokes_only_temporary_token_and_does_not_print_it(client, capsys):
    client._token = "private-token"
    client.close()
    client.close()
    client._session.post.assert_called_once()
    assert client._session.post.call_args.kwargs["data"] == {
        "token": "private-token", "token_type_hint": "access_token"}
    assert client._token is None
    client._session.close.assert_called_once()
    assert "private-token" not in capsys.readouterr().out


def test_close_does_not_ignore_server_cooldown(client):
    client._token = "private-token"
    client._next_request = client._clock() + 500
    client.close()
    client._session.post.assert_not_called()
    assert client._token is None


def test_conflicting_comment_permalink_is_rejected_not_rewritten(client):
    item = comment("expected")
    item["data"]["permalink"] = "/r/test/comments/post/thread/someoneelse/"
    item["data"]["link_permalink"] = "/r/test/comments/post/thread/"
    assert client._comment(item, "test") is None


def test_missing_comment_permalink_can_use_matching_thread_url(client):
    item = comment("expected")
    item["data"].pop("permalink")
    item["data"]["link_permalink"] = "/r/test/comments/post/thread/"
    assert client._comment(item, "test")["comment_permalink"].endswith("/expected/")


def test_parent_survives_title_filter_when_submission_metadata_is_unavailable(client):
    child = comment("child", "t1_parent", title="US Churning Discussion")
    parent = comment("parent", created=CUTOFF - timedelta(days=1), title="")
    client._session.request.side_effect = [token(), listing([child]), response(status=503),
        response(status=503), response(status=503), listing([parent])]
    with scraper.collection_diagnostics() as warnings:
        rows = client.fetch_subreddit("test", CUTOFF, END, title_filter="US Churning")
    assert [row["id"] for row in rows] == ["t1_child", "t1_parent"]
    assert rows[1]["post_title"] == "US Churning Discussion"
    assert "parent context unavailable" not in str(warnings)
    assert "HTTP 503" in str(warnings)


def test_unrelated_old_listing_comments_do_not_expand_ancestors(client):
    old = comment("old", "t1_unneeded", created=CUTOFF - timedelta(minutes=1))
    client._session.request.side_effect = [token(), listing([old]), listing([post()])]
    with scraper.collection_diagnostics() as warnings:
        rows = client.fetch_subreddit("test", CUTOFF, END)
    assert len(rows) == 1
    assert client._session.request.call_count == 3
    assert warnings == []
    assert "context_status" not in rows[0]


def test_parent_cycle_is_reported_and_does_not_fetch_forever(client):
    client._session.request.side_effect = [token(), listing([comment("a", "t1_b"), comment("b", "t1_a")]),
                                            listing([post()])]
    with scraper.collection_diagnostics() as warnings:
        rows = client.fetch_subreddit("test", CUTOFF, END)
    assert len(rows) == 2
    assert "cycle" in str(warnings)
    assert all(row["context_status"] == "partial" for row in rows)
    assert client._session.request.call_count == 3


def test_submission_title_cannot_cross_subreddit_boundary(client):
    client._session.request.side_effect = [token(), listing([comment(title="Correct title")]),
                                            listing([post(title="Wrong title", sub="elsewhere")])]
    with scraper.collection_diagnostics() as warnings:
        rows = client.fetch_subreddit("test", CUTOFF, END)
    assert rows[0]["post_title"] == "Correct title"
    assert "invalid submission" in str(warnings)


def test_current_child_is_partial_when_an_older_ancestor_is_missing(client):
    child = comment("child", "t1_parent")
    parent = comment("parent", "t1_missing", created=CUTOFF - timedelta(days=2))
    client._session.request.side_effect = [token(), listing([child, parent]), listing([post()]), listing()]
    with scraper.collection_diagnostics() as warnings:
        rows = client.fetch_subreddit("test", CUTOFF, END)
    by_id = {row["id"]: row for row in rows}
    assert by_id["t1_child"]["context_status"] == "partial"
    assert "context_status" not in by_id["t1_parent"]
    assert by_id["t1_parent"]["parent_id"] == "t1_missing"
    assert "parent context unavailable" in str(warnings)


def test_complete_current_chain_ignores_unrelated_old_padding_gap(client):
    child = comment("child", "t1_parent")
    parent = comment("parent", created=CUTOFF - timedelta(days=1))
    old_padding = comment("padding", "t1_unrelatedmissing", created=CUTOFF - timedelta(hours=1))
    client._session.request.side_effect = [token(), listing([child], "t1_child"),
        listing([parent, old_padding]), listing([post()])]
    with scraper.collection_diagnostics() as warnings:
        rows = client.fetch_subreddit("test", CUTOFF, END)
    assert warnings == []
    assert [row["id"] for row in rows] == ["t1_child", "t1_parent", "t1_padding"]
    assert all("context_status" not in row for row in rows)


def test_current_child_at_ancestry_depth_limit_is_partial(client):
    rows = [comment("c0", "t1_c1")]
    for index in range(1, 67):
        rows.append(comment(f"c{index}", f"t1_c{index + 1}" if index < 66 else "t3_post",
                            created=CUTOFF - timedelta(days=1)))
    client._session.request.side_effect = [token(), listing(rows), listing([post()])]
    with scraper.collection_diagnostics() as warnings:
        collected = client.fetch_subreddit("test", CUTOFF, END)
    assert collected[0]["context_status"] == "partial"
    assert all("context_status" not in row for row in collected[1:])
    assert "depth limit" in str(warnings)
