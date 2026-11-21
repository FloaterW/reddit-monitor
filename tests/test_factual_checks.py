"""Real replay regressions: identities, promised credits and edition-relative time."""

import json
from datetime import date, datetime, timezone

import pytest

from digest_schema import response_schema
from editorial_profiles import resolve_editorial
from factual_checks import edition_prompt_context, factual_issues, parse_edition_cutoff
from verified_digest import _source_records, render_checked, summarize_verified

RETENTION_BODY = (
    "Just got the same result today. Annual fee ($229) posted this morning. "
    "Called to cancel the card. The rep said they'd give me a $229 credit for "
    "keeping the card open. No spend requirement attached. The process took 5 minutes."
)


def source(body=RETENTION_BODY):
    return {"id": "t1_report", "author": "sample_reporter", "body": body,
            "created": "2026-11-02 17:23 UTC", "context_status": "unavailable_rss",
            "post_title": "Chase waived my Southwest Priority fee today",
            "post_permalink": "https://reddit.com/r/churning/comments/thread/topic/"}


def draft(text, title="Retention offer"):
    return {"sections": [{"title": title, "bullets": [
        {"text": text, "source_ids": ["t1_report"], "source_position": "end", "indent": 0}
    ]}]}


def test_unavailable_parent_title_cannot_supply_missing_card_identity():
    row = source()
    assert _source_records([row])[0]["thread"] is None
    assert factual_issues("Southwest Priority retention credit", [row])
    assert not factual_issues("Unidentified card retention offer", [row])


def test_body_alias_or_known_parent_can_support_card_identity():
    assert not factual_issues("Southwest Priority offer", [source("My SW Priority has a retention offer.")])
    row = source()
    row["parent_context"] = [{"body": "I called about my Southwest Rapid Rewards Priority."}]
    assert not factual_issues("Southwest Priority offer", [row])
    assert _source_records([row])[0]["thread"] == row["post_title"]
    assert not factual_issues("Ink Preferred offer", [source("My CIP offer is targeted.")])


@pytest.mark.parametrize("text", [
    "A user reports receiving a $229 credit to keep the card open.",
    "A user received a $229 statement credit.",
    "The $229 retention credit posted.",
    "$229 credit received",
])
def test_promised_credit_and_posted_annual_fee_do_not_establish_credit_receipt(text):
    issues = factual_issues(text, [source()])
    assert any("receipt/posting" in issue for issue in issues)


@pytest.mark.parametrize("text", [
    "A user reports being offered a $229 credit to keep the card open.",
    "The rep promised a $229 credit. Posting was not reported.",
    "The $229 credit has not posted.",
    "A user has not received a $229 credit.",
    "A user received a $229 credit offer.",
])
def test_offer_or_explicitly_unconfirmed_credit_is_not_a_payout_claim(text):
    assert not factual_issues(text, [source()])


def test_explicit_credit_posting_supports_receipt_but_negated_posting_does_not():
    statement = "A user received a $229 credit."
    assert not factual_issues(statement, [source("The $229 credit posted today.")])
    assert factual_issues(statement, [source("I have not received a $229 credit.")])


def test_bullet_and_heading_are_both_checked_on_the_churning_path():
    rows = [source()]
    for document in [draft("A user reports receiving a $229 credit."),
                     draft("A user was offered a $229 credit.", "Southwest Priority retention offer")]:
        result, issues = render_checked(document, rows, factual_checks=True)
        assert result is None and issues
    # These specialized card checks do not change another monitor's semantics.
    result, issues = render_checked(draft("A user reports receiving a $229 credit."), rows)
    assert result and not issues


def test_historical_two_day_offer_is_not_stale_at_the_same_day_cutoff():
    row = source("Delta Business Gold has an elevated offer for around 2 days or so.")
    row["created"] = "2026-11-02 08:37 UTC"
    for cutoff in ("2026-11-02T22:30:00+00:00", "2026-11-02", None):
        assert any("stale/expired" in issue for issue in factual_issues(
            "The Delta deadline is stale and unverified.", [row], edition_cutoff=cutoff))
        assert not factual_issues("The source describes an elevated offer for around 2 days; exact deadline unverified.",
                                  [row], edition_cutoff=cutoff)
    assert not factual_issues("The deadline is stale.", [row], edition_cutoff="2026-11-06")


def test_explicit_source_expiry_is_not_overridden_by_edition_guard():
    row = source("The offer expired despite being advertised for another 2 days.")
    assert not factual_issues("The source reports the offer expired.", [row], edition_cutoff="2026-11-02")


def test_edition_context_preserves_date_only_unknown_and_timezone():
    assert parse_edition_cutoff("2026-11-02") == date(2026, 11, 2)
    assert "cutoff time and timezone are unknown" in edition_prompt_context("2026-11-02")
    assert "date and cutoff are unknown" in edition_prompt_context(None)
    assert "timezone unknown" in edition_prompt_context(datetime(2026, 11, 2, 18, 30))
    assert "+00:00" in edition_prompt_context(datetime(2026, 11, 2, 22, 30, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        parse_edition_cutoff("not a date")


def test_edition_cutoff_reaches_extraction_planning_and_writing():
    row = source("An offer was displayed. Posting was not reported.")
    calls = []

    def invoke(prompt, **kwargs):
        stage = response_schema(prompt)["required"][0]
        calls.append((stage, prompt))
        if stage == "notes":
            return json.dumps({"notes": [{"topic": "Offer", "text": "An offer was displayed.",
                                          "source_ids": ["t1_report"]}] * 9})
        if stage == "stories":
            return json.dumps({"stories": [
                {"topic": "Offer", "note_ids": ["n0"], "status": "new", "reason": "current lead"},
                {"topic": "Duplicates", "note_ids": [f"n{i}" for i in range(1, 9)],
                 "status": "repeat", "reason": "same report"}]})
        return json.dumps(draft("A user reports an offer was displayed."))

    result = summarize_verified([row], lambda rows: [rows], invoke,
                                editorial_profile=resolve_editorial({"preset": "churning"}),
                                edition_cutoff="2026-11-02T22:30:00+00:00")
    assert result.startswith("# Daily Digest — November 02, 2026")
    assert [stage for stage, _ in calls] == ["notes", "stories", "sections"]
    for _, prompt in calls:
        assert "Intended edition cutoff: 2026-11-02T22:30:00+00:00" in prompt
        assert "never the execution date" in prompt


def test_unsupported_card_identity_is_repaired_against_originals():
    replies = [draft("A user was offered a $229 credit.", "Southwest Priority retention offer"),
               draft("A user was offered a $229 credit.", "Unidentified card retention offer")]
    prompts = []

    def invoke(prompt, **kwargs):
        prompts.append(prompt)
        return json.dumps(replies.pop(0))

    result = summarize_verified([source()], None, invoke,
                                recovered_notes=[{"topic": "Southwest Priority", "text": "A $229 credit was offered.",
                                                  "source_ids": ["t1_report"]}],
                                editorial_profile=resolve_editorial({"preset": "churning"}),
                                edition_cutoff="2026-11-02")
    assert "Southwest Priority" not in result and "Unidentified card" in result
    assert len(prompts) == 2 and "Remove the unsupported card identity" in prompts[1]
