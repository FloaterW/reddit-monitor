"""Regressions for the expanded API workload exhausting planning time."""

import json
from unittest.mock import patch

import pytest

import daily_digest as digest
import verified_digest as verified
from digest_schema import response_schema
from synthesis_budget import SynthesisBudget


@pytest.mark.parametrize("batches,expected", [(0, 1500), (1, 1500), (19, 1700), (30, 2100), (100, 2400)])
def test_allowance_scales_with_parallel_work_but_has_hard_ceiling(batches, expected):
    assert SynthesisBudget(batches).seconds == expected


def test_job_deadline_takes_priority_and_is_not_reset_between_phases():
    now = [0]
    budget = SynthesisBudget(30, available=1300, clock=lambda: now[0])
    assert budget.timeout(120) == 100
    now[0] = 100
    with pytest.raises(RuntimeError, match="extraction time budget exhausted"):
        budget.timeout(120)
    budget.phase = "planning"
    assert budget.timeout(240) == 240
    now[0] = 700
    with pytest.raises(RuntimeError, match="planning time budget exhausted"):
        budget.timeout(240)
    budget.phase = "writing"
    assert budget.timeout(240) == 240
    now[0] = 1299
    assert budget.timeout(240) == 1
    now[0] = 1300
    with pytest.raises(RuntimeError, match="writing time budget exhausted"):
        budget.timeout(240)
    assert budget.deadline == 1300


def test_exhausted_collection_does_not_start_another_ai_budget():
    budget = SynthesisBudget(30, available=-10)
    budget.phase = "writing"
    assert budget.seconds == 0
    with pytest.raises(RuntimeError):
        budget.timeout(240)


def test_worst_allowed_extraction_and_review_preserve_full_planning_and_writing():
    now = [0]
    budget = SynthesisBudget(30, clock=lambda: now[0])
    # Even complete consumption of the extraction allowance (including repeated
    # stalled calls) cannot recreate Nov 19's 79-second planning allowance.
    now[0] = 899
    assert budget.timeout(120) == 1
    now[0] = 900
    with pytest.raises(RuntimeError):
        budget.timeout(120)
    budget.phase = "review"
    assert budget.timeout(90) == 90
    now[0] = 1080
    budget.phase = "planning"
    assert budget.timeout(240) == 240
    now[0] += 240
    assert budget.timeout(240) == 180  # Retry cannot borrow writing time.
    now[0] += 180
    budget.phase = "writing"
    assert budget.deadline - now[0] == 600


def test_completed_extraction_survives_failed_writer_and_recovery_revalidates(tmp_path):
    rows = [{"id": "t1_a", "author": "alice", "body": "Received $50.",
             "post_permalink": "https://reddit.com/r/test/comments/p/topic/"}]
    notes = {"notes": [{"topic": "credit", "text": "Received $50.", "source_ids": ["t1_a"]}]}
    document = {"sections": [{"title": "Credit", "bullets": [
        {"text": "Received $50.", "source_ids": ["t1_a"]}]}]}
    limits = []

    def interrupted(prompt, **kwargs):
        stage = response_schema(prompt)["required"][0]
        limits.append((stage, kwargs["timeout"]))
        if stage == "notes":
            return json.dumps(notes)
        raise digest.LLMTimeoutError("provider stalled")

    with pytest.raises(digest.LLMTimeoutError):
        verified.summarize_verified(rows, lambda r: [r], interrupted, artifact_dir=tmp_path)
    assert limits == [("notes", 120), ("sections", 240), ("sections", 240)]
    assert not (tmp_path / "validated-digest.json").exists()
    interrupted_report = json.loads((tmp_path / "ai-budget.json").read_text())
    assert [e["outcome"] for e in interrupted_report["events"]] == ["completed", "timeout", "timeout"]
    stages = []

    def resume(prompt, **kwargs):
        stages.append(response_schema(prompt)["required"][0])
        return json.dumps(document)

    result = verified.summarize_verified(rows, lambda r: [r], resume, artifact_dir=tmp_path)
    assert stages == ["sections"] and "$50" in result and "[u/alice]" in result
    report = json.loads((tmp_path / "ai-budget.json").read_text())
    assert [e["outcome"] for e in report["events"]] == ["reused", "completed"]
    # Cached model responses never waive validation on a subsequent replay.
    rows[0]["body"] = "Received $5."
    with patch.object(verified, "AI_BUDGET_SECONDS", 2400), pytest.raises(RuntimeError, match="failed validation"):
        verified.summarize_verified(rows, None, resume, recovered_notes=notes["notes"])
