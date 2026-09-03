"""Tests for daily_digest.py — time filtering, dedup, markdown preprocessing, summarizer errors."""

import re
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from daily_digest import (
    _build_llm_command,
    _chunk_comments,
    _comment_in_window,
    _fetch_all_comments_rss,
    _markdown_to_safe_html,
    _preprocess_md,
    _wrap_html_email,
    format_comments_for_prompt,
    sanitize_digest_markdown,
    scrape_all,
    summarize,
)


# ---------------------------------------------------------------------------
# Time-window filtering
# ---------------------------------------------------------------------------
class TestCommentInWindow:
    def test_recent_comment_included(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        ts = recent.strftime("%Y-%m-%d %H:%M UTC")
        assert _comment_in_window(ts, cutoff) is True

    def test_old_comment_excluded(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        ts = old.strftime("%Y-%m-%d %H:%M UTC")
        assert _comment_in_window(ts, cutoff) is False

    def test_empty_timestamp_included(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        assert _comment_in_window("", cutoff) is True

    def test_malformed_timestamp_included(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        assert _comment_in_window("not-a-date", cutoff) is True


class TestTimeAll:
    """Verify that --time all skips timestamp cutoff filtering."""

    def test_scrape_all_skips_cutoff_for_all(self):
        old_ts = "2020-01-01 00:00 UTC"
        fake_comments = [
            {"id": "t1_old", "body": "chase offer here", "author": "u1",
             "created": old_ts, "subreddit": "test", "post_title": "t"},
        ]

        with patch("daily_digest._fetch_all_comments", return_value=fake_comments):
            result = scrape_all(["chase"], ["test"], 1, "all")

        assert len(result) == 1
        assert result[0]["id"] == "t1_old"

    def test_scrape_all_filters_old_for_day(self):
        old_ts = "2020-01-01 00:00 UTC"
        fake_comments = [
            {"id": "t1_old", "body": "chase offer here", "author": "u1",
             "created": old_ts, "subreddit": "test", "post_title": "t"},
        ]

        with patch("daily_digest._fetch_all_comments", return_value=fake_comments):
            result = scrape_all(["chase"], ["test"], 1, "day")

        assert len(result) == 0

    def test_scrape_all_reports_raw_and_matched_counts(self):
        fake_comments = [
            {"id": "t1_match", "body": "chase offer", "created": ""},
            {"id": "t1_other", "body": "unrelated", "created": ""},
        ]
        stats = {}
        with patch("daily_digest._fetch_all_comments", return_value=fake_comments):
            result = scrape_all(["chase"], ["test"], 1, "all", stats=stats)

        assert len(result) == 1
        assert stats == {"raw_comment_count": 2, "matched_comment_count": 1}


class TestRSSFallback:
    def test_filters_comments_to_selected_posts(self):
        comments = [
            {"id": "t1_a", "_post_id": "a", "post_title": "slug a"},
            {"id": "t1_c", "_post_id": "c", "post_title": "slug c"},
        ]
        with (
            patch("daily_digest.fetch_subreddit_posts_rss", return_value={"a": "Keep me"}) as posts,
            patch("daily_digest.fetch_subreddit_comments_rss", return_value=comments),
            patch("daily_digest.time.sleep"),
        ):
            result = _fetch_all_comments_rss(
                ["test"], posts_per_sub=7, post_sort="top", time_filter="week",
                title_filters={},
            )

        posts.assert_called_once_with("test", limit=7, sort="top", time_filter="week")
        assert [comment["id"] for comment in result] == ["t1_a"]
        assert result[0]["post_title"] == "Keep me"
        assert "_post_id" not in result[0]

    def test_applies_title_filter_before_comment_selection(self):
        comments = [
            {"id": "t1_a", "_post_id": "a", "post_title": "slug a"},
            {"id": "t1_b", "_post_id": "b", "post_title": "slug b"},
        ]
        with (
            patch(
                "daily_digest.fetch_subreddit_posts_rss",
                return_value={"a": "Daily thread", "b": "Keep weekly thread"},
            ),
            patch("daily_digest.fetch_subreddit_comments_rss", return_value=comments),
            patch("daily_digest.time.sleep"),
        ):
            result = _fetch_all_comments_rss(
                ["test"], 10, "new", "day", {"test": "weekly"},
            )

        assert [comment["id"] for comment in result] == ["t1_b"]


# ---------------------------------------------------------------------------
# Markdown preprocessing
# ---------------------------------------------------------------------------
class TestPreprocessMd:
    def test_inserts_blank_line_before_list(self):
        md = "Some text\n- item one\n- item two"
        result = _preprocess_md(md)
        lines = result.split("\n")
        idx = next(i for i, line in enumerate(lines) if line.strip().startswith("- item one"))
        assert lines[idx - 1].strip() == ""

    def test_doubles_nested_indent(self):
        md = "- parent\n  - child"
        result = _preprocess_md(md)
        assert "    - child" in result

    def test_preserves_non_list_content(self):
        md = "# Title\n\nSome paragraph text."
        assert _preprocess_md(md) == md

    def test_handles_numbered_lists(self):
        md = "Intro\n1. First\n2. Second"
        result = _preprocess_md(md)
        lines = result.split("\n")
        idx = next(i for i, line in enumerate(lines) if line.strip().startswith("1."))
        assert lines[idx - 1].strip() == ""


# ---------------------------------------------------------------------------
# Comment formatting
# ---------------------------------------------------------------------------
class TestFormatComments:
    def test_includes_permalink(self):
        comments = [{
            "subreddit": "churning", "post_title": "Test",
            "author": "alice", "score": 10, "created": "2025-06-24",
            "body": "test body", "matched_keywords": ["chase"],
            "depth": 0, "parent_id": "", "id": "t1_abc",
            "post_permalink": "https://reddit.com/r/churning/comments/xyz/test/",
        }]
        text = format_comments_for_prompt(comments)
        assert "abc" in text
        assert "reddit.com" in text

    def test_handles_missing_id(self):
        comments = [{
            "subreddit": "churning", "post_title": "Test",
            "author": "alice", "score": 10, "created": "2025-06-24",
            "body": "test body", "matched_keywords": ["chase"],
            "depth": 0, "parent_id": "", "id": "",
            "post_permalink": "https://reddit.com/r/churning/comments/xyz/test/",
        }]
        text = format_comments_for_prompt(comments)
        assert "reddit.com" in text


# ---------------------------------------------------------------------------
# Summarizer error handling
# ---------------------------------------------------------------------------
class TestSummarizeErrors:
    def test_missing_cli_raises(self):
        with patch("daily_digest.LLM_COMMAND", "__nonexistent_command__"):
            try:
                summarize([{"body": "test", "subreddit": "t", "post_title": "t",
                            "author": "a", "score": 0, "created": "", "id": "",
                            "matched_keywords": [], "depth": 0, "parent_id": "",
                            "post_permalink": ""}])
                assert False, "Should have raised"
            except RuntimeError as e:
                assert "not found" in str(e)

    def test_nonzero_exit_raises(self):
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="model error")
        with patch("subprocess.run", return_value=fake):
            try:
                summarize([{"body": "test", "subreddit": "t", "post_title": "t",
                            "author": "a", "score": 0, "created": "", "id": "",
                            "matched_keywords": [], "depth": 0, "parent_id": "",
                            "post_permalink": ""}])
                assert False, "Should have raised"
            except RuntimeError as e:
                assert "exited with code 1" in str(e)

    def test_timeout_raises(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="test", timeout=600)):
            try:
                summarize([{"body": "test", "subreddit": "t", "post_title": "t",
                            "author": "a", "score": 0, "created": "", "id": "",
                            "matched_keywords": [], "depth": 0, "parent_id": "",
                            "post_permalink": ""}])
                assert False, "Should have raised"
            except RuntimeError as e:
                assert "timed out" in str(e)

    def test_strips_llm_preamble(self):
        preamble = "No skills apply here — this is a content synthesis task.\n\n"
        digest = "# Daily Digest\n\n## Section One\n\nContent here."
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=preamble + digest, stderr="")
        with patch("subprocess.run", return_value=fake):
            result = summarize([{"body": "test", "subreddit": "t", "post_title": "t",
                                 "author": "a", "score": 0, "created": "", "id": "",
                                 "matched_keywords": [], "depth": 0, "parent_id": "",
                                 "post_permalink": ""}])
            assert result.startswith("# Daily Digest")
            assert "skills" not in result

    def test_preserves_clean_output(self):
        digest = "# Daily Digest\n\n## Section One\n\nContent here."
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=digest, stderr="")
        with patch("subprocess.run", return_value=fake):
            result = summarize([{"body": "test", "subreddit": "t", "post_title": "t",
                                 "author": "a", "score": 0, "created": "", "id": "",
                                 "matched_keywords": [], "depth": 0, "parent_id": "",
                                 "post_permalink": ""}])
            assert result == digest


# ---------------------------------------------------------------------------
# Monitor config propagation into the summarization prompt
# ---------------------------------------------------------------------------
class TestPromptMonitorPropagation:
    def _capture_prompt(self, **kwargs):
        captured = {}

        def fake_run(cmd, **run_kwargs):
            captured["prompt"] = run_kwargs.get("input", "")
            return subprocess.CompletedProcess(args=cmd, returncode=0,
                                               stdout="# Digest", stderr="")

        comment = {"body": "test", "subreddit": "t", "post_title": "t",
                   "author": "a", "score": 0, "created": "", "id": "",
                   "matched_keywords": [], "depth": 0, "parent_id": "",
                   "post_permalink": ""}
        with patch("subprocess.run", side_effect=fake_run):
            summarize([comment], **kwargs)
        return captured["prompt"]

    def test_default_prompt_is_churning(self):
        prompt = self._capture_prompt()
        assert "credit card churning and award travel enthusiasts" in prompt
        assert "r/churning, r/CreditCards, r/awardtravel, and r/churningcanada" in prompt
        assert "Monitor focus:" not in prompt

    def test_job_market_monitor_values_reach_prompt(self):
        from monitor_config import load_monitor
        cfg = load_monitor("job-market")
        prompt = self._capture_prompt(
            audience=cfg["digest"]["audience"],
            monitor_name=cfg["name"],
            description=cfg["description"],
            subreddits=cfg["subreddits"],
        )
        assert "software engineers and CS students tracking the job market" in prompt
        assert "r/cscareerquestions" in prompt
        assert "job-market-watch" in prompt
        assert "churning" not in prompt

    def test_explicit_subreddits_used_in_prompt(self):
        prompt = self._capture_prompt(subreddits=["python"])
        assert "scraped from r/python in" in prompt


# ---------------------------------------------------------------------------
# Email wrapper header/footer
# ---------------------------------------------------------------------------
class TestEmailWrapper:
    def test_header_uses_provided_subreddits(self):
        html = _wrap_html_email("<p>x</p>", "Job Digest",
                                subreddits=["cscareerquestions", "experienceddevs"])
        assert "Auto-generated from r/cscareerquestions, r/experienceddevs" in html
        assert "r/churning" not in html

    def test_header_defaults_to_churning_subreddits(self):
        html = _wrap_html_email("<p>x</p>", "Churning Digest")
        assert "r/churning" in html

    def test_no_hardcoded_delivery_time(self):
        html = _wrap_html_email("<p>x</p>", "Digest")
        assert "6:30" not in html


class TestLLMSecurityBoundary:
    def test_claude_runs_without_tools_or_settings(self):
        with patch("daily_digest._resolve_llm_executable", return_value="claude"):
            cmd = _build_llm_command()

        assert cmd[cmd.index("--tools") + 1] == ""
        assert cmd[cmd.index("--setting-sources") + 1] == ""
        assert "--strict-mcp-config" in cmd
        assert "--append-system-prompt" in cmd

    def test_sanitizer_removes_active_and_external_content(self):
        unsafe = (
            "# Digest\n"
            "![beacon](https://evil.example/track)\n"
            "[bad](https://evil.example/phish)\n"
            "[good](https://reddit.com/r/test/comments/abc/post/def/)\n"
            "<script>alert(1)</script>"
        )
        cleaned = sanitize_digest_markdown(unsafe)

        assert "![" not in cleaned
        assert "evil.example" not in cleaned
        assert (
            "[good](https://reddit.com/r/test/comments/abc/post/def/)"
            in cleaned.splitlines()
        )
        assert "<script>" not in cleaned

    def test_email_html_allowlist_blocks_active_content(self):
        rendered = _markdown_to_safe_html(
            "<img src=https://evil.example/x>\n"
            "<form action=https://evil.example><input></form>\n"
            "[source](https://reddit.com/r/test/comments/a/b/c/)"
        )

        assert "<img" not in rendered
        assert "<form" not in rendered
        assert "evil.example" not in rendered
        assert re.findall(r'href="([^"]+)"', rendered) == [
            "https://reddit.com/r/test/comments/a/b/c/"
        ]

    def test_chunking_never_drops_comments(self):
        comments = [
            {"id": f"t1_{i}", "body": "x" * 5000, "author": "a"}
            for i in range(30)
        ]
        with patch("daily_digest.LLM_MAX_INPUT_CHARS", 10_000):
            chunks = _chunk_comments(comments)

        flattened = [comment for chunk in chunks for comment in chunk]
        assert flattened == comments
        assert len(chunks) > 1
