"""Tests for reddit_scraper.py — HTML parsing, keyword matching, dedup."""

from reddit_scraper import (
    _dedup_key,
    _matches_query,
    _parse_comments,
    _parse_search_results,
    _parse_things,
    _strip_html,
)


# ---------------------------------------------------------------------------
# Post parser
# ---------------------------------------------------------------------------
class TestParseThings:
    def test_parses_two_posts(self, post_html):
        results = _parse_things(post_html, limit=10)
        assert len(results) == 2

    def test_skips_promoted(self, post_html):
        results = _parse_things(post_html, limit=10)
        authors = [r["author"] for r in results]
        assert "ad_account" not in authors

    def test_extracts_fields(self, post_html):
        results = _parse_things(post_html, limit=10)
        first = results[0]
        assert first["score"] == 42
        assert first["num_comments"] == 7
        assert first["author"] == "testuser"
        assert first["subreddit"] == "churning"
        assert "abc123" in first["permalink"]

    def test_respects_limit(self, post_html):
        results = _parse_things(post_html, limit=1)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Comment parser
# ---------------------------------------------------------------------------
class TestParseComments:
    def test_parses_comments(self, comment_html):
        results = _parse_comments(comment_html, limit=10)
        assert len(results) == 2

    def test_top_level_depth(self, comment_html):
        results = _parse_comments(comment_html, limit=10)
        top = [c for c in results if c["id"] == "t1_aaa111"]
        assert len(top) == 1
        assert top[0]["depth"] == 0

    def test_nested_depth(self, comment_html):
        results = _parse_comments(comment_html, limit=10)
        reply = [c for c in results if c["id"] == "t1_bbb222"]
        assert len(reply) == 1
        assert reply[0]["depth"] == 1
        assert reply[0]["parent_id"] == "t1_aaa111"

    def test_extracts_author(self, comment_html):
        results = _parse_comments(comment_html, limit=10)
        authors = {c["author"] for c in results}
        assert authors == {"alice", "bob"}

    def test_extracts_body(self, comment_html):
        results = _parse_comments(comment_html, limit=10)
        bodies = [c["body"] for c in results]
        assert any("Chase Sapphire" in b for b in bodies)
        assert any("Chase ink" in b for b in bodies)


# ---------------------------------------------------------------------------
# Search results parser
# ---------------------------------------------------------------------------
class TestParseSearchResults:
    def test_parses_results(self, search_html):
        results = _parse_search_results(search_html, limit=10)
        assert len(results) == 2

    def test_extracts_fields(self, search_html):
        results = _parse_search_results(search_html, limit=10)
        first = results[0]
        assert first["title"] == "Great Amex Deal"
        assert first["score"] == 55
        assert first["num_comments"] == 12
        assert first["subreddit"] == "churning"

    def test_normalizes_permalink(self, search_html):
        results = _parse_search_results(search_html, limit=10)
        for r in results:
            assert "old.reddit.com" not in r["permalink"]
            assert r["permalink"].startswith("https://reddit.com")


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------
class TestMatchesQuery:
    def test_word_boundary_match(self):
        assert _matches_query("chase", "I applied for Chase Sapphire")

    def test_word_boundary_no_match(self):
        assert not _matches_query("ink", "I was thinking about it")

    def test_word_boundary_exact(self):
        assert _matches_query("ink", "The Ink card is great")

    def test_case_insensitive(self):
        assert _matches_query("amex", "Got an AMEX offer today")

    def test_substring_mode(self):
        assert _matches_query("ink", "I was thinking about it", use_word_boundary=False)

    def test_empty_text(self):
        assert not _matches_query("chase", "")

    def test_special_chars_escaped(self):
        assert _matches_query("5/24", "I'm at 5/24 right now")


# ---------------------------------------------------------------------------
# Dedup keys
# ---------------------------------------------------------------------------
class TestDedupKey:
    def test_uses_id_when_present(self):
        comment = {"id": "t1_abc123", "author": "alice", "body": "hello"}
        assert _dedup_key(comment) == "t1_abc123"

    def test_falls_back_to_composite_hash(self):
        comment = {"id": "", "author": "alice", "body": "hello world",
                   "created": "2025-06-24", "post_permalink": "https://reddit.com/r/test/123/"}
        key = _dedup_key(comment)
        assert isinstance(key, str)
        assert len(key) == 64  # sha256 hex digest

    def test_different_bodies_different_hashes(self):
        c1 = {"id": "", "body": "first comment"}
        c2 = {"id": "", "body": "second comment"}
        assert _dedup_key(c1) != _dedup_key(c2)

    def test_same_author_different_comments_not_deduped(self):
        c1 = {"id": "t1_aaa", "author": "alice", "body": "Same prefix but different ending one"}
        c2 = {"id": "t1_bbb", "author": "alice", "body": "Same prefix but different ending two"}
        assert _dedup_key(c1) != _dedup_key(c2)

    def test_no_id_same_post_different_bodies_not_deduped(self):
        shared = "https://reddit.com/r/churning/comments/xyz/post/"
        c1 = {"id": "", "author": "alice", "body": "First comment",
              "created": "2025-06-24 12:00 UTC", "post_permalink": shared}
        c2 = {"id": "", "author": "bob", "body": "Second comment",
              "created": "2025-06-24 12:05 UTC", "post_permalink": shared}
        assert _dedup_key(c1) != _dedup_key(c2)


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------
class TestStripHtml:
    def test_strips_tags(self):
        assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_unescapes_entities(self):
        assert _strip_html("&amp; &lt; &gt;") == "& < >"

    def test_collapses_whitespace(self):
        assert _strip_html("<p>  lots   of   space  </p>") == "lots of space"
