"""End-to-end delivery lifecycle tests with isolated state and mocked services."""

import json
import smtplib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email import message_from_string
from threading import Event
from unittest.mock import patch

import pytest

import check_digest_ran as watchdog
import daily_digest as digest
import delivery_guard as guard
import manage_delivery
from storage import DigestDB

SUMMARY = "# Daily Digest — November 04, 2026\n\nA synthetic test newsletter.\n"


@pytest.fixture
def run_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(digest, "EMAIL_FROM", "sender@example.com")
    monkeypatch.setattr(digest, "EMAIL_TO", "reader@example.com")
    monkeypatch.setattr(digest, "GMAIL_APP_PASSWORD", "synthetic-secret")
    raw = tmp_path / "source.json"
    raw.write_text(json.dumps([{"id": "t1_test", "body": "A synthetic credit report.",
                                "author": "test", "subreddit": "test"}]), encoding="utf-8")
    ledger = tmp_path / "deliveries.db"
    status = tmp_path / "status.json"
    output = tmp_path / "digest.md"
    args = ["--from-json", str(raw), "--digest-date", "2026-11-04", "--quality", "off",
            "--subreddits", "test", "--keywords", "credit", "--save", str(output),
            "--status-file", str(status), "--delivery-ledger", str(ledger), "--quiet-summary"]
    key = {"monitor": "default", "edition_date": "2026-11-04", "destination": digest.EMAIL_TO}
    return args, ledger, status, output, key


def test_sequential_duplicate_stops_before_generation_or_status_overwrite(run_setup):
    args, ledger, status, output, key = run_setup
    with patch.object(digest, "summarize", return_value=SUMMARY) as generate, \
            patch.object(digest, "send_email", return_value="sent") as send:
        assert digest.main(args) == 0
        saved_status, saved_digest = status.read_bytes(), output.read_bytes()
        assert digest.main(args) == 0
    generate.assert_called_once()
    send.assert_called_once()
    assert status.read_bytes() == saved_status and output.read_bytes() == saved_digest
    assert guard.get_delivery(ledger, **key)["state"] == "sent"


def test_concurrent_duplicate_stops_before_generation_or_status_overwrite(run_setup):
    args, ledger, status, output, key = run_setup
    entered, release = Event(), Event()

    def generate(*unused, **kwargs):
        entered.set()
        assert release.wait(10), "test did not release the first run"
        return SUMMARY

    with patch.object(digest, "summarize", side_effect=generate) as generation, \
            patch.object(digest, "send_email", return_value="sent") as send, \
            ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(digest.main, args)
        try:
            assert entered.wait(10)
            running_status = status.read_bytes()
            assert digest.main(args) == 6
            assert status.read_bytes() == running_status
            assert not output.exists()
        finally:
            release.set()
        assert first.result(timeout=10) == 0
    generation.assert_called_once()
    send.assert_called_once()
    assert guard.get_delivery(ledger, **key)["state"] == "sent"


@pytest.mark.parametrize("failure", [
    smtplib.SMTPRecipientsRefused({"reader@example.com": (550, b"Rejected")}),
    smtplib.SMTPSenderRefused(550, b"Rejected", "sender@example.com"),
    smtplib.SMTPDataError(550, b"Rejected"),
])
def test_definitive_rejection_allows_retry_with_same_message_id(run_setup, failure):
    args, ledger, status, output, key = run_setup
    with patch.object(digest, "summarize", return_value=SUMMARY), patch("smtplib.SMTP_SSL") as smtp:
        server = smtp.return_value.__enter__.return_value
        server.sendmail.side_effect = [failure, {}]
        assert digest.main(args) == 4
        assert guard.get_delivery(ledger, **key)["state"] == "retryable"
        assert digest.main(args) == 0
        messages = [message_from_string(call.args[2]) for call in server.sendmail.call_args_list]
    assert len(messages) == 2
    assert messages[0]["Message-ID"] == messages[1]["Message-ID"]
    assert messages[0]["Message-ID"] == guard.get_delivery(ledger, **key)["message_id"]


def test_login_failure_is_retryable_without_submission(run_setup):
    args, ledger, status, output, key = run_setup
    with patch.object(digest, "summarize", return_value=SUMMARY), patch("smtplib.SMTP_SSL") as smtp:
        server = smtp.return_value.__enter__.return_value
        server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Synthetic rejection")
        assert digest.main(args) == 4
        server.sendmail.assert_not_called()
    assert guard.get_delivery(ledger, **key)["state"] == "retryable"


def test_preparation_failure_is_retryable_without_smtp(run_setup):
    args, ledger, status, output, key = run_setup
    with patch.object(digest, "summarize", return_value=SUMMARY), \
            patch.object(digest, "_markdown_to_safe_html", side_effect=ValueError("Synthetic rendering failure")), \
            patch("smtplib.SMTP_SSL") as smtp:
        assert digest.main(args) == 4
        smtp.assert_not_called()
    assert guard.get_delivery(ledger, **key)["state"] == "retryable"


@pytest.mark.parametrize("failure", [TimeoutError("Synthetic timeout"), smtplib.SMTPServerDisconnected("Synthetic disconnect")])
def test_ambiguous_submission_blocks_automatic_retry(run_setup, failure):
    args, ledger, status, output, key = run_setup
    with patch.object(digest, "summarize", return_value=SUMMARY) as generate, patch("smtplib.SMTP_SSL") as smtp:
        server = smtp.return_value.__enter__.return_value
        server.sendmail.side_effect = failure
        assert digest.main(args) == 5
        saved_status = status.read_bytes()
        assert digest.main(args) == 5
        assert status.read_bytes() == saved_status
        generate.assert_called_once()
        server.sendmail.assert_called_once()
    assert guard.get_delivery(ledger, **key)["state"] == "uncertain"


def test_accepted_mail_with_quit_failure_stays_sent(run_setup):
    args, ledger, status, output, key = run_setup
    with patch.object(digest, "summarize", return_value=SUMMARY), patch("smtplib.SMTP_SSL") as smtp:
        smtp.return_value.__enter__.return_value.sendmail.return_value = {}
        smtp.return_value.__exit__.side_effect = smtplib.SMTPServerDisconnected("QUIT failed")
        assert digest.main(args) == 0
    assert guard.get_delivery(ledger, **key)["state"] == "sent"
    assert json.loads(status.read_text(encoding="utf-8"))["email_status"] == "sent"


def test_database_failure_after_acceptance_cannot_cause_resend(run_setup, tmp_path):
    args, ledger, status, output, key = run_setup
    args += ["--db", str(tmp_path / "history.db")]
    with patch.object(digest, "summarize", return_value=SUMMARY) as generate, \
            patch.object(digest, "send_email", return_value="sent") as send, \
            patch.object(digest, "_save_run_history", side_effect=OSError("Synthetic database failure")):
        assert digest.main(args) == 0
        failed_status = status.read_bytes()
        assert digest.main(args) == 0
    assert status.read_bytes() == failed_status
    generate.assert_called_once()
    send.assert_called_once()
    assert guard.get_delivery(ledger, **key)["state"] == "sent"


def test_claim_write_failure_prevents_smtp(run_setup):
    args, ledger, status, output, key = run_setup
    with patch.object(digest, "summarize", return_value=SUMMARY), \
            patch.object(guard, "claim_delivery", side_effect=guard.DeliveryGuardError("Synthetic disk failure")), \
            patch.object(digest, "send_email") as send:
        assert digest.main(args) == 1
    send.assert_not_called()
    assert not ledger.exists()


def test_outcome_write_failure_leaves_pending_and_blocks_resend(run_setup):
    args, ledger, status, output, key = run_setup
    with patch.object(digest, "summarize", return_value=SUMMARY) as generate, \
            patch.object(digest, "send_email", return_value="sent") as send, \
            patch.object(guard, "mark_sent", side_effect=guard.DeliveryGuardError("Synthetic disk failure")):
        assert digest.main(args) == 1
        assert digest.main(args) == 5
    assert guard.get_delivery(ledger, **key)["state"] == "pending"
    generate.assert_called_once()
    send.assert_called_once()


def test_no_email_preview_never_opens_delivery_ledger(run_setup):
    args, ledger, status, output, key = run_setup
    with patch.object(digest, "summarize", return_value=SUMMARY), \
            patch.object(guard, "edition_lock") as lock, \
            patch.object(guard, "claim_delivery") as claim, patch.object(digest, "send_email") as send:
        assert digest.main(args + ["--no-email"]) == 0
    lock.assert_not_called()
    claim.assert_not_called()
    send.assert_not_called()
    assert not ledger.exists()


def test_legacy_sent_history_uses_edition_heading_not_delivery_day(run_setup, tmp_path):
    args, ledger, status, output, key = run_setup
    history = tmp_path / "history.db"
    db = DigestDB(history)
    db.save_run(monitor_name="default", time_filter="day", posts_per_subreddit=10,
                comments=[], digest_md=SUMMARY, email_status="sent",
                started_at=datetime(2026, 11, 5, 1, tzinfo=timezone.utc))
    db.close()
    assert digest._previously_sent_edition([history], "default", "2026-11-05") is None
    status.write_text("existing unrelated status", encoding="utf-8")
    with patch.object(digest, "summarize") as generate, patch.object(digest, "send_email") as send:
        assert digest.main(args + ["--history-db", str(history)]) == 0
    generate.assert_not_called()
    send.assert_not_called()
    assert status.read_text(encoding="utf-8") == "existing unrelated status"
    assert not output.exists()
    assert guard.get_delivery(ledger, **key)["state"] == "sent"


def test_cli_status_is_read_only_and_resolves_monitor_profile(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(digest, "EMAIL_TO", "reader@example.com")
    path = tmp_path / "ledger.db"
    key = {"monitor": "credit-card-churning", "edition_date": "2026-11-04", "destination": digest.EMAIL_TO}
    claim = guard.claim_delivery(path, **key, content_hash=guard.hash_content("Synthetic body"))
    before = path.read_bytes()
    with patch("smtplib.SMTP_SSL") as smtp:
        assert manage_delivery.main(["status", "--monitor", "churning", "--date", "2026-11-04",
                                     "--ledger", str(path)]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["token"] == claim.token and record["message_id"] == claim.message_id
    assert record["state"] == "pending" and path.read_bytes() == before
    smtp.assert_not_called()


@pytest.mark.parametrize("outcome", ["sent", "retryable"])
def test_cli_reconciliation_is_explicit_and_never_sends(run_setup, outcome):
    args, ledger, status, output, key = run_setup
    claim = guard.claim_delivery(ledger, **key, content_hash=guard.hash_content("Synthetic body"))
    command = ["reconcile", "--monitor", "default", "--date", "2026-11-04", "--ledger", str(ledger),
               "--expected-token", claim.token, "--outcome", outcome, "--reason", "Synthetic independent verification"]
    with patch("smtplib.SMTP_SSL") as smtp, patch.object(digest, "main") as run:
        assert manage_delivery.main(command) == 0
    smtp.assert_not_called()
    run.assert_not_called()
    assert guard.get_delivery(ledger, **key)["state"] == outcome


@pytest.mark.parametrize("missing", ["--expected-token", "--outcome", "--reason"])
def test_cli_reconciliation_requires_all_safety_fields(run_setup, missing):
    args, ledger, status, output, key = run_setup
    command = ["reconcile", "--monitor", "default", "--date", "2026-11-04", "--ledger", str(ledger),
               "--expected-token", "token", "--outcome", "sent", "--reason", "Verified"]
    index = command.index(missing)
    del command[index:index + 2]
    with pytest.raises(SystemExit) as error, patch("smtplib.SMTP_SSL") as smtp:
        manage_delivery.main(command)
    assert error.value.code == 2
    smtp.assert_not_called()
    assert not ledger.exists()


def test_cli_absent_status_creates_no_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(digest, "EMAIL_TO", "reader@example.com")
    path = tmp_path / "absent" / "ledger.db"
    with patch("smtplib.SMTP_SSL") as smtp:
        assert manage_delivery.main(["status", "--monitor", "default", "--date", "2026-11-04",
                                     "--ledger", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"
    assert not path.parent.exists()
    smtp.assert_not_called()


def test_cli_rejects_stale_reconciliation_token_without_mutation(run_setup):
    args, ledger, status, output, key = run_setup
    claim = guard.claim_delivery(ledger, **key, content_hash=guard.hash_content("Synthetic body"))
    with patch("smtplib.SMTP_SSL") as smtp:
        assert manage_delivery.main([
            "reconcile", "--monitor", "default", "--date", "2026-11-04", "--ledger", str(ledger),
            "--expected-token", "stale", "--outcome", "retryable", "--reason", "Synthetic verification",
        ]) == 1
    record = guard.get_delivery(ledger, **key)
    assert record["state"] == "pending" and record["token"] == claim.token
    smtp.assert_not_called()


def test_corrupt_ledger_blocks_before_artifact_changes(run_setup):
    args, ledger, status, output, key = run_setup
    ledger.write_text("not a database", encoding="utf-8")
    status.write_text("prior status", encoding="utf-8")
    with patch.object(digest, "summarize") as generate, patch.object(digest, "send_email") as send:
        assert digest.main(args) == 1
    generate.assert_not_called()
    send.assert_not_called()
    assert status.read_text(encoding="utf-8") == "prior status" and not output.exists()


def test_recovery_status_records_intended_edition(run_setup):
    args, ledger, status, output, key = run_setup
    args[args.index("--digest-date") + 1] = "2026-11-03"
    with patch.object(digest, "summarize", return_value=SUMMARY):
        assert digest.main(args + ["--no-email"]) == 0
    assert json.loads(status.read_text(encoding="utf-8"))["edition_date"] == "2026-11-03"


@pytest.mark.parametrize("edition_date,expected", [("2026-11-03", "missing"),
                                                   ("2026-11-04", "success"), (None, "missing")])
def test_watchdog_checks_intended_edition_not_just_send_date(tmp_path, edition_date, expected):
    output = tmp_path / "digest.md"
    output.write_text(SUMMARY, encoding="utf-8")
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"date": "2026-11-04", "edition_date": edition_date,
                                  "status": "completed", "email_status": "sent",
                                  "digest_path": str(output)}), encoding="utf-8")
    assert watchdog.check_run_status(status, datetime(2026, 11, 4, 20))[0] == expected


def test_watchdog_preserves_status_without_new_edition_field(tmp_path):
    output = tmp_path / "digest.md"
    output.write_text(SUMMARY, encoding="utf-8")
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"date": "2026-11-04", "status": "completed", "email_status": "sent",
                                  "digest_path": str(output)}), encoding="utf-8")
    assert watchdog.check_run_status(status, datetime(2026, 11, 4, 20))[0] == "success"


@pytest.mark.parametrize("dated_sidecar", [False, True])
def test_emailed_replay_requires_explicit_edition_before_any_mutation(run_setup, dated_sidecar):
    args, ledger, status, output, key = run_setup
    index = args.index("--digest-date")
    del args[index:index + 2]
    if dated_sidecar:
        from pathlib import Path
        raw = Path(args[args.index("--from-json") + 1])
        raw.with_suffix(".coverage.json").write_text(json.dumps({
            "window_start": "2026-11-02T23:30:00+00:00", "window_end": "2026-11-03T23:30:00+00:00",
            "collection_warnings": [],
        }), encoding="utf-8")
    status.write_text("Prior status must survive", encoding="utf-8")
    output.write_text("Prior newsletter must survive", encoding="utf-8")
    with patch.object(digest, "summarize") as generate, patch.object(digest, "send_email") as send, \
            pytest.raises(SystemExit) as error:
        digest.main(args)
    assert error.value.code == 2
    generate.assert_not_called()
    send.assert_not_called()
    assert status.read_text(encoding="utf-8") == "Prior status must survive"
    assert output.read_text(encoding="utf-8") == "Prior newsletter must survive"
    assert not ledger.exists() and not (ledger.parent / ".delivery-locks").exists()
