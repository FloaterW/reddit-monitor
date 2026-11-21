"""Conservative final checks for repeated background, with no silent deletion."""

import copy
import json

import pytest

import verified_digest as verified
from digest_schema import response_schema
from editorial_novelty import novelty_issues, repeat_fact_context, repeated_background
from editorial_profiles import resolve_editorial

OLD = "The Atmos Business offer is 90K/$6K/90d with a $195 annual fee."
UPDATE = "A user reports an Atmos Business denial at 2/12 personal and 2/12 business."


def context(text=OLD, *, status="repeat", topic="Atmos Business welcome offer", history=None):
    note = {"note_id": "n0", "topic": topic, "text": text, "source_ids": ["t1_old"]}
    plan = {"stories": [{"topic": topic, "note_ids": ["n0"], "status": status}]}
    supplied = [{"date": "2026-11-18", "items": [text]}] if history is None else history
    return repeat_fact_context(plan, {"notes": [note]}, supplied)


def document(text, title="Atmos Business denial", source="t1_parent"):
    return {"sections": [{"title": title, "bullets": [{"text": text, "source_ids": [source]}]}]}


def test_repeat_requires_explicit_planner_decision_and_actual_full_history():
    assert context()
    assert not context(status="low_value")
    assert not context(status="update")
    assert not context(history=[])
    assert not context(history=[{"date": "2026-11-18", "items": [], "topic_index": [OLD]}])
    assert not context(history=[{"date": "2026-11-18", "items": ["Unrelated information."]}])


def test_parent_evidence_cannot_reintroduce_prior_numeric_fact_as_new_news():
    draft = document(OLD)
    before = copy.deepcopy(draft)
    findings = repeated_background(draft, context())
    assert len(findings) == 1
    assert findings[0]["note_id"] == "n0" and findings[0]["previous_edition"] == "2026-11-18"
    assert "Remove this repeated clause, not the current update" in novelty_issues(draft, context())[0]
    assert draft == before  # Diagnostics never remove a source, clause or bullet.


@pytest.mark.parametrize("text", [
    "The Atmos Business offer is 100K/$6K/90d with a $195 annual fee.",
    "An applicant was approved for Atmos Business at 90K/$6K/90d with a $195 annual fee.",
    "An applicant was denied for Atmos Business at 90K/$6K/90d with a $195 annual fee.",
    "The Atmos Business offer at 90K/$6K/90d with a $195 annual fee expires tomorrow.",
    "An applicant disputes the Atmos Business offer at 90K/$6K/90d with a $195 annual fee.",
    "The Atmos Business offer at 90K/$6K/90d with a $195 annual fee has returned.",
    "The Atmos Business offer has 6K/$90K/90d terms with a $195 annual fee.",
])
def test_changed_terms_outcomes_contradictions_and_deadlines_are_retained(text):
    assert not repeated_background(document(text), context())


def test_same_number_set_different_product_does_not_trigger():
    old = "The Chase Ink Preferred welcome offer gives 100K points after $8K spend within 90 days."
    facts = context(old, topic="Chase Ink Preferred offer")
    other = "The Chase Sapphire Reserve welcome offer gives 100K points after $8K spend within 90 days."
    assert not repeated_background(document(other, "Chase Sapphire Reserve"), facts)


@pytest.mark.parametrize("old_card,new_card", [
    ("Chase Ink Preferred", "Chase Ink Cash"),
    ("Chase Ink Preferred", "Chase Ink Unlimited"),
    ("Chase Ink Cash", "Chase Ink Unlimited"),
    ("Chase Sapphire Reserve for Business", "Chase Sapphire Reserve"),
    ("Chase Sapphire Reserve", "Chase Sapphire Reserve for Business"),
    ("Amex Business Gold", "Amex Personal Gold"),
    ("Amex Personal Gold", "Amex Business Gold"),
])
def test_shared_card_family_does_not_block_new_product_with_identical_terms(old_card, new_card):
    old = f"The {old_card} welcome offer gives 100K points after $8K spend within 90 days."
    new = f"The {new_card} welcome offer gives 100K points after $8K spend within 90 days."
    facts = context(old, topic=old_card + " offer")
    assert facts  # This is still a recognised old fact for its own product.
    assert not repeated_background(document(new, new_card), facts)
    # A combined section heading must not pool the two products' identities.
    assert not repeated_background(document(new, old_card + " and " + new_card), facts)


def test_changed_scope_cannot_become_repeat_evidence_from_matching_old_numbers():
    previous = "The Atmos Business targeted offer is 90K/$6K/90d with a $195 annual fee."
    current = "The Atmos Business public offer is 90K/$6K/90d with a $195 annual fee."
    assert not context(current, history=[{"date": "2026-11-18", "items": [previous]}])


def test_ambiguous_short_overlap_is_not_a_blocking_decision():
    assert not repeated_background(document("Annual fee $195; spend $6K."), context())
    assert not context("The Atmos fee is $195.")
    assert not context("The offer is 90K/$6K/90d and a $195 annual fee.", topic="Welcome offer")


def test_mixed_bullet_flags_only_old_clause_and_preserves_current_update():
    draft = document(OLD + " " + UPDATE)
    issues = repeated_background(draft, context())
    assert len(issues) == 1 and issues[0]["clause"] == OLD
    assert "denial" not in issues[0]["clause"]


@pytest.mark.parametrize("qualifier", [
    "now public", "formerly targeted and now public", "newly referral-only",
    "available online for the first time", "available to first-time cardholders only",
    "in-branch only", "targeted to existing customers", "restricted to incorporated businesses",
    "not available to sole proprietors", "for eligible new customers",
])
def test_unchanged_quantities_do_not_hide_qualitative_offer_changes(qualifier):
    text = f"The Atmos Business offer is 90K/$6K/90d with a $195 annual fee, {qualifier}."
    assert not repeated_background(document(text), context())
    assert not repeated_background(document(OLD + f" The offer is {qualifier}."), context())


def test_scope_change_in_heading_or_separate_selected_note_preserves_terms():
    assert not repeated_background(document(OLD, "Atmos Business offer now public"), context())
    notes = [{"note_id": "n0", "topic": "Atmos Business offer", "text": OLD, "source_ids": ["t1_old"]},
             {"note_id": "n1", "topic": "Atmos Business offer availability", "text": "The Atmos Business offer is now public.",
              "source_ids": ["t1_current"]}]
    plan = {"stories": [{"status": "repeat", "note_ids": ["n0"]},
                        {"status": "update", "note_ids": ["n1"]}]}
    facts = repeat_fact_context(plan, {"notes": notes}, [{"items": [OLD]}])
    assert not repeated_background(document(OLD, source="t1_current"), facts)
    assert repeated_background(document(OLD, source="t1_parent"), facts)


def test_separate_selected_offer_with_different_terms_does_not_exempt_old_background():
    notes = [{"note_id": "n0", "topic": "Atmos Business offer", "text": OLD, "source_ids": ["t1_old"]},
             {"note_id": "n1", "topic": "Atmos Business offer availability", "text": "Atmos Business is now public at 100K/$8K/90d.",
              "source_ids": ["t1_current"]}]
    plan = {"stories": [{"status": "repeat", "note_ids": ["n0"]}, {"status": "update", "note_ids": ["n1"]}]}
    facts = repeat_fact_context(plan, {"notes": notes}, [{"items": [OLD]}])
    assert repeated_background(document(OLD, source="t1_current"), facts)


def test_earning_and_spend_tiers_match_with_formatting_variants():
    old = ("Atmos Business earns 10x on qualifying Alaska and Hawaiian flights through Atmos for Business and 2x on dining. "
           "The $100k and $200k spend tiers each unlock a 50% flight discount and 10,000 Status Points.")
    facts = context(old)
    draft = document("The **$100K** and **$200K** tiers offer a **50% flight discount** and **10,000 Status Points**.")
    assert repeated_background(draft, facts)


def test_context_and_correction_payloads_are_bounded():
    notes = [{"note_id": f"n{i}", "topic": "Atmos Business offer",
              "text": f"Atmos Business offers {90+i}K points for $6K spend within 90 days with a $195 annual fee.",
              "source_ids": [f"t1_{i}"]} for i in range(100)]
    plan = {"stories": [{"status": "repeat", "note_ids": [n["note_id"] for n in notes]}]}
    facts = repeat_fact_context(plan, {"notes": notes}, [{"items": [n["text"] for n in notes]}])
    assert len(facts) == 80
    draft = {"sections": [{"title": "Atmos", "bullets": [{"text": OLD, "source_ids": ["t1_a"]} for _ in range(30)]}]}
    assert len(novelty_issues(draft, context())) == 12


def test_pipeline_keeps_new_ink_product_when_other_ink_product_is_a_repeat(tmp_path, monkeypatch):
    monkeypatch.setattr(verified, "identify_candidates", lambda *args, **kwargs: [])
    old = "The Chase Ink Preferred welcome offer gives 100K points after $8K spend within 90 days."
    new = "A user reports the Chase Ink Cash welcome offer gives 100K points after $8K spend within 90 days; terms are unverified."
    rows = [{"id": "t1_old", "author": "first", "body": old, "context_only": True,
             "post_permalink": "https://reddit.com/r/churning/comments/p/topic/"},
            {"id": "t1_current", "author": "second", "body": new,
             "post_permalink": "https://reddit.com/r/churning/comments/p/topic/"}]
    notes = [{"topic": "Chase Ink Preferred offer", "text": old, "source_ids": ["t1_old"]},
             {"topic": "Chase Ink Cash offer", "text": new, "source_ids": ["t1_current"]}]
    stages = []

    def invoke(prompt, **kwargs):
        stage = response_schema(prompt)["required"][0]
        stages.append(stage)
        if stage == "stories":
            return json.dumps({"stories": [
                {"topic": "Ink Preferred offer", "note_ids": ["n0"], "status": "repeat", "reason": "Already delivered."},
                {"topic": "Ink Cash offer", "note_ids": ["n1"], "status": "new", "reason": "Different card."}]})
        return json.dumps(document(new, "Chase Ink Cash offer", source="t1_current"))

    result = verified.summarize_verified(rows, None, invoke, artifact_dir=tmp_path, recovered_notes=notes,
        editorial_profile=resolve_editorial({"preset": "churning"}),
        recent_editions=[{"date": "2026-11-18", "items": [old]}])
    assert "Ink Cash" in result and "100K" in result
    assert stages == ["stories", "sections"]


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_pipeline_repairs_repetition_with_existing_bounded_calls_and_keeps_current_evidence(tmp_path, monkeypatch, repair_succeeds):
    monkeypatch.setattr(verified, "identify_candidates", lambda *args, **kwargs: [])
    rows = [{"id": "t1_old", "author": "first", "body": OLD, "context_only": True,
             "post_permalink": "https://reddit.com/r/churning/comments/p/topic/"},
            {"id": "t1_current", "author": "second", "body": UPDATE,
             "post_permalink": "https://reddit.com/r/churning/comments/p/topic/"}]
    notes = [{"topic": "Atmos Business offer", "text": OLD, "source_ids": ["t1_old"]},
             {"topic": "Atmos Business denial", "text": UPDATE, "source_ids": ["t1_current"]}]
    stages = []

    def invoke(prompt, **kwargs):
        stage = response_schema(prompt)["required"][0]
        stages.append(stage)
        if stage == "stories":
            return json.dumps({"stories": [
                {"topic": "Atmos offer", "note_ids": ["n0"], "status": "repeat", "reason": "Already delivered."},
                {"topic": "Atmos denial", "note_ids": ["n1"], "status": "update", "reason": "New denial."}]})
        if stages.count("sections") > 1:
            assert "unchanged background from planner-repeat note n0" in prompt
        draft = document(UPDATE, source="t1_current")
        if stages.count("sections") == 1 or not repair_succeeds:
            draft["sections"][0]["bullets"].insert(0, {"text": OLD, "source_ids": ["t1_old"]})
        return json.dumps(draft)

    args = dict(artifact_dir=tmp_path, recovered_notes=notes,
                editorial_profile=resolve_editorial({"preset": "churning"}),
                recent_editions=[{"date": "2026-11-18", "items": [OLD]}])
    if repair_succeeds:
        result = verified.summarize_verified(rows, None, invoke, **args)
        assert UPDATE in result and "90K" not in result
        report = json.loads((tmp_path/"novelty-review.json").read_text(encoding="utf-8"))
        assert report["remaining_repeated_clauses"] == 0 and report["repair_checks_triggered"] == 1
        assert stages == ["stories", "sections", "sections"]
    else:
        with pytest.raises(RuntimeError, match="failed validation"):
            verified.summarize_verified(rows, None, invoke, **args)
        assert stages == ["stories", "sections", "sections", "sections"]
        assert not (tmp_path/"validated-digest.json").exists()
        failed = json.loads((tmp_path/"novelty-review.json").read_text(encoding="utf-8"))
        assert not failed["completed"] and len(failed["repair_attempts"]) == 3
