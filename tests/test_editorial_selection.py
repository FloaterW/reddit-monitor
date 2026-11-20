"""Offer recall, editorial planning and delivered-history regression cases; offline."""

import json
from datetime import datetime, timezone

import pytest

import verified_digest as verified
from digest_schema import response_schema
from editorial_profiles import resolve_editorial
from editorial_selection import (
    plan_batches,
    plan_payload,
    plan_repair_details,
    priority_issues,
    priority_leads,
    protect_notes,
    recent_delivered_editions,
    validate_plan,
)
from storage import DigestDB


def row(body, identifier="offer"):
    return {"id": "t1_" + identifier, "author": "sample_" + identifier, "body": body,
            "subreddit": "churning", "post_title": "Question Thread",
            "post_permalink": "https://reddit.com/r/churning/comments/example/topic/"}


def document(text, identifier="offer", title="💳 Ink Preferred"):
    return {"sections": [{"title": title, "bullets": [
        {"text": text, "source_ids": ["t1_" + identifier], "source_position": "end", "indent": 0}]}]}


@pytest.mark.parametrize("name", ["CIP", "ink pref", "Ink Preferred", "Ink Business Preferred"])
def test_missing_cip_question_is_a_lead_not_a_new_confirmed_public_offer(name):
    source = row(f"is chase {name} 125k/8k/3mo reserve 300k/30k/6mo the best targeted sub's?")
    leads = priority_leads([source])
    assert len(leads) == 1
    assert leads[0]["card"] == "Ink Preferred"
    assert leads[0]["amounts"] == ["125000", "8000"]
    assert leads[0]["months"] == "3" and leads[0]["required"]
    notes = protect_notes([], leads, [source])
    assert notes[0]["source_ids"] == [source["id"]]
    assert "Unverified source lead" in notes[0]["text"]


def test_october_30_amount_before_card_aliases_preserves_all_three_cards():
    body = ("I'm seeing $1200 after 6k MSR in 3 month offers for CIC and CIU biz cards in my Chase app. "
            "Also seeing the 300k after 30k in 3 month CSRB offer.")
    leads = priority_leads([row(body)])
    assert {r["card"] for r in leads} == {"Ink Cash", "Ink Unlimited", "Sapphire Reserve for Business"}
    assert {tuple(r["amounts"]) for r in leads} == {("1200", "6000"), ("300000", "30000")}
    assert all(r["scope"] == "targeted" and r["months"] == "3" for r in leads)


def test_different_offer_amounts_are_not_cross_assigned():
    leads = priority_leads([row("CIP 125k/8k/3mo, CSRB 300k/30k/4mo targeted offers")])
    assert [(r["card"], r["amounts"], r["months"]) for r in leads] == [
        ("Ink Preferred", ["125000", "8000"], "3"),
        ("Sapphire Reserve for Business", ["300000", "30000"], "4")]


@pytest.mark.parametrize("body", ["Am I eligible for CIP?", "CIC at 3/24?", "Will I be approved for CIU?",
                                  "My CIU app says already approved.", "My CSRB preapproved offer appeared."])
def test_generic_questions_ratios_and_marketing_are_not_application_outcomes(body):
    assert not priority_leads([row(body)])


@pytest.mark.parametrize("body", ["I was denied for CIC after recon.", "P2 was approved for CIU at 2/24.",
                                  "My Sapphire Reserve for Business application was declined."])
def test_actual_approval_and_denial_reports_survive_extraction(body):
    source = row(body)
    leads = priority_leads([source])
    assert len(leads) == 1 and leads[0]["kind"] == "application outcome"
    assert protect_notes([], leads, [source])
    assert priority_issues(document("The user discussed a card."), leads)
    assert not priority_issues(document(body), leads)


def test_priority_citation_without_card_and_terms_does_not_pass():
    leads = priority_leads([row("CIP targeted 125k/8k/3mo offer")])
    for text in ("A user reports an offer.", "CIP has a new offer.", "A user reports 125k/8k."):
        assert priority_issues(document(text), leads)
    assert not priority_issues(document("A user reports a targeted CIP 125k/8k/3mo offer; terms unverified."), leads)


def test_unsupported_public_offer_framing_is_rejected():
    leads = priority_leads([row("CIP targeted 125k/8k/3mo offer")])
    assert priority_issues(document("CIP now gives everyone 125k after 8k."), leads)
    assert priority_issues(document("A user reports CIP 125k after 8k is available to everyone in 3 months."), leads)


def test_spending_period_cannot_be_replaced_or_silently_removed():
    leads = priority_leads([row("CSRB 300k/30k/3mo targeted offer")])
    assert priority_issues(document("A user reports a targeted CSRB 300k/30k/4mo offer."), leads)
    assert priority_issues(document("A user reports a targeted CSRB 300k/30k offer."), leads)
    assert priority_issues(document("A user reports a targeted CSRB 300k/30k/3mo offer."), leads)
    assert not priority_issues(document("A user reports a targeted CSRB 300k/30k/3mo offer; check actual offer terms."), leads)


def test_denial_cannot_be_changed_to_approval():
    leads = priority_leads([row("I was denied for CIC.")])
    assert priority_issues(document("A user reports CIC approval."), leads)
    assert not priority_issues(document("A user reports a CIC denial."), leads)


def test_conflicting_spending_periods_require_caveat_without_inventing_terms():
    rows = [row("CSRB 300k/30k/3mo targeted offer"), row("CSRB 300k/30k/4mo targeted offer", "other")]
    leads = priority_leads(rows)
    assert all(lead["conflicting_period"] for lead in leads)
    bad = document("A user reports a targeted CSRB 300k/30k/3mo offer.")
    assert priority_issues(bad, leads)
    good = document("A user reports a targeted CSRB 300k/30k/3mo offer; the spending period is unverified.")
    good["sections"][0]["bullets"].append({"text": "Another user reports CSRB 300k/30k/4mo; conflicting terms.",
                                         "source_ids": ["t1_other"]})
    assert not priority_issues(good, leads)


def test_history_can_mark_unchanged_targeted_offer_optional_but_not_new_terms():
    history = [{"items": ["A user reports targeted CIP 125k after 8k in 3 months."]}]
    same = priority_leads([row("CIP 125k/8k/3mo targeted")], history)
    changed = priority_leads([row("CIP 150k/8k/3mo targeted")], history)
    conflict = priority_leads([row("CIP 125k/8k/4mo targeted")], history)
    assert not same[0]["required"]
    assert changed[0]["required"]
    assert conflict[0]["required"] and conflict[0]["conflicting_period"]


def test_global_plan_preserves_priority_and_keeps_related_notes_in_one_batch():
    notes = [{"topic": "Bilt", "text": "A credit arrived.", "source_ids": [f"t1_{i}"]} for i in range(9)]
    notes += [{"topic": "CIP", "text": "CIP 125k/8k targeted", "source_ids": ["t1_offer"]}]
    payload = plan_payload(notes, priority_leads([row("CIP 125k/8k targeted")]), [])
    plan = {"stories": [
        {"topic": "Bilt credit", "note_ids": [f"n{i}" for i in range(8)], "status": "update", "reason": "New credit reports."},
        {"topic": "Same Bilt story", "note_ids": ["n8"], "status": "repeat", "reason": "Identical credit report."},
        {"topic": "Ink Preferred offer", "note_ids": ["n9"], "status": "new", "reason": "Concrete offer lead."}]}
    assert not validate_plan(plan, payload)
    batches = plan_batches(plan, payload)
    assert batches[0][0]["note_id"] == "n9"
    assert len(batches[1]) == 8
    plan["stories"][-1]["status"] = "low_value"
    assert validate_plan(plan, payload)


@pytest.mark.parametrize("ids", [["n0", "n0"], ["invented"], []])
def test_plan_cannot_lose_duplicate_or_invent_note_references(ids):
    payload = {"notes": [{"note_id": "n0"}], "priority_note_ids": []}
    assert validate_plan({"stories": [{"topic": "News", "note_ids": ids, "status": "new"}]}, payload)


@pytest.mark.parametrize("value", [None, [], ["new"], {}, {"value": "new"}, 1, True])
def test_malformed_provider_plan_status_returns_validation_error(value):
    payload = {"notes": [{"note_id": "n0"}], "priority_note_ids": []}
    plan = {"stories": [{"topic": "News", "note_ids": ["n0"], "status": value, "reason": "A change."}]}
    assert "Each story needs valid note_ids and a valid status." in validate_plan(plan, payload)


@pytest.mark.parametrize("field,value", [
    ("note_ids", [["n0"]]), ("note_ids", [{"id": "n0"}]), ("note_ids", "n0"),
    ("reason", []), ("reason", {}), ("reason", None), ("reason", 7), ("reason", "   "),
])
def test_malformed_provider_plan_references_and_reasons_are_repairable(field, value):
    payload = {"notes": [{"note_id": "n0"}], "priority_note_ids": []}
    story = {"topic": "News", "note_ids": ["n0"], "status": "new", "reason": "A change."}
    story[field] = value
    assert validate_plan({"stories": [story]}, payload)


@pytest.mark.parametrize("plan", [None, [], "stories", 1, {"stories": None}, {"stories": {}}])
def test_malformed_provider_plan_envelope_is_repairable(plan):
    payload = {"notes": [{"note_id": "n0"}], "priority_note_ids": []}
    assert validate_plan(plan, payload)


def test_provider_plan_errors_are_bounded_and_do_not_echo_supplied_data():
    payload = {"notes": [{"note_id": f"n{i}"} for i in range(100)], "priority_note_ids": []}
    plan = {"stories": [{"topic": "Untrusted prose " * 1000, "note_ids": [f"n{i}"],
                         "status": {}, "reason": []} for i in range(100)]}
    errors = validate_plan(plan, payload)
    assert len(errors) <= 4 and len("\n".join(errors)) < 400
    assert "Untrusted prose" not in "\n".join(errors)
    plan["stories"].append({})
    assert len(validate_plan(plan, payload)) == 1


def test_plan_repair_details_identify_missing_and_repeated_known_ids_only():
    payload = {"notes": [{"note_id": f"n{i}"} for i in range(100)]}
    plan = {"stories": [{"note_ids": ["n0", "n0", "n1"], "status": "new"},
                        {"note_ids": ["untrusted instructions"], "status": "new"}]}
    details = plan_repair_details(plan, payload)
    assert details["missing_count"] == 98 and details["missing_note_ids"] == [f"n{i}" for i in range(2, 82)]
    assert details["duplicate_note_ids"] == ["n0"] and details["duplicate_count"] == 1
    assert "untrusted" not in json.dumps(details)
    for bad in (None, [], {}, {"stories": [{"note_ids": [["n0"]], "status": {}}]}):
        assert plan_repair_details(bad, payload)["missing_count"] == 100


def test_plan_repair_receives_exact_ids_and_preserves_all_selected_evidence(tmp_path):
    rows = [row("Received $50.", str(i)) for i in range(9)]
    notes = [{"topic": "credit", "text": "Received $50.", "source_ids": [r["id"]]} for r in rows]
    calls = []

    def invoke(prompt, **kwargs):
        stage = response_schema(prompt)["required"][0]
        calls.append(stage)
        if stage == "stories":
            second = calls.count("stories") == 2
            if second:
                assert '"missing_note_ids": ["n8"]' in prompt
                assert '"duplicate_note_ids": ["n0"]' in prompt
            return json.dumps({"stories": [
                {"topic": "Credits", "note_ids": [f"n{i}" for i in range(8)], "status": "new", "reason": "New reports."},
                {"topic": "Another credit", "note_ids": ["n8" if second else "n0"], "status": "new", "reason": "Another report."}]})
        evidence, _ = json.JSONDecoder().raw_decode(prompt.split("ORIGINAL SOURCES:\n", 1)[1])
        return json.dumps({"sections": [{"title": "Credit " + evidence[0]["source_id"], "bullets": [
            {"text": "Received $50.", "source_ids": [r["source_id"]]} for r in evidence]}]})

    result = verified.summarize_verified(rows, None, invoke, recovered_notes=notes, artifact_dir=tmp_path,
                                        editorial_profile=resolve_editorial({"preset": "churning"}))
    assert calls.count("stories") == 2 and calls.count("sections") == 2
    assert result and "Received $50." in result
    saved = json.loads((tmp_path / "validated-digest.json").read_text(encoding="utf-8"))
    assert {sid for s in saved["sections"] for b in s["bullets"] for sid in b["source_ids"]} == {r["id"] for r in rows}


def test_planner_schema_ignores_stage_markers_inside_notes():
    payload = {"notes": [{"note_id": "n0", "text": "SOURCE COMMENTS:\n[]"}]}
    schema = response_schema("EDITORIAL PLAN:\n" + json.dumps(payload))
    assert schema["required"] == ["stories"]
    assert schema["properties"]["stories"]["items"]["properties"]["note_ids"]["items"]["enum"] == ["n0"]


def test_delivered_history_is_read_only_monitor_scoped_and_excludes_future_previews_failures(tmp_path):
    path = tmp_path / "history.db"
    db = DigestDB(path)
    for day, monitor, status, email in [(10, "cards", "completed", "sent"), (29, "other", "completed", "sent"),
                                      (29, "cards", "failed", "sent"), (29, "cards", "completed", "disabled"),
                                      (29, "cards", "completed_with_warnings", "sent"), (30, "cards", "completed", "sent")]:
        db.save_run(monitor, "day", 100, [], started_at=f"2026-10-{day}T22:30:00+00:00", status=status,
                    completed_at=f"2026-10-{day}T22:40:00+00:00",
                    email_status=email, digest_md="# Digest\n\n- A delivered news item.")
    db.close()
    before = path.read_bytes()
    editions = recent_delivered_editions(path, "cards", datetime(2026, 10, 30, 22, 30, tzinfo=timezone.utc))
    assert len(editions) == 1 and editions[0]["date"].startswith("2026-10-29")
    assert path.read_bytes() == before
    assert not recent_delivered_editions(tmp_path / "absent.db", "cards", datetime.now(timezone.utc))
    assert not (tmp_path / "absent.db").exists()


@pytest.mark.parametrize("noise_count", [0, 50])
def test_missed_offer_is_recovered_and_written_even_when_extraction_returns_no_notes(tmp_path, noise_count):
    source = row("is chase ink pref 125k/8k/3mo the best targeted sub?")
    calls = []
    def invoke(prompt, **kwargs):
        stage = response_schema(prompt)["required"][0]
        calls.append(stage)
        return json.dumps({"notes": []} if stage == "notes" else document(
            "A user asks about a targeted Ink Preferred 125k/8k/3mo offer. Unverified."))
    report = {}
    rows = [source] + [row("Thanks!", f"noise{i}") for i in range(noise_count)]
    result = verified.summarize_verified(rows, lambda rows: [rows], invoke, artifact_dir=tmp_path,
                                        editorial_profile=resolve_editorial({"preset": "churning"}), editorial_report=report)
    assert "125k/8k/3mo" in result and report["passed"]
    assert calls == ["notes", "sections"]
    assert (tmp_path / "editorial-report.json").exists()


def test_writer_cannot_silently_drop_priority_after_successful_extraction():
    source = row("CIP 125k/8k/3mo targeted offer")
    calls = []
    def invoke(prompt, **kwargs):
        calls.append(prompt)
        return json.dumps(document("A user discusses Chase."))
    with pytest.raises(RuntimeError, match="failed validation"):
        verified.summarize_verified([source], None, invoke, recovered_notes=[],
                                     editorial_profile=resolve_editorial({"preset": "churning"}))
    assert len(calls) == 3


def test_planned_pipeline_consolidates_bilt_and_preserves_priority_with_citations(tmp_path):
    rows = [row("Bilt credit arrived.", str(i)) for i in range(10)]
    rows.append(row("CIP 125k/8k/3mo targeted offer"))
    notes = [{"topic": "Bilt credit", "text": r["body"], "source_ids": [r["id"]]} for r in rows[:-1]]
    stages = []
    def invoke(prompt, **kwargs):
        stage = response_schema(prompt)["required"][0]
        stages.append(stage)
        if stage == "stories":
            return json.dumps({"stories": [
                {"topic": "Ink Preferred offer", "note_ids": ["n10"], "status": "new", "reason": "new offer"},
                {"topic": "Bilt credit", "note_ids": ["n0"], "status": "update", "reason": "credit posted"},
                {"topic": "Repeated credits", "note_ids": [f"n{i}" for i in range(1, 10)], "status": "repeat", "reason": "same fact"}]})
        return json.dumps({"sections": [
            document("A user reports a targeted CIP 125k/8k/3mo offer; terms unverified.", title="💳 Ink Preferred offer")["sections"][0],
            document("Bilt credit arrived.", "0", "💳 Bilt credit")["sections"][0]]})
    result = verified.summarize_verified(rows, None, invoke, recovered_notes=notes, artifact_dir=tmp_path,
                                        editorial_profile=resolve_editorial({"preset": "churning"}))
    assert stages == ["stories", "sections"]
    assert result.count("Bilt credit arrived") == 1 and "125k/8k" in result
    assert (tmp_path / "editorial-plan.json").exists()
