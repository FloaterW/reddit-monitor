"""Offline reproductions of September 18 delivery-review findings."""

import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

import pytest

import daily_digest as digest
import reddit_scraper as scraper
import verified_digest as verified
from evaluate_digest import check_citation_integrity
from financial_checks import arithmetic_issues


@pytest.mark.parametrize("text", [
    "Booked for 190,000, requiring 133,000 Amex points with a 30% transfer bonus.",
    "133k Amex points became 190k with a 30% transfer bonus.",
    "$250 SUB plus $300 Rakuten bonus: $650 return.",
    "Paid $528 after a $200 credit and $100 cash, net cost was $328.",
    "A $4,500 mortgage requires $3,000 in spending under the 75% option.",
])
def test_inconsistent_arithmetic_is_rejected(text):
    assert arithmetic_issues(text)


@pytest.mark.parametrize("text", [
    "Booked for 190,000, requiring 147,000 Amex points with a 30% transfer bonus.",
    "Booked for 190,000, requiring 133,000 Amex points with a 30% transfer bonus; source arithmetic is inconsistent.",
    "$250 SUB plus $300 Rakuten bonus: $550 return.",
    "Paid $528 after a $200 credit and $100 cash, net cost was $228, plus a $100 property credit.",
    "Paid $528 versus a $386 direct rate; after a $200 credit and $100 cash, net cost was $228.",
    "Paid $528 after a $200 credit or $100 cash, net cost was $328.",
    "A $4,500 mortgage requires $3,375 in spending under the 75% option.",
    "Two different offers: 133k and 190k. Another bank has a 30% transfer bonus.",
    "Transferred 133k to top up an existing 190k balance with a 30% transfer bonus.",
])
def test_consistent_or_ambiguous_relationships_are_not_falsely_rejected(text):
    assert not arithmetic_issues(text)


def row(identifier="a", author="a_user"):
    return {"id": "t1_" + identifier, "author": author, "body": "Received a credit.",
            "post_permalink": "https://reddit.com/r/test/comments/p/topic/"}


def draft(ids, position="start"):
    return {"sections": [{"title": "💳 Credits", "bullets": [{
        "text": "reports a credit." if position == "start" else "Users reported credits.",
        "source_ids": ids, "source_position": position, "indent": 0}]}]}


def test_multi_author_subject_is_rejected_but_end_citations_work():
    rows = [row(), row("b", "bob")]
    assert verified.render_checked(draft(["t1_a", "t1_b"]), rows)[1]
    result, issues = verified.render_checked(draft(["t1_a", "t1_b"], "end"), rows)
    assert not issues and check_citation_integrity(result, rows)["passed"]


def test_duplicate_author_is_not_presented_as_independent_reports():
    assert verified.render_checked(draft(["t1_a", "t1_b"], "end"), [row(), row("b")])[1]


def test_username_underscores_are_literal_in_html_and_valid_citations():
    rows = [row(author="____hope____")]
    text, issues = verified.render_checked(draft(["t1_a"]), rows)
    assert not issues and check_citation_integrity(text, rows)["passed"]
    html = digest._markdown_to_safe_html(text)
    assert "u/____hope____</a>" in html


def test_rss_title_and_missing_parent_are_explicit():
    entry = ET.fromstring('<entry xmlns="http://www.w3.org/2005/Atom">'
        '<id>t1_a</id><author><name>/u/alice</name></author>'
        '<title>alice on Bilt upgrades: new details</title>'
        '<link href="https://reddit.com/r/test/comments/old/short_slug/a/"/>'
        '<content>This ends tomorrow</content></entry>')
    result = scraper._rss_entry_to_comment(entry)
    assert result["post_title"] == "Bilt upgrades: new details"
    assert result["context_status"] == "unavailable_rss"
    assert not result["parent_id"]
    assert verified._source_records([result])[0]["context_status"] == "unavailable_rss"


def test_collection_warning_is_retained_and_disclosed():
    with scraper.collection_diagnostics() as warnings:
        with patch.object(scraper, "_fetch_rss", return_value=None):
            assert scraper.fetch_subreddit_comments_rss("test") == []
    assert len(warnings) == 1
    notice = digest.collection_notice(warnings)
    assert "not exhaustive" in notice and "r/test" in notice
    with scraper.collection_diagnostics() as other:
        assert other == []


def test_rss_retries_are_bounded_and_have_no_final_useless_sleep():
    response = Mock(status_code=429, headers={"Retry-After": "30"})
    with patch.object(scraper._RSS_SESSION, "get", return_value=response) as get, patch.object(scraper, "_retry_sleep") as sleep:
        assert scraper._fetch_rss("/test/.rss") is None
    assert get.call_count == 3
    assert sleep.call_count == 2


def test_pacing_carries_cooldown_between_feeds():
    now = [0.0]
    response = Mock(status_code=429, headers={"Retry-After": "30"})
    def sleep(seconds):
        now[0] += seconds
    with scraper.collection_diagnostics(), patch.object(scraper.time, "monotonic", side_effect=lambda: now[0]), patch.object(scraper.time, "sleep", side_effect=sleep), patch.object(scraper._RSS_SESSION, "get", return_value=response) as get:
        scraper._fetch_rss("/first/.rss")
        before = now[0]
        scraper._fetch_rss("/second/.rss")
    assert get.call_count == 6
    assert now[0] - before >= 60


def test_old_parent_is_explanatory_not_current_news():
    parent = {**row(), "created": "2020-01-01 00:00 UTC", "body": "Chase offer", "parent_id": "t3_p"}
    reply = {**row("b"), "created": "2026-09-18 12:00 UTC", "body": "This ends tomorrow", "parent_id": "t1_a"}
    with patch.object(digest, "_fetch_all_comments", return_value=[reply, parent]):
        result = digest.scrape_all(["chase"], ["test"], 100, "day", window_end=datetime(2026, 9, 18, 22, 30, tzinfo=timezone.utc))
    assert result[0]["context_status"] == "available"
    assert result[1]["context_only"] is True
    assert verified._source_records(result)[0]["parent_context"][0]["background_only"] is True


def test_retry_after_longer_than_budget_is_not_shortened():
    assert scraper._rss_retry_delay("900", 30) == 900
    response = Mock(status_code=429, headers={"Retry-After": "900"})
    with scraper.collection_diagnostics(), scraper.request_budget(300), patch.object(scraper._RSS_SESSION, "get", return_value=response) as get:
        with pytest.raises(TimeoutError):
            scraper._fetch_rss("/test/.rss")
    assert get.call_count == 1


def test_retry_after_http_date_is_honored():
    clock = Mock()
    clock.now.return_value = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    with patch.object(scraper, "datetime", clock):
        assert scraper._rss_retry_delay("Sun, 20 Sep 2026 12:03:00 GMT", 30) == 180


def test_exhausted_feed_is_not_reported_as_complete():
    with scraper.collection_diagnostics() as warnings, patch.object(scraper, "_fetch_rss", return_value=ET.fromstring('<feed xmlns="http://www.w3.org/2005/Atom"/>')):
        scraper.fetch_subreddit_comments_rss("test", cutoff=datetime(2026, 9, 18, tzinfo=timezone.utc))
    assert warnings and "boundary" in warnings[0]["reason"]


def test_original_bad_claim_fails_even_when_all_numbers_exist_in_source():
    text = "Booked for 190,000, requiring 133,000 Amex points with a 30% transfer bonus."
    rows = [{**row(), "body": text}]
    document = draft(["t1_a"], "end")
    document["sections"][0]["bullets"][0]["text"] = text
    assert any("arithmetically inconsistent" in issue for issue in verified.render_checked(document, rows)[1])


def test_coverage_warning_reaches_email_status_and_sidecar(tmp_path):
    rows = [row()]
    document, _ = verified.render_checked(draft(["t1_a"]), rows)
    warning = {"subreddit": "test", "reason": "collection budget reached"}
    def scrape(*args, **kwargs):
        kwargs["stats"].update(raw_comment_count=1, matched_comment_count=1, collection_warnings=[warning])
        return rows
    status = tmp_path / "status.json"
    with patch.object(digest, "scrape_all", side_effect=scrape), patch.object(digest, "summarize", return_value=document), patch.object(digest, "send_email", return_value="sent") as send:
        result = digest.main(["--no-db", "--save", str(tmp_path / "digest.md"),
                              "--save-raw", str(tmp_path / "raw.json"), "--quality", "strict",
                              "--status-file", str(status), "--quiet-summary"])
    assert result == 0
    assert "not exhaustive" in send.call_args.args[1]
    assert json.loads(status.read_text())["status"] == "completed_with_warnings"
    assert json.loads(status.read_text())["collection_warnings"] == [warning]
    assert json.loads((tmp_path / "raw.coverage.json").read_text())["collection_warnings"] == [warning]


def test_unknown_replay_coverage_remains_unknown_after_resaving(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps([row()]))
    text, _ = verified.render_checked(draft(["t1_a"]), [row()])
    copy = tmp_path / "copy.json"
    with patch.object(digest, "summarize", return_value=text), patch.object(digest, "send_email") as send:
        args = ["--no-email", "--no-db", "--quiet-summary", "--status-file", str(tmp_path / "status.json"),
                "--save", str(tmp_path / "digest.md")]
        assert digest.main(args + ["--from-json", str(raw), "--save-raw", str(copy)]) == 0
        assert digest.main(args + ["--from-json", str(copy)]) == 0
    send.assert_not_called()
    assert "completeness is unknown" in (tmp_path / "digest.md").read_text(encoding="utf-8")


def test_eligibility_ratio_cannot_be_invented_from_an_account_count():
    rows = [{**row(), "body": "Only 3 were open in the last 24 months. Approval after recon."}]
    document = draft(["t1_a"])
    document["sections"][0]["bullets"][0]["text"] = "reports approval at 4/24."
    assert verified.render_checked(document, rows)[1]
    document["sections"][0]["bullets"][0]["text"] = "reports approval after opening 3 accounts in the last 24 months."
    assert not verified.render_checked(document, rows)[1]


def test_counted_additional_reporters_require_clearer_attribution():
    rows = [row(), row("b", "bob")]
    document = draft(["t1_a", "t1_b"], "end")
    document["sections"][0]["bullets"][0]["text"] = "Two additional users say the offer worked."
    assert any("independent reporter" in issue for issue in verified.render_checked(document, rows)[1])


def test_arithmetic_failure_blocks_email_in_strict_mode(tmp_path):
    raw = tmp_path / "raw.json"
    claim = "Booked for 190,000, requiring 133,000 Amex points with a 30% transfer bonus."
    raw.write_text(json.dumps([{**row(), "body": claim}]))
    text = "# Digest\n\n- " + claim + " [u/a_user](https://reddit.com/r/test/comments/p/topic/a/)\n"
    with patch.object(digest, "summarize", return_value=text), patch.object(digest, "send_email") as send:
        assert digest.main(["--from-json", str(raw), "--no-db", "--source-safe", "--quality", "strict",
                            "--save", str(tmp_path / "digest.md"), "--status-file", str(tmp_path / "status.json"),
                            "--quiet-summary"]) == 3
    send.assert_not_called()


def test_rss_continues_beyond_ten_pages_and_keeps_active_old_threads():
    calls = []
    def fetch(path, params):
        index = len(calls)
        calls.append(params)
        timestamp = "2026-09-17T20:00:00Z" if index == 12 else "2026-09-18T12:00:00Z"
        return ET.fromstring('<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            f'<id>t1_a{index}</id><updated>{timestamp}</updated>'
            '<author><name>/u/alice</name></author><title>alice on Chase news</title>'
            f'<link href="https://reddit.com/r/test/comments/old{index}/topic/a{index}/"/>'
            '<content>Chase bonus details</content></entry></feed>')
    stats = {}
    with patch.object(digest, "old_reddit_available", return_value=False), patch.object(scraper, "_fetch_rss", side_effect=fetch), patch.object(scraper.time, "sleep"):
        result = digest.scrape_all(["chase"], ["test"], 1, "day", stats=stats,
                                  window_end=datetime(2026, 9, 18, 22, 30, tzinfo=timezone.utc))
    assert len(calls) == 13 and len(result) == 12
    assert len({r["post_permalink"] for r in result}) == 12
    assert stats["collection_warnings"] == [{"subreddit": "test", "reason": "RSS parent context is unavailable; ambiguous replies may be omitted."}]


def test_nested_duplicate_groups_remain_attached():
    rows = [row(), row("b", "bob")]
    notes = [{"topic": str(n), "text": "Credit.", "source_ids": ["t1_a"]} for n in range(9)]
    def invoke(prompt, **kwargs):
        return json.dumps({"sections": [{"title": "💳 Credit", "bullets": [
            {"text": "A user reports a credit.", "source_ids": ["t1_a"], "indent": 0},
            {"text": "Supporting detail.", "source_ids": ["t1_b"], "indent": 1}]}]})
    result = verified.summarize_verified(rows, None, invoke, recovered_notes=notes)
    assert result.count("A user reports a credit.") == 1
    assert result.count("  - Supporting detail.") == 1
