"""Durable, fail-closed guard against duplicate SMTP submissions.

A claim must commit before submitting mail. A pending claim never expires: a
process crash cannot tell us whether the server accepted the message. Only a
definitive failure before submission or an explicit SMTP rejection may become
retryable. This is duplicate prevention, not a promise of exactly-once delivery.
"""

import errno
import hashlib
import os
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import getaddresses
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery_guard (
    monitor TEXT NOT NULL,
    edition_date TEXT NOT NULL,
    destination_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'uncertain', 'retryable', 'sent')),
    token TEXT NOT NULL,
    message_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (monitor, edition_date, destination_key)
)
"""


class DeliveryGuardError(RuntimeError):
    """The ledger cannot safely permit or record a submission."""


class StaleDeliveryClaim(DeliveryGuardError):
    """A transition was attempted without ownership of the current claim."""


class DeliveryBusy(DeliveryGuardError):
    """Another process is already handling this edition and recipient."""


@dataclass(frozen=True)
class DeliveryClaim:
    """A newly owned claim, or evidence that an edition must not be resent.

    ``state`` is claimed, sent, or uncertain. Only a claimed result has a token.
    The recipient is represented by a hash; no email body or credentials are
    persisted. ``content_hash`` is the recorded version, which may differ from
    the caller's proposed content when submission is blocked.
    """

    monitor: str
    edition_date: str
    destination_key: str
    state: str
    token: str | None
    message_id: str
    content_hash: str


def hash_content(content):
    """Fingerprint the proposed email body without storing it in the ledger."""
    if not isinstance(content, str):
        raise ValueError("Delivery content must be a string")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def normalize_destination(destination):
    """Normalize the single recipient supported by the current SMTP sender."""
    if not isinstance(destination, str) or any(c in destination for c in "\r\n\x00"):
        raise ValueError("Delivery destination must be one email address")
    addresses = getaddresses([destination.strip()])
    if len(addresses) != 1:
        raise ValueError("Delivery destination must be one email address")
    address = addresses[0][1]
    if not re.fullmatch(r"[^\s@<>;,]+@[^\s@<>;,]+", address):
        raise ValueError("Delivery destination must be one email address")
    return address.casefold()


def _key(monitor, edition_date, destination):
    if not isinstance(monitor, str) or not monitor.strip() or any(c in monitor for c in "\r\n\x00"):
        raise ValueError("Delivery monitor must be a nonempty name")
    if not isinstance(edition_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", edition_date):
        raise ValueError("Delivery edition must use YYYY-MM-DD")
    date.fromisoformat(edition_date)
    destination_key = hashlib.sha256(normalize_destination(destination).encode("utf-8")).hexdigest()
    return monitor.strip(), edition_date, destination_key


def _timestamp(now):
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Delivery timestamps must be timezone-aware")
    return now.astimezone(timezone.utc).isoformat()


def _connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(_SCHEMA)
        return conn
    except BaseException:
        conn.close()
        raise


@contextmanager
def edition_lock(path, *, monitor, edition_date, destination):
    """Exclude concurrent edition work; the OS releases this lock on process death.

    Acquire before touching shared run artifacts/status, and hold through final
    bookkeeping. The durable ledger separately protects ambiguous SMTP outcomes.
    Lock files remain on disk: deleting one could let processes lock different
    inodes for the same edition. Their existence alone does not mean 'locked'.
    """
    key = _key(monitor, edition_date, destination)
    name = hashlib.sha256("\0".join(key).encode("utf-8")).hexdigest() + ".lock"
    lock_path = Path(path).parent / ".delivery-locks" / name
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise DeliveryBusy("Another process is already handling this digest edition") from exc
            raise
    except (OSError, sqlite3.Error) as exc:
        if handle is not None:
            handle.close()
        raise DeliveryGuardError("Edition lock is unavailable; do not start the digest") from exc
    except BaseException:
        if handle is not None:
            handle.close()
        raise
    try:
        yield
    finally:
        # Closing the OS handle releases flock/byte-range locks even on failure.
        handle.close()


def get_delivery(path, *, monitor, edition_date, destination):
    """Read recorded metadata without creating/modifying a database or claim.

    Returns a dictionary (including the token needed for explicit reconciliation)
    or None. A pending row remains pending; observing it must not change the
    active sender's state. Call under edition_lock for a send preflight.
    """
    key = _key(monitor, edition_date, destination)
    path = Path(path)
    if not path.exists():
        return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM delivery_guard WHERE monitor=? AND edition_date=? AND destination_key=?", key,
            ).fetchone()
            return dict(row) if row is not None else None
    except (OSError, sqlite3.Error) as exc:
        raise DeliveryGuardError("Delivery ledger cannot be read; do not submit email") from exc


def claim_delivery(path, *, monitor, edition_date, destination, content_hash, now=None):
    """Atomically claim an edition, or block a sent/possibly-sent edition.

    A changed content hash does not bypass a previous submission. A retryable
    attempt receives a new ownership token and preserves its original Message-ID.
    Ledger errors fail closed and must prevent the caller from sending email.
    """
    key = _key(monitor, edition_date, destination)
    if not isinstance(content_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", content_hash):
        raise ValueError("Delivery content_hash must be a SHA256 hexadecimal digest")
    stamp = _timestamp(now)
    try:
        with closing(_connect(path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM delivery_guard WHERE monitor=? AND edition_date=? AND destination_key=?",
                key,
            ).fetchone()
            if row is not None and row["state"] != "retryable":
                conn.commit()
                return DeliveryClaim(
                    *key, "sent" if row["state"] == "sent" else "uncertain", None,
                    row["message_id"], row["content_hash"],
                )
            token = uuid.uuid4().hex
            message_id = row["message_id"] if row else f"<{uuid.uuid4().hex}@reddit-digest.invalid>"
            if row is None:
                conn.execute(
                    "INSERT INTO delivery_guard (monitor, edition_date, destination_key, state, token, "
                    "message_id, content_hash, created_at, updated_at) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                    (*key, token, message_id, content_hash, stamp, stamp),
                )
            else:
                conn.execute(
                    "UPDATE delivery_guard SET state='pending', token=?, content_hash=?, "
                    "attempts=attempts+1, updated_at=? WHERE monitor=? AND edition_date=? AND destination_key=?",
                    (token, content_hash, stamp, *key),
                )
            conn.commit()
            return DeliveryClaim(*key, "claimed", token, message_id, content_hash)
    except (OSError, sqlite3.Error) as exc:
        raise DeliveryGuardError("Delivery ledger is unavailable; email must not be submitted") from exc


def _transition(path, claim, state, *, now=None):
    if not isinstance(claim, DeliveryClaim) or claim.state != "claimed" or not claim.token:
        raise StaleDeliveryClaim("Only the owner of a fresh delivery claim may change its outcome")
    stamp = _timestamp(now)
    # Ambiguous submissions cannot be made retryable by ordinary send handling.
    allowed = ("pending", "uncertain", "sent") if state == "sent" else ("pending",)
    placeholders = ",".join("?" for _ in allowed)
    try:
        with closing(_connect(path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                f"UPDATE delivery_guard SET state=?, updated_at=?, "
                f"sent_at=CASE WHEN ?='sent' THEN COALESCE(sent_at, ?) ELSE sent_at END "
                f"WHERE monitor=? AND edition_date=? AND destination_key=? AND token=? "
                f"AND state IN ({placeholders})",
                (state, stamp, state, stamp, claim.monitor, claim.edition_date,
                 claim.destination_key, claim.token, *allowed),
            )
            if cursor.rowcount != 1:
                raise StaleDeliveryClaim("Delivery claim no longer owns this transition; do not resend")
            conn.commit()
    except (OSError, sqlite3.Error) as exc:
        raise DeliveryGuardError("Delivery outcome could not be recorded; do not resend automatically") from exc


def mark_sent(path, claim, *, now=None):
    """Persist SMTP acceptance immediately, before unrelated run-history writes."""
    _transition(path, claim, "sent", now=now)


def mark_retryable(path, claim, *, now=None):
    """Release only after a definitive pre-submit failure or explicit rejection.

    Do not use this for a timeout/disconnect during or after SMTP DATA: the server
    may have accepted the email. Such outcomes must remain uncertain.
    """
    _transition(path, claim, "retryable", now=now)


def mark_uncertain(path, claim, *, now=None):
    """Block resubmission after an ambiguous transport/SMTP outcome."""
    _transition(path, claim, "uncertain", now=now)


def resolve_delivery(path, *, monitor, edition_date, destination, expected_token,
                     outcome, reason, now=None):
    """Explicitly reconcile an inactive ambiguous attempt, without sending mail.

    The operator must first independently determine whether SMTP accepted the
    email. ``outcome`` is 'sent' or 'retryable'; the latter authorizes a future
    ordinary run. An exact token, an explanation, and no active edition worker
    are required. Already-sent and retryable rows cannot be reset through this API.
    """
    key = _key(monitor, edition_date, destination)
    if not isinstance(outcome, str) or outcome not in {"sent", "retryable"}:
        raise ValueError("Reconciliation outcome must be sent or retryable")
    if not isinstance(expected_token, str) or not expected_token:
        raise ValueError("Reconciliation requires the recorded attempt token")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise ValueError("Reconciliation requires an explanation of at most 500 characters")
    stamp = _timestamp(now)
    with edition_lock(path, monitor=monitor, edition_date=edition_date, destination=destination):
        try:
            with closing(_connect(path)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT state, token FROM delivery_guard WHERE monitor=? AND edition_date=? AND destination_key=?", key,
                ).fetchone()
                if row is None or row["token"] != expected_token or row["state"] not in {"pending", "uncertain"}:
                    raise StaleDeliveryClaim("Reconciliation token/state changed; inspect the ledger again")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS delivery_reconciliations ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, monitor TEXT NOT NULL, edition_date TEXT NOT NULL, "
                    "destination_key TEXT NOT NULL, token TEXT NOT NULL, previous_state TEXT NOT NULL, "
                    "outcome TEXT NOT NULL, reason TEXT NOT NULL, resolved_at TEXT NOT NULL)",
                )
                conn.execute(
                    "UPDATE delivery_guard SET state=?, updated_at=?, sent_at=CASE WHEN ?='sent' THEN ? ELSE sent_at END "
                    "WHERE monitor=? AND edition_date=? AND destination_key=? AND token=?",
                    (outcome, stamp, outcome, stamp, *key, expected_token),
                )
                conn.execute(
                    "INSERT INTO delivery_reconciliations (monitor, edition_date, destination_key, token, "
                    "previous_state, outcome, reason, resolved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (*key, expected_token, row["state"], outcome, reason.strip(), stamp),
                )
                conn.commit()
        except (OSError, sqlite3.Error) as exc:
            raise DeliveryGuardError("Delivery reconciliation could not be recorded; do not resend") from exc
