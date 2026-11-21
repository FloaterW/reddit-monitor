"""Offline history, ordering and honest coverage-diagnostic regressions."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from editorial_selection import (
    HISTORY_EDITION_CHAR_LIMIT,
    HISTORY_TOTAL_CHAR_LIMIT,
    plan_batches,
    priority_leads,
    recent_delivered_editions,
    selection_report,
)
from evaluate_digest import check_completeness, evaluate, format_report
from storage import DigestDB


def save_edition(db, day, body, *, started=None, completed=None, **kwargs):
    day = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    started = started or day.replace(hour=23, minute=30)
    completed = completed or started + timedelta(minutes=10)
    return db.save_run(
        kwargs.pop("monitor", "cards"), "day", 100, [], started_at=started,
        completed_at=completed, status=kwargs.pop("status", "completed"),
        email_status=kwargs.pop("email_status", "sent"),
        digest_md=f"# Daily Digest — {day:%B %d, %Y}\n\n{body}", **kwargs,
    )


def history(path, date="2026-11-04T23:30:00+00:00"):
    return recent_delivered_editions(path, "cards", datetime.fromisoformat(date))


def cite(author="sample_user", identifier="abc123"):
    return f"[u/{author}](https://reddit.com/r/cards/comments/post1/topic/{identifier}/)"


RETENTION_STORY = (
    "## Converted Citi AAdvantage cards: product changes and retention\n\n"
    f"- {cite('sample_one', 'source1')} reports no retention offer on a converted Globe card.\n"
    f"- {cite('sample_two', 'source2')} reports no-fee product changes unavailable until March 2027; "
    "that is a representative's claim, not established policy.\n"
    f"  - **Retention:** $50 or 3k miles for $1k spend; no spending deadline supplied. {cite('sample_two', 'source2')}"
)


def test_history_keeps_complete_parent_nested_story_past_old_6000_limit(tmp_path):
    path = tmp_path / "history.db"
    db = DigestDB(path)
    preceding = "\n\n".join(f"## Earlier {i}\n- " + "Earlier distinct news. " * 40 for i in range(8))
    save_edition(db, "2026-11-03", preceding + "\n\n" + RETENTION_STORY)
    db.close()
    before = path.read_bytes()
    editions = history(path)
    assert path.read_bytes() == before
    edition = editions[0]
    item_index = next(i for i, text in enumerate(edition["items"]) if "Converted Citi" in text)
    text = edition["items"][item_index]
    assert "March 2027" in text and "$50 or 3k miles for $1k spend" in text
    assert "no spending deadline" in text and "not established policy" in text
    assert "\n  - **Retention:" in text
    assert edition["provenance"][item_index] == {
        "item_index": item_index, "source_ids": ["t1_source1", "t1_source2"],
        "authors": ["sample_one", "sample_two"],
    }
    assert edition["coverage"]["published_stories_complete"]
    assert not edition["coverage"]["raw_source_context_included"]


def test_oversized_story_is_omitted_whole_and_loss_disclosed(tmp_path):
    path = tmp_path / "history.db"
    db = DigestDB(path)
    body = "## Too large\n- Parent identity.\n  - " + "q" * HISTORY_EDITION_CHAR_LIMIT
    body += "\n\n## Small story\n- Distinct useful fact."
    save_edition(db, "2026-11-03", body)
    db.close()
    edition = history(path)[0]
    assert edition["items"] == ["## Small story\n- Distinct useful fact."]
    assert edition["coverage"]["omitted_story_count"] == 1
    assert edition["coverage"]["omitted_topics"] == ["Too large"]
    assert not edition["coverage"]["published_stories_complete"]


def test_total_serialized_budget_is_bounded_newest_first_without_partial_stories(tmp_path):
    path = tmp_path / "history.db"
    db = DigestDB(path)
    for offset in range(7):
        day = datetime(2026, 11, 3) - timedelta(days=offset)
        body = "\n\n".join(
            f"## Story {i} {'Long title ' * (i % 12)}\n- Parent {i} " + "text " * 160 +
            f"\n  - Qualification {i}. {cite(identifier=f'source{i}')}"
            for i in range(16)
        )
        save_edition(db, day.date().isoformat(), body)
    db.close()
    editions = history(path)
    assert len(editions) == 7
    assert len(json.dumps(editions, ensure_ascii=False)) <= HISTORY_TOTAL_CHAR_LIMIT
    assert len(editions[0]["items"]) > len(editions[-1]["items"])
    for edition in editions:
        assert sum(map(len, edition["items"])) <= HISTORY_EDITION_CHAR_LIMIT
        assert len(edition["items"]) == len(edition["provenance"])
        coverage = edition["coverage"]
        assert coverage["included_story_count"] + coverage["omitted_story_count"] == 16
        assert not coverage["published_stories_complete"]
        for text in edition["items"]:
            assert text.count("\n- Parent") == text.count("\n  - Qualification") == 1


def test_seven_prior_editions_survive_dst_and_report_gaps_and_duplicate_reruns(tmp_path):
    path = tmp_path / "history.db"
    db = DigestDB(path)
    for offset in range(7):
        day = datetime(2026, 11, 3) - timedelta(days=offset)
        # Before DST ends, the same local run has an earlier UTC time. A 7x24h
        # lookback at Nov 4 23:30 would incorrectly discard the Oct 28 edition.
        hour = 22 if day.month == 10 else 23
        save_edition(db, day.date().isoformat(), "## News\n- Old revision.",
                     started=day.replace(hour=hour, minute=30, tzinfo=timezone.utc))
    save_edition(db, "2026-11-03", "## News\n- Latest delivered revision.",
                 started=datetime(2026, 11, 4, 12, tzinfo=timezone.utc))
    db.close()
    editions = history(path)
    assert [e["edition_date"] for e in editions] == [
        "2026-11-03", "2026-11-02", "2026-11-01", "2026-10-31", "2026-10-30", "2026-10-29", "2026-10-28"]
    assert "Latest delivered revision" in editions[0]["items"][0]
    assert all(e["coverage"]["days_to_newer_edition"] == 1 for e in editions)
    assert all(e["coverage"]["lookback_days"] == 14 for e in editions)


def test_history_excludes_future_delivery_heading_and_stale_recovered_edition(tmp_path):
    path = tmp_path / "history.db"
    db = DigestDB(path)
    save_edition(db, "2026-11-02", "## News\n- Included.")
    save_edition(db, "2026-11-03", "## News\n- Not delivered at cutoff.",
                 completed=datetime(2026, 11, 5, tzinfo=timezone.utc))
    save_edition(db, "2026-11-05", "## News\n- Future printed edition.",
                 started=datetime(2026, 11, 3, tzinfo=timezone.utc))
    save_edition(db, "2026-10-01", "## News\n- Old recovered edition.",
                 started=datetime(2026, 11, 3, tzinfo=timezone.utc))
    db.close()
    editions = history(path)
    assert [e["edition_date"] for e in editions] == ["2026-11-02"]
    assert editions[0]["coverage"]["days_to_newer_edition"] == 2


def test_legacy_unheaded_parent_and_nested_lines_are_one_story(tmp_path):
    path = tmp_path / "history.db"
    db = DigestDB(path)
    save_edition(db, "2026-11-03", "- Parent.\n  - Its qualification.\n- Other parent.\n  - Other condition.")
    db.close()
    assert history(path)[0]["items"] == ["- Parent.\n  - Its qualification.", "- Other parent.\n  - Other condition."]


def test_real_november_3_local_history_replay_retains_citi_qualification(tmp_path):
    # Production artifacts stay ignored/private. CI uses the synthetic fixture
    # above; this additional local replay verifies the actual incident edition.
    saved = Path(__file__).resolve().parents[1] / "digest_20261103_1730.md"
    if not saved.is_file():
        pytest.skip("Private local delivered edition is not a repository fixture")
    path = tmp_path / "history.db"
    db = DigestDB(path)
    db.save_run("cards", "day", 100, [], digest_md=saved.read_text(encoding="utf-8"),
                started_at="2026-11-03T22:30:00+00:00", completed_at="2026-11-03T22:45:00+00:00",
                status="completed", email_status="sent")
    db.close()
    edition = history(path)[0]
    text = next(item for item in edition["items"] if "Converted Citi AAdvantage" in item)
    assert "**$50 or 3k miles for $1k spend**" in text
    assert "**March 2027**" in text and "no spending deadline" in text
    assert "t1_19s374gxjmicl" in next(p["source_ids"] for p in edition["provenance"]
                                      if p["item_index"] == edition["items"].index(text))


def test_whole_story_history_does_not_cross_assign_card_offer_terms():
    prior = [{"items": ["## CIP and CSRB offers\n"
                        "- Targeted CIP 125k after 8k in 3 months.\n"
                        "- Targeted CSRB 300k after 30k in 6 months."]}]
    def lead(text):
        return priority_leads([{"id": "t1_today", "body": text}], prior)[0]
    assert not lead("CIP targeted 125k/8k/3mo")["required"]
    assert lead("CIP targeted 300k/30k/6mo")["required"]
    assert not lead("CSRB targeted 300k/30k/6mo")["required"]
    assert lead("CSRB targeted 125k/8k/3mo")["required"]
    conflict = lead("CIP targeted 125k/8k/6mo")
    assert conflict["required"] and conflict["conflicting_period"]


def test_planner_order_keeps_substantive_updates_ahead_of_minor_new_items():
    payload = {"notes": [{"note_id": f"n{i}"} for i in range(5)], "priority_note_ids": ["n3", "n4"]}
    plan = {"stories": [{"topic": f"Topic {i}", "note_ids": [f"n{i}"], "status": status}
                        for i, status in enumerate(["update", "new", "deadline", "update", "new"])]}
    result = [n["note_id"] for batch in plan_batches(plan, payload, maximum=2) for n in batch]
    assert result == ["n3", "n4", "n0", "n1", "n2"]


def test_bilt_metrics_distinguish_body_heading_and_source_community():
    doc = {"sections": [
        {"title": "Bilt credits", "bullets": [{"text": "A credit posted.", "source_ids": ["t1_a"]},
                                                 {"text": "Obsidian fee reported.", "source_ids": ["t1_b"]}]},
        {"title": "Rental car benefit", "bullets": [{"text": "Not a Bilt card benefit.", "source_ids": ["t1_a", "t1_a", "t1_b"]}]},
        {"title": "Other", "bullets": [{"text": "biltmore and obsidians are not matches.", "source_ids": ["t1_unknown"]}]},
    ]}
    comments = [{"id": "t1_a", "subreddit": "BiltRewards"}, {"id": "t1_b", "subreddit": "churning"}]
    report = selection_report(doc, [], comments=comments)
    assert report["bilt_body_mention_bullets"] == report["bilt_bullets"] == 2
    assert report["bilt_heading_sections"] == 1 and report["bilt_heading_context_bullets"] == 2
    assert report["source_subreddit_bullets"] == {"biltrewards": 2, "churning": 2}
    assert report["source_subreddit_metrics_available"]
    assert not selection_report(doc, [])["source_subreddit_metrics_available"]


@pytest.mark.parametrize("scores", [[], [0, 0, 0], [None, None], [0, None, "unknown"]])
def test_missing_or_zero_scores_are_unavailable_not_arbitrary_completeness(scores):
    comments = [{"id": f"t1_{i}", "author": f"sample{i}", "body": "A useful observation.", "score": score}
                for i, score in enumerate(scores)]
    result = check_completeness("Unrelated digest", comments)
    assert not result["available"] and not result["required"]
    assert result["top_count"] == result["covered_count"] == result["missing_count"] == 0
    assert result["covered"] == result["missing"] == []
    rendered = format_report(evaluate("No numeric claim.", comments))
    assert "Completeness [UNAVAILABLE]" in rendered
    assert "top-scored comments represented" not in rendered


def test_available_scores_still_report_real_top_comment_coverage():
    comments = [{"author": "low", "score": 1}, {"author": "high", "score": 20}]
    result = check_completeness("u/high reports a change.", comments)
    assert result["available"] and result["covered"] == [{"author": "high", "score": 20}]
    assert result["top_count"] == 1 and result["passed"]
