"""Accepted SMTP and bookkeeping failures must never create duplicate sends."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

import daily_digest as digest
from delivery_guard import get_delivery
from delivery_recovery import receipt_path


def setup(tmp_path, monkeypatch):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps([{"id": "t1_a", "body": "Received $50.", "author": "alice"}]), encoding="utf-8")
    monkeypatch.setattr(digest, "EMAIL_TO", "reader@example.com")
    monkeypatch.setattr(digest, "summarize", Mock(return_value="# Daily Digest — November 19, 2026\n\nReceived $50.\n"))
    send = Mock(return_value="sent")
    monkeypatch.setattr(digest, "send_email", send)
    args = ["--from-json", str(raw), "--digest-date", "2026-11-19", "--quality", "off",
            "--save", str(tmp_path/"digest.md"), "--status-file", str(tmp_path/"status.json"),
            "--delivery-ledger", str(tmp_path/"delivery.db"), "--db", str(tmp_path/"history.db"), "--quiet-summary"]
    key = {"monitor": "default", "edition_date": "2026-11-19", "destination": digest.EMAIL_TO}
    return args, send, key


def test_history_outage_is_a_delivery_warning_and_repaired_once(tmp_path, monkeypatch):
    args, send, key = setup(tmp_path, monkeypatch)
    original = digest._save_run_history
    monkeypatch.setattr(digest, "_save_run_history", Mock(side_effect=OSError("disk unavailable")))
    assert digest.main(args) == 0
    status = json.loads((tmp_path/"status.json").read_text())
    assert status["email_status"] == "sent" and status["status"] == "completed_with_warnings"
    assert "history" in status["error"]
    assert get_delivery(tmp_path/"delivery.db", **key)["attempts"] == 1
    monkeypatch.setattr(digest, "_save_run_history", original)
    assert digest.main(args) == 0
    restored = (tmp_path/"status.json").read_bytes()
    assert json.loads(restored)["error"] is None
    assert digest.main(args) == 0
    assert (tmp_path/"status.json").read_bytes() == restored
    with sqlite3.connect(tmp_path/"history.db") as conn:
        assert conn.execute("SELECT count(*) FROM runs WHERE email_status='sent'").fetchone() == (1,)
    send.assert_called_once()
    digest.summarize.assert_called_once()


def test_status_write_failure_is_reconciled_without_generation(tmp_path, monkeypatch):
    args, send, key = setup(tmp_path, monkeypatch)
    original = digest.write_run_status

    def fail_final(*args, **kwargs):
        if kwargs.get("email_status") == "sent":
            raise OSError("status unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(digest, "write_run_status", fail_final)
    assert digest.main(args) == 1
    assert json.loads((tmp_path/"status.json").read_text())["status"] == "running"
    monkeypatch.setattr(digest, "write_run_status", original)
    assert digest.main(args) == 0
    assert json.loads((tmp_path/"status.json").read_text())["email_status"] == "sent"
    send.assert_called_once()
    digest.summarize.assert_called_once()


@pytest.mark.parametrize("unavailable", ["history", "status"])
def test_reconciliation_preserves_warn_only_quality_failure(tmp_path, monkeypatch, unavailable):
    args, send, key = setup(tmp_path, monkeypatch)
    args[args.index("--quality") + 1] = "warn"
    monkeypatch.setattr("evaluate_digest.evaluate", lambda *a, **kw: {})
    monkeypatch.setattr("evaluate_digest.evaluation_passed", lambda *a: False)
    monkeypatch.setattr("evaluate_digest.format_report", lambda *a: "Synthetic quality warning")
    original_history, original_status = digest._save_run_history, digest.write_run_status
    if unavailable == "history":
        monkeypatch.setattr(digest, "_save_run_history", Mock(side_effect=OSError("history unavailable")))
    else:
        def fail_final(*a, **kw):
            if kw.get("email_status") == "sent":
                raise OSError("status unavailable")
            return original_status(*a, **kw)
        monkeypatch.setattr(digest, "write_run_status", fail_final)
    assert digest.main(args) == (0 if unavailable == "history" else 1)
    assert get_delivery(tmp_path/"delivery.db", **key)["state"] == "sent"
    monkeypatch.setattr(digest, "_save_run_history", original_history)
    monkeypatch.setattr(digest, "write_run_status", original_status)
    assert digest.main(args) == 0
    status = json.loads((tmp_path/"status.json").read_text())
    assert status["status"] == "completed_with_warnings" and status["quality_status"] == "failed"
    assert not status["collection_warnings"]  # Quality is the only remaining warning.
    with sqlite3.connect(tmp_path/"history.db") as conn:
        assert conn.execute("SELECT status, quality_status FROM runs WHERE email_status='sent'").fetchall() == [
            ("completed_with_warnings", "failed")]
    send.assert_called_once()
    digest.summarize.assert_called_once()


def test_old_edition_reconciliation_cannot_overwrite_newer_status(tmp_path, monkeypatch):
    args, send, key = setup(tmp_path, monkeypatch)
    assert digest.main(args) == 0
    newer = json.dumps({"monitor": "default", "edition_date": "2026-11-20", "status": "running"})
    (tmp_path/"status.json").write_text(newer)
    assert digest.main(args) == 0
    assert (tmp_path/"status.json").read_text() == newer
    send.assert_called_once()


def test_receipt_wrong_identity_or_changed_body_cannot_repair_status(tmp_path, monkeypatch):
    args, send, key = setup(tmp_path, monkeypatch)
    assert digest.main(args) == 0
    record = get_delivery(tmp_path/"delivery.db", **key)
    path = receipt_path(tmp_path/"delivery.db", record["monitor"], record["edition_date"], record["destination_key"])
    original = json.loads(path.read_text())
    status = json.dumps({"monitor": "default", "edition_date": "2026-11-19", "status": "failed"})
    for field, wrong in (("monitor", "other"), ("edition_date", "2026-11-18"),
                         ("destination_key", "different"), ("content_hash", "0"*64)):
        (tmp_path/"status.json").write_text(status)
        path.write_text(json.dumps({**original, field: wrong}))
        assert digest.main(args) == 0
        assert (tmp_path/"status.json").read_text() == status
    path.write_text(json.dumps(original))
    Path(original["digest_path"]).write_text("changed body")
    assert digest.main(args) == 0
    assert (tmp_path/"status.json").read_text() == status
    send.assert_called_once()


def test_uncertain_ledger_ignores_even_valid_receipt(tmp_path, monkeypatch):
    args, send, key = setup(tmp_path, monkeypatch)
    send.return_value = "uncertain"
    assert digest.main(args) == 5
    before = (tmp_path/"status.json").read_bytes()
    assert digest.main(args) == 5
    assert (tmp_path/"status.json").read_bytes() == before
    send.assert_called_once()
