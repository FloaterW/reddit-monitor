"""Offline filtering audit checks; diagnostics must not alter source selection."""

import copy
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import daily_digest as digest
from collection_audit import build_collection_audit

END = datetime(2026, 11, 5, 23, 30, tzinfo=timezone.utc)


def source(identifier, body="BFMR payout delayed", **fields):
    return {
        "id": "t1_" + identifier, "body": body, "author": "example",
        "subreddit": "churning", "post_title": "Discussion",
        "created": "2026-11-05 10:00 UTC",
        "post_permalink": "https://reddit.com/r/churning/comments/thread/topic/",
        **fields,
    }


def test_audit_explains_each_observed_decision_and_keyword_origin_without_mutation():
    rows = [
        source("parent", "Chase offer details", created="2026-11-04 20:00 UTC"),
        source("body"),
        source("title", "Thank you", post_title="BFMR report"),
        source("reply", "This ends tomorrow", parent_id="t1_parent"),
        source("body"),
        source("old", created="2026-11-04 20:00 UTC"),
        source("undated", created="invalid"),
        source("unmatched", "Nothing relevant"),
        source("future", created="2026-11-06 00:00 UTC"),
    ]
    before = copy.deepcopy(rows)
    audit, stats = {}, {}
    with patch.object(digest, "_fetch_all_comments", return_value=rows) as fetch:
        selected = digest.scrape_all(["BFMR", "Chase"], ["churning"], 100, "day",
                                     window_end=END, collection_audit=audit, stats=stats)
        ordinary = digest.scrape_all(["BFMR", "Chase"], ["churning"], 100, "day", window_end=END)
    assert fetch.call_count == 2  # One unchanged fetch call per requested scrape.
    assert selected == ordinary
    assert rows == before
    assert audit["observed_count"] == 9 and audit["retained_count"] == 4
    assert audit["reason_counts"] == {
        "background_context": 1, "keyword_match": 3, "duplicate": 1,
        "outside_window": 2, "undated": 1, "no_keyword": 1,
    }
    records = {record["observation_index"]: record for record in audit["records"]}
    assert records[1]["keyword_matches"] == {"body": ["BFMR"], "title": [], "parent": []}
    assert records[2]["keyword_matches"] == {"body": [], "title": ["BFMR"], "parent": []}
    assert records[3]["keyword_matches"] == {"body": [], "title": [], "parent": ["Chase"]}
    assert "body_excerpt" not in records[1]
    assert records[7]["body_excerpt"] == "Nothing relevant"
    assert audit["feeds"]["churning"]["pre_thread_filter_count"] is None
    assert audit["feeds"]["churning"]["earliest_observed_utc"] == "2026-11-04T20:00:00+00:00"
    assert audit["feeds"]["churning"]["latest_observed_utc"] == "2026-11-06T00:00:00+00:00"


def test_audit_caps_details_but_keeps_all_aggregate_counts_and_declares_truncation():
    rows = [source(str(index), "x" * 700) for index in range(5)]
    decisions = {index: {"retained": False, "reason": "no_keyword"} for index in range(5)}
    audit = build_collection_audit(rows, decisions, max_records=2, excerpt_chars=20)
    assert audit["observed_count"] == 5 and audit["rejected_count"] == 5
    assert audit["reason_counts"] == {"no_keyword": 5}
    assert audit["records_saved"] == 2 and audit["records_omitted"] == 3
    assert audit["records_truncated"] is True
    assert len(audit["records"][0]["body_excerpt"]) == 20
    assert audit["records"][0]["body_excerpt_truncated"] is True
    assert audit["feeds"]["churning"]["observed_count"] == 5


def test_keyword_and_warning_truncation_is_explicit():
    decision = {"retained": True, "reason": "keyword_match", "keyword_matches": {"body": [str(i) for i in range(70)]}}
    warnings = [{"subreddit": "test", "reason": "collection budget reached"}] * 101
    audit = build_collection_audit([source("many")], {0: decision}, warnings=warnings)
    assert audit["records"][0]["keyword_matches_truncated"] is True
    assert len(audit["records"][0]["keyword_matches"]["body"]) == 64
    assert audit["collection_warning_count"] == 101
    assert audit["collection_warnings_truncated"] is True
    assert len(audit["collection_warnings"]) == 100


def test_audit_bounds_and_sanitizes_identifiers_links_and_text():
    row = source("unsafe\n<script>", post_permalink="https://reddit.com.evil.invalid/r/churning/comments/p/title/",
                 body="Line one\nLine two", subreddit="test\x00name")
    audit = build_collection_audit([row], {0: {"retained": False, "reason": "no_keyword"}})
    record = audit["records"][0]
    assert record["source_id"] is None and record["post_permalink"] is None
    assert record["subreddit"] == "test name" and record["body_excerpt"] == "Line one Line two"
    row = source("safe", post_permalink="https://www.reddit.com/r/churning/comments/p/title/?tracking=private")
    audit = build_collection_audit([row], {0: {"retained": True, "reason": "keyword_match"}})
    assert audit["records"][0]["post_permalink"] == "https://reddit.com/r/churning/comments/p/title/"


def arguments(tmp_path, *extra):
    return ["--monitor", "churning", "--subreddits", "churning", "--keywords", "BFMR",
            "--no-email", "--no-db", "--quality", "off", "--quiet-summary",
            "--status-file", str(tmp_path / "status.json"), "--save", str(tmp_path / "digest.md"), *extra]


def test_live_save_persists_separate_audit_without_changing_retained_archive(tmp_path):
    raw = tmp_path / "saved.json"
    rows = [source("yes"), source("no", "Unrelated text")]
    with (
        patch.object(digest, "_fetch_all_comments", return_value=rows),
        patch.object(digest, "summarize", return_value="# Digest\n\nSummary."),
    ):
        assert digest.main(arguments(tmp_path, "--time", "all", "--save-raw", str(raw))) == 0
    saved = json.loads(raw.read_text(encoding="utf-8"))
    assert [row["id"] for row in saved] == ["t1_yes"]
    assert "keyword_matches" not in saved[0]  # Match origins belong only to the audit.
    audit = json.loads(raw.with_suffix(".collection-audit.json").read_text(encoding="utf-8"))
    assert audit["reason_counts"] == {"keyword_match": 1, "no_keyword": 1}
    assert audit["records"][1]["body_excerpt"] == "Unrelated text"
    coverage = json.loads(raw.with_suffix(".coverage.json").read_text(encoding="utf-8"))
    assert coverage["collection_audit_path"] == "saved.collection-audit.json"


def test_zero_matches_still_save_diagnostics_before_failure(tmp_path):
    raw = tmp_path / "empty.json"
    with patch.object(digest, "_fetch_all_comments", return_value=[source("no", "Unrelated text")]), patch.object(digest, "summarize") as model:
        assert digest.main(arguments(tmp_path, "--time", "all", "--save-raw", str(raw))) == 1
    model.assert_not_called()
    assert json.loads(raw.read_text()) == []
    audit = json.loads(raw.with_suffix(".collection-audit.json").read_text())
    assert audit["reason_counts"] == {"no_keyword": 1}


@pytest.mark.parametrize("resave", [False, True])
def test_replay_preserves_original_audit_and_creates_only_requested_manifest(tmp_path, resave):
    raw = tmp_path / "original.json"
    raw.write_text(json.dumps([source("original")]), encoding="utf-8")
    original_audit = raw.with_suffix(".collection-audit.json")
    original_audit.write_text('{"private_original": true}', encoding="utf-8")
    before = {path: path.read_bytes() for path in (raw, original_audit)}
    extra = ["--from-json", str(raw)]
    copy_path = tmp_path / "copy.json"
    if resave:
        extra += ["--save-raw", str(copy_path)]
    with patch.object(digest, "scrape_all", side_effect=AssertionError("No collection during replay")), patch.object(digest, "summarize", return_value="# Digest\n\nSummary."):
        assert digest.main(arguments(tmp_path, *extra)) == 0
    assert {path: path.read_bytes() for path in before} == before
    assert copy_path.with_suffix(".collection-audit.json").exists() is resave
    if resave:
        manifest = json.loads(copy_path.with_suffix(".collection-audit.json").read_text())
        assert manifest["mode"] == "saved_input_replay" and manifest["filtering_observed"] is False
        assert manifest["original_collection_audit"] == str(original_audit.resolve())
        assert "records" not in manifest


@pytest.mark.parametrize("target_name", ["original.json", "original.copy", "original.collection-audit.json"])
def test_replay_save_cannot_overwrite_original_or_its_sidecars(tmp_path, target_name):
    raw = tmp_path / "original.json"
    raw.write_text(json.dumps([source("original")]), encoding="utf-8")
    before = raw.read_bytes()
    with pytest.raises(SystemExit) as error:
        digest.main(arguments(tmp_path, "--from-json", str(raw), "--save-raw", str(tmp_path / target_name)))
    assert error.value.code == 2 and raw.read_bytes() == before
    assert not (tmp_path / "status.json").exists()


def test_footer_does_not_duplicate_punctuation_after_diagnostic_sentences():
    notice = digest.collection_notice([
        {"subreddit": "", "reason": "Daily collection window extended; recovery is not exhaustive."},
        {"subreddit": "test", "reason": "collection budget reached"},
    ])
    assert ".." not in notice
    assert "exhaustive. Affected feeds" in notice
