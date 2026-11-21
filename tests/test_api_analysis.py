"""Evidence, retention, two-pass collection and workflow safety checks (offline)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

import collect_snapshot
import daily_digest as digest
import enhanced_collection as collection
from analysis_report import write_report
from intelligence_store import IntelligenceStore, scope_key
from offer_facts import extract_facts
from reddit_api import RedditAPIClient
from reddit_scraper import collection_diagnostics
from source_records import source_url_key
from verified_digest import _source_records, render_checked

NOW = datetime(2026, 11, 18, 17, 30, tzinfo=timezone.utc)
MONITOR = {"name": "test", "subreddits": ["test"], "keywords": ["CIU", "offer"],
           "posts_per_subreddit": 50, "time_filter": "day", "post_sort": "new", "title_filters": {}}


def source(sid="t1_a", body="CIU has a 100k offer after $6k spend in 3 months.", **kwargs):
    kind = "submission" if sid.startswith("t3_") else "comment"
    post = sid if kind == "submission" else "t3_post"
    url = f"https://reddit.com/r/test/comments/{post[3:]}/thread/"
    return {"id": sid, "source_kind": kind, "body": body, "author": "alice", "score": 1,
            "link_id": post, "parent_id": post if kind == "comment" else "",
            "post_title": "CIU offer", "post_permalink": url,
            "comment_permalink": url + sid[3:] + "/" if kind == "comment" else None,
            "created_utc": (NOW-timedelta(hours=1)).timestamp(),
            "created": (NOW-timedelta(hours=1)).strftime("%Y-%m-%d %H:%M UTC"), "subreddit": "test", **kwargs}


def submission_item(sid="post", *, when=NOW, body="100k offer after $6k spend", title="CIU offer"):
    return {"kind": "t3", "data": {"name": "t3_"+sid, "id": sid, "subreddit": "test",
        "created_utc": when.timestamp(), "selftext": body, "title": title, "author": "alice",
        "permalink": f"/r/test/comments/{sid}/thread/"}}


@pytest.fixture
def client():
    return RedditAPIClient("test-client", "test:digest:v1", session=Mock())


def test_submission_identity_and_original_title_are_evidence():
    row = source("t3_post")
    assert source_url_key(row) == "/r/test/comments/post/thread"
    record = _source_records([row])[0]
    assert record["title_is_source_evidence"] is True
    assert "title_is_source_evidence" not in _source_records([source()])[0]
    document = {"sections": [{"title": "💳 CIU offer", "bullets": [{
        "text": "Reports a **100k offer** after **$6k** spend in **3 months**.",
        "source_ids": [row["id"]], "source_position": "start", "indent": 0}]}]}
    markdown, errors = render_checked(document, [row], minimum_sections=1)
    assert not errors
    assert "https://reddit.com/r/test/comments/post/thread/)" in markdown
    assert "/t3_post/" not in markdown


@pytest.mark.parametrize("override", [
    {"id": "t3_other"}, {"subreddit": "other"}, {"post_permalink": "https://evil.test/r/test/comments/post/thread/"},
    {"post_permalink": "https://reddit.com/r/test/comments/post/thread/a/"},
    {"post_permalink": "https://reddit.com/r/test/comments/post/thread/?x=1"},
    {"post_permalink": "https://alice@reddit.com/r/test/comments/post/thread/"},
    {"post_permalink": "https://reddit.com:444/r/test/comments/post/thread/"},
    {"post_permalink": "https://reddit.com/r/test/comments/post/evil)injection/"},
])
def test_post_citation_forgeries_rejected(override):
    assert source_url_key({**source("t3_post"), **override}) is None


def test_post_pagination_full_body_boundary_and_title_filter(client, monkeypatch):
    body = "body " * 5000
    pages = [{"children": [submission_item(body=body)], "after": "t3_post"},
             {"children": [submission_item("old", when=NOW-timedelta(days=2))], "after": None}]
    get = Mock(side_effect=pages)
    monkeypatch.setattr(client, "_get", get)
    with collection_diagnostics() as warnings:
        rows = client.fetch_submissions("test", NOW-timedelta(days=1), NOW, title_filter="CIU")
    assert not warnings
    assert rows[0]["body"] == "CIU offer\n\n" + body
    assert get.call_args_list[1].args[2]["after"] == "t3_post"


def test_post_budget_warns_instead_of_claiming_complete(client, monkeypatch):
    monkeypatch.setattr(client, "_get", Mock(return_value={"children": [submission_item()], "after": "t3_post"}))
    with collection_diagnostics() as warnings:
        assert len(client.fetch_submissions("test", NOW-timedelta(days=1), NOW, max_pages=1)) == 1
    assert "page limit" in str(warnings)


@pytest.mark.parametrize("removed", ["[deleted]", "[removed]"])
def test_removed_submission_never_keeps_old_title_as_live_evidence(client, removed):
    row = client._submission(submission_item(body=removed), "test")
    assert row["content_removed"] and row["body"] == "[removed]"


def test_refresh_network_failure_is_not_deletion(client, monkeypatch):
    monkeypatch.setattr(client, "_get", Mock(return_value=None))
    assert client.refresh_sources([source("t3_post")]) == ({}, set(), {"t3_post"})
    monkeypatch.setattr(client, "_get", Mock(return_value={"children": []}))
    assert client.refresh_sources([source("t3_post")]) == ({}, {"t3_post"}, set())


def test_refresh_limit_explicitly_excludes_unverified_sources(client, monkeypatch):
    monkeypatch.setattr(client, "_get", Mock(return_value={"children": []}))
    rows = [source(f"t1_{i}") for i in range(101)]
    _, removed, failed = client.refresh_sources(rows, max_batches=1)
    assert len(removed) == 100 and len(failed) == 1


@pytest.mark.parametrize("body,expected", [
    ("CIU has a 100k offer after $6k spend in 3 months.", {"bonus_points": 100000, "spend_amount": 6000, "spend_period": "3 months"}),
    ("CIU targeted offer: 100k after $6,000 in 90 days via email.", {"bonus_points": 100000, "spend_amount": 6000, "spend_period": "90 days", "availability": "targeted", "channel": "email"}),
    ("CIU has a $750 bonus after $6k spend.", {"cash_bonus": 750, "spend_amount": 6000}),
    ("CIU 100k bonus. I have a $9k account balance.", {"bonus_points": 100000}),
    ("CIU 100k bonus after $6.5k spend.", {"bonus_points": 100000, "spend_amount": 6500}),
])
def test_offer_facts_preserve_values_spans_and_unknowns(body, expected):
    events = extract_facts(source(body=body))
    assert len(events) == 1
    assert events[0]["values"] == expected
    for field in events[0]["fields"].values():
        assert body[field["start"]:field["end"]] == field["text"]
    assert events[0]["evidence_status"] == "reddit_report_unverified"


@pytest.mark.parametrize("body", [
    "Does CIU have a 100k offer?", "CIU might have a 100k bonus.", "CIU does not have a 100k bonus.",
    "CIU has a 100k offer and CIP has a 120k offer.", "CIU offer went from 75k points to 100k points.",
    "CIU has a public 75k offer or targeted 100k bonus.", "CIU: I was not approved.",
    "CIU: I might get approved.", "CIU: I was pre-approved.", "CIU: my friend was approved.",
    "CIU: I heard you were approved.", "CIU: I will get my bonus.", "CIU: Did I get my bonus?",
    "I have a CSR. My price match claim was denied.",
    "I closed Quicksilver. I was denied. CSR couldn't give a shit.",
    "Palladium: I earned 17,000 points in addition to my sign up bonus.",
    "CSP: I got the bonus offer.",
    "CIU has a 750 bonus.",
])
def test_ambiguous_questions_negations_and_other_people_not_promoted(body):
    assert extract_facts(source(body=body)) == []


@pytest.mark.parametrize("body,kind", [("I was approved for CIU at 2/24.", "approved"),
    ("P2 was denied for CIU at 3/24.", "denied"), ("My CIU application is pending.", "pending"),
    ("I received my CIU bonus.", None), ("CIU: I received my bonus.", "bonus_paid")])
def test_personal_outcomes_are_reported_not_probabilities(body, kind):
    events = extract_facts(source(body=body))
    assert [e["kind"] for e in events] == ([kind] if kind else [])


def test_store_dedup_variants_independence_and_edits(tmp_path):
    with IntelligenceStore(tmp_path/"cache.db") as store:
        a, b = source(), source("t1_b")
        first = store.ingest("s", [a, b], NOW)
        assert [e["observation"] for e in first["events"]] == ["new_report", "matching_report"]
        report = store.report("s", NOW)
        assert report["facts"][0]["distinct_reporters"] == 1
        assert report["facts"][0]["report_count"] == 2
        assert store.ingest("s", [a], NOW)["events"][0]["observation"] == "same_observation"
        c = source("t1_c", body="CIU has a 90k offer after $6k spend.", author="bob")
        assert store.ingest("s", [c], NOW)["events"][0]["observation"] == "different_reported_terms"
        changed = {**c, "body": "CIU has an 80k offer after $6k spend."}
        assert store.ingest("s", [changed], NOW)["events"][0]["observation"] == "source_edited"
        assert store.report("s", NOW, verified_ids={a["id"]})["facts"][0]["report_count"] == 1
        assert "previous_collection_not_publication" in json.dumps(first)


def test_store_deletion_expiry_and_scope_remove_derived_content(tmp_path):
    path = tmp_path/"cache.db"
    with IntelligenceStore(path) as store:
        store.ingest("s", [source()], NOW)
        store.ingest("other", [source()], NOW)
        store.ingest("s", [source(body="[removed]")], NOW)
        assert store.report("s", NOW)["event_count"] == 0
        assert store.report("other", NOW)["event_count"] == 1
        store.prune(NOW+timedelta(hours=48))
        assert store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    assert b"alice" not in path.read_bytes()


def test_deleted_author_removes_old_attribution(tmp_path):
    path = tmp_path/"cache.db"
    with IntelligenceStore(path) as store:
        store.ingest("s", [source()], NOW)
        store.ingest("s", [source(author="[deleted]")], NOW)
        assert store.report("s", NOW)["event_count"] == 0
    assert b"alice" not in path.read_bytes()


def test_watermark_overlap_invalidation_and_configuration_isolation(tmp_path):
    start = NOW-timedelta(days=1)
    with IntelligenceStore(tmp_path/"cache.db") as store:
        store.record_window("s", "test", start, NOW, True)
        assert store.fetch_start("s", "test", start+timedelta(hours=6), NOW+timedelta(hours=6)) == NOW-timedelta(minutes=5)
        store.record_window("s", "test", start, NOW-timedelta(hours=2), True)
        assert store.fetch_start("s", "test", start, NOW+timedelta(hours=6)) == NOW-timedelta(minutes=5)
        store.remove("s", ["t1_a"])
        assert store.fetch_start("s", "test", start, NOW+timedelta(hours=6)) == start
    assert scope_key(MONITOR) != scope_key({**MONITOR, "keywords": ["changed"]})


def test_capacity_expiry_invalidate_completeness(tmp_path):
    with IntelligenceStore(tmp_path/"cache.db", max_sources=1) as store:
        store.record_window("s", "test", NOW-timedelta(days=1), NOW, True)
        result = store.ingest("s", [source(), source("t1_b")], NOW)
        assert result["capacity_evictions"] == 1
        assert not store.conn.execute("SELECT complete FROM windows").fetchone()[0]


class FakeClient:
    request_count = 3
    _ancestry_problem = staticmethod(RedditAPIClient._ancestry_problem)

    def __init__(self, rows, refresh=None):
        self.rows, self.refresh, self.starts = rows, refresh, []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def fetch_submissions(self, sub, start, end, **kwargs):
        self.starts.append(start)
        return [r for r in self.rows if r["source_kind"] == "submission"]

    def fetch_subreddit(self, *args, **kwargs):
        assert kwargs["include_submissions"]
        return [r for r in self.rows if r["source_kind"] == "comment"]

    def refresh_sources(self, rows, **kwargs):
        return self.refresh if self.refresh is not None else ({r["id"]: r for r in rows}, set(), set())


def setup_collector(monkeypatch, client):
    monkeypatch.setenv("DIGEST_REDDIT_SOURCE", "api")
    monkeypatch.setattr(RedditAPIClient, "from_environment", Mock(return_value=client))


def test_midday_evening_reuse_overlap_and_exact_cutoff(tmp_path, monkeypatch):
    a = source()
    noon = FakeClient([a, source("t3_post")])
    setup_collector(monkeypatch, noon)
    path = tmp_path/"cache.db"
    collection.collect_enhanced(MONITOR, path, window_end=NOW, now=NOW)
    end = NOW+timedelta(hours=6)
    b = source("t1_b", created_utc=(end-timedelta(hours=1)).timestamp(), created=(end-timedelta(hours=1)).strftime("%Y-%m-%d %H:%M UTC"))
    future = source("t1_future", created_utc=(end+timedelta(seconds=1)).timestamp(), created=end.strftime("%Y-%m-%d %H:%M UTC"))
    evening = FakeClient([b, future])
    setup_collector(monkeypatch, evening)
    stats = {}
    rows, context, report = collection.collect_enhanced(MONITOR, path, window_end=end, now=end, stats=stats)
    assert evening.starts == [NOW-timedelta(minutes=5)]
    assert {r["id"] for r in rows} == {"t1_a", "t1_b", "t3_post"}
    assert stats["cached_sources_reused"] == 2
    assert len(report["runs"]) == 2 and context["events"]
    assert "standalone post bodies are not collected" not in digest.collection_notice(stats["collection_warnings"])


@pytest.mark.parametrize("unavailable", [True, False])
def test_missing_cached_source_never_enters_newsletter(tmp_path, monkeypatch, unavailable):
    path = tmp_path/"cache.db"
    with IntelligenceStore(path) as store:
        store.ingest(scope_key(MONITOR), [source()], NOW)
    client = FakeClient([], ({}, {"t1_a"}, set()) if unavailable else ({}, set(), {"t1_a"}))
    setup_collector(monkeypatch, client)
    stats = {}
    rows, context, report = collection.collect_enhanced(MONITOR, path, window_end=NOW, now=NOW, stats=stats)
    assert not rows and not context["events"] and not report["facts"]
    with IntelligenceStore(path) as store:
        assert len(store.cached(scope_key(MONITOR), NOW)) == (0 if unavailable else 1)


def test_corrupt_optional_cache_uses_full_fresh_collection_without_destroying_it(tmp_path, monkeypatch):
    path = tmp_path/"cache.db"
    path.write_bytes(b"corrupt cache fixture")
    setup_collector(monkeypatch, FakeClient([source()]))
    stats = {}
    rows, _, _ = collection.collect_enhanced(MONITOR, path, window_end=NOW, now=NOW, stats=stats)
    assert rows and path.read_bytes() == b"corrupt cache fixture"
    assert "cache was unavailable" in digest.collection_notice(stats["collection_warnings"])


def test_old_parent_is_only_background_and_keeps_reply_context(tmp_path, monkeypatch):
    old = NOW-timedelta(days=2)
    parent = source("t3_post", created_utc=old.timestamp(), created=old.strftime("%Y-%m-%d %H:%M UTC"))
    child = source(body="This ends tomorrow.", post_title="Discussion")
    setup_collector(monkeypatch, FakeClient([parent, child]))
    rows, context, _ = collection.collect_enhanced(MONITOR, tmp_path/"cache.db", window_end=NOW, now=NOW)
    assert {r["id"] for r in rows} == {parent["id"], child["id"]}
    assert next(r for r in rows if r["id"] == parent["id"])["context_only"]
    assert not context["events"]


def test_snapshot_calls_no_model_or_mail_and_uses_separate_status(tmp_path, monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(collect_snapshot, "datetime", FixedDatetime)
    monkeypatch.setattr(collect_snapshot, "load_monitor", lambda _: MONITOR)
    setup_collector(monkeypatch, FakeClient([source()]))
    model, mail = Mock(side_effect=AssertionError("model forbidden")), Mock(side_effect=AssertionError("mail forbidden"))
    monkeypatch.setattr(digest, "summarize", model)
    monkeypatch.setattr(digest, "send_email", mail)
    assert collect_snapshot.main(["--monitor", "test", "--store", str(tmp_path/"cache.db")]) == 0
    assert (tmp_path/"snapshot-status.json").exists()
    assert not (tmp_path/"last_run_status.json").exists()
    model.assert_not_called()
    mail.assert_not_called()


def test_report_escapes_untrusted_content_and_cleanup_removes_exports(tmp_path, monkeypatch):
    path = tmp_path/"cache.db"
    with IntelligenceStore(path) as store:
        old = NOW-timedelta(hours=50)
        store.ingest(scope_key(MONITOR), [source(created_utc=old.timestamp(), created=old.strftime("%Y-%m-%d %H:%M UTC"))], NOW-timedelta(hours=49))
        report = store.report(scope_key(MONITOR), NOW-timedelta(hours=49))
        assert report["event_count"] == 1
    report["limitations"] = ['<script>alert("bad")</script>']
    write_report(tmp_path/"analysis.html", report, editorial={"stories": [{"topic": "<img src=x>", "reason": "test"}]})
    text = (tmp_path/"analysis.html").read_text(encoding="utf-8")
    assert "<script>" not in text and "<img " not in text
    monkeypatch.setattr(collect_snapshot, "load_monitor", lambda _: MONITOR)
    monkeypatch.setattr(collect_snapshot, "collect_enhanced", Mock(side_effect=AssertionError("network forbidden")))
    assert collect_snapshot.main(["--monitor", "test", "--store", str(path), "--cleanup-only"]) == 0
    assert json.loads((tmp_path/"analysis.json").read_text())["event_count"] == 0


def test_enhanced_main_uses_existing_email_renderer_and_quality_gate(tmp_path, monkeypatch):
    row = source()
    report = {"generated_at": NOW.isoformat(), "source_count": 1, "facts": []}
    monkeypatch.setattr(collection, "collect_enhanced", Mock(return_value=([row], {"events": []}, report)))
    model = Mock(return_value="# Daily Digest\n\nTest digest.")
    monkeypatch.setattr(digest, "summarize", model)
    send = Mock(side_effect=AssertionError("email forbidden"))
    monkeypatch.setattr(digest, "send_email", send)
    assert digest.main(["--monitor", "churning", "--intelligence-store", str(tmp_path/"cache.db"),
        "--source-safe", "--no-email", "--no-db", "--quality", "off", "--status-file", str(tmp_path/"status.json"),
        "--save", str(tmp_path/"digest.md"), "--quiet-summary"]) == 0
    assert model.call_args.kwargs["analysis_context"] == {"events": []}
    assert (tmp_path/"analysis.html").exists()
    send.assert_not_called()
