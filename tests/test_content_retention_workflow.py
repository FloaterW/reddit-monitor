"""Offline provider contracts for omitted-source review and bounded extraction."""
import json

import pytest

import verified_digest as verified
from editorial_profiles import resolve_editorial


def source(identifier="a", body="My cashback was not transferred to miles; both balances remain separate."):
    return {"id": f"t1_{identifier}", "author": f"user_{identifier}", "body": body,
            "subreddit": "test", "created": "2026-11-04 20:00 UTC",
            "post_permalink": "https://reddit.com/r/test/comments/post/topic/",
            "context_status": "unavailable_rss", "parent_context": []}


def note(sid, text="Cashback remained separate from miles after the account transition."):
    return {"topic": "Rewards transition", "text": text, "source_ids": [sid]}


def document(ids, text="reports that cashback remained separate from miles after the transition."):
    return {"sections": [{"title": "💳 Rewards transition", "bullets": [
        {"text": text, "source_ids": [sid], "source_position": "start", "indent": 0} for sid in ids]}]}


def payload(prompt, marker):
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1].lstrip())[0]


def test_review_recovers_omitted_firsthand_source_and_writes_a_trace(tmp_path):
    row = source()
    stages = []

    def invoke(prompt, **kwargs):
        if "OMISSION REVIEW:\n" in prompt:
            stages.append("review")
            data = payload(prompt, "OMISSION REVIEW:\n")
            assert data["existing_notes"] == []
            assert kwargs["timeout"] <= 90
            return json.dumps({"notes": [note(row["id"])], "decisions": [
                {"source_id": row["id"], "status": "added", "reason": "Firsthand conversion outcome was absent."}]})
        if "ORIGINAL SOURCES:\n" in prompt:
            stages.append("write")
            return json.dumps(document([row["id"]]))
        stages.append("extract")
        return '{"notes": []}'

    report = {}
    output = verified.summarize_verified([row], lambda rows: [rows], invoke, artifact_dir=tmp_path,
                                         editorial_report=report, edition_cutoff="2026-11-04",
                                         editorial_profile=resolve_editorial({"preset": "churning"}))
    assert stages == ["extract", "review", "write"]
    assert "cashback remained separate from miles" in output
    trace = json.loads((tmp_path / "source-selection.json").read_text(encoding="utf-8"))
    assert trace["sources"][0]["outcome"] == "published"
    assert trace["sources"][0]["omission_review"]["status"] == "added"
    assert report["omission_review"]["reviewed_count"] == 1
    assert trace["document_validation"] == "passed"
    assert trace["delivery_status"] == "not_observed"


def test_review_bound_is_disclosed_and_source_ids_are_not_called_complete(monkeypatch, tmp_path):
    rows = [source(str(i)) for i in range(3)]
    monkeypatch.setattr(verified, "MAX_OMISSION_SOURCES", 2)
    review_calls = []

    def invoke(prompt, **kwargs):
        if "OMISSION REVIEW:\n" in prompt:
            data = payload(prompt, "OMISSION REVIEW:\n")
            review_calls.append(data)
            return json.dumps({"notes": [], "decisions": [
                {"source_id": s["source_id"], "status": "already_covered", "reason": "The note already preserves the separate balances."}
                for s in data["sources"]]})
        if "ORIGINAL SOURCES:\n" in prompt:
            return json.dumps(document([r["id"] for r in rows]))
        return json.dumps({"notes": [note(r["id"]) for r in rows]})

    report = {}
    verified.summarize_verified(rows, lambda rows: [rows], invoke, artifact_dir=tmp_path,
                               editorial_report=report, edition_cutoff="2026-11-04",
                               editorial_profile=resolve_editorial({"preset": "churning"}))
    assert len(review_calls) == 1 and len(review_calls[0]["sources"]) == 2
    assert report["omission_review"]["candidate_count"] == 3
    assert report["omission_review"]["unreviewed_count"] == 1


def test_churning_extraction_splits_large_character_batches_by_source_count(tmp_path):
    rows = [source(str(i), "A source-described account change.") for i in range(81)]
    sizes = []

    def invoke(prompt, **kwargs):
        assert "OMISSION REVIEW:\n" not in prompt
        if "ORIGINAL SOURCES:\n" in prompt:
            data = payload(prompt, "ORIGINAL SOURCES:\n")
            return json.dumps(document([r["source_id"] for r in data], "reports an account change."))
        data = payload(prompt, "SOURCE COMMENTS:\n")
        sizes.append(len(data))
        return json.dumps({"notes": [note(data[0]["source_id"], "A source-described account change.")]})

    verified.summarize_verified(rows, lambda rows: [rows], invoke, artifact_dir=tmp_path, edition_cutoff="2026-11-04",
                               editorial_profile=resolve_editorial({"preset": "churning"}))
    assert sorted(sizes) == [1, 80]


def test_general_profile_does_not_inherit_domain_candidate_review(tmp_path):
    row = source()

    def invoke(prompt, **kwargs):
        assert "OMISSION REVIEW:\n" not in prompt
        if "ORIGINAL SOURCES:\n" in prompt:
            return json.dumps(document([row["id"]]))
        return json.dumps({"notes": [note(row["id"])]})

    report = {}
    verified.summarize_verified([row], lambda rows: [rows], invoke, artifact_dir=tmp_path,
                               editorial_profile=resolve_editorial({"preset": "general"}), editorial_report=report)
    assert report["omission_review"]["enabled"] is False


@pytest.mark.parametrize("broken", [{"sections": None}, {"sections": [None]},
                                     {"sections": [{"title": "Update", "bullets": None}]}])
def test_malformed_writer_structure_gets_bounded_repair_instead_of_type_error(tmp_path, broken):
    row = source(body="A source-described account change.")
    writes = []

    def invoke(prompt, **kwargs):
        if "ORIGINAL SOURCES:\n" in prompt:
            writes.append(prompt)
            return json.dumps(broken if len(writes) == 1 else document([row["id"]], "reports an account change."))
        return json.dumps({"notes": [note(row["id"], "Account change.")]})

    verified.summarize_verified([row], lambda rows: [rows], invoke, artifact_dir=tmp_path,
                               editorial_profile=resolve_editorial({"preset": "churning"}))
    assert len(writes) == 2
    assert "REPAIR THIS DRAFT" in writes[1]


def test_persistently_malformed_writer_stops_after_bounded_repairs(tmp_path):
    row = source(body="A source-described account change.")
    writes = []

    def invoke(prompt, **kwargs):
        if "ORIGINAL SOURCES:\n" in prompt:
            writes.append(prompt)
            return '{"sections": null}'
        return json.dumps({"notes": [note(row["id"], "Account change.")]})

    with pytest.raises(RuntimeError, match="failed validation after repair"):
        verified.summarize_verified([row], lambda rows: [rows], invoke, artifact_dir=tmp_path,
                                   editorial_profile=resolve_editorial({"preset": "churning"}))
    assert len(writes) == 3
    assert not (tmp_path / "validated-digest.json").exists()


def test_title_only_identity_requires_local_discussion_attribution_in_heading_and_bullet():
    row = source(body="My purchase promotion arrived by email; I enrolled.")
    row["post_title"] = "Fidelity Visa promotion"
    draft = document([row["id"]], "reports enrolling in the Fidelity Visa promotion.")
    draft["sections"][0]["title"] = "Fidelity Visa promotion"
    rendered, issues = verified.render_checked(draft, [row], factual_checks=True)
    assert rendered is None
    assert any(issue.startswith("Heading") and "discussion" in issue for issue in issues)
    assert any(issue.startswith("Bullet") and "discussion" in issue for issue in issues)
    draft["sections"][0]["title"] = "Purchase promotion reported in a Fidelity Visa discussion"
    draft["sections"][0]["bullets"][0]["text"] = "reports enrolling in an emailed purchase promotion."
    rendered, issues = verified.render_checked(draft, [row], factual_checks=True)
    assert rendered and not issues


def test_domain_title_check_does_not_change_general_rendering():
    row = source(body="My purchase promotion arrived by email; I enrolled.")
    row["post_title"] = "Fidelity Visa promotion"
    draft = document([row["id"]], "reports enrolling in the Fidelity Visa promotion.")
    rendered, issues = verified.render_checked(draft, [row], factual_checks=False)
    assert rendered and not issues
