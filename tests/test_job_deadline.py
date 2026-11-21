"""Full threaded synthesis under a deterministic two-worker latency model."""

import json
import threading
from unittest.mock import patch

import pytest

import daily_digest as digest
import verified_digest as verified
from digest_schema import response_schema
from editorial_profiles import resolve_editorial
from synthesis_budget import SynthesisBudget


def rows(count):
    return [{"id": f"t1_n{i}", "author": f"author{i}", "body": "Received $50 credit.",
             "subreddit": "test", "post_permalink": "https://reddit.com/r/test/comments/p/topic/"}
            for i in range(count)]


class LatencyModel:
    """Real worker threads; pairs advance simulated wall time concurrently.

    Equal-duration pairs exactly model two concurrent requests. An unequal pair
    deliberately holds its faster worker until the slower finishes, providing a
    conservative schedule rather than pretending their durations add serially.
    """
    def __init__(self, count, now, seconds=30, failures=False):
        self.count, self.now, self.seconds, self.failures = count, now, seconds, failures
        self.lock = threading.Lock()
        self.calls = []
        self.visits = {}
        self.wave = []
        self.barrier = threading.Barrier(2, action=self.advance)

    def advance(self):
        with self.lock:
            self.now[0] += max(self.wave)
            self.wave.clear()

    def invoke(self, prompt, **kwargs):
        stage = response_schema(prompt)["required"][0]
        if stage == "notes":
            evidence, _ = json.JSONDecoder().raw_decode(prompt.split("SOURCE COMMENTS:\n", 1)[1])
            sid = evidence[0]["source_id"]
            with self.lock:
                visit = self.visits.get(sid, 0)
                self.visits[sid] = visit + 1
                number = len(self.calls)
                timeout = self.failures and sid == "t1_n0" and visit == 0
                duration = kwargs["timeout"] if timeout else min(self.seconds, kwargs["timeout"])
                self.calls.append({"source": sid, "timeout": kwargs["timeout"], "timed_out": timeout})
                solo = self.count % 2 and number == self.count - 1
                if solo:
                    self.now[0] += duration
                else:
                    self.wave.append(duration)
            if not solo:
                self.barrier.wait(timeout=10)
            if timeout:
                raise digest.LLMTimeoutError("synthetic stalled extraction")
            if self.failures and sid == "t1_n4" and visit == 0:
                return json.dumps({"notes": [{"topic": "Credit", "text": "Received $50 credit.", "source_ids": ["invented"]}]})
            return json.dumps({"notes": [{"topic": "Credit", "text": "Received $50 credit.", "source_ids": [sid]}]})
        self.now[0] += 20
        if stage == "stories":
            payload, _ = json.JSONDecoder().raw_decode(prompt.split("EDITORIAL PLAN:\n", 1)[1])
            ids = [n["note_id"] for n in payload["notes"]]
            stories = [{"topic": "Credits", "note_ids": ids[:8], "status": "new", "reason": "Selected fixture reports."}]
            if ids[8:]:
                stories.append({"topic": "Similar credits", "note_ids": ids[8:], "status": "repeat", "reason": "Unchanged fixture reports."})
            return json.dumps({"stories": stories})
        evidence, _ = json.JSONDecoder().raw_decode(prompt.split("ORIGINAL SOURCES:\n", 1)[1])
        return json.dumps({"sections": [{"title": "💳 Credits", "bullets": [
            {"text": "Received $50 credit.", "source_ids": [r["source_id"]]} for r in evidence]}]})


@pytest.mark.parametrize("count,expected_calls,success", [(19, 19, True), (30, 30, True), (100, 80, False)])
def test_cold_threaded_workloads_finish_or_fail_closed_at_real_phase_boundary(tmp_path, count, expected_calls, success):
    now = [0]
    model = LatencyModel(count, now)
    with patch.object(verified.time, "monotonic", side_effect=lambda: now[0]):
        if success:
            result = verified.summarize_verified(rows(count), lambda r: [[c] for c in r], model.invoke,
                artifact_dir=tmp_path, editorial_profile=resolve_editorial({"preset": "churning"}), job_deadline=3000)
            assert "Received $50 credit" in result
            assert json.loads((tmp_path/"editorial-plan.json").read_text())["inputs"]["notes"]
        else:
            with pytest.raises(RuntimeError, match="extraction time budget exhausted"):
                verified.summarize_verified(rows(count), lambda r: [[c] for c in r], model.invoke,
                    artifact_dir=tmp_path, editorial_profile=resolve_editorial({"preset": "churning"}), job_deadline=3000)
            assert not (tmp_path/"validated-digest.json").exists()
    assert len(model.calls) == expected_calls
    assert now[0] <= 3000


def test_timeout_retry_and_semantic_repair_share_concurrent_workload_allowance(tmp_path):
    now = [0]
    model = LatencyModel(32, now, failures=True)
    with patch.object(verified.time, "monotonic", side_effect=lambda: now[0]):
        assert verified.summarize_verified(rows(30), lambda r: [[c] for c in r], model.invoke,
            artifact_dir=tmp_path, editorial_profile=resolve_editorial({"preset": "churning"}), job_deadline=3000)
    assert len(model.calls) == 32 and sum(c["timed_out"] for c in model.calls) == 1
    assert all(c["timeout"] <= 120 for c in model.calls)
    assert model.visits["t1_n0"] == model.visits["t1_n4"] == 2
    assert now[0] < 900


def test_mixed_cached_and_fresh_chunks_keep_every_note_and_do_not_repeat_cached_work(tmp_path):
    now = [0]
    initial = LatencyModel(30, now)
    profile = resolve_editorial({"preset": "churning"})
    with patch.object(verified.time, "monotonic", side_effect=lambda: now[0]):
        first = verified.summarize_verified(rows(30), lambda r: [[c] for c in r], initial.invoke,
            artifact_dir=tmp_path, editorial_profile=profile, job_deadline=3000)
    removed = set()
    for path in sorted(tmp_path.glob("checkpoint-*.json")):
        reply = json.loads(path.read_text())["reply"]
        if "notes" in reply and len(removed) < 8:
            removed.add(reply["notes"][0]["source_ids"][0])
            path.unlink()
    now[0] = 0
    resumed = LatencyModel(8, now)
    with patch.object(verified.time, "monotonic", side_effect=lambda: now[0]):
        second = verified.summarize_verified(rows(30), lambda r: [[c] for c in r], resumed.invoke,
            artifact_dir=tmp_path, editorial_profile=profile, job_deadline=3000)
    assert first == second and {c["source"] for c in resumed.calls} == removed
    plan = json.loads((tmp_path/"editorial-plan.json").read_text())
    assert len(plan["inputs"]["notes"]) == 30


def test_preprocessing_does_not_extend_absolute_deadline():
    now, limits = [0], []
    def chunks(records):
        now[0] += 20
        return [records]
    def invoke(prompt, **kwargs):
        limits.append(kwargs["timeout"])
        if response_schema(prompt)["required"][0] == "notes":
            return json.dumps({"notes": [{"text": "Received $50 credit.", "source_ids": ["t1_n0"]}]})
        return json.dumps({"sections": [{"title": "Credit", "bullets": [{"text": "Received $50 credit.", "source_ids": ["t1_n0"]}]}]})
    with patch.object(verified.time, "monotonic", side_effect=lambda: now[0]):
        assert verified.summarize_verified(rows(1), chunks, invoke, job_deadline=100)
    assert limits == [80, 80]
    assert SynthesisBudget(30, deadline=100, clock=lambda: 101).seconds == 0
