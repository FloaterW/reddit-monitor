"""
Email an alert when a scheduled digest run fails.

Invoked by run_digest.bat when daily_digest.py exits non-zero, so a broken
run surfaces in your inbox instead of sitting silently in digest_run.log.

Usage:
  python notify_failure.py <exit_code> [log_file]
"""

import smtplib
import sys
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
            return "".join(f.readlines()[-lines:]).strip()
    except OSError as e:
        return f"(could not read {log_path}: {e})"


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
        return 0

    stamp = datetime.now().strftime("%B %d, %Y at %H:%M")
    body = (
        f"The Reddit digest run failed on {stamp}.\n"
        f"Exit code: {exit_code}\n"
        f"Log file: {log_path}\n\n"
        f"Last {LOG_TAIL_LINES} lines of the log:\n"
        f"{'-' * 60}\n"
        f"{read_log_tail(log_path)}\n"
        f"{'-' * 60}\n\n"
        f"If this is an authentication error, run 'claude' interactively and\n"
        f"use /login, then rerun. To regenerate without re-scraping Reddit:\n"
        f"  python daily_digest.py --monitor churning --from-json <raw>.json\n"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[FAILED] Reddit Digest — {datetime.now():%B %d, %Y}"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, GMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"[OK] Failure alert emailed to {EMAIL_TO}")
        return 0
    except Exception as e:
        # Never let the notifier itself change the run's outcome.
        print(f"[ERROR] Failure alert could not be sent: {e}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
