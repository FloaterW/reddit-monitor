"""Bounded editorial review and narrow, source-bound material fact checks."""

import json

import pytest

from editorial_profiles import resolve_editorial
from editorial_retention import identify_candidates, retention_issues, selected_note_requirements


def source(sid, body, **extra):
    return {"id": sid, "body": body, **extra}


def note(text, *sids, **extra):
    return {"text": text, "source_ids": list(sids), **extra}


def document(*items):
    return {"sections": [{"title": "Card reports", "bullets": [
        {"text": text, "source_ids": list(sids)} for text, sids in items
    ]}]}


CERTIFICATE = [source("initial", "I received a $125 flight certificate and a companion certificate."),
               source("clarification", "It's a 20k spend for the flight credit. The companion pass was an annual thing every renewal.")]
CERTIFICATE_NOTES = [note("A flight credit and companion certificate were received; the flight credit requires 20k spend.", "initial", "clarification")]
PC_BODY = ("Mine came due in September and had no PC options either. "
           "I converted a Citi AA not through Barclay's a few months earlier "
           "to an AT&T no annual fee via chat with no issues.")
REWARDS_BODY = ("I was part of the transition and my Discover IT cashback was not transferred to COVX miles. "
                "I did not look into this further. I have both miles and rewards cash at the same time.")


def test_candidates_find_distinct_operational_facts_not_just_offer_amounts():
    rows = [source("spend", CERTIFICATE[1]["body"]), source("pc", PC_BODY),
            source("rewards", REWARDS_BODY),
            source("account", "My bank suspended external transfers after its microdeposits could not be withdrawn."),
            source("old", "I saw an 80k bonus offer a couple of weeks ago."),
            source("chatter", "Thanks for explaining points and credit cards.")]
    candidates = identify_candidates(rows)
    assert [row["source_id"] for row in candidates] == ["pc", "rewards", "account", "spend", "old"]
    assert candidates[0]["categories"] == ["contrasting_product_change"]
    assert candidates[1]["categories"] == ["rewards_conversion"]
    assert json.loads(json.dumps(candidates)) == candidates


def test_candidate_bound_prioritizes_operational_dps_over_many_qualifications():
    rows = [source(str(i), "The welcome bonus requires $4000 spend.") for i in range(40)]
    rows.extend([source("late", REWARDS_BODY), source("late", REWARDS_BODY),
                 source("parent", PC_BODY, context_only=True)])
    candidates = identify_candidates(rows, limit=3)
    assert [row["source_id"] for row in candidates] == ["late", "0", "1"]
    assert identify_candidates(rows, limit=0) == []
    with pytest.raises(ValueError):
        identify_candidates(rows, limit=-1)


@pytest.mark.parametrize("body", [
    "The credit requires $100 annual fee.",
    "The free-night certificate requires 10 nights.",
    "I spent $2000 for groceries after receiving a flight credit.",
    "The flight credit does not require $20000 spend.",
    "The $20000 spend is not required for the flight credit.",
    "Does the flight credit require $20000 spend?",
    "Would $20000 spend for the flight credit be necessary?",
    "I'm not sure whether the flight credit requires $20000 spend.",
    "Maybe $20000 spend unlocks the flight credit.",
    "I did not convert my Citi AA to AT&T; there were no PC options.",
    "I did not expect cashback to transfer to miles, but it did.",
    "Can cashback be converted to miles?",
])
def test_no_mandatory_spend_or_success_from_fee_nights_incidental_spend_or_negation(body):
    notes = [note("The flight credit, free-night certificate, welcome bonus and product conversion were discussed.", "x")]
    assert selected_note_requirements(notes, [source("x", body)]) == []


def test_selected_flight_condition_cannot_disappear_with_its_clarification_source():
    draft = document(("Received a $125 flight certificate and a companion certificate.", ["initial"]))
    issues = retention_issues(draft, CERTIFICATE_NOTES, CERTIFICATE)
    assert len(issues) == 1 and "20k" in issues[0] and "clarification" in issues[0]


@pytest.mark.parametrize("text", [
    "The flight credit requires **$20,000 spend**; the companion certificate was annual.",
    "Reports $20k in spending for the flight certificate.",
    "**Qualifying spend:** $20,000 spend was required.",
    "Spending $20k unlocks the flight credit.",
    "The flight credit is tied to $20,000 spending.",
])
def test_condition_accepts_equivalent_format_and_concise_nested_detail(text):
    draft = document(("Received a flight certificate.", ["initial"]), (text, ["clarification"]))
    assert retention_issues(draft, CERTIFICATE_NOTES, CERTIFICATE) == []


@pytest.mark.parametrize("text,sid", [
    ("The flight certificate requires $20k spend.", "other"),
    ("A companion certificate requires $20k spend.", "clarification"),
    ("The flight certificate is worth $20k.", "clarification"),
    ("The flight certificate does not require $20k spend.", "clarification"),
])
def test_same_amount_wrong_source_benefit_or_negation_does_not_satisfy_condition(text, sid):
    draft = document(("Received a flight certificate.", ["initial"]), (text, [sid]))
    assert retention_issues(draft, CERTIFICATE_NOTES, CERTIFICATE)


def test_omitting_conditioned_benefit_entirely_does_not_imply_unconditional_benefit():
    draft = document(("Received an annual companion certificate.", ["initial"]))
    assert retention_issues(draft, CERTIFICATE_NOTES, CERTIFICATE) == []


def test_separate_notes_in_same_planned_story_protect_qualification():
    notes = [note("Received a flight certificate.", "initial", editorial_story="certificates"),
             note("Clarifies the qualifying spend.", "clarification", editorial_story="certificates")]
    draft = document(("Received a flight certificate.", ["initial"]))
    assert retention_issues(draft, notes, CERTIFICATE)
    notes[0]["editorial_story"] = "unrelated"
    assert retention_issues(draft, notes, CERTIFICATE) == []


def test_contrasting_pc_requirement_detects_fact_already_lost_from_note():
    notes = [note("Another cardholder had no product conversion options.", "pc", "other")]
    rows = [source("pc", PC_BODY), source("other", "My converted card had no PC options.")]
    draft = document(("No product changes were available on converted cards.", ["other"]))
    requirements = selected_note_requirements(notes, rows)
    assert requirements[0]["destination_terms"] == ["AT&T"]
    assert requirements[0]["event_age"] == "a few months earlier"
    assert retention_issues(draft, notes, rows)


@pytest.mark.parametrize("text", [
    "Reports no PC options on the converted card, but earlier successfully converted a native Citi AA to AT&T.",
    "Previously converted a non-Barclays Citi AA to AT&T with no issues; the converted card had no options.",
])
def test_successful_native_product_contrast_paraphrases_preserved(text):
    notes, rows = [note("No PC options but an earlier conversion worked.", "pc")], [source("pc", PC_BODY)]
    assert retention_issues(document((text, ["pc"])), notes, rows) == []


@pytest.mark.parametrize("text", [
    "Previously converted the card to AT&T.",  # Product origin lost.
    "Successfully converted a native Citi AA to AT&T.",  # Historical timing lost.
    "Previously could not successfully convert a native Citi AA to AT&T.",
    "Previously converted a native Citi AA to an unrelated card; AT&T was blocked.",
])
def test_pc_contrast_does_not_accept_omitted_origin_age_or_reversed_outcome(text):
    notes, rows = [note("No PC options but an earlier conversion worked.", "pc")], [source("pc", PC_BODY)]
    assert retention_issues(document((text, ["pc"])), notes, rows)


def test_product_change_rules_are_not_bound_to_one_institution():
    body = "My card had no conversion options. I downgraded an Acme Travel to a Basic card last month with no issues."
    requirements = selected_note_requirements([note("Different product conversion outcomes.", "a")], [source("a", body)])
    assert requirements[0]["destination_terms"] == ["Basic"]
    assert not retention_issues(document(("Previously downgraded an Acme Travel to Basic successfully.", ["a"])),
                                [note("Product conversion outcomes.", "a")], [source("a", body)])


@pytest.mark.parametrize("text", [
    "Cashback was not transferred to miles; both balances remain visible. Single DP.",
    "Reports no conversion of cashback to miles after the transition.",
    "Reports cashback and miles remained separate after the transition.",
    "Reports rewards stayed cash rather than miles.",
])
def test_observed_rewards_nonconversion_kept(text):
    notes, rows = [note("The rewards conversion left separate balances.", "cash")], [source("cash", REWARDS_BODY)]
    assert retention_issues(document((text, ["cash"])), notes, rows) == []


@pytest.mark.parametrize("text", [
    "Cashback converted to miles successfully.",
    "The transition went smoothly; the app displays rewards.",
])
def test_nonconversion_outcome_cannot_be_reversed_or_lost(text):
    notes, rows = [note("The rewards conversion left separate balances.", "cash")], [source("cash", REWARDS_BODY)]
    assert retention_issues(document((text, ["cash"])), notes, rows)


@pytest.mark.parametrize("body", [
    "I downgraded my travel card and kept my anniversary points. The number didn't change. I prefer cash back.",
    "I kept my points and cash back, but my card number didn't change.",
    "My points and cash back remain available; my annual fee did not change.",
])
def test_unrelated_unchanged_card_details_are_not_rewards_conversion_failures(body):
    rows = [source("unchanged", body)]
    notes = [note("A product downgrade retained the card number and rewards.", "unchanged")]
    assert not selected_note_requirements(notes, rows)
    assert not retention_issues(document(("Reports a product downgrade with rewards retained.", ["unchanged"])), notes, rows)
    assert not any("rewards_conversion" in item["categories"] for item in identify_candidates(rows))


def test_actual_failed_conversion_is_retained_alongside_unchanged_card_number():
    body = "My card number didn't change. My cashback did not transfer to miles."
    rows = [source("cash", body)]
    notes = [note("The rewards conversion failed.", "cash")]
    assert selected_note_requirements(notes, rows)[0]["kind"] == "rewards_conversion"
    assert retention_issues(document(("The card number remained the same.", ["cash"])), notes, rows)
    assert not retention_issues(document(("Cashback did not transfer to miles.", ["cash"])), notes, rows)


@pytest.mark.parametrize("age", ["a couple of weeks ago", "two weeks ago", "a year ago", "last month"])
def test_older_offer_cannot_be_repackaged_as_newly_available(age):
    rows = [source("offer", f"I saw an 80k bonus offer {age}.")]
    notes = [note("An 80k bonus offer was reported.", "offer")]
    assert retention_issues(document(("Reports an 80k bonus offer.", ["offer"])), notes, rows)
    assert not retention_issues(document((f"Reports an 80k bonus offer seen {age}.", ["offer"])), notes, rows)
    assert not retention_issues(document(("Reports a historical 80k bonus offer.", ["offer"])), notes, rows)


def test_unrelated_old_offer_amount_does_not_taint_a_separate_current_offer():
    rows = [source("offer", "I saw a 50k offer last month. My current offer is 80k.")]
    notes = [note("The current offer is 80k.", "offer")]
    assert selected_note_requirements(notes, rows) == []


def test_historical_parent_does_not_generate_independent_current_requirement():
    rows = [source("parent", CERTIFICATE[1]["body"], context_only=True)]
    notes = [note("The flight certificate was discussed.", "parent")]
    assert not selected_note_requirements(notes, rows)


def test_prompt_refinements_preserve_general_profile_isolation():
    churning = resolve_editorial({"preset": "churning"})
    general = resolve_editorial({"preset": "general"})
    assert "benefit-qualifying spend" in churning.extraction
    assert "newly posted" in churning.extraction
    assert "cashback" not in general.extraction
