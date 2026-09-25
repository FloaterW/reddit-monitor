"""One-shot saved-input verification in the real Windows task identity.

No request (or an expired request) returns 75, allowing the normal daily job.
An accepted request is consumed before testing. No email or production DB writes.
"""
import json
import re
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "data/codex-migration"
REQUEST = BASE / "test-request.json"


def consume_request(path=REQUEST, now=None):
    if not path.exists():
        return None
    now = now or datetime.now(timezone.utc)
    # Claim before doing any work so a second trigger cannot repeat the test.
    claimed = path.with_suffix(".consumed.json")
    path.replace(claimed)
    try:
        request = json.loads(claimed.read_text(encoding="utf-8"))
        expiry = datetime.fromisoformat(request["expires_at"])
        if expiry.tzinfo is None or not 0 < (expiry - now).total_seconds() <= 600:
            return None
        name = request["raw_file"]
        if not isinstance(name, str) or not re.fullmatch(r"digest_\d{8}_\d{4}\.json", name):
            return None
        return request
    except (ValueError, KeyError, TypeError):
        return None


def main():
    request = consume_request()
    if request is None:
        print("No valid diagnostic request; normal daily run may proceed.")
        return 75
    import daily_digest as digest
    started = datetime.now(timezone.utc).isoformat()
    result = {"started_at": started, "status": "running", "provider": digest.LLM_COMMAND}
    status = BASE / "background-verification.json"
    digest.atomic_write_json(status, result)
    try:
        if Path(digest.LLM_COMMAND).stem.lower() != "codex":
            raise RuntimeError("Background configuration did not select Codex")
        executable = digest._resolve_llm_executable(digest.LLM_COMMAND)
        result["executable"] = executable
        edition = datetime.strptime(request["raw_file"][7:15], "%Y%m%d").date().isoformat()
        code = digest.main([
            "--monitor", "churning", "--source-safe", "--quality", "strict",
            "--from-json", str(ROOT / request["raw_file"]), "--digest-date", edition,
            "--no-email", "--no-db", "--quiet-summary",
            "--save", str(BASE / "background.md"),
            "--status-file", str(BASE / "background-run.status.json"),
            "--evaluation-report", str(BASE / "background.evaluation.json"),
        ])
        if code:
            raise RuntimeError(f"Saved-input Codex run failed with code {code}; see background.log")
        # Verify the same account can authenticate to SMTP, without sending mail.
        import smtplib
        if not digest.GMAIL_APP_PASSWORD:
            raise RuntimeError("Email credential unavailable in background task")
        with smtplib.SMTP_SSL(
            "smtp.gmail.com", 465, timeout=30, context=ssl.create_default_context()
        ) as smtp:
            smtp.login(digest.EMAIL_FROM, digest.GMAIL_APP_PASSWORD)
            reply, _ = smtp.noop()
            if reply != 250:
                raise RuntimeError("SMTP connection check failed")
        result.update(status="passed", smtp_auth="passed", email_sent=False)
        return 0
    except Exception as exc:
        result.update(status="failed", error=str(exc)[:700], email_sent=False)
        return 1
    finally:
        result["completed_at"] = datetime.now(timezone.utc).isoformat()
        digest.atomic_write_json(status, result)


if __name__ == "__main__":
    sys.exit(main())
