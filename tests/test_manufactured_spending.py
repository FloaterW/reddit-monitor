"""Offline checks for the manufactured-spending watchlist and matching boundaries."""

from unittest.mock import patch

import pytest

import daily_digest as digest
from monitor_config import load_monitor
from reddit_scraper import _matches_query


@pytest.mark.parametrize("keyword,text", [
    ("buying group", "This buying-group has delayed payouts."),
    ("buying groups", "Several BUYING GROUPS changed their acceptance rules."),
    ("BFMR", "bfmr has a new purchase limit."),
    ("Buy For Me Retail", "Buy-for-me-retail confirmed receipt."),
    ("Amazon", "Amazon cancelled the order."),
    ("Prime", "The Prime promotion ends tomorrow."),
    ("Best Buy", "Best\u2011Buy restricted quantities."),
    ("MS", "MS: a new purchase cap applies."),
    ("manufactured spend", "The manufactured-spend strategy no longer works."),
])
def test_new_terms_retain_comment_without_existing_keyword(keyword, text):
    profile = load_monitor("churning")
    assert keyword in profile["keywords"]
    source = {"id": "t1_ms", "body": text, "post_title": "Daily discussion"}
    with patch.object(digest, "_fetch_all_comments", return_value=[source]):
        matched = digest.scrape_all(profile["keywords"], ["churning"], 100, "all")
    assert len(matched) == 1
    assert keyword in matched[0]["matched_keywords"]


@pytest.mark.parametrize("text", [
    "The terms changed.", "The items shipped.", "A claims department.",
    "Microsoft Teams", "An SMS arrived.", "The MSR was completed.",
])
def test_ms_does_not_match_inside_unrelated_words(text):
    assert not _matches_query("MS", text)


def test_new_terms_do_not_change_existing_monitor_scope():
    profile = load_monitor("churning")
    assert profile["subreddits"] == [
        "churning", "CreditCards", "awardtravel", "churningcanada", "biltrewards",
    ]
    assert profile["posts_per_subreddit"] == 100
    assert profile["digest"]["editorial"]["preset"] == "churning"
    assert len(profile["keywords"]) == len({term.casefold() for term in profile["keywords"]})


def test_reply_retains_buying_group_parent_context():
    parent = {
        "id": "t1_parent", "body": "BFMR payout report", "post_permalink": "https://reddit.com/r/churning/comments/test/topic/",
    }
    reply = {
        "id": "t1_reply", "parent_id": "t1_parent", "body": "Mine is overdue too.",
        "post_permalink": parent["post_permalink"],
    }
    with patch.object(digest, "_fetch_all_comments", return_value=[parent, reply]):
        matched = digest.scrape_all(["BFMR"], ["churning"], 100, "all")
    assert len(matched) == 2
    assert matched[1]["parent_context"][0]["id"] == "t1_parent"
    assert matched[1]["matched_keywords"] == ["BFMR"]
