"""Repair local bookkeeping after a durably recorded send, never resend mail."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from delivery_guard import _key, hash_content


def receipt_path(ledger, monitor, edition_date, destination_key):
    identity = json.dumps([monitor, edition_date, destination_key], separators=(",", ":"))
    name = hashlib.sha256(identity.encode()).hexdigest() + ".json"
    return Path(ledger).parent / ".delivery-receipts" / name


def save_receipt(ledger, delivery_key, summary, *, status_path, db_path, started_at,
                 digest_path, raw_json_path, evaluation_path, quality_status,
                 collection_warnings, time_filter, posts, raw_comment_count):
    """Persist artifact references before SMTP. This file alone proves no send."""
    from daily_digest import atomic_write_json

    monitor, edition, destination = _key(**delivery_key)
    raw = Path(raw_json_path).resolve() if raw_json_path else None
    digest = Path(digest_path).resolve()
    if hash_content(digest.read_text(encoding="utf-8")) != hash_content(summary):
        raise RuntimeError("Saved digest changed before delivery")
    payload = {"version": 1, "monitor": monitor, "edition_date": edition,
               "destination_key": destination, "content_hash": hash_content(summary),
               "status_path": str(Path(status_path).resolve()),
               "db_path": str(Path(db_path).resolve()) if db_path else None,
               "digest_path": str(digest), "raw_json_path": str(raw) if raw else None,
               "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest() if raw and raw.is_file() else None,
               "started_at": started_at.isoformat(), "quality_status": quality_status,
               "evaluation_path": str(Path(evaluation_path).resolve()) if evaluation_path else None,
               "collection_warnings": collection_warnings, "time_filter": time_filter,
               "posts": posts, "raw_comment_count": raw_comment_count}
    atomic_write_json(receipt_path(ledger, monitor, edition, destination), payload)


def reconcile_sent(ledger, record, *, status_path, db_path=None):
    """Called under edition_lock; ledger acceptance remains the authority.

    Never write an unrelated/newer edition's status or use mismatched artifact
    content. Receipts contain no credentials. A missing/stale receipt still
    blocks duplicate delivery; it merely prevents automatic bookkeeping repair.
    """
    from daily_digest import (
        _load_comments_json,
        _previously_sent_edition,
        _save_run_history,
        write_run_status,
    )

    if record.get("state") != "sent":
        return False
    try:
        path = receipt_path(ledger, record["monitor"], record["edition_date"], record["destination_key"])
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict):
            raise ValueError("Receipt must be an object")
        keys = ("monitor", "edition_date", "destination_key", "content_hash")
        if receipt.get("version") != 1 or any(receipt.get(key) != record[key] for key in keys):
            raise ValueError("Receipt does not match the sent ledger entry")
        summary = Path(receipt["digest_path"]).read_text(encoding="utf-8")
        if hash_content(summary) != record["content_hash"]:
            raise ValueError("Saved digest no longer matches the sent message")
        started = datetime.fromisoformat(receipt["started_at"])
        if started.tzinfo is None:
            raise ValueError("Receipt start time has no timezone")
        completed_status = ("completed_with_warnings"
                            if receipt["quality_status"] == "failed" or receipt["collection_warnings"]
                            else "completed")
        warning, history_repaired = None, False
        if db_path and receipt.get("db_path") == str(Path(db_path).resolve()):
            try:
                previous = _previously_sent_edition([db_path], record["monitor"], record["edition_date"])
                if previous is not None and hash_content(previous) != record["content_hash"]:
                    raise ValueError("Run history conflicts with the sent receipt")
                if previous is None:
                    raw_path = receipt.get("raw_json_path")
                    if not raw_path or hashlib.sha256(Path(raw_path).read_bytes()).hexdigest() != receipt.get("raw_sha256"):
                        raise ValueError("Original sources are unavailable or changed")
                    _save_run_history(db_path=db_path, monitor_name=record["monitor"],
                        time_filter=receipt["time_filter"], posts=receipt["posts"],
                        comments=_load_comments_json(raw_path), summary=summary,
                        digest_path=receipt["digest_path"], raw_json_path=raw_path,
                        raw_comment_count=receipt["raw_comment_count"], started_at=started,
                        status=completed_status,
                        email_status="sent", quality_status=receipt["quality_status"], error=None)
                    history_repaired = True
            except Exception as exc:
                warning = "Email delivered; database history reconciliation is still needed"
                print(f"[WARN] {warning} ({type(exc).__name__}).")
        target = Path(status_path).resolve()
        if receipt.get("status_path") != str(target):
            return history_repaired
        current = None
        if target.exists():
            try:
                current = json.loads(target.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                return history_repaired  # Cannot establish ownership of a corrupt status.
            if (not isinstance(current, dict) or current.get("monitor") != record["monitor"]
                    or current.get("edition_date") != record["edition_date"]):
                return history_repaired
        if (current and current.get("email_status") == "sent"
                and current.get("status") in {"completed", "completed_with_warnings"}
                and not history_repaired):
            return False  # Ordinary repeated commands remain true no-ops.
        write_run_status(target, started_at=started, monitor_name=record["monitor"],
            edition_date=record["edition_date"], status="completed_with_warnings" if warning else completed_status,
            exit_code=0, digest_path=receipt["digest_path"], raw_json_path=receipt["raw_json_path"],
            email_status="sent", quality_status=receipt["quality_status"], evaluation_path=receipt["evaluation_path"],
            collection_warnings=receipt["collection_warnings"], error=warning)
        print("[OK] Reconciled previously sent edition; no email submitted.")
        return True
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"[WARN] Sent edition receipt cannot be reconciled ({type(exc).__name__}); duplicate delivery remains blocked.")
        return False
