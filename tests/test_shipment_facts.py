"""Counterfactual card shipment must not become an observed nonshipment fact."""

import pytest

from factual_checks import factual_issues
from verified_digest import render_checked

COUNTERFACTUAL = (
    "My clock ran out. Even if they hadn't shipped a new card back in September, "
    "all cash reloads were shut off for me yesterday."
)


def source(body=COUNTERFACTUAL):
    return {"id": "t1_example", "author": "sample_reporter", "body": body,
            "subreddit": "churning", "post_title": "Cash reloads",
            "post_permalink": "https://reddit.com/r/churning/comments/post/topic/"}


@pytest.mark.parametrize("claim", [
    "Cash reloads ended despite no replacement card having shipped.",
    "The replacement card had not shipped when cash reloads ended.",
    "They hadn't shipped a new card when the feature ended.",
    "They hadn’t sent a replacement card before reloads ended.",
    "No new card was shipped, but cash reloads ended.",
    "They never mailed a replacement card.",
])
def test_counterfactual_nonshipment_cannot_be_asserted_as_fact(claim):
    assert any("nonshipment" in issue for issue in factual_issues(claim, [source()]))


@pytest.mark.parametrize("claim", [
    "The user says cash reloads ended yesterday.",
    "Even if they hadn't shipped a new card, cash reloads would have ended.",
    "It is unclear whether the replacement card had not shipped.",
    "The commenter describes a hypothetical where no replacement card had shipped.",
    "If no new card was shipped, the reload cutoff would still apply.",
])
def test_preserved_hypothetical_or_omitted_shipment_detail_is_allowed(claim):
    assert not factual_issues(claim, [source()])


@pytest.mark.parametrize("body", [
    "They had not shipped a new card before reloads ended.",
    "No replacement card had shipped when reloads stopped.",
    COUNTERFACTUAL + " In fact, no replacement card was shipped.",
])
def test_explicit_observed_nonshipment_can_support_the_claim(body):
    assert not factual_issues("No replacement card had shipped.", [source(body)])


def test_supplied_parent_can_establish_observed_nonshipment():
    row = source()
    row["parent_context"] = [{"body": "They never shipped a replacement card to me."}]
    assert not factual_issues("No replacement card had shipped.", [row])


def test_counterfactual_in_parent_does_not_establish_nonshipment():
    row = source("The cash reload feature ended yesterday.")
    row["parent_context"] = [{"body": COUNTERFACTUAL}]
    assert any("nonshipment" in issue for issue in factual_issues("No replacement card had shipped.", [row]))


def test_unrelated_if_in_previous_sentence_does_not_hide_actual_source_nonshipment():
    row = source("If needed, call support. They hadn't shipped a replacement card before reloads ended.")
    assert not factual_issues("No replacement card had shipped.", [row])


def test_guard_does_not_invent_a_general_shipment_evidence_policy():
    # This intentionally narrow guard checks a detected hypothetical-to-fact
    # transformation. It does not claim to validate all shipment wording.
    assert not factual_issues("No replacement card had shipped.", [source("No shipment details supplied.")])


def test_source_safe_rendering_blocks_fact_upgrade_and_allows_corrected_wording():
    document = {"sections": [{"title": "Cash reload cutoff", "bullets": [
        {"text": "Cash reloads ended despite no replacement card having shipped.",
         "source_ids": ["t1_example"], "source_position": "end", "indent": 0}]}]}
    rendered, issues = render_checked(document, [source()], factual_checks=True)
    assert rendered is None and any("nonshipment" in issue for issue in issues)
    document["sections"][0]["bullets"][0]["text"] = "A user reports that cash reloads ended yesterday."
    rendered, issues = render_checked(document, [source()], factual_checks=True)
    assert rendered and not issues
