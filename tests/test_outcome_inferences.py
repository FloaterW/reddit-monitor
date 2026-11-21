"""Unquantified spend anecdotes and payment advice do not prove completion."""

import pytest

from factual_checks import factual_issues

BILL_BODY = "It's a 20k spend for the flight credit. I put one pretty large bill on it early this year."
PAYMENT_ADVICE = "If you scan at payment it should use it to pay."
PAYMENT_SUCCESS = "I had to wait until the payment screen for it to work."


def source(body, author="sample_one", **fields):
    return {"id": f"t1_{author}", "author": author, "body": body, **fields}


@pytest.mark.parametrize("claim", [
    "The credit requires 20k spend, which they met with a large bill early this year.",
    "They hit the spending threshold using one large bill.",
    "Their large bill covered the spending requirement.",
])
def test_unquantified_large_bill_does_not_prove_threshold_completion(claim):
    assert any("bill alone" in issue for issue in factual_issues(claim, [source(BILL_BODY)]))


@pytest.mark.parametrize("claim", [
    "The credit requires 20k spend. The commenter also says they put a large bill on the card early this year.",
    "The commenter may have met the spending requirement with a large bill; the amount was not supplied.",
    "The large bill did not cover the spending requirement.",
])
def test_separate_or_uncertain_bill_observation_is_allowed(claim):
    assert not factual_issues(claim, [source(BILL_BODY)])


@pytest.mark.parametrize("body", [
    "The credit requires 20k spend. I met it with a large bill early this year.",
    "The credit requires 20k spend. My large bill covered the requirement.",
    "The credit requires 20k spend. I put a large bill for 25k on it.",
])
def test_explicit_bill_completion_or_amount_is_outside_unquantified_guard(body):
    assert not factual_issues("They met the spending threshold with a large bill.", [source(body)])


def test_conditional_source_completion_is_not_affirmative_confirmation():
    assert factual_issues("They met the spend with a large bill.",
                          [source("I might have met the spend with a large bill.")])


@pytest.mark.parametrize("claim", [
    "Two commenters report successful gift-card payment at the payment screen.",
    "Both users report successful gift-card payment.",
    "At the payment screen, both worked.",
])
def test_one_success_and_one_suggestion_are_not_two_successes(claim):
    assert any("conditional suggestion" in issue for issue in factual_issues(
        claim, [source(PAYMENT_ADVICE), source(PAYMENT_SUCCESS, "sample_two")]))


@pytest.mark.parametrize("claim", [
    "A user reports that scanning at the payment screen enabled payment.",
    "The gift-card payment worked.",
    "A commenter reports successful gift-card payment.",
])
def test_single_modal_suggestion_cannot_become_completed_payment(claim):
    assert any("conditional suggestion" in issue for issue in factual_issues(claim, [source(PAYMENT_ADVICE)]))


@pytest.mark.parametrize("claim", [
    "A commenter reports staff scanned the gift card while adding items. "
    "The commenter says scanning at the payment screen should use it to pay; "
    "no successful payment is explicitly reported in this comment.",
    "No successful gift-card payment was reported.",
])
def test_explicit_no_success_report_does_not_erase_useful_conditional_remedy(claim):
    assert not factual_issues(claim, [source(PAYMENT_ADVICE)])


def test_source_denial_of_success_cannot_support_a_positive_payment_claim():
    row = source("Scanning at payment should work, but no successful gift-card payment was reported.")
    assert factual_issues("A commenter reports successful gift-card payment.", [row])


def test_two_actual_successes_with_one_also_giving_advice_are_allowed():
    rows = [source("Scanning at payment should work. My gift-card payment worked."),
            source(PAYMENT_SUCCESS, "sample_two")]
    assert not factual_issues("Two commenters report successful gift-card payment.", rows)


def test_actual_success_and_separately_attributed_conditional_guidance_are_allowed():
    rows = [source(PAYMENT_ADVICE), source(PAYMENT_SUCCESS, "sample_two")]
    text = ("One commenter reports successful gift-card payment after waiting for the payment screen. "
            "Another suggests that scanning there should use it to pay.")
    assert not factual_issues(text, rows)


def test_parent_success_does_not_turn_reply_advice_into_another_successful_user():
    row = source(PAYMENT_ADVICE, parent_context=[{"body": PAYMENT_SUCCESS}])
    assert factual_issues("A commenter reports successful gift-card payment.", [row])


def test_same_author_multiple_comments_are_not_independent_successes():
    rows = [source("Scanning at payment should work. The gift-card payment worked."),
            source(PAYMENT_SUCCESS)]
    assert factual_issues("Two commenters report successful gift-card payment.", rows)


def test_payment_conditionals_do_not_create_a_general_bank_or_approval_rule():
    rows = [source("Your bill payment could post later. I was approved for a new card.")]
    assert not factual_issues("A user reports successful bill payment and approval.", rows)
