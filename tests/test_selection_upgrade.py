"""Offline regressions for bounded review, complete history, and local diagnostics."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from analysis_report import collection_diagnostics, write_report
from editorial_retention import identify_candidates, manufactured_spending_signal
from editorial_selection import (
    HISTORY_EDITION_CHAR_LIMIT,
    HISTORY_TOTAL_CHAR_LIMIT,
    plan_payload,
    priority_leads,
    protect_notes,
    recent_delivered_editions,
    validate_plan,
)
from storage import DigestDB


def source(body, identifier="a", **extra):
    return {"id": "t1_" + identifier, "body": body, "author": "sample",
            "subreddit": "churning", **extra}


@pytest.mark.parametrize("card,name", [("CIP", "Ink Preferred"), ("CIC", "Ink Cash"),
                                      ("CIU", "Ink Unlimited"), ("CSRB", "Sapphire Reserve for Business")])
@pytest.mark.parametrize("terms", ["100K SUB for $8K spend", "earn 100k when you spend $8k",
                                   "100,000 points after you spend $8,000"])
def test_natural_offer_terms_are_recognized_without_becoming_automatic_news(card, name, terms):
    rows = [source(f"{card} has {terms} in 3 months.")]
    lead, = priority_leads(rows)
    assert lead["card"] == name and lead["amounts"] == ["100000", "8000"]
    assert lead["months"] == "3" and lead["review_only"] and not lead["required"]
    assert "watchlist_offer_or_outcome" in identify_candidates(rows)[0]["categories"]


def test_recommendation_can_be_explicitly_rejected_without_losing_recognized_terms():
    # The Nov19 recommendation was a recognition gap, not proof of missed new news.
    rows = [source("Chase Ink Preferred Business has a 100K SUB for $8K spend. Which card should I get?")]
    leads = priority_leads(rows)
    notes = protect_notes([], leads, rows) + [{"topic": "Other", "text": "A distinct update.", "source_ids": ["t1_other"]}]
    payload = plan_payload(notes, leads, [])
    assert payload["optional_watchlist_note_ids"] == ["n0"] and not payload["priority_note_ids"]
    plan = {"stories": [
        {"topic": "Recommendation", "note_ids": ["n0"], "status": "low_value", "reason": "Routine question; no changed offer or new outcome established."},
        {"topic": "Other", "note_ids": ["n1"], "status": "new", "reason": "Distinct update."}]}
    assert validate_plan(plan, payload) == []
    plan["stories"][0]["reason"] = ""
    assert validate_plan(plan, payload)


@pytest.mark.parametrize("body", ["New targeted CIP offer: 100k SUB for $8k spend in 3 months.",
                                  "My app shows CIP earn 100k when you spend $8k in 3 months."])
def test_explicit_offer_sighting_uses_existing_priority_recovery(body):
    rows = [source(body)]
    lead, = priority_leads(rows)
    assert lead["required"] and not lead["review_only"]
    assert protect_notes([], [lead], rows)
    # Deterministic protection already covers this fact; no redundant watchlist review.
    assert not any("watchlist_offer_or_outcome" in c["categories"] for c in identify_candidates(rows))


@pytest.mark.parametrize("body", ["Would the new CIP 100k SUB for $8k spend be best?",
                                  "New CIP 100k SUB for $8k spend?",
                                  "The previous targeted CIP 100k SUB for $8k spend expired."])
def test_hypothetical_or_historical_natural_terms_remain_review_only(body):
    assert priority_leads([source(body)])[0]["review_only"]


def test_offer_products_periods_dates_and_nonoffer_amounts_are_not_pooled():
    leads = priority_leads([source("CIP 100k SUB for $8k spend in 3 months and CSRB 200k SUB for $30k spend in 6 months.")])
    assert [(r["card"], r["amounts"], r["months"]) for r in leads] == [
        ("Ink Preferred", ["100000", "8000"], "3"),
        ("Sapphire Reserve for Business", ["200000", "30000"], "6")]
    for text in ("CIP plans for 2026/2027", "CIP: 100k for $8k valuation", "CIP approved at 3/24", "CIP costs $1000 for $8000 income"):
        assert not priority_leads([source(text)])
    assert not priority_leads([source("CIP 100k SUB for $8k spend", context_only=True)])


@pytest.mark.parametrize("body", ["BFMR payout delayed for my Amazon order.",
                                  "My buying group changed its payment limits.",
                                  "My VGC reload was declined.", "MS gift card liquidation fees changed."])
def test_ms_operational_signals_get_bounded_review(body):
    assert manufactured_spending_signal(body)
    assert "manufactured_spending_operation" in identify_candidates([source(body)])[0]["categories"]


@pytest.mark.parametrize("body", ["Amazon Prime is great.", "Best Buy payments are delayed.",
                                  "Prime Day orders were canceled.", "MS announced earnings.",
                                  "My MS medication payment was declined.", "Buying group question: which one?"])
def test_merchant_or_ambiguous_ms_chatter_is_not_an_operational_candidate(body):
    assert not manufactured_spending_signal(body)
    assert not identify_candidates([source(body)])


def test_new_omission_review_is_bounded_and_keeps_operational_priority():
    rows = [source("CIP 100k SUB for $8k spend", str(i)) for i in range(50)]
    rows += [source("BFMR payout delayed.", "operational")]
    found = identify_candidates(rows)
    assert len(found) == 24 and found[0]["source_id"] == "t1_operational"
    assert identify_candidates(rows, limit=0) == []


def test_compact_index_retrieves_old_relevant_whole_story_and_discloses_bounds(tmp_path):
    path = tmp_path / "history.db"
    db = DigestDB(path)
    cutoff = datetime(2026, 11, 20, 23, 30, tzinfo=timezone.utc)
    special = ("## Converted Citi Globe retention\n- A representative said March 2027.\n"
               "  - Retention: $50 or 3k miles for $1k spend; no supplied deadline. "
               "[u/sample](https://reddit.com/r/churning/comments/post/topic/special/)\n"
               "  - " + "Necessary context. " * 140)
    for offset in range(7):
        day = cutoff - timedelta(days=offset+1)
        body = "\n\n".join(f"## Unrelated topic {i}\n- Parent {i}. " + "Long explanation. "*55 + "\n  - Full qualifier."
                            for i in range(35))
        if offset == 6:
            body += "\n\n" + special
        db.save_run("cards", "day", 100, [], digest_md=f"# Daily Digest — {day:%B %d, %Y}\n{body}",
                    started_at=day, completed_at=day+timedelta(minutes=10), status="completed", email_status="sent")
    db.close()
    before = path.read_bytes()
    legacy = recent_delivered_editions(path, "cards", cutoff)
    targeted = recent_delivered_editions(path, "cards", cutoff, current_sources=[source("Converted Citi Globe retention question")])
    assert path.read_bytes() == before
    assert not any("Retention: $50" in text for edition in legacy for text in edition["items"])
    assert any("Retention: $50" in text and "March 2027" in text and "no supplied deadline" in text
               for text in targeted[-1]["items"])
    assert len(json.dumps(targeted, ensure_ascii=False)) <= HISTORY_TOTAL_CHAR_LIMIT
    assert sum(len(e["story_index"]) for e in targeted) == 246
    for edition in targeted:
        assert edition["items"] and edition["coverage"]["index_fields_partial"]
        assert edition["coverage"]["index_complete"]
        assert "not evidence" in edition["coverage"]["note"]
        assert sum(map(len, edition["items"])) <= HISTORY_EDITION_CHAR_LIMIT
        assert len(edition["items"]) == len(edition["provenance"])
        assert not edition["coverage"]["published_stories_complete"]
        for item in edition["items"]:
            assert "\n  - " in item  # Whole story, never a clipped parent without its qualifier.


def test_empty_targeted_history_is_compatible(tmp_path):
    path = tmp_path / "empty.db"
    DigestDB(path).close()
    assert recent_delivered_editions(path, "cards", datetime(2026, 11, 20, tzinfo=timezone.utc), current_sources=[]) == []


def test_index_overflow_is_disclosed_and_whole_story_payload_stays_bounded():
    from published_story_memory import populate_history
    groups = [[{"topic": f"Edition {day} topic {i} " + "long title "*8,
                "text": "## Topic\n- " + "Detail. "*300 + "\n  - Required qualification.",
                "source_ids": [], "authors": []} for i in range(150)] for day in range(7)]
    editions = [{"items": [], "provenance": [], "coverage": {"story_count": len(g)}} for g in groups]
    result = populate_history(editions, groups, [], edition_limit=12000, total_limit=60000)
    assert len(json.dumps(result, ensure_ascii=False)) <= 60000
    assert all(0 < len(e["story_index"]) < 150 for e in result)
    assert all(not e["coverage"]["index_complete"] for e in result)
    assert all(e["coverage"]["index_story_count"] + e["coverage"]["index_omitted_story_count"] == 150 for e in result)
    assert all("Required qualification." in item for e in result for item in e["items"])


def test_background_only_text_cannot_bias_retrieval_but_current_parent_id_can():
    from published_story_memory import populate_history
    groups = [[{"topic": title, "text": title + "\n- " + "Context. "*220 + "\n  - Qualifier.",
                "source_ids": [sid], "authors": ["sample"]}
               for title, sid in (("First unrelated topic", "t1_first"), ("Old special retention", "t1_special"))]]
    def retrieve(rows):
        editions = [{"items": [], "provenance": [], "coverage": {"story_count": 2}}]
        return populate_history(editions, groups, rows, edition_limit=3000, total_limit=16000)[0]
    background = source("Old special retention", context_only=True)
    assert retrieve([background])["items"][0].startswith("First unrelated")
    current_reply = source("Here is an update.", parent_context=[{"id": "t1_special"}])
    edition = retrieve([background, current_reply])
    assert edition["items"][0].startswith("Old special retention")
    assert edition["provenance"][0]["source_ids"] == ["t1_special"]
    assert edition["provenance"][0]["authors"] == ["sample"]


def test_local_diagnostics_separate_context_and_escape_untrusted_names(tmp_path):
    rows = [source("CIP 100k SUB for $8k spend", context_status="partial"),
            source("A post", "post", source_kind="submission", subreddit="<script>x</script>"),
            source("CIP 200k SUB for $30k spend", "old", context_only=True),
            source("Amazon Prime", "other", subreddit="BiltRewards", context_status="complete")]
    diagnostics = collection_diagnostics(rows, extraction_batches=[rows])
    assert diagnostics["source_count"] == 4
    assert diagnostics["current_source_count"] == 3 and diagnostics["background_source_count"] == 1
    assert diagnostics["current_comment_context"] == {"complete": 1, "partial": 1}
    assert diagnostics["watchlist_current_body_mentions"]["Ink Preferred"] == 1
    assert diagnostics["watchlist_review_only_leads"] == 1
    assert diagnostics["extraction"]["sources_per_batch"] == [4]
    from verified_digest import _source_records
    assert diagnostics["extraction"]["source_payload_characters"] == [len(json.dumps(_source_records(rows), ensure_ascii=False))]
    report = {"generated_at": "2026-11-20T12:00:00Z", "source_count": 4, "facts": [], "collection_diagnostics": diagnostics}
    path = tmp_path / "report.html"
    write_report(path, report)
    html = path.read_text(encoding="utf-8")
    assert "Current edition coverage diagnostics" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))["collection_diagnostics"] == diagnostics
