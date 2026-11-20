"""
Email an alert when a scheduled digest run fails.

Invoked by run_digest.bat when daily_digest.py exits non-zero, so a broken
run surfaces in your inbox instead of sitting silently in digest_run.log.

Usage:
  python notify_failure.py <exit_code> [log_file]
"""

import smtplib
import ssl
import sys
from collections import deque
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from daily_digest import EMAIL_FROM, EMAIL_TO, GMAIL_APP_PASSWORD

# How much of the tail of the log to include in the alert.
LOG_TAIL_LINES = 40


def read_log_tail(log_path, lines=LOG_TAIL_LINES):
    """Return the last `lines` lines of the log, or a placeholder."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            return "".join(deque(f, maxlen=lines)).strip()
    except OSError as e:
        return f"(could not read {log_path}: {e})"


def recovery_guidance(log_tail):
    """Do not suggest model reauthentication or replaying an empty collection."""
    if any(marker in log_tail for marker in (
        "API request failed:", "API source scope:", "API collection", "API listing",
        "REDDIT_CLIENT_ID", "REDDIT_API_USER_AGENT", "DIGEST_REDDIT_SOURCE",
    )):
        return (
            "Reddit API collection could not complete. Check the configured installed-app "
            "client ID, API User-Agent, access status and reported HTTP/rate-limit errors. "
            "See docs/COLLECTION_ACCESS.md. Do not switch to retired RSS or change AI "
            "credentials to resolve an API access failure.\n"
            "An empty saved JSON file cannot recover a missed edition. Use only nonempty, "
            "dated saved comments for recovery.\n"
        )
    if any(marker in log_tail for marker in (
        "Reddit collection failed:", "RSS request failed:", "Total comments fetched: 0",
    )):
        return (
            "Collection failed before a newsletter could be generated. Check the HTTP/feed "
            "errors above. Reddit announced RSS retirement for November 13, 2026; "
            "see docs/COLLECTION_ACCESS.md for supported-source migration requirements.\n"
            "An empty saved JSON file cannot recover the missed edition. Restore a supported "
            "data source before retrying; changing AI credentials will not restore Reddit feeds.\n"
        )
    if "Saved input contains no comments" in log_tail:
        return "The selected saved file is empty. Recovery requires nonempty, dated source comments.\n"
    return (
        "For authentication errors, check the configured provider: Claude uses\n"
        "its interactive /login; Codex uses 'codex login status' and 'codex login'.\n"
        "To preview a recovered edition from nonempty saved comments without re-scraping or sending an email:\n"
        "  python daily_digest.py --monitor churning --from-json <raw>.json "
        "--digest-date YYYY-MM-DD --source-safe --quality strict --no-email --no-db "
        "--save data/recovery.md --status-file data/recovery.status.json\n"
        "Review the preview before sending. Keep the original raw file.\n"
    )


def main():
    exit_code = sys.argv[1] if len(sys.argv) > 1 else "unknown"

    if len(sys.argv) > 2:
        log_path = Path(sys.argv[2])
    else:
        log_path = Path(__file__).parent / "digest_run.log"
    if not log_path.is_absolute():
        log_path = Path(__file__).parent / log_path

    if not GMAIL_APP_PASSWORD:
        print("[SKIP] No Gmail app password configured — alert not sent.")
        return 1

    stamp = datetime.now().strftime("%B %d, %Y at %H:%M")
    log_tail = read_log_tail(log_path)
    body = (
        f"The Reddit digest run failed on {stamp}.\n"
        f"Exit code: {exit_code}\n"
        f"Log file: {log_path}\n\n"
        f"Last {LOG_TAIL_LINES} lines of the log:\n"
        f"{'-' * 60}\n"
        f"{log_tail}\n"
        f"{'-' * 60}\n\n"
        f"{recovery_guidance(log_tail)}"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[FAILED] Reddit Digest — {datetime.now():%B %d, %Y}"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL(
            "smtp.gmail.com", 465, timeout=30, context=ssl.create_default_context()
        ) as server:
            server.login(EMAIL_FROM, GMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"[OK] Failure alert emailed to {EMAIL_TO}")
        return 0
    except Exception as e:
        print(f"[ERROR] Failure alert could not be sent: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
