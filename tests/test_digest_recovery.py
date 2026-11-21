"""One saved-input retry must preserve edition, context, deadlines and safeguards."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

import daily_digest as digest
import digest_recovery as recovery
from digest_schema import response_schema


def setup(tmp_path, monkeypatch, *, no_email=True):
    raw = tmp_path / "comments.json"
    rows = [{"id": "t1_a", "author": "alice", "body": "Received $50 credit.", "subreddit": "test",
             "post_permalink": "https://reddit.com/r/test/comments/p/topic/"}]
    raw.write_text(json.dumps(rows), encoding="utf-8")
    raw.with_suffix(".coverage.json").write_text(json.dumps({
        "window_start": "2026-11-18T23:30:00+00:00", "window_end": "2026-11-19T23:30:00+00:00",
        "collection_warnings": [], "collector": "fixture", "replay_unknown": False}), encoding="utf-8")
    monkeypatch.setattr(digest, "EMAIL_TO", "reader@example.com")
    send = Mock(return_value="sent")
    monkeypatch.setattr(digest, "send_email", send)
    collect = Mock(side_effect=AssertionError("must not collect again"))
    monkeypatch.setattr(digest, "scrape_all", collect)
    args = ["--from-json", str(raw), "--digest-date", "2026-11-19", "--source-safe",
            "--subreddits", "test", "--keywords", "credit", "--quality", "strict",
            "--save", str(tmp_path/"digest.md"), "--status-file", str(tmp_path/"status.json"),
            "--delivery-ledger", str(tmp_path/"ledger.db"), "--quiet-summary"]
    if no_email:
        args.append("--no-email")
    return args, raw, send, collect


def test_real_pipeline_retry_reuses_checkpoint_and_sends_at_most_once(tmp_path, monkeypatch):
    args, raw, send, collect = setup(tmp_path, monkeypatch, no_email=False)
    calls, deadlines = [], []

    def invoke(prompt, **kwargs):
        stage = response_schema(prompt)["required"][0]
        calls.append(stage)
        if stage == "notes":
            return json.dumps({"notes": [{"topic": "credit", "text": "Received $50 credit.", "source_ids": ["t1_a"]}]})
        if calls.count("sections") <= 2:
            raise digest.LLMTimeoutError("synthetic timeout")
        return json.dumps({"sections": [{"title": "💳 Credit", "bullets": [
            {"text": "Received $50 credit.", "source_ids": ["t1_a"]}]}]})

    monkeypatch.setattr(digest, "_invoke_llm", invoke)
    def run(argv, **kwargs):
        deadlines.append(kwargs["job_deadline"])
        return digest.main(argv, **kwargs)

    before = recovery.source_fingerprints(raw)
    assert recovery.main(args, runner=run) == 0
    assert deadlines[0] == deadlines[1]
    assert calls == ["notes", "sections", "sections", "sections"]
    assert recovery.source_fingerprints(raw) == before
    assert json.loads((tmp_path/"status.json").read_text())["edition_date"] == "2026-11-19"
    report = json.loads((tmp_path/"digest.recovery.json").read_text())
    assert [r["exit_code"] for r in report["attempts"]] == [1, 0]
    send.assert_called_once()
    collect.assert_not_called()


@pytest.mark.parametrize("failure", [RuntimeError("quota exhausted"), digest.LLMTransientError("auth unavailable"),
                                     RuntimeError("quality validation failed")])
def test_non_timeout_failures_never_retry(tmp_path, monkeypatch, failure):
    args, raw, send, collect = setup(tmp_path, monkeypatch)
    model = Mock(side_effect=failure)
    monkeypatch.setattr(digest, "summarize", model)
    assert recovery.main(args) == 1
    model.assert_called_once()
    assert len(json.loads((tmp_path/"digest.recovery.json").read_text())["attempts"]) == 1
    assert not (tmp_path/"ledger.db").exists()
    send.assert_not_called()


@pytest.mark.parametrize("artifact", ["raw", "coverage", "collection-audit"])
def test_changed_source_blocks_retry(tmp_path, monkeypatch, artifact):
    args, raw, send, collect = setup(tmp_path, monkeypatch)
    raw.with_suffix(".collection-audit.json").write_text('{"version": 1}', encoding="utf-8")
    target = raw if artifact == "raw" else raw.with_suffix(f".{artifact}.json")
    def changed(*args, **kwargs):
        target.write_text("[]")
        raise digest.LLMTimeoutError("timeout")
    model = Mock(side_effect=changed)
    monkeypatch.setattr(digest, "summarize", model)
    assert recovery.main(args) == 1
    model.assert_called_once()
    send.assert_not_called()


def test_retry_never_resets_job_deadline_or_retries_more_than_once(tmp_path, monkeypatch):
    args, raw, send, collect = setup(tmp_path, monkeypatch)
    now, calls = [0], []
    def run(argv, **kwargs):
        calls.append((argv, kwargs["job_deadline"]))
        kwargs["recovery_state"].update(retryable=True, error_type="LLMTimeoutError",
            source_fingerprints=recovery.source_fingerprints(raw), raw_json_path=str(raw), edition_date="2026-11-19")
        now[0] += 1400
        return 1
    assert recovery.main(args, runner=run, clock=lambda: now[0]) == 1
    assert len(calls) == 2 and calls[0][1] == calls[1][1] == 3000
    assert calls[1][0].count("--from-json") == 1 and "--save-raw" not in calls[1][0]
    assert "--no-email" in calls[1][0]


def test_insufficient_time_no_retry(tmp_path, monkeypatch):
    args, raw, send, collect = setup(tmp_path, monkeypatch)
    now, calls = [0], []
    def run(argv, **kwargs):
        calls.append(argv)
        kwargs["recovery_state"].update(retryable=True, error_type="LLMTimeoutError",
            source_fingerprints=recovery.source_fingerprints(raw), raw_json_path=str(raw), edition_date="2026-11-19")
        now[0] = 2900
        return 1
    assert recovery.main(args, runner=run, clock=lambda: now[0]) == 1
    assert len(calls) == 1


def test_frozen_context_and_history_are_reused_and_config_change_stops_retry(tmp_path, monkeypatch):
    args, raw, send, collect = setup(tmp_path, monkeypatch)
    model = Mock(side_effect=digest.LLMTimeoutError("timeout"))
    monkeypatch.setattr(digest, "summarize", model)
    state = {}
    assert digest.main(args, recovery_state=state) == 1
    frozen = json.loads(json.dumps(state["frozen_context"]))
    # An unedited source replay must preserve the original planner inputs.
    assert digest.main(args, recovery_state=state) == 1
    assert state["frozen_context"] == frozen
    assert model.call_count == 2
    changed = list(args)
    changed[changed.index("--keywords")+1] = "different"
    assert digest.main(changed, recovery_state=state) == 1
    assert model.call_count == 2 and not state["retryable"]
    send.assert_not_called()


def test_ambiguous_smtp_is_never_automatically_retried(tmp_path, monkeypatch):
    args, raw, send, collect = setup(tmp_path, monkeypatch, no_email=False)
    monkeypatch.setattr(digest, "summarize", Mock(return_value="# Daily Digest\n\nReceived $50 credit. [u/alice](https://reddit.com/r/test/comments/p/topic/a/)"))
    # This test isolates send classification; output quality is covered above.
    args[args.index("--quality")+1] = "off"
    send.return_value = "uncertain"
    assert recovery.main(args) == 5
    send.assert_called_once()
    assert len(json.loads((tmp_path/"digest.recovery.json").read_text())["attempts"]) == 1


def test_extended_collection_window_keeps_same_prompt_and_never_recollects(tmp_path, monkeypatch):
    from coverage_continuity import CoverageWindow

    args, raw, send, collect = setup(tmp_path, monkeypatch)
    args = recovery._replace_option(args, "--from-json")
    args = recovery._replace_option(args, "--digest-date")
    args = recovery._replace_option(args, "--save-raw", str(raw))
    args = recovery._replace_option(args, "--quality", "off")
    end = datetime(2026, 11, 19, 23, 30, tzinfo=timezone.utc)
    window = CoverageWindow(end - timedelta(hours=25), end, extended=True)
    monkeypatch.setattr("coverage_continuity.resolve_daily_window", lambda *a: window)
    rows = json.loads(raw.read_text())

    def scrape(*args, **kwargs):
        kwargs["stats"].update(raw_comment_count=len(rows), matched_comment_count=len(rows),
                               window_start=window.start.isoformat(), window_end=window.end.isoformat())
        return rows

    collect.side_effect = scrape
    labels = []
    def summarize(comments, label, **kwargs):
        labels.append(label)
        if len(labels) == 1:
            raise digest.LLMTimeoutError("synthetic timeout")
        return "# Daily Digest\n\nReceived $50 credit."

    monkeypatch.setattr(digest, "summarize", summarize)
    assert recovery.main(args) == 0
    assert labels == ["25 hours", "25 hours"]
    collect.assert_called_once()
    send.assert_not_called()
