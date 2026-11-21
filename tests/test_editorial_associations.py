"""Regression checks for preserving the relationship between cards and facts."""

import pytest

from editorial_selection import outcomes, priority_issues, priority_leads


def source(body):
    return {"id": "t1_report", "body": body}


def document(*texts):
    return {"sections": [{"title": "Card reports", "bullets": [
        {"text": text, "source_ids": ["t1_report"], "indent": int(index != 0)}
        for index, text in enumerate(texts)
    ]}]}


@pytest.mark.parametrize("body", [
    "I was not approved for CIC.",
    "I wasn't approved for CIU.",
    "My CIP application was not automatically approved.",
    "I have never been approved for CSRB.",
    "I was not denied for CIC; the application is pending.",
    "My CIC approval was not confirmed.",
    "I have not been approved or denied for CIC; still pending.",
    "My CIC application has neither been approved nor denied.",
])
def test_negated_or_pending_outcome_does_not_require_a_positive_fact(body):
    assert priority_leads([source(body)]) == []


def test_negation_does_not_swallow_a_separate_reported_outcome():
    assert outcomes("I was not approved instantly, but was approved after recon.") == {"approved"}
    assert outcomes("I wasn't approved; the application was denied.") == {"denied"}
    assert outcomes("I was neither approved nor denied initially, but approved after recon.") == {"approved"}


@pytest.mark.parametrize("body", [
    "I was denied for CIC but approved for CIU.",
    "I received a CIC denial and a CIU approval.",
    "My CIC was denied; my CIU was approved.",
])
def test_different_card_outcomes_are_not_interchangeable(body):
    leads = priority_leads([source(body)])
    assert {lead["card"]: lead["outcomes"] for lead in leads} == {
        "Ink Cash": ["denied"], "Ink Unlimited": ["approved"],
    }
    assert not priority_issues(document("A user reports a CIC denial and a CIU approval."), leads)
    assert priority_issues(document("A user reports a CIC approval and a CIU denial."), leads)
    assert priority_issues(document("A user reports a CIC approval.", "CIU was denied."), leads)


def test_one_cards_denial_then_approval_cannot_lose_either_outcome():
    leads = priority_leads([source("I was denied for CIC but approved after recon.")])
    assert leads[0]["outcomes"] == ["approved", "denied"]
    assert priority_issues(document("A user reports a CIC denial."), leads)
    assert not priority_issues(document("A user reports a CIC denial followed by approval after recon."), leads)


@pytest.mark.parametrize("body", [
    "125k/8k/3mo for CIP and 300k/30k/4mo for CSRB.",
    "125k after 8k in 3 months for CIP, 300k after 30k in 4 months for CSRB.",
    "CIP 125k/8k/3mo versus 300k/30k/4mo for CSRB.",
])
def test_offer_amounts_before_card_names_stay_with_the_correct_card(body):
    leads = priority_leads([source(body)])
    assert [(lead["card"], lead["amounts"], lead["months"]) for lead in leads] == [
        ("Ink Preferred", ["125000", "8000"], "3"),
        ("Sapphire Reserve for Business", ["300000", "30000"], "4"),
    ]
    assert not priority_issues(document(
        "A user reports CIP 125k/8k/3mo and CSRB 300k/30k/4mo; terms unverified."), leads)
    assert priority_issues(document(
        "A user reports CIP 300k/30k/4mo and CSRB 125k/8k/3mo; terms unverified."), leads)


def test_shared_offer_and_nested_details_keep_the_shared_caveat():
    leads = priority_leads([source(
        "I see targeted $1200 after 6k in 3 months for CIC and CIU. Also 300k after 30k in 3 months for CSRB.")])
    assert len(leads) == 3
    assert not priority_issues(document(
        "A user reports a targeted CIC and CIU $1200 after 6k in 3 months offer; terms unverified.",
        "CSRB: 300k after 30k in 3 months."), leads)


@pytest.mark.parametrize("relation", ["for", "with"])
def test_natural_offer_wording_cannot_swap_card_amounts(relation):
    leads = priority_leads([source("CIP 125k/8k/3mo and CSRB 300k/30k/4mo.")])
    assert not priority_issues(document(
        f"A user reports CIP 125k points {relation} 8k spend in 3 months and "
        f"CSRB 300k points {relation} 30k spend in 4 months; terms unverified."), leads)
    assert priority_issues(document(
        f"A user reports CIP 300k points {relation} 30k spend in 4 months and "
        f"CSRB 125k points {relation} 8k spend in 3 months; terms unverified."), leads)


def test_ambiguous_unsplit_amounts_do_not_become_a_guessed_required_offer():
    assert not priority_leads([source("125k/8k CIP 300k/30k CSRB")])


def test_ambiguous_mixed_outcomes_are_left_for_ordinary_editorial_review():
    assert not priority_leads([source("I had CIC CIU approved denied")])
