"""Synthetic regressions for shared-parent facts copied between current stories."""

import copy
import json

import pytest

import verified_digest as verified
from digest_schema import response_schema
from editorial_novelty import (
    same_edition_repeats,
    scoped_repeat_repair_issues,
    shared_fact_retention_issues,
)
from editorial_profiles import resolve_editorial

OFFER = "Targeted Bilt Card holders can earn 900 bonus points after spending $450 in everyday purchases."
REPEAT = "The Bilt Card Holiday Shopping Offer lets targeted cardholders earn 900 bonus points after spending $450 in everyday purchases."
MATCH = "The JetBlue match costs 8,000 points."


def bullet(text, source="t1_shared", indent=0):
    return {"text": text, "source_ids": [source], "source_position": "end", "indent": indent}


def draft(second=REPEAT):
    return {"sections": [
        {"title": "Bilt holiday shopping", "bullets": [bullet(OFFER)]},
        {"title": "JetBlue match", "bullets": [bullet(MATCH), bullet(second, indent=1)]}]}


def test_strong_same_source_numeric_repeat_is_diagnostic_only():
    document = draft()
    before = copy.deepcopy(document)
    findings = same_edition_repeats(document)
    assert len(findings) == 1
    assert findings[0]["section_index"] == 1 and findings[0]["repeated_from_section"] == 0
    assert findings[0]["source_ids"] == ["t1_shared"]
    assert document == before


@pytest.mark.parametrize("second", [
    REPEAT.replace("900", "950"),
    REPEAT.replace("900", "450").replace("$450", "$900"),
    REPEAT.replace("Bilt Card", "Bilt Obsidian Card"),
    REPEAT.replace("targeted cardholders", "new customers"),
    REPEAT.replace("everyday purchases", "grocery purchases"),
    REPEAT.replace("900 bonus points", "$900 bonus points").replace("$450", "450"),
    REPEAT + " It is now public.",
    REPEAT + " It expires tomorrow.",
    REPEAT + " One applicant was denied.",
    REPEAT + " The terms contradict yesterday's report.",
])
def test_changed_terms_scope_outcomes_and_qualifications_are_retained(second):
    assert not same_edition_repeats(draft(second))


def test_same_numbers_different_chase_product_and_card_scope_are_not_duplicates():
    for first, second in [
        ("Chase Ink Cash", "Chase Ink Preferred"),
        ("Bilt Obsidian", "Bilt Palladium"),
        ("Atmos Business", "Atmos Personal"),
        ("Chase Ink cash", "Chase Ink preferred"),
    ]:
        document = draft()
        document["sections"][0]["bullets"][0]["text"] = OFFER.replace("Bilt Card", first)
        document["sections"][1]["bullets"][1]["text"] = OFFER.replace("Bilt Card", second)
        assert not same_edition_repeats(document)


def test_distinct_corroborating_sources_and_within_story_context_are_preserved():
    document = draft()
    document["sections"][1]["bullets"][1]["source_ids"] = ["t1_other_author"]
    assert not same_edition_repeats(document)
    document["sections"][1]["bullets"][1]["source_ids"] = ["t1_shared", "t1_other_author"]
    assert not same_edition_repeats(document)
    document = draft()
    document["sections"][0]["bullets"].extend(document["sections"].pop()["bullets"])
    assert not same_edition_repeats(document)


@pytest.mark.parametrize("parent_section", [0, 1])
def test_parent_offer_with_unique_nested_child_is_not_removed_or_reparented(parent_section):
    document = {"sections": [
        {"title": "Bilt holiday shopping", "bullets": [bullet(OFFER)]},
        {"title": "Other benefits", "bullets": [bullet(MATCH), bullet(REPEAT)]},
    ]}
    document["sections"][parent_section]["bullets"].append(
        bullet("This cardholder's enrollment confirmation must be retained for support.", indent=1))
    original = copy.deepcopy(document)
    assert not same_edition_repeats(document)
    assert document == original


def test_ambiguous_generic_overlap_is_not_blocked_and_repair_cannot_remove_all_facts():
    document = draft()
    for section in document["sections"]:
        section["bullets"] = [bullet("The welcome offer earns 900 bonus points after spending $450.")]
    assert not same_edition_repeats(document)
    original = draft()
    findings = same_edition_repeats(original)
    assert not shared_fact_retention_issues(original, findings)
    assert shared_fact_retention_issues({"sections": [{"title": "Other", "bullets": [bullet(MATCH)]}]}, findings)


def test_heading_cardholder_context_and_awards_paraphrase_retain_first_specific_story():
    document = draft()
    document["sections"][0]["title"] = "Targeted Bilt holiday shopping bonus through Nov 30th"
    document["sections"][0]["bullets"][0]["text"] = OFFER.replace("Bilt Card holders", "cardholders").replace("spending ", "")
    document["sections"].append({"title": "Bilt statement-credit boost", "bullets": [
        bullet(REPEAT.replace("lets targeted cardholders earn", "awards targeted cardholders"))]})
    findings = same_edition_repeats(document)
    assert len(findings) == 2 and all(f["repeated_from_section"] == 0 for f in findings)
    assert {f["section_index"] for f in findings} == {1, 2}


@pytest.mark.parametrize("damage", ["drop", "rewrite", "source", "indent", "unaffected"])
def test_scoped_mixed_bullet_preservation_is_enforced(damage):
    unique = "Account support is available by email."
    original = draft(REPEAT + " " + unique)
    original["sections"][1]["bullets"][1]["source_position"] = "start"
    findings = same_edition_repeats(original)
    repaired = copy.deepcopy(original)
    mixed = repaired["sections"][1]["bullets"][1]
    mixed["text"] = unique
    mixed["source_position"] = "end"  # Same source, complete retained sentence.
    assert not scoped_repeat_repair_issues(original, repaired, findings)
    if damage == "drop":
        repaired["sections"][1]["bullets"].pop()
    elif damage == "rewrite":
        mixed["text"] = "Contact support online."
    elif damage == "source":
        mixed["source_ids"] = ["t1_other"]
    elif damage == "indent":
        mixed["indent"] = 0
    else:
        repaired["sections"][1]["bullets"][0]["text"] = "A different supported detail."
    assert scoped_repeat_repair_issues(original, repaired, findings)


@pytest.mark.parametrize("cross_parts,repair_mode", [
    (False, "success"), (False, "remove_all"), (True, "success"),
    (True, "unchanged"), (True, "renamed"), (True, "bad_source"),
    (False, "drop_unique"), (True, "drop_unique"), (True, "rewrite_unique"), (True, "missing_title"),
])
def test_pipeline_repairs_only_affected_sections_with_bounded_calls(tmp_path, monkeypatch, cross_parts, repair_mode):
    monkeypatch.setattr(verified, "identify_candidates", lambda *a, **kw: [])
    monkeypatch.setattr(verified, "MAX_WRITING_NOTES", 1 if cross_parts else 8)
    rows = [{"id": "t1_shared", "author": "fixture", "body": OFFER + " " + MATCH,
             "post_permalink": "https://reddit.com/r/churning/comments/fixture/topic/"}]
    notes = [{"topic": "Bilt holiday shopping", "text": OFFER, "source_ids": ["t1_shared"]},
             {"topic": "JetBlue match", "text": MATCH, "source_ids": ["t1_shared"]}]
    calls, limits = [], []
    original = draft()
    if repair_mode in {"drop_unique", "rewrite_unique"}:
        unique = "JetBlue account support is available by email."
        rows[0]["body"] += " " + unique
        notes[1]["text"] += " " + unique
        original["sections"][1]["bullets"].append(bullet(unique))
    fixed = copy.deepcopy(original)
    fixed["sections"][1]["bullets"].pop(1)

    def invoke(prompt, **kwargs):
        stage = response_schema(prompt)["required"][0]
        calls.append(stage)
        limits.append(kwargs["timeout"])
        if stage == "stories":
            return json.dumps({"stories": [
                {"topic": "Bilt holiday shopping", "note_ids": ["n0"], "status": "new", "reason": "New shopping offer."},
                {"topic": "JetBlue match", "note_ids": ["n1"], "status": "new", "reason": "Separate match."}]})
        if "SCOPED SAME-EDITION REPAIR:" in prompt:
            assert ("shared-source numeric clause repeats" in prompt or repair_mode in {
                "renamed", "bad_source", "remove_all", "drop_unique", "rewrite_unique", "missing_title"})
            result = {"sections": [copy.deepcopy(fixed["sections"][1])]}
            if repair_mode == "unchanged":
                result = {"sections": [copy.deepcopy(original["sections"][1])]}
            elif repair_mode == "renamed":
                result["sections"][0]["title"] = "Unassigned title"
            elif repair_mode == "bad_source":
                result["sections"][0]["bullets"][0]["source_ids"] = ["t1_invented"]
            elif repair_mode == "remove_all":
                result = copy.deepcopy(fixed)
                result["sections"][0]["bullets"] = [bullet(MATCH)]
            elif repair_mode == "drop_unique":
                result["sections"][0]["bullets"].pop()
            elif repair_mode == "rewrite_unique":
                result["sections"][0]["bullets"][-1]["text"] = "Account support is available."
            elif repair_mode == "missing_title":
                result["sections"][0].pop("title")
            return json.dumps(result)
        if "REPAIR THIS DRAFT:" in prompt:
            if repair_mode == "remove_all":
                removed = copy.deepcopy(fixed)
                removed["sections"][0]["bullets"] = [bullet(MATCH)]
                return json.dumps(removed)
            return json.dumps(fixed)
        if cross_parts:
            assigned, _ = json.JSONDecoder().raw_decode(prompt.split("NOTES (check against originals; notes may contain errors):\n", 1)[1])
            index = 0 if assigned[0]["note_id"] == "n0" else 1
            return json.dumps({"sections": [copy.deepcopy(original["sections"][index])]})
        return json.dumps(original)

    options = dict(artifact_dir=tmp_path, recovered_notes=notes,
                   editorial_profile=resolve_editorial({"preset": "churning"}),
                   recent_editions=[{"date": "2026-01-01", "items": ["An unrelated previous story."]}])
    if repair_mode == "success":
        result = verified.summarize_verified(rows, None, invoke, **options)
        assert result.count("900") == 1 and "8,000" in result
        saved = json.loads((tmp_path/"validated-digest.json").read_text(encoding="utf-8"))
        assert saved["sections"][0] == original["sections"][0]
        assert saved["sections"][1] == fixed["sections"][1]
        report = json.loads((tmp_path/"novelty-review.json").read_text(encoding="utf-8"))
        assert report["remaining_same_edition_clauses"] == 0
        assert report["same_edition_repair_checks"] == 1
        assert len(calls) == (4 if cross_parts else 3)
    else:
        with pytest.raises(RuntimeError, match="failed validation"):
            verified.summarize_verified(rows, None, invoke, **options)
        assert len(calls) == (5 if cross_parts else 4)  # at most two correction attempts
        assert not (tmp_path/"validated-digest.json").exists()
        report = json.loads((tmp_path/"novelty-review.json").read_text(encoding="utf-8"))
        assert report["same_edition_repair_checks"] == 2
    assert all(limit <= 240 for limit in limits)


def test_scoped_repair_cannot_extend_the_shared_deadline(tmp_path, monkeypatch):
    now, calls = [0], []
    monkeypatch.setattr(verified.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(verified, "identify_candidates", lambda *a, **kw: [])
    rows = [{"id": "t1_shared", "author": "fixture", "body": OFFER + " " + MATCH,
             "post_permalink": "https://reddit.com/r/churning/comments/fixture/topic/"}]
    notes = [{"topic": "Bilt holiday shopping", "text": OFFER, "source_ids": ["t1_shared"]},
             {"topic": "JetBlue match", "text": MATCH, "source_ids": ["t1_shared"]}]

    def invoke(prompt, **kwargs):
        stage = response_schema(prompt)["required"][0]
        calls.append(stage)
        now[0] += 10
        if stage == "stories":
            return json.dumps({"stories": [
                {"topic": "Bilt holiday shopping", "note_ids": ["n0"], "status": "new", "reason": "New offer."},
                {"topic": "JetBlue match", "note_ids": ["n1"], "status": "new", "reason": "Separate match."}]})
        return json.dumps(draft())

    def exhausted_before_repair(document):
        # All collection, generation and validation share this absolute clock.
        now[0] = 1300
        return same_edition_repeats(document)

    monkeypatch.setattr(verified, "same_edition_repeats", exhausted_before_repair)
    with pytest.raises(RuntimeError, match="writing time budget exhausted"):
        verified.summarize_verified(rows, None, invoke, artifact_dir=tmp_path, recovered_notes=notes,
            editorial_profile=resolve_editorial({"preset": "churning"}),
            recent_editions=[{"date": "2026-01-01", "items": ["Unrelated history."]}], job_deadline=1300)
    assert calls == ["stories", "sections"]
    assert not (tmp_path/"validated-digest.json").exists()
