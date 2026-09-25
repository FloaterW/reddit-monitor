"""Regression tests for the failures observed in the expanded live run."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest

import check_digest_ran as watchdog
import reddit_scraper as scraper
import verified_digest as verified
from evaluate_digest import evaluate, evaluation_passed


def comment(identifier="a", body="I received $50."):
    return {"id": f"t1_{identifier}", "author": "alice", "body": body,
            "post_permalink": "https://reddit.com/r/test/comments/p/topic/", "score": 0}


def feed(timestamp):
    return ET.fromstring(
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        '<id>t1_a</id><title>Comment</title>'
        f'<updated>{timestamp}</updated>'
        '<author><name>/u/alice</name></author>'
        '<content>source</content></entry></feed>'
    )


def test_rss_stops_at_old_page():
    with patch.object(scraper, "_fetch_rss", return_value=feed("2020-01-01T00:00:00Z")) as fetch:
        result = scraper.fetch_subreddit_comments_rss("test", cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert len(result) == 1
    assert fetch.call_count == 1


def test_unknown_timestamp_does_not_trigger_time_cutoff():
    with (
        patch.object(scraper, "_fetch_rss", side_effect=[feed("invalid"), None]) as fetch,
        patch.object(scraper.time, "sleep"),
    ):
        scraper.fetch_subreddit_comments_rss("test", cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert fetch.call_count == 2


def test_budget_preserves_already_fetched_comments():
    with (
        patch.object(scraper, "_fetch_rss", side_effect=[feed("2026-09-15T10:00:00Z"), TimeoutError]),
        patch.object(scraper.time, "sleep"),
    ):
        result = scraper.fetch_subreddit_comments_rss("test")
    assert len(result) == 1


def test_budget_blocks_excessive_wait_and_restores_scope():
    with scraper.request_budget(1):
        assert 0 < scraper._request_timeout() <= 1
        with pytest.raises(TimeoutError):
            scraper._retry_sleep(120)
    assert scraper._request_timeout() == 20


def document(text="A user received **$50**.", ids=None):
    return {"sections": [{"title": "💳 Credit report", "bullets": [
        {"text": text, "source_ids": ids if ids is not None else ["t1_a"]},
    ]}]}


def test_citations_are_constructed_and_unrelated_comments_not_appended():
    rows = [comment(), comment("b", "lol thanks")]
    result, errors = verified.render_checked(document(), rows)
    assert not errors
    assert "## 💳" in result and "[u/alice]" in result
    assert "lol thanks" not in result and "Source excerpts" not in result
    assert evaluation_passed(evaluate(result, rows))


@pytest.mark.parametrize("text,ids", [
    ("Received $500.", ["t1_a"]), ("Received $50.", ["t1_invented"]),
    ("<script>alert(1)</script>", ["t1_a"]), ("> raw dump", ["t1_a"]),
    ("[u/bob](https://evil.example/)", ["t1_a"]), ("word " * 101, ["t1_a"]),
])
def test_bad_claims_markup_and_raw_dumps_are_rejected(text, ids):
    result, errors = verified.render_checked(document(text, ids), [comment()])
    assert result is None and errors


def test_source_records_preserve_reply_relationship():
    parent = comment(body="The Chase deal gives $50.")
    reply = {**comment("b", "This ends tomorrow."), "parent_id": "t1_a", "parent_context": [parent]}
    result = verified._source_records([reply, {**parent, "context_only": True}])
    assert result[0]["parent_id"] == "t1_a" and result[1]["background_only"]
    assert result[0]["parent_context"][0]["body"] == parent["body"]
    assert result[0]["parent_context"][0]["background_only"]


def test_newsletter_supports_leading_attribution_nested_details_and_headline_amount():
    draft = document("reports a new credit.")
    section = draft["sections"][0]
    section["title"] = "💳 New $50 Credit"
    section["bullets"][0]["source_position"] = "start"
    section["bullets"].append({"text": "**Amount:** $50.", "source_ids": ["t1_a"], "indent": 1})
    result, errors = verified.render_checked(draft, [comment()])
    assert not errors
    assert "- [u/alice]" in result and "  - **Amount:** $50." in result
    assert evaluation_passed(evaluate(result, [comment()]))


def test_headline_cannot_borrow_numbers_from_another_section():
    draft = document()
    draft["sections"][0]["title"] = "💳 New $500 Credit"
    draft["sections"].append({"title": "💳 Different Offer", "bullets": [
        {"text": "Received $500.", "source_ids": ["t1_b"]}]})
    result, errors = verified.render_checked(draft, [comment(), comment("b", "Received $500.")])
    assert result is None and errors


def test_nested_bullet_cannot_borrow_parent_numeric_claim():
    draft = document()
    draft["sections"][0]["bullets"].append(
        {"text": "Received $50.", "source_ids": ["t1_b"], "indent": 1})
    result, errors = verified.render_checked(draft, [comment(), comment("b", "No credit.")])
    assert result is None and errors


def test_orphan_nested_bullet_is_rejected():
    draft = document()
    draft["sections"][0]["bullets"][0]["indent"] = 1
    result, errors = verified.render_checked(draft, [comment()])
    assert result is None and errors


def test_busy_newsletter_can_keep_more_than_twenty_four_topics():
    draft = {"sections": [{"title": f"💳 Topic {i}", "bullets": document()["sections"][0]["bullets"]}
                          for i in range(31)]}
    result, errors = verified.render_checked(draft, [comment()])
    assert not errors and result.count("\n## ") == 31


@pytest.mark.parametrize("error", [RuntimeError, OSError])
def test_ai_failure_never_returns_a_raw_comment_dump(error):
    rows = [comment(), comment("b")]
    with patch.object(verified, "AI_BUDGET_SECONDS", 10):
        calls = []

        def invoke(prompt, timeout, structured):
            calls.append(timeout)
            raise error("AI unavailable")

        with pytest.raises(error):
            verified.summarize_verified(rows, lambda c: [[x] for x in c], invoke)
    assert calls
    assert all(0 < timeout <= 10 for timeout in calls)


def test_expired_ai_budget_uses_no_model_calls():
    with patch.object(verified, "AI_BUDGET_SECONDS", 0):
        with pytest.raises(RuntimeError, match="budget exhausted"):
            verified.summarize_verified([comment()], lambda c: [c], None)


def test_synthesis_repairs_incorrect_number_once():
    notes = {"notes": [{"topic": "credit", "text": "Received $50.", "source_ids": ["t1_a"]}]}
    responses = [notes, document("Received $500."), document()]
    calls = []

    def invoke(prompt, **kwargs):
        calls.append(prompt)
        return json.dumps(responses.pop(0))

    result = verified.summarize_verified([comment()], lambda c: [c], invoke)
    assert len(calls) == 3 and "VALIDATION ISSUES" in calls[-1]
    assert "$500" not in result and "## 💳" in result


def test_diagnostic_coverage_does_not_override_numeric_failure():
    assert evaluation_passed({"coverage": {"passed": False, "required": False},
                              "numbers": {"passed": True}})
    assert not evaluation_passed({"coverage": {"passed": False, "required": False},
                                  "numbers": {"passed": False}})


def test_extraction_repairs_unknown_sources_and_keeps_artifacts(tmp_path):
    responses = [
        {"notes": [{"text": "Received $50.", "source_ids": ["invented"]}]},
        {"notes": [{"text": "Received\n**$50**.", "source_ids": ["a"]}]},
        document(),
    ]
    calls = []

    def invoke(prompt, **kwargs):
        calls.append(prompt)
        return json.dumps(responses.pop(0))

    result = verified.summarize_verified([comment()], lambda c: [c], invoke,
                                        artifact_dir=tmp_path)
    assert len(calls) == 3 and "previous reply" in calls[1]
    assert '"source_ids": ["t1_a"]' in calls[2]
    assert len(list(tmp_path.glob("model-*.json"))) == 3
    assert "## 💳" in result


def test_extraction_never_accepts_unknown_sources_after_repair():
    response = {"notes": [{"text": "Received $50.", "source_ids": ["invented"]}]}
    calls = []

    def invoke(prompt, **kwargs):
        calls.append(prompt)
        return json.dumps(response)

    with pytest.raises(RuntimeError, match="after repair"):
        verified.summarize_verified([comment()], lambda c: [c], invoke)
    assert len(calls) == 2


def test_watchdog_distinguishes_running_from_stale(tmp_path):
    now = datetime(2026, 9, 15, 19, 30).astimezone()
    status = tmp_path / "status.json"
    for minutes, expected in [(60, "running"), (91, "failed"), (-5, "failed")]:
        status.write_text(json.dumps({"date": now.date().isoformat(), "status": "running",
                                      "started_at": (now - timedelta(minutes=minutes)).isoformat()}))
        assert watchdog.check_run_status(status, now)[0] == expected


def test_watchdog_waits_and_alerts_if_running_becomes_failed():
    with (
        patch.object(watchdog, "check_run_status", side_effect=[("running", "working"), ("failed", "stale")]),
        patch.object(watchdog.time, "sleep") as sleep,
        patch.object(watchdog, "send_alert", return_value=True) as alert,
        patch.object(watchdog, "datetime") as clock,
    ):
        clock.now.return_value = datetime(2026, 9, 15, 21)
        result = watchdog.main(["--wait-running"], now=datetime(2026, 9, 15, 19, 30))
    assert result == 0
    sleep.assert_called_once_with(30)
    alert.assert_called_once()
