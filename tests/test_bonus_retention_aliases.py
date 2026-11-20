"""Bonus aliases preserve the condition without confusing different benefits."""

import pytest

from editorial_retention import retention_issues, selected_note_requirements


def check(source_body, draft_text, *, cited_id="source", note_text="Card welcome bonus and credit report."):
    rows = [{"id": "source", "body": source_body}]
    notes = [{"text": note_text, "source_ids": ["source"]}]
    draft = {"sections": [{"title": "Card report", "bullets": [
        {"text": draft_text, "source_ids": [cited_id]}]}]}
    return retention_issues(draft, notes, rows)


@pytest.mark.parametrize("source_label", ["bonus", "welcome bonus", "welcome offer", "sign-up bonus", "signup offer", "SUB"])
@pytest.mark.parametrize("draft_label", ["bonus", "welcome bonus", "welcome offer", "sign-up offer", "SUB"])
def test_equivalent_bonus_labels_preserve_same_spending_condition(source_label, draft_label):
    assert not check(f"A 100k {source_label} after $6k spend.",
                     f"Reports a 100k {draft_label} after $6,000 spending.",
                     note_text=f"A 100k {source_label}, described as a {draft_label}, is reported.")


def test_november_16_source_and_correct_first_repair_are_accepted():
    body = ("For your profile in active military, Sapphire Reserve is the obvious add, "
            "$795 fee gone, 100k bonus after $6k spend, and the $300 travel credit is free money as fees don't apply. "
            "It also gives you a second currency in UR with 1:1 to Hyatt.")
    text = ("cites a Sapphire Reserve welcome bonus of 100k after $6k spend. "
            "The commenter also claims an active-military waiver of the $795 fee and a $300 travel credit. "
            "The recommendation supplies no spending period or application channel, and documents no approval, fee waiver or bonus payout.")
    assert not check(body, text)


@pytest.mark.parametrize("text", [
    "Reports a 100k welcome bonus without specifying qualifying spending.",
    "Reports a 100k welcome bonus after $5k spend.",
    "Reports a 100k welcome bonus. A hotel credit requires $6k spend.",
    "Reports a 100k welcome bonus. A retention bonus requires $6k spend.",
    "Reports a 100k welcome bonus that does not require $6k spend.",
])
def test_missing_wrong_or_unrelated_spending_still_blocks_delivery(text):
    assert check("A 100k welcome bonus after $6k spend.", text,
                 note_text="A card welcome bonus report.")


def test_matching_spending_from_a_different_source_does_not_satisfy_requirement():
    rows = [{"id": "source", "body": "A 100k bonus after $6k spend."},
            {"id": "other", "body": "My separate offer requires $6k spend."}]
    notes = [{"text": "A card bonus report.", "source_ids": ["source"]}]
    draft = {"sections": [{"title": "Offers", "bullets": [
        {"text": "Reports a 100k welcome bonus.", "source_ids": ["source"]},
        {"text": "Qualifying spend of $6k was required.", "source_ids": ["other"]}]}]}
    assert retention_issues(draft, notes, rows)


@pytest.mark.parametrize("distinct", ["flight credit", "hotel credit", "companion certificate", "retention bonus", "upgrade bonus", "referral bonus", "anniversary bonus"])
def test_bonus_cannot_substitute_for_an_explicitly_different_benefit(distinct):
    assert check(f"The {distinct} requires $6k spend.",
                 f"The {distinct} was reported. A welcome bonus requires $6k spend.",
                 note_text=f"The {distinct} was reported.")
    assert check("The welcome bonus requires $6k spend.",
                 f"The welcome bonus was reported. The {distinct} requires $6k spend.")


def test_omitted_bonus_does_not_attach_its_condition_to_unrelated_travel_credit():
    assert not check("A 100k bonus after $6k spend, plus a $300 travel credit.",
                     "The commenter claims a $300 travel credit.")


@pytest.mark.parametrize("benefit", ["travel credit", "certificate", "cashback"])
def test_generic_nonbonus_terms_cannot_use_the_welcome_bonus_alias_bridge(benefit):
    assert check(f"The {benefit} requires $6k spend.",
                 f"The {benefit} was reported. A welcome bonus requires $6k spend.",
                 note_text=f"The {benefit} was reported.")


def test_sub_shorthand_still_rejects_the_wrong_spending_amount_for_generic_bonus():
    assert check("A 100k bonus after $6k spend.", "Reports a 100k SUB after $5k spend.")


def test_separate_bonus_and_credit_requirements_both_survive():
    body = "A 100k bonus after $6k spend. The flight credit requires $20k spend."
    notes = [{"text": "A bonus and flight credit.", "source_ids": ["source"]}]
    requirements = selected_note_requirements(notes, [{"id": "source", "body": body}])
    assert {(r["benefit"], r["amount"]) for r in requirements} == {
        ("credit or bonus", "6000"), ("flight credit/certificate", "20000")}
    assert not check(body, "A 100k welcome bonus after $6k spend; the flight credit requires $20k spend.",
                     note_text="A bonus and flight credit.")
    assert check(body, "A 100k welcome bonus after $20k spend; the flight credit requires $6k spend.",
                 note_text="A bonus and flight credit.")
