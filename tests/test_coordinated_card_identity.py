"""Synthetic regressions for explicit coordinated card names in cited evidence."""

from datetime import datetime, timezone

import pytest

from editorial_retention import retention_issues
from factual_checks import factual_issues
from verified_digest import render_checked


@pytest.mark.parametrize("names", [
    "Ink Cash & Unlimited", "Ink Cash and Unlimited", "Ink Business Cash and Unlimited cards",
    "Chase Ink Cash / Unlimited", "Ink Cash and Ink Unlimited", "CIC and CIU", "CIC & Unlimited",
    "CIU and Ink Cash", "ink cash & unlimited",
])
@pytest.mark.parametrize("use_parent", [False, True])
def test_explicit_coordinated_ink_names_support_card_identity(names, use_parent):
    body = f"The {names} offer is targeted."
    row = {"body": "I received this offer.", "parent_context": [{"body": body}]} if use_parent else {"body": body}
    assert not factual_issues("Ink Cash and Ink Unlimited targeted offers.", [row])


@pytest.mark.parametrize("names", ["Ink Cash, Unlimited and Preferred", "CIC / CIU / CIP",
                                   "Ink Cash & Unlimited & Preferred", "Ink Preferred and Cash and Unlimited"])
def test_explicit_three_variant_list_and_card_abbreviations(names):
    assert not factual_issues("Ink Cash, Ink Unlimited and Ink Preferred offers.", [{"body": names + "."}])


@pytest.mark.parametrize("body", [
    "Ink Cash and unlimited lounge access.", "Ink Cash & unlimited cashback.",
    "Ink Cash & unlimited support.", "Ink Cash and Unlimited Cash Rewards.",
    "Ink Cash and Chase Freedom Unlimited.", "Ink Cash and BofA Unlimited Cash Rewards.",
    "Ink Cash and Preferred Rewards.", "Ink Cash and Preferred Hotels.",
    "I have Ink Cash. Unlimited support is useful.", "Cash & Unlimited offers.",
])
def test_nearby_perks_or_other_cards_do_not_supply_a_missing_ink_identity(body):
    target = "Ink Preferred" if "Preferred" in body else "Ink Unlimited"
    assert any(target in issue for issue in factual_issues(target + " offer.", [{"body": body}]))


@pytest.mark.parametrize("before,after", [("Ink Cash &", "Unlimited"), ("Ink", "Unlimited"),
                                           ("Sapphire Reserve for", "Business")])
@pytest.mark.parametrize("use_parent", [False, True])
def test_identity_cannot_be_completed_across_separate_evidence_bodies(before, after, use_parent):
    sources = ([{"body": before, "parent_context": [{"body": after}]}] if use_parent else
               [{"body": before}, {"body": after}])
    target = "Sapphire Reserve for Business" if "Sapphire" in before else "Ink Unlimited"
    assert any(target in issue for issue in factual_issues(target + " offer.", sources))


def test_title_only_coordinated_names_cannot_supply_evidence():
    row = {"body": "I received a targeted offer.", "post_title": "Ink Cash & Unlimited offers"}
    assert factual_issues("Ink Cash and Ink Unlimited offers.", [row])


def test_coordinated_identity_and_shared_variant_retention_gates_agree():
    source = {"id": "t3_abc123", "author": "example_reporter", "source_kind": "submission", "subreddit": "example",
              "body": "My current offer is Ink Cash & Unlimited for $1200 back.",
              "permalink": "https://reddit.com/r/example/comments/abc123/offer/", "created": "2026-11-20 15:00 UTC"}
    note = {"text": "A user reports $1200 back offers for Ink Cash and Ink Unlimited.", "source_ids": [source["id"]]}
    doc = {"sections": [{"title": "Ink Cash and Ink Unlimited offers", "bullets": [
        {"text": note["text"], "source_ids": [source["id"]]}]}]}
    _, issues = render_checked(doc, [source], minimum_sections=1, factual_checks=True,
                               digest_date=datetime(2026, 11, 20),
                               edition_cutoff=datetime(2026, 11, 20, 23, 30, tzinfo=timezone.utc))
    assert not issues
    assert not retention_issues(doc, [note], [source])
    doc["sections"][0]["bullets"][0]["text"] = "A user reports an Ink Cash offer for $1200 back."
    assert any("Ink Unlimited" in issue for issue in retention_issues(doc, [note], [source]))
