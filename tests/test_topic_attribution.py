"""Title-only context is a discussion label, not proof of product identity."""

import pytest

from topic_attribution import topic_attribution_issues


def source(title, body, parents=(), **extra):
    return {"post_title": title, "body": body, "parent_context": list(parents), **extra}


CASES = [
    ("Fidelity Visa limited time offer", "Mine is: Make 10 transactions of $10 or more and earn a $10 statement credit.",
     "Fidelity Visa targeted transaction promotion", "Fidelity Visa"),
    ("Barclays nightmare: how to verify", "I faxed documents; the security manager reviewed the scans and said approved.",
     "Barclays verification escalation succeeded", "Barclays"),
    ("Capital One offering to combine limits across cards", "I saw a website banner offering to transfer $1500 limit to another card.",
     "Capital One website limit-transfer prompt", "Capital One"),
    ("New Bilt Palladium is a more direct competitor", "She sees the $400 hotel credit; there is no semiannual restriction on this new card.",
     "Reports the new Palladium has a $400 hotel credit without a semiannual restriction.", "Bilt Palladium"),
]


@pytest.mark.parametrize("title,body,text,name", CASES)
def test_actual_title_only_identity_assertions_are_flagged(title, body, text, name):
    issues = topic_attribution_issues(text, [source(title, body)])
    assert len(issues) == 1
    assert name in issues[0]


@pytest.mark.parametrize("title,body,text,name", CASES)
def test_body_and_actual_parent_support_are_allowed(title, body, text, name):
    assert not topic_attribution_issues(text, [source(title, f"My {name} card. {body}")])
    assert not topic_attribution_issues(text, [source(title, body, [{"body": f"My {name} card."}])])


@pytest.mark.parametrize("text", [
    "In a Fidelity Visa discussion, a commenter reports a targeted transaction promotion.",
    "Fidelity Visa discussion: reported transaction promotion",
    "Discussion: Fidelity Visa — a member's targeted transaction terms",
    "A comment in a thread about Fidelity Visa reports $10 for ten transactions.",
    "The thread is about Fidelity Visa; the comment reports a targeted promotion.",
    "Reports a promotion in the Fidelity Visa card discussion.",
    "In a **Fidelity Visa** discussion, a commenter reports a targeted promotion.",
])
def test_explicit_local_discussion_attribution_is_allowed(text):
    title, body, _, _ = CASES[0]
    assert not topic_attribution_issues(text, [source(title, body)])


@pytest.mark.parametrize("text", [
    "In this discussion, Fidelity Visa offers $10 for ten transactions.",
    "Fidelity Visa offers $10. There is a discussion about it.",
    "Fidelity Visa offers $10. Unconfirmed.",
    "In a Fidelity Visa discussion, someone reports that Fidelity Visa offers the promotion.",
    "Fidelity Visa discussion: Fidelity Visa offers a $10 bonus.",
])
def test_disclaimers_and_earlier_safe_mentions_do_not_cover_later_assertions(text):
    title, body, _, _ = CASES[0]
    assert topic_attribution_issues(text, [source(title, body)])


def test_one_named_discussion_does_not_support_another_issuer():
    rows = [source("Fidelity Visa and Capital One discussion", "I saw a transaction promotion.")]
    issues = topic_attribution_issues("In a Fidelity Visa discussion, Capital One offers a transaction bonus.", rows)
    assert len(issues) == 1 and "Capital One" in issues[0]


def test_each_identity_can_be_attributed_to_its_own_discussion():
    rows = [source("Fidelity Visa and Capital One", "I saw a transaction promotion.")]
    text = "In a Fidelity Visa discussion, one member reports a bonus; a Capital One thread contains another report."
    assert not topic_attribution_issues(text, rows)


@pytest.mark.parametrize("title,body,text", [
    ("Capital One card changes", "I have a C1 card.", "Capital One credit-limit prompt"),
    ("C1 card changes", "I have a Capital One card.", "C1 credit-limit prompt"),
    ("Barclays changes", "My Barclay's card changed.", "Barclays verification"),
    ("Amex changes", "My American Express account.", "Amex changes"),
    ("Bank of America changes", "My BofA account.", "Bank of America changes"),
])
def test_equivalent_issuer_aliases_in_bodies_are_evidence(title, body, text):
    assert not topic_attribution_issues(text, [source(title, body)])


def test_network_specificity_requires_more_than_issuer_support():
    rows = [source("Fidelity Visa limited time offer", "My Fidelity card has a promotion.")]
    issues = topic_attribution_issues("Fidelity Visa targeted promotion", rows)
    assert len(issues) == 1 and "Fidelity Visa" in issues[0]


def test_known_parent_title_does_not_count_as_parent_body_evidence():
    rows = [source("Fidelity Visa limited time offer", "Mine is a targeted promotion.",
                   [{"post_title": "Fidelity Visa", "body": "What offer did you get?"}])]
    assert topic_attribution_issues("Fidelity Visa promotion", rows)


def test_other_cited_body_can_support_a_section_identity():
    rows = [source("Fidelity Visa", "My targeted promotion gives $10."),
            source("Other", "Fidelity Visa has a targeted promotion.")]
    assert not topic_attribution_issues("Fidelity Visa promotion", rows)


def test_bounded_patterns_do_not_guess_arbitrary_card_names_or_generic_aliases():
    rows = [source("Gold Platinum MS business card discussions", "I received a promotion.")]
    assert not topic_attribution_issues("Gold Platinum MS business offers", rows)
    assert not topic_attribution_issues("Unknown Card X offers a bonus", rows)


def test_missing_titles_and_bodies_do_not_crash_or_create_guessed_identity():
    assert not topic_attribution_issues("Fidelity Visa", [{"body": None, "parent_context": None}])
    assert not topic_attribution_issues("Unidentified card", [source("Fidelity Visa", "")])


def test_rendered_source_record_thread_and_hint_fields_are_supported():
    for field in ("thread", "discussion_topic_hint"):
        assert topic_attribution_issues("Palladium credit terms", [{field: "Bilt Palladium", "body": "My card has a credit."}])
