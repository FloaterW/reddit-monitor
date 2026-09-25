"""Regression cases from delivered September 20-22 newsletters; no network."""

import json
from unittest.mock import patch

import pytest

import daily_digest as digest
import verified_digest as verified
from evaluate_digest import evaluate


def source(body="The credits arrived."):
    return {"id": "t1_a", "author": "alice", "body": body,
            "post_permalink": "https://reddit.com/r/test/comments/p/topic/"}


def document(text, position="end"):
    return {"sections": [{"title": "💳 Update", "bullets": [
        {"text": text, "source_ids": ["t1_a"], "source_position": position, "indent": 0}]}]}


@pytest.mark.parametrize("text", ["reports credits arrived.", "Reports credits arrived.",
    "**reports** credits arrived.", "notes a change.", "confirms approval.", "asks about eligibility."])
def test_subjectless_end_citations_are_rejected(text):
    result, errors = verified.render_checked(document(text), [source()])
    assert result is None and any("subject" in e for e in errors)


@pytest.mark.parametrize("text,position", [("reports credits arrived.", "start"),
    ("A cardholder reports credits arrived.", "end"), ("**Credit timing:** Credits arrived.", "end"),
    ("Reports of missing credits remain unresolved.", "end"), ("Check the terms before applying.", "end")])
def test_valid_sentences_and_leading_attribution_are_retained(text, position):
    assert not verified.render_checked(document(text, position), [source()])[1]


@pytest.mark.parametrize("text", ["There was roughly a **3-4** wait for an agent.",
    "There was a 3–4 wait.", "The wait was 3 to 4.", "Waited about 3-4 for an agent.",
    "An agent answered after a 3 wait."])
def test_missing_wait_units_are_rejected(text):
    assert verified.render_checked(document(text), [source(text)])[1]


@pytest.mark.parametrize("text", ["The wait was long; no duration was specified.",
    "A 3-4 wait was reported (units unspecified).", "Waited 3–4 hours for an agent.",
    "A $3-4 credit is pending.", "Waited for the 3-4 offer to appear.",
    "The wait was 3-4 days.", "Waited 3 days and 4 hours."])
def test_clear_or_explicitly_ambiguous_waits_are_allowed(text):
    assert not verified.render_checked(document(text), [source(text)])[1]


def test_repair_cannot_invent_hours_for_unitless_source():
    text = "Waited 3-4 hours for an agent."
    assert verified.render_checked(document(text), [source("There is a 3-4 wait for an agent.")])[1]


@pytest.mark.parametrize("prefix", ["alice", "u/alice", "ALICE"])
def test_leading_link_replaces_exact_repeated_author(prefix):
    rendered, errors = verified.render_checked(document(f"{prefix} reports credits arrived.", "start"), [source()])
    assert not errors
    assert ") reports credits arrived." in rendered


def test_author_removal_preserves_other_people_and_end_cited_sentences():
    for text, position in [("bob reports credits arrived.", "start"),
                           ("alice reports credits arrived.", "end"),
                           ("alice accounts are different.", "start")]:
        rendered, errors = verified.render_checked(document(text, position), [source()])
        assert not errors and text in rendered


@pytest.mark.parametrize("verb", ["estimates", "lists", "booked", "claims"])
def test_september_23_redundant_subject_variants(verb):
    rendered, errors = verified.render_checked(document(f"alice {verb} an award.", "start"), [source()])
    assert not errors and f") {verb} an award." in rendered


def test_wait_unit_spelling_and_dash_variants_are_equivalent():
    assert not verified.render_checked(document("Waited 3–4 hours for an agent."), [source("A 3-4 hr wait.")])[1]


def test_evaluation_catches_rendered_fragment_and_missing_units():
    text = "# Digest\n\n- reports a 3-4 wait. [u/alice](https://reddit.com/r/test/comments/p/topic/a/)"
    assert not evaluate(text, [source("A 3-4 wait.")])["editorial_quality"]["passed"]


def test_bounded_model_repair_resolves_fragment():
    replies = [document("reports credits arrived."), document("A cardholder reports credits arrived.")]
    def invoke(prompt, **kwargs):
        return json.dumps(replies.pop(0))
    notes = [{"topic": "Credits", "text": "Credits arrived.", "source_ids": ["t1_a"]}]
    result = verified.summarize_verified([source()], None, invoke, recovered_notes=notes)
    assert "A cardholder reports" in result and not replies


def test_persistent_fragment_fails_without_sending_email(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps([source()]))
    bad = "# Digest\n\n- reports credits arrived. [u/alice](https://reddit.com/r/test/comments/p/topic/a/)"
    with patch.object(digest, "summarize", return_value=bad), patch.object(digest, "send_email") as send:
        code = digest.main(["--from-json", str(raw), "--no-db", "--source-safe", "--quality", "strict",
                           "--save", str(tmp_path / "digest.md"), "--status-file", str(tmp_path / "status.json"),
                           "--quiet-summary"])
    assert code == 3
    send.assert_not_called()
