"""A reactive reply cannot become its parent author's personal approval report."""

import json

import pytest

from digest_schema import response_schema
from editorial_profiles import resolve_editorial
from factual_checks import factual_issues
from verified_digest import render_checked, summarize_verified

PARENT_ID = "t1_19ss2jhciu3b6"
CHILD_ID = "t1_19sslc23dxqo2"
PARENT_BODY = (
    "tldr: got my second summit card on the same atmos and bofa account while holding the first summit card.\n\n"
    "1st summit card got 105k + 25k cert by signing up from the initial interest email when the card first came out. "
    "signed up end of oct 2025.\n\njust applied to my second summit card for the 100k bonus and 25k cert today. "
    "instant approval, credit limit 24k, same as the first one.\n\nbefore application: 6/24 consumer, "
    "8/24 counting biz cards, 1/24 bofa, have bofa bank account that I direct deposit into to not be assessed fees, "
    "opened 10 months ago. I wanted oneworld status so this was my daily spender."
)
CHILD_BODY = "I didn’t know you can hold and get subs for multiple summit cards"
CLAIM = "One applicant reports instant approval for a second Atmos Summit while holding the first on the same Atmos and Bank of America accounts."


def records(child_body=CHILD_BODY, parent_body=PARENT_BODY):
    parent = {"id": PARENT_ID, "author": "CorrectCombination11", "body": parent_body,
              "created": "2026-11-17 16:01 UTC", "parent_context": [],
              "post_permalink": "https://reddit.com/r/churning/comments/1xu8wnr/data_points_weekly_week_of_november_12_2026/"}
    child = {"id": CHILD_ID, "author": "ilovejellydonuts", "body": child_body,
             "created": "2026-11-18 08:00 UTC", "parent_context": [parent],
             "post_permalink": parent["post_permalink"]}
    return child, parent


def attribution_issues(text, sources):
    return [issue for issue in factual_issues(text, sources) if "reactive reply" in issue]


def draft(source_id):
    return {"sections": [{"title": "💳 Second Atmos Summit instant approval", "bullets": [
        {"text": CLAIM, "source_ids": [source_id], "source_position": "end", "indent": 0}]}]}


def test_real_reactive_child_cannot_inherit_parent_approval():
    child, _ = records()
    issues = attribution_issues(CLAIM, [child])
    assert len(issues) == 1
    assert PARENT_ID in issues[0] and "Cite the actual parent source_id" in issues[0]
    assert attribution_issues("Second Atmos Summit instant approval", [child])


@pytest.mark.parametrize("reaction", ["I did not know that", "I didn't know that", "Congratulations!",
                                       "Congrats on your second card!", "Thanks for sharing!", "Thank you!"])
def test_explicit_reactions_are_not_personal_approval_reports(reaction):
    child, _ = records(child_body=reaction)
    assert attribution_issues(CLAIM, [child])


@pytest.mark.parametrize("body", ["Thanks! I was instantly approved for my second Summit too.",
                                  "Congratulations! We got approved for a second card as well.",
                                  "Thanks! Approved instantly yesterday for my second Summit.",
                                  "Thanks! I was approved, although I could not get a higher limit.",
                                  "Thanks! Got approved today for my second Summit.",
                                  "Thanks! We were both approved.",
                                  "Thanks! I actually was approved.",
                                  "I was approved for my second Atmos Summit."])
def test_direct_personal_outcome_survives_greeting_or_reaction(body):
    child, _ = records(child_body=body)
    assert not attribution_issues(CLAIM, [child])


@pytest.mark.parametrize("reaction", ["Congrats! You were instantly approved for a second Summit.",
                                       "Congratulations on your instant approval!",
                                       "Thanks! I heard you were approved for another Summit."])
def test_reaction_explicitly_about_another_person_does_not_support_personal_outcome(reaction):
    child, _ = records(child_body=reaction)
    assert attribution_issues(CLAIM, [child])


@pytest.mark.parametrize("text", ["Was the applicant instantly approved?",
                                  "Instant approval was not reported.",
                                  "The commenter was not approved.",
                                  "You might get approved for another card.",
                                  "Applicants can improve their instant approval odds.",
                                  "Tips for instant approval", "Approval advice for applicants"])
def test_questions_negations_and_generic_advice_are_not_asserted_outcomes(text):
    child, _ = records()
    assert not attribution_issues(text, [child])


@pytest.mark.parametrize("parent_body", ["I was not approved for my second Summit.",
                                         "Was I instantly approved?", "I might get approved later.",
                                         "Instant approval is possible for people with good credit."])
def test_parent_must_actually_report_affirmative_personal_outcome(parent_body):
    child, _ = records(parent_body=parent_body)
    assert not attribution_issues(CLAIM, [child])


def test_same_author_parent_and_missing_parent_are_outside_narrow_guard():
    child, parent = records()
    parent["author"] = child["author"]
    assert not attribution_issues(CLAIM, [child])
    child["parent_context"] = []
    assert not attribution_issues(CLAIM, [child])


def test_direct_parent_citation_or_parent_plus_child_supports_approval():
    child, parent = records()
    assert not attribution_issues(CLAIM, [parent])
    assert not attribution_issues(CLAIM, [child, parent])
    result, issues = render_checked(draft(PARENT_ID), [child, parent], factual_checks=True)
    assert result and not issues
    assert "CorrectCombination11" in result and "ilovejellydonuts" not in result


def test_parent_can_still_supply_a_card_referent_without_transferred_outcome():
    child, _ = records(child_body="This expires tomorrow.")
    assert not attribution_issues("A reply warns that the Atmos Summit offer expires tomorrow.", [child])


def test_source_safe_repair_can_cite_the_actual_parent_from_original_sources():
    child, parent = records()
    prompts = []

    def invoke(prompt, **kwargs):
        prompts.append(prompt)
        sources, _ = json.JSONDecoder().raw_decode(prompt.split("ORIGINAL SOURCES:\n", 1)[1])
        assert {row["source_id"] for row in sources} == {CHILD_ID, PARENT_ID}
        schema = response_schema(prompt)
        allowed = schema["properties"]["sections"]["items"]["properties"]["bullets"]["items"]["properties"]["source_ids"]["items"]["enum"]
        assert PARENT_ID in allowed
        return json.dumps(draft(CHILD_ID if len(prompts) == 1 else PARENT_ID))

    result = summarize_verified(
        [child, parent], None, invoke,
        recovered_notes=[{"topic": "Second Atmos Summit approval", "text": CLAIM, "source_ids": [CHILD_ID]}],
        editorial_profile=resolve_editorial({"preset": "churning"}),
        edition_cutoff="2026-11-18T15:30:00+00:00",
    )
    assert len(prompts) == 2
    assert "Cite the actual parent source_id" in prompts[1]
    assert "CorrectCombination11" in result and "ilovejellydonuts" not in result
