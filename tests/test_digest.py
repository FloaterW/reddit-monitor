"""Tests for daily_digest.py — time filtering, dedup, markdown preprocessing, summarizer errors."""

import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from daily_digest import (
    _comment_in_window,
    _preprocess_md,
    _wrap_html_email,
    format_comments_for_prompt,
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
