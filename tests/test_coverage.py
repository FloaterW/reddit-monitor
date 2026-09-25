"""Offline regressions for coverage, context, and keyword variants."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

import daily_digest as digest
import reddit_scraper as scraper
from monitor_config import load_monitor


@pytest.mark.parametrize("keyword,text", [
    ("clawback", "A claw-back occurred"),
    ("claw back", "A clawback occurred"),
    ("popup", "pop\u2011up jail"),
    ("sign-up bonus", "signup bonus"),
    ("shut down", "shutdown"),
    ("annual fee", "annual\tfee"),
    ("sapphire preferred", "SAPPHIRE-PREFERRED"),
    ("/24", "7/24"),
])
def test_separator_variants(keyword, text):
    assert scraper._matches_query(keyword, text)


@pytest.mark.parametrize("keyword,text", [
    ("ink", "thinking"), ("SUB", "submarine"), ("/24", "3/240"),
    ("pop up", "pop upper"), ("annual fee", "annualfee"),
])
def test_variants_preserve_boundaries(keyword, text):
    assert not scraper._matches_query(keyword, text)


def test_reply_inherits_old_parent_without_promoting_old_news():
    parent = {"id": "t1_parent", "body": "Chase offer details", "created": "2020-01-01 00:00 UTC",
              "post_permalink": "https://reddit.com/r/test/comments/p/topic/"}
    reply = {"id": "t1_reply", "parent_id": "t1_parent", "body": "This ends tomorrow",
             "created": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
             "post_permalink": parent["post_permalink"]}
    unrelated = {"id": "t1_other", "body": "Unrelated conversation", "created": ""}
    with patch.object(digest, "_fetch_all_comments", return_value=[reply, parent, unrelated]):
        result = digest.scrape_all(["chase"], ["test"], 100, "day")
    assert [c["id"] for c in result] == ["t1_reply", "t1_parent"]
    assert result[1]["context_only"] is True
    prompt = digest.format_comments_for_prompt([result[0]])
    assert "Chase offer details" in prompt and "This ends tomorrow" in prompt
    assert "BACKGROUND ONLY" in prompt
    assert "parent_context" not in reply  # Source data is not mutated.


def test_title_matching_and_parent_cycle_are_safe():
    comments = [
        {"id": "t1_a", "parent_id": "t1_b", "body": "Ends tomorrow", "post_title": "Bilt changes"},
        {"id": "t1_b", "parent_id": "t1_a", "body": "Confirmed"},
    ]
    with patch.object(digest, "_fetch_all_comments", return_value=comments):
        result = digest.scrape_all(["bilt"], ["test"], 100, "all")
    assert result[0]["id"] == "t1_a"
    assert digest.format_comments_for_prompt(result)


def test_daily_window_is_fixed_before_collection_and_excludes_late_comments():
    end = datetime(2026, 9, 15, 22, 30, 2, tzinfo=timezone.utc)
    rows = [
        {"id": "t1_start", "body": "Bilt news", "created": "2026-09-14 22:30 UTC"},
        {"id": "t1_end", "body": "Bilt news", "created": "2026-09-15 22:30 UTC"},
        {"id": "t1_old", "body": "Bilt news", "created": "2026-09-14 22:29 UTC"},
        {"id": "t1_late", "body": "Bilt news", "created": "2026-09-15 22:31 UTC"},
        {"id": "t1_unknown", "body": "Bilt news", "created": ""},
    ]
    stats = {}
    with patch.object(digest, "_fetch_all_comments", return_value=rows) as fetch:
        result = digest.scrape_all(["bilt"], ["test"], 100, "day", stats=stats, window_end=end)
    assert [r["id"] for r in result] == ["t1_start", "t1_end"]
    assert fetch.call_args.kwargs["window_end"] == end.replace(second=0)
    assert stats["window_start"] == "2026-09-14T22:30:00+00:00"
    assert stats["window_end"] == "2026-09-15T22:30:00+00:00"


def test_collection_freezes_clock_before_fetch():
    start = datetime(2026, 9, 15, 22, 30, tzinfo=timezone.utc)
    with patch.object(digest, "datetime") as clock, patch.object(
        digest, "_fetch_all_comments", return_value=[]
    ) as fetch:
        clock.now.side_effect = [start, start + timedelta(minutes=25)]
        digest.scrape_all(["bilt"], ["test"], 100, "day")
    clock.now.assert_called_once()
    assert fetch.call_args.kwargs["window_end"] == start


def test_bounded_window_rejects_unknown_timestamp():
    end = datetime(2026, 9, 15, 22, 30, tzinfo=timezone.utc)
    assert not digest._comment_in_window("invalid", end - timedelta(days=1), end)


def test_rss_uses_same_start_cutoff_for_each_subreddit():
    end = datetime(2026, 9, 15, 22, 30, tzinfo=timezone.utc)
    with (
        patch.object(scraper, "fetch_subreddit_posts_rss", return_value={}),
        patch.object(digest, "fetch_subreddit_comments_rss", return_value=[]) as fetch,
        patch.object(digest.time, "sleep"),
    ):
        digest._fetch_all_comments_rss(["one", "two"], 100, "new", "day", {}, window_end=end)
    assert fetch.call_count == 2
    assert all(c.kwargs["cutoff"] == end - timedelta(days=1) for c in fetch.call_args_list)


def feed(ids):
    return ET.fromstring('<feed xmlns="http://www.w3.org/2005/Atom">' + ''.join(
        f'<entry><id>{identifier}</id><title>Title {identifier}</title></entry>'
        for identifier in ids
    ) + '</feed>')


def test_post_rss_paginates_even_when_server_returns_short_pages():
    with (
        patch.object(scraper, "_fetch_rss", side_effect=[feed(["t3_a"]), feed(["t3_b"])] ) as fetch,
        patch.object(scraper.time, "sleep"),
    ):
        assert len(scraper.fetch_subreddit_posts_rss("test", limit=2)) == 2
    assert fetch.call_args_list[1].args[1]["after"] == "t3_a"


def test_comment_rss_stops_repeated_pages_without_duplicates(capsys):
    with (
        patch.object(scraper, "_fetch_rss", return_value=feed(["t1_a"] )) as fetch,
        patch.object(scraper.time, "sleep"),
    ):
        result = scraper.fetch_subreddit_comments_rss("test")
    assert len(result) == 1
    assert fetch.call_count == 2
    assert "repeated a page" in capsys.readouterr().out


def test_html_listing_paginates(post_html, comment_html):
    second_page = post_html.replace("abc123", "ghi789").replace("def456", "jkl012")
    with (
        patch.object(digest, "old_reddit_available", return_value=True),
        patch.object(digest, "_fetch", side_effect=[post_html, second_page] + [comment_html] * 6) as fetch,
        patch.object(digest.time, "sleep"),
    ):
        result = digest._fetch_all_comments(["test"], 3, "new", "day", {})
    assert fetch.call_args_list[1].args[1]["after"] == "t3_def456"
    assert len({c["post_permalink"] for c in result}) == 3


def test_monitor_includes_bilt_and_broader_coverage():
    config = load_monitor("churning")
    assert "biltrewards" in config["subreddits"]
    assert config["posts_per_subreddit"] == 100
