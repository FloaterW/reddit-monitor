"""Offline release-security checks: verified SMTP TLS and private-file exclusions."""

import ssl
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import daily_digest as digest
import notify_failure
import scheduled_digest_test as diagnostic


@pytest.mark.parametrize("target", ["digest", "alert", "diagnostic"])
@pytest.mark.parametrize("invalid_certificate", [False, True])
def test_smtp_verifies_certificate_before_authentication(
    target, invalid_certificate, tmp_path, monkeypatch
):
    monkeypatch.setattr(digest, "GMAIL_APP_PASSWORD", "test-only")
    monkeypatch.setattr(notify_failure, "GMAIL_APP_PASSWORD", "test-only")
    monkeypatch.setattr(digest, "EMAIL_FROM", "sender@example.com")
    monkeypatch.setattr(digest, "EMAIL_TO", "recipient@example.com")
    monkeypatch.setattr(notify_failure, "EMAIL_FROM", "sender@example.com")
    monkeypatch.setattr(notify_failure, "EMAIL_TO", "recipient@example.com")
    monkeypatch.setattr(sys, "argv", ["notify_failure.py", "1", str(tmp_path / "absent.log")])
    monkeypatch.setattr(diagnostic, "BASE", tmp_path)
    monkeypatch.setattr(diagnostic, "consume_request", lambda: {"raw_file": "digest_20260101_1830.json"})
    monkeypatch.setattr(digest, "LLM_COMMAND", "codex")
    monkeypatch.setattr(digest, "_resolve_llm_executable", lambda _: "codex")
    monkeypatch.setattr(digest, "main", lambda _: 0)

    with patch("smtplib.SMTP_SSL") as smtp:
        connection = smtp.return_value.__enter__.return_value
        connection.noop.return_value = (250, b"OK")
        if invalid_certificate:
            smtp.side_effect = ssl.SSLCertVerificationError("test certificate rejected")
        if target == "digest":
            result = digest.send_email("Test", "Synthetic test body")
            assert result == ("failed" if invalid_certificate else "sent")
        elif target == "alert":
            assert notify_failure.main() == int(invalid_certificate)
        else:
            assert diagnostic.main() == int(invalid_certificate)

        context = smtp.call_args.kwargs["context"]
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        if invalid_certificate:
            connection.login.assert_not_called()
            connection.sendmail.assert_not_called()
        else:
            connection.login.assert_called_once()
            if target == "diagnostic":
                connection.sendmail.assert_not_called()


@pytest.mark.parametrize("name", [
    ".env", ".env.backup", ".env.production", ".gmail_app_password",
    "gmail_app_password.backup", "private.pem", "private.key", "private.p12",
    "private.pfx", "monitor.db", "monitor.db-wal", "monitor.sqlite3",
    "data/report.json", "digest_20260101_1830.work/draft.json",
    "digest_20260101_1830.json", "digest_20260101_1830.md", "digest_run.log",
    ".planning/debug/private.md", ".codex/config.toml", ".agents/local.md",
])
def test_private_artifacts_are_git_ignored(name):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", name], cwd=root, check=False
    )
    assert result.returncode == 0


def test_environment_template_is_publishable():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", ".env.example"], cwd=root, check=False
    )
    assert result.returncode == 1
