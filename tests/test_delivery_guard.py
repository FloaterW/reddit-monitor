"""Durable duplicate-send protection using temporary ledgers, never SMTP."""

import multiprocessing
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone

import pytest

import delivery_guard as guard

_ARGS = {
    "monitor": "churning", "edition_date": "2026-11-04",
    "destination": "Reader@Example.com", "content_hash": guard.hash_content("synthetic body"),
}


def _claim_process(path):
    return guard.claim_delivery(path, **_ARGS).state


def _hold_lock_process(path, connection):
    with guard.edition_lock(path, **{k: v for k, v in _ARGS.items() if k != "content_hash"}):
        connection.send("locked")
        connection.recv()


def test_concurrent_processes_allow_only_one_claim(tmp_path):
    path = str(tmp_path / "deliveries.db")
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
        states = list(pool.map(_claim_process, [path] * 6))
    assert states.count("claimed") == 1
    assert states.count("uncertain") == 5


def test_pending_survives_restart_and_never_expires(tmp_path):
    path = tmp_path / "deliveries.db"
    first = guard.claim_delivery(path, **_ARGS, now=datetime(2026, 11, 4, tzinfo=timezone.utc))
    # A fresh connection years later still cannot infer whether SMTP accepted.
    later = guard.claim_delivery(path, **_ARGS, now=datetime(2036, 11, 4, tzinfo=timezone.utc))
    assert first.state == "claimed"
    assert later.state == "uncertain" and later.token is None
    assert later.message_id == first.message_id


def test_sent_blocks_changed_content_and_normalized_recipient(tmp_path):
    path = tmp_path / "deliveries.db"
    first = guard.claim_delivery(path, **_ARGS)
    guard.mark_sent(path, first)
    second = guard.claim_delivery(path, **{**_ARGS, "destination": " reader@example.COM ",
                                          "content_hash": guard.hash_content("rewritten body")})
    assert second.state == "sent" and second.token is None
    assert second.message_id == first.message_id
    assert second.content_hash == first.content_hash


@pytest.mark.parametrize("field,value", [
    ("monitor", "another"), ("edition_date", "2026-11-05"), ("destination", "other@example.com"),
])
def test_independent_editions_monitors_and_recipients_can_send(tmp_path, field, value):
    path = tmp_path / "deliveries.db"
    guard.mark_sent(path, guard.claim_delivery(path, **_ARGS))
    assert guard.claim_delivery(path, **{**_ARGS, field: value}).state == "claimed"


def test_explicit_failure_retry_changes_token_but_preserves_message_id(tmp_path):
    path = tmp_path / "deliveries.db"
    first = guard.claim_delivery(path, **_ARGS)
    guard.mark_retryable(path, first)
    retry = guard.claim_delivery(path, **_ARGS)
    assert retry.state == "claimed" and retry.token != first.token
    assert retry.message_id == first.message_id
    with pytest.raises(guard.StaleDeliveryClaim):
        guard.mark_sent(path, first)
    with pytest.raises(guard.StaleDeliveryClaim):
        guard.mark_retryable(path, first)
    guard.mark_sent(path, retry)
    assert guard.claim_delivery(path, **_ARGS).state == "sent"


def test_uncertain_is_not_automatically_retryable(tmp_path):
    path = tmp_path / "deliveries.db"
    claim = guard.claim_delivery(path, **_ARGS)
    guard.mark_uncertain(path, claim)
    assert guard.claim_delivery(path, **_ARGS).state == "uncertain"
    with pytest.raises(guard.StaleDeliveryClaim):
        guard.mark_retryable(path, claim)


def test_confirmed_acceptance_can_complete_uncertain_owned_claim(tmp_path):
    path = tmp_path / "deliveries.db"
    claim = guard.claim_delivery(path, **_ARGS)
    guard.mark_uncertain(path, claim)
    guard.mark_sent(path, claim)
    guard.mark_sent(path, claim)  # Idempotent outcome recording.
    assert guard.claim_delivery(path, **_ARGS).state == "sent"


def test_sent_cannot_be_released_and_blocked_claim_cannot_transition(tmp_path):
    path = tmp_path / "deliveries.db"
    claim = guard.claim_delivery(path, **_ARGS)
    guard.mark_sent(path, claim)
    with pytest.raises(guard.StaleDeliveryClaim):
        guard.mark_retryable(path, claim)
    blocked = guard.claim_delivery(path, **_ARGS)
    with pytest.raises(guard.StaleDeliveryClaim):
        guard.mark_sent(path, blocked)


def test_wrong_token_cannot_change_outcome(tmp_path):
    path = tmp_path / "deliveries.db"
    claim = guard.claim_delivery(path, **_ARGS)
    with pytest.raises(guard.StaleDeliveryClaim):
        guard.mark_retryable(path, replace(claim, token="not-the-owner"))
    assert guard.claim_delivery(path, **_ARGS).state == "uncertain"


def test_ledger_contains_no_body_or_email_address(tmp_path):
    path = tmp_path / "deliveries.db"
    claim = guard.claim_delivery(path, **_ARGS)
    guard.mark_sent(path, claim)
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT * FROM delivery_guard").fetchone()
    serialized = repr(row).lower()
    assert "synthetic body" not in serialized and "reader@example.com" not in serialized
    assert claim.content_hash in serialized
    assert claim.message_id.startswith("<") and claim.message_id.endswith(">")


def test_corrupt_ledger_fails_closed(tmp_path):
    path = tmp_path / "deliveries.db"
    path.write_text("not sqlite", encoding="utf-8")
    with pytest.raises(guard.DeliveryGuardError, match="must not be submitted"):
        guard.claim_delivery(path, **_ARGS)


def test_failed_outcome_write_leaves_pending_protection(tmp_path, monkeypatch):
    path = tmp_path / "deliveries.db"
    claim = guard.claim_delivery(path, **_ARGS)
    original_connect = guard._connect

    def unavailable(_):
        raise sqlite3.OperationalError("synthetic disk error")

    monkeypatch.setattr(guard, "_connect", unavailable)
    with pytest.raises(guard.DeliveryGuardError, match="do not resend"):
        guard.mark_sent(path, claim)
    monkeypatch.setattr(guard, "_connect", original_connect)
    assert guard.claim_delivery(path, **_ARGS).state == "uncertain"


@pytest.mark.parametrize("field,value", [
    ("monitor", ""), ("monitor", "bad\nname"), ("edition_date", "2026-11-31"),
    ("edition_date", "20261104"), ("destination", ""), ("destination", "not-an-address"),
    ("destination", "a@example.com,b@example.com"), ("destination", "a@example.com\nBcc:x@y.com"),
    ("content_hash", "not-a-hash"),
])
def test_invalid_claim_data_rejected_without_creating_ledger(tmp_path, field, value):
    path = tmp_path / "deliveries.db"
    with pytest.raises(ValueError):
        guard.claim_delivery(path, **{**_ARGS, field: value})
    assert not path.exists()


def test_naive_timestamp_rejected_without_creating_ledger(tmp_path):
    path = tmp_path / "deliveries.db"
    with pytest.raises(ValueError, match="timezone-aware"):
        guard.claim_delivery(path, **_ARGS, now=datetime(2026, 11, 4))
    assert not path.exists()


def test_get_delivery_is_read_only_and_does_not_create_absent_database(tmp_path):
    path = tmp_path / "nested" / "deliveries.db"
    key = {k: v for k, v in _ARGS.items() if k != "content_hash"}
    assert guard.get_delivery(path, **key) is None
    assert not path.parent.exists()
    claim = guard.claim_delivery(path, **_ARGS)
    before = path.read_bytes()
    record = guard.get_delivery(path, **key)
    assert record["state"] == "pending" and record["token"] == claim.token
    assert path.read_bytes() == before


def test_edition_lock_releases_on_normal_failure_without_pending_submission(tmp_path):
    path = tmp_path / "deliveries.db"
    key = {k: v for k, v in _ARGS.items() if k != "content_hash"}
    with pytest.raises(RuntimeError, match="generation failed"):
        with guard.edition_lock(path, **key):
            raise RuntimeError("generation failed")
    assert not path.exists()
    with guard.edition_lock(path, **key):
        assert guard.get_delivery(path, **key) is None


def test_edition_lock_excludes_process_and_releases_after_process_death(tmp_path):
    path = str(tmp_path / "deliveries.db")
    key = {k: v for k, v in _ARGS.items() if k != "content_hash"}
    context = multiprocessing.get_context("spawn")
    parent_pipe, child_pipe = context.Pipe()
    process = context.Process(target=_hold_lock_process, args=(path, child_pipe))
    process.start()
    try:
        assert parent_pipe.poll(15), "child failed to acquire edition lock"
        assert parent_pipe.recv() == "locked"
        with pytest.raises(guard.DeliveryBusy):
            with guard.edition_lock(path, **key):
                pytest.fail("concurrent work was allowed")
        claim = guard.claim_delivery(path, **_ARGS)
        with pytest.raises(guard.DeliveryBusy):
            guard.resolve_delivery(path, **key, expected_token=claim.token,
                                   outcome="retryable", reason="Synthetic attempted reset")
    finally:
        process.terminate()
        process.join(timeout=10)
        parent_pipe.close()
        child_pipe.close()
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
    assert not process.is_alive()
    with guard.edition_lock(path, **key):
        # The OS lock is gone, but the possibly-submitted ledger record remains.
        assert guard.claim_delivery(path, **_ARGS).state == "uncertain"


@pytest.mark.parametrize("outcome", ["sent", "retryable"])
def test_explicit_reconciliation_requires_token_and_records_reason(tmp_path, outcome):
    path = tmp_path / "deliveries.db"
    key = {k: v for k, v in _ARGS.items() if k != "content_hash"}
    claim = guard.claim_delivery(path, **_ARGS)
    guard.mark_uncertain(path, claim)
    with pytest.raises(guard.StaleDeliveryClaim):
        guard.resolve_delivery(path, **key, expected_token="old-token", outcome=outcome,
                               reason="Synthetic verification")
    assert guard.get_delivery(path, **key)["state"] == "uncertain"
    guard.resolve_delivery(path, **key, expected_token=claim.token, outcome=outcome,
                           reason="Synthetic external delivery verification")
    assert guard.get_delivery(path, **key)["state"] == outcome
    with sqlite3.connect(path) as conn:
        recorded = conn.execute("SELECT outcome, reason FROM delivery_reconciliations").fetchall()
    assert recorded == [(outcome, "Synthetic external delivery verification")]
    # No stale reconciliation may undo a resolved decision.
    with pytest.raises(guard.StaleDeliveryClaim):
        guard.resolve_delivery(path, **key, expected_token=claim.token, outcome="retryable",
                               reason="Repeated reset")


@pytest.mark.parametrize("outcome,reason", [("force", "A reason"), ([], "A reason"),
                                         ("retryable", ""), ("retryable", "x" * 501)])
def test_invalid_reconciliation_rejected(tmp_path, outcome, reason):
    key = {k: v for k, v in _ARGS.items() if k != "content_hash"}
    with pytest.raises(ValueError):
        guard.resolve_delivery(tmp_path / "deliveries.db", **key, expected_token="token",
                               outcome=outcome, reason=reason)
