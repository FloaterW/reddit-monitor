"""Synthetic source-bound regressions for selected bank/variant qualifications."""

import pytest

from editorial_retention import retention_issues, selected_note_requirements

DEPOSIT_SOURCE = {
    "id": "t1_bank_example",
    "body": "I met the $500 deposit requirement for OnePay Checking within 30 days. "
            "My $250 OnePay Checking bonus arrived minutes after the final direct deposit. "
            "Outgoing transfers have been fast.",
}
DEPOSIT_NOTE = {"text": "The reported $250 OnePay Checking bonus arrived after the last direct deposit, "
                        "following $500 deposited in 30 days. The funding source was unspecified.",
                "source_ids": [DEPOSIT_SOURCE["id"]]}
VARIANT_SOURCE = {"id": "t1_offer_example",
                  "body": "The current offer is Chase Ink Cash & Unlimited for $1200 back in my app. "
                          "It says already approved; I have not applied."}
VARIANT_NOTE = {"text": "A user saw $1200 back offers for both Ink Cash and Ink Unlimited under the "
                        "green-star banner. No application outcome was reported.",
                "source_ids": [VARIANT_SOURCE["id"]]}


def document(text, sid="t1_bank_example", *, title="Reports"):
    return {"sections": [{"title": title, "bullets": [{"text": text, "source_ids": [sid]}]}]}


def kinds(notes, sources):
    return {f["kind"] for f in selected_note_requirements(notes, sources)}


def test_reported_direct_deposit_path_is_not_upgraded_to_an_official_rule():
    facts = selected_note_requirements([DEPOSIT_NOTE], [DEPOSIT_SOURCE])
    fact, = [f for f in facts if f["kind"] == "direct_deposit"]
    assert fact["relationship"] == "reported_payout_path"
    assert not fact["transfer_qualification_statements"]
    bad = document("Reports a $250 OnePay Checking bonus after meeting a $500 deposit requirement in 30 days.")
    issues = retention_issues(bad, [DEPOSIT_NOTE], [DEPOSIT_SOURCE])
    assert len(issues) == 1 and "direct-deposit" in issues[0] and DEPOSIT_SOURCE["id"] in issues[0]


@pytest.mark.parametrize("text", [
    "Reports the $250 OnePay Checking bonus arrived after the last direct deposit; $500 was deposited within 30 days.",
    "Reports the $250 OnePay Checking bonus within minutes of completing direct deposits. The precise funding source was unspecified.",
    "Reports a $250 OnePay Checking bonus after DD. Whether an ordinary bank transfer qualifies is unclear.",
    "The $250 OnePay Checking bonus payout followed the last direct deposit; the funding source was unspecified.",
])
def test_reported_dd_is_preserved_without_guessing_transfer_eligibility(text):
    assert not retention_issues(document(text), [DEPOSIT_NOTE], [DEPOSIT_SOURCE])


@pytest.mark.parametrize("claim", ["Ordinary transfers qualify.", "Ordinary transfers do not qualify.",
                                   "Regular bank transfers count toward this bonus."])
def test_dd_does_not_establish_ordinary_transfer_eligibility(claim):
    text = "Reports the $250 OnePay Checking bonus after a direct deposit. " + claim
    issues = retention_issues(document(text), [DEPOSIT_NOTE], [DEPOSIT_SOURCE])
    assert len(issues) == 1 and "Do not infer" in issues[0]


def test_different_source_cannot_supply_the_missing_qualifier():
    doc = document("Reports the $250 OnePay Checking bonus after a $500 deposit.")
    doc["sections"][0]["bullets"].append({"text": "Another bonus required a direct deposit.", "source_ids": ["other"]})
    assert retention_issues(doc, [DEPOSIT_NOTE], [DEPOSIT_SOURCE])


def test_omitting_bonus_does_not_force_an_unrelated_deposit_condition():
    assert not retention_issues(document("Reports fast outgoing OnePay transfers."), [DEPOSIT_NOTE], [DEPOSIT_SOURCE])


def test_unselected_or_different_account_condition_is_not_mandatory():
    assert "direct_deposit" not in kinds([{"text": "OnePay outgoing transfers were fast.", "source_ids": [DEPOSIT_SOURCE["id"]]}], [DEPOSIT_SOURCE])
    unrelated = {"text": "The $250 OtherBank Checking bonus required direct deposits.", "source_ids": [DEPOSIT_SOURCE["id"]]}
    assert "direct_deposit" not in kinds([unrelated], [DEPOSIT_SOURCE])
    assert "direct_deposit" not in kinds([DEPOSIT_NOTE], [{**DEPOSIT_SOURCE, "context_only": True}])
    reply = {"id": "t1_later", "body": "The bonus finally posted.", "parent_context": [DEPOSIT_SOURCE]}
    assert "direct_deposit" not in kinds([{**DEPOSIT_NOTE, "source_ids": [reply["id"]]}], [reply])


@pytest.mark.parametrize("body", [
    "Does the OnePay Checking bonus require direct deposits?",
    "The OnePay Checking bonus does not require direct deposits.",
    "Direct deposits are not required for the OnePay Checking bonus.",
    "Maybe the OnePay Checking bonus requires direct deposits.",
    "My payroll direct deposit arrived. A different checking bonus paid after an ordinary transfer.",
])
def test_questions_negation_and_unrelated_deposits_are_not_requirement_evidence(body):
    assert "direct_deposit" not in kinds([DEPOSIT_NOTE], [{**DEPOSIT_SOURCE, "body": body}])


def test_explicit_requirement_cannot_be_softened_to_an_incidental_payout_path():
    src = {"id": "t1_bank_example", "body": "The $250 OnePay Checking bonus requires qualifying direct deposits."}
    note = {"text": src["body"], "source_ids": [src["id"]]}
    assert retention_issues(document("The $250 OnePay Checking bonus arrived after a direct deposit."), [note], [src])
    assert not retention_issues(document(src["body"]), [note], [src])


def test_payout_heading_binds_its_own_cited_body_even_without_a_bonus_noun():
    body = "Reports $250 paid within minutes of the final deposit after meeting $500 within 30 days. Deposit source unspecified."
    doc = document(body, title="OnePay Checking $250 payout")
    assert len(retention_issues(doc, [DEPOSIT_NOTE], [DEPOSIT_SOURCE])) == 1
    doc["sections"][0]["bullets"][0]["text"] = body.replace("final deposit", "final direct deposit")
    assert not retention_issues(doc, [DEPOSIT_NOTE], [DEPOSIT_SOURCE])


def test_heading_does_not_supply_missing_dd_or_cross_section_evidence():
    body = "Reports $250 paid after the final deposit."
    doc = document(body, title="OnePay Checking direct-deposit bonus payout")
    assert retention_issues(doc, [DEPOSIT_NOTE], [DEPOSIT_SOURCE])
    doc = document(body, title="OnePay Checking payout")
    doc["sections"].append(document("Reports $250 paid after a direct deposit.", title="OtherBank Checking payout")["sections"][0])
    assert len(retention_issues(doc, [DEPOSIT_NOTE], [DEPOSIT_SOURCE])) == 1
    unrelated = document("Outgoing transfers were fast.")
    unrelated["sections"].append(document("Reports a payout after a direct deposit.", "unrelated", title="OnePay Checking bonus")["sections"][0])
    assert not retention_issues(unrelated, [DEPOSIT_NOTE], [DEPOSIT_SOURCE])


@pytest.mark.parametrize("text,title", [
    ("The $250 OnePay Checking bonus requires direct deposits.", "Bank reports"),
    ("The bonus arrived after a direct deposit. Direct deposits are required.", "OnePay Checking payout"),
    ("Reports $250 paid after the last direct deposit.", "OnePay Checking bonus requires direct deposits"),
])
def test_observed_dd_payout_cannot_be_upgraded_to_a_required_rule(text, title):
    issues = retention_issues(document(text, title=title), [DEPOSIT_NOTE], [DEPOSIT_SOURCE])
    assert len(issues) == 1 and "reported funding path" in issues[0]


def test_selected_shared_offer_cannot_drop_the_unlimited_variant():
    assert "shared_offer_variants" in kinds([VARIANT_NOTE], [VARIANT_SOURCE])
    doc = document("Reports Chase Ink offers for $1200 back, including Ink Cash.", VARIANT_SOURCE["id"])
    issues = retention_issues(doc, [VARIANT_NOTE], [VARIANT_SOURCE])
    assert len(issues) == 1 and "Ink Unlimited" in issues[0]


@pytest.mark.parametrize("text", [
    "Reports $1200 back offers for both Ink Cash and Ink Unlimited; no application result supplied.",
    "Reports Ink Cash & Unlimited offers for $1,200 back; terms unverified.",
    "Reports CIC and CIU both have a $1,200 bonus offer; this is not an approval outcome.",
])
def test_explicit_shared_variants_are_preserved_with_source_and_amount(text):
    assert not retention_issues(document(text, VARIANT_SOURCE["id"]), [VARIANT_NOTE], [VARIANT_SOURCE])


@pytest.mark.parametrize("body", [
    "I have Ink Cash and Unlimited. My Sapphire card has a $1200 bonus.",
    "Ink Cash has $1200 back and Ink Unlimited has $750 back.",
    "Does Ink Cash and Unlimited offer $1200 back?",
    "Ink Cash and Unlimited both have a $95 annual fee.",
    "Ink Cash and Unlimited are not both offering $1200 back.",
])
def test_other_mentions_distinct_offers_questions_and_fees_are_not_shared_offer_proof(body):
    assert "shared_offer_variants" not in kinds([VARIANT_NOTE], [{**VARIANT_SOURCE, "body": body}])


def test_only_selected_shared_offer_variants_are_required():
    cash_only = {"text": "A user saw an Ink Cash offer for $1200 back.", "source_ids": [VARIANT_SOURCE["id"]]}
    assert "shared_offer_variants" not in kinds([cash_only], [VARIANT_SOURCE])
    assert "shared_offer_variants" not in kinds([VARIANT_NOTE], [{**VARIANT_SOURCE, "context_only": True}])
    assert not retention_issues(document("Reports no application outcome.", VARIANT_SOURCE["id"]), [VARIANT_NOTE], [VARIANT_SOURCE])


def test_selected_reply_retains_explicit_shared_parent_offer_without_promoting_other_parent_facts():
    reply = {"id": "t1_reply", "body": "My financing terms were different.",
             "parent_context": [{**VARIANT_SOURCE, "context_only": True}]}
    note = {**VARIANT_NOTE, "source_ids": [reply["id"]]}
    bad = document("Reports Ink Cash offers for $1200 back.", VARIANT_SOURCE["id"])
    assert any("Ink Unlimited" in issue for issue in retention_issues(bad, [note], [reply]))
    good = document("Reports $1200 back offers for both Ink Cash and Ink Unlimited.", reply["id"])
    assert not retention_issues(good, [note], [reply])
    unrelated = {"text": "Financing terms were different.", "source_ids": [reply["id"]]}
    assert "shared_offer_variants" not in kinds([unrelated], [reply])
    assert not retention_issues(document("Reports different financing terms.", reply["id"]), [note], [reply])


def test_another_same_amount_offer_in_the_story_does_not_inherit_shared_variants():
    unrelated = {"id": "t1_other_offer", "body": "I accepted a $1200 back offer. The card variant was unspecified."}
    notes = [{**VARIANT_NOTE, "editorial_story": "Ink offers"},
             {"text": unrelated["body"], "source_ids": [unrelated["id"]], "editorial_story": "Ink offers"}]
    doc = document(unrelated["body"], unrelated["id"])
    assert not retention_issues(doc, notes, [VARIANT_SOURCE, unrelated])


def test_matching_selected_replies_can_share_the_same_parent_requirement():
    replies = [{"id": sid, "body": "Additional details.", "parent_context": [VARIANT_SOURCE]}
               for sid in ("t1_reply_a", "t1_reply_b")]
    notes = [{**VARIANT_NOTE, "source_ids": [reply["id"]]} for reply in replies]
    facts = [f for f in selected_note_requirements(notes, replies) if f["kind"] == "shared_offer_variants"]
    assert len(facts) == 1
    assert set(facts[0]["evidence_source_ids"]) == {VARIANT_SOURCE["id"], "t1_reply_a", "t1_reply_b"}
    assert retention_issues(document("Reports an Ink Cash $1200 back offer.", "t1_reply_b"), notes, replies)


def test_selected_onepay_clause_does_not_inherit_another_banks_required_policy():
    source = {"id": "t1_bank_example", "body": "The Chase bonus requires direct deposits. My OnePay bonus arrived after the last direct deposit."}
    selected = {"text": "My OnePay bonus arrived after the last direct deposit.", "source_ids": [source["id"]]}
    facts = [f for f in selected_note_requirements([selected], [source]) if f["kind"] == "direct_deposit"]
    assert len(facts) == 1 and facts[0]["relationship"] == "reported_payout_path"
    assert "OnePay" in facts[0]["evidence"]
    assert not retention_issues(document(selected["text"], title="OnePay payout"), [selected], [source])


def test_ambiguous_multiple_account_dd_clauses_are_not_forced_into_paraphrased_note():
    source = {"id": "t1_bank_example", "body": "My first bank bonus arrived after a direct deposit. My second bank bonus arrived after a direct deposit."}
    selected = {"text": "A banking bonus paid after direct deposits.", "source_ids": [source["id"]]}
    assert "direct_deposit" not in kinds([selected], [source])


def test_unbound_other_bank_transfer_claim_does_not_inherit_onepay_limits():
    other = "OtherBank's bonus accepts ordinary bank transfers that qualify."
    source = {"id": "t1_bank_example", "body": "The Chase bonus requires direct deposits. My OnePay bonus arrived after the last direct deposit. " + other}
    selected = {"text": "My OnePay bonus arrived after the last direct deposit.", "source_ids": [source["id"]]}
    assert not retention_issues(document(other, title="OtherBank payout"), [selected], [source])


def test_relationship_distinguishes_two_explicitly_selected_clauses():
    source = {"id": "t1_bank_example", "body": "The Chase bonus requires direct deposits. My OnePay bonus arrived after the last direct deposit."}
    selected = {"text": source["body"], "source_ids": [source["id"]]}
    facts = [f for f in selected_note_requirements([selected], [source]) if f["kind"] == "direct_deposit"]
    assert {f["relationship"] for f in facts} == {"required", "reported_payout_path"}
    assert not retention_issues(document(source["body"], title="Bank payouts"), [selected], [source])


def test_explicit_following_transfer_statement_is_source_evidence_not_inference():
    source = {"id": "t1_bank_example", "body": "My $250 OnePay Checking bonus arrived after a direct deposit. Ordinary bank transfers qualify for this bonus."}
    selected = {"text": source["body"], "source_ids": [source["id"]]}
    assert not retention_issues(document(source["body"], title="OnePay payout"), [selected], [source])


@pytest.mark.parametrize("source_claim,output_claim", [
    ("Ordinary bank transfers do not qualify for this bonus.", "Ordinary bank transfers qualify for this bonus."),
    ("Ordinary bank transfers qualify for this bonus.", "Ordinary bank transfers do not qualify for this bonus."),
    ("Ordinary bank transfers qualify for this bonus only after manual review.", "Ordinary bank transfers qualify for this bonus."),
    ("Ordinary bank transfers qualify for this bonus.", "Ordinary bank transfers might qualify for this bonus."),
])
def test_explicit_transfer_statement_does_not_authorize_changed_polarity_or_conditions(source_claim, output_claim):
    payout = "My $250 OnePay Checking bonus arrived after a direct deposit. "
    source = {"id": "t1_bank_example", "body": payout + source_claim}
    selected = {"text": source["body"], "source_ids": [source["id"]]}
    assert not retention_issues(document(source["body"], title="OnePay payout"), [selected], [source])
    issues = retention_issues(document(payout + output_claim, title="OnePay payout"), [selected], [source])
    assert len(issues) == 1 and "direction or conditions" in issues[0]


def test_another_accounts_transfer_statement_is_not_onepay_evidence():
    source = {**DEPOSIT_SOURCE, "body": DEPOSIT_SOURCE["body"] + " OtherBank Checking permits ordinary bank transfers to qualify for its bonus."}
    bad = document("The $250 OnePay Checking bonus paid after a direct deposit. Ordinary transfers qualify.")
    assert any("Do not infer" in issue for issue in retention_issues(bad, [DEPOSIT_NOTE], [source]))


@pytest.mark.parametrize("text", ["The Sapphire card offers a $1200 bonus.",
                                  "Ink Cash is still open. The Sapphire card offers a $1200 bonus."])
def test_different_same_amount_product_in_same_source_does_not_inherit_ink_variants(text):
    source = {**VARIANT_SOURCE, "body": "Ink Cash and Unlimited both offer $1200 back. My Sapphire card offers a $1200 bonus."}
    note = {"text": "Ink Cash and Unlimited both offer $1200 back.", "source_ids": [source["id"]]}
    assert not retention_issues(document(text, source["id"], title="Sapphire offer"), [note], [source])
