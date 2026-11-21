"""Watchdog delivery evidence and TLS regression checks; no network calls."""

import json
import ssl
from datetime import datetime
from unittest.mock import patch

import pytest

import check_digest_ran as watchdog


@pytest.mark.parametrize("email_status", ["disabled", None, "unknown", "", "sent"])
def test_watchdog_requires_confirmed_submission(tmp_path, email_status):
    digest = tmp_path / "digest.md"
    digest.write_text("# Synthetic preview", encoding="utf-8")
    status = tmp_path / "status.json"
    status.write_text(json.dumps({
        "date": "2026-11-04", "status": "completed",
        "digest_path": str(digest), "email_status": email_status,
    }), encoding="utf-8")

    state, _ = watchdog.check_run_status(status, datetime(2026, 11, 4, 20))

    assert state == ("success" if email_status == "sent" else "failed")


@pytest.mark.parametrize("stale_status", [False, True])
def test_saved_preview_cannot_hide_missing_delivery(tmp_path, monkeypatch, stale_status):
    monkeypatch.setattr(watchdog, "PROJECT_DIR", tmp_path)
    (tmp_path / "digest_20261104_1830.md").write_text("# Preview", encoding="utf-8")
    status = tmp_path / "status.json"
    if stale_status:
        status.write_text(json.dumps({"date": "2026-11-03", "status": "completed"}),
                          encoding="utf-8")
    with patch.object(watchdog, "send_alert", return_value=True) as alert:
        result = watchdog.main(["--status-file", str(status)], now=datetime(2026, 11, 4, 20))

    assert result == 0  # Alert submission succeeded, not digest delivery.
    alert.assert_called_once()


@pytest.mark.parametrize("payload", [[], None, "completed", 1, {
    "date": "2026-11-04", "status": "completed", "digest_path": 123,
}, {"date": "2026-11-04", "status": []}, {"date": "2026-11-04", "status": {}}])
def test_malformed_status_fails_without_crashing(tmp_path, payload):
    status = tmp_path / "status.json"
    status.write_text(json.dumps(payload), encoding="utf-8")
    state, _ = watchdog.check_run_status(status, datetime(2026, 11, 4, 20))
    assert state == "failed"


def test_invalid_utf8_status_fails_without_crashing(tmp_path):
    status = tmp_path / "status.json"
    status.write_bytes(b"\xff\xfe")
    state, _ = watchdog.check_run_status(status, datetime(2026, 11, 4, 20))
    assert state == "failed"


@pytest.mark.parametrize("invalid_certificate", [False, True])
def test_watchdog_verifies_tls_before_sending_credentials(monkeypatch, invalid_certificate):
    monkeypatch.setattr(watchdog, "GMAIL_APP_PASSWORD", "synthetic-test-secret")
    monkeypatch.setattr(watchdog, "EMAIL_FROM", "sender@example.com")
    monkeypatch.setattr(watchdog, "EMAIL_TO", "recipient@example.com")
    monkeypatch.setattr(watchdog, "read_log_tail", lambda _: "Synthetic log")
    with patch("smtplib.SMTP_SSL") as smtp:
        connection = smtp.return_value.__enter__.return_value
        if invalid_certificate:
            smtp.side_effect = ssl.SSLCertVerificationError("test certificate rejected")
        assert watchdog.send_alert(datetime(2026, 11, 4)) is not invalid_certificate
        context = smtp.call_args.kwargs["context"]
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        if invalid_certificate:
            connection.login.assert_not_called()
            connection.sendmail.assert_not_called()
        else:
            connection.login.assert_called_once()
            connection.sendmail.assert_called_once()
