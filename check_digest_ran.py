"""
Watchdog: alert if today's digest never ran at all.

run_digest.bat can only report failures it is present for. If the scheduled
task never starts — machine asleep, session not active, scheduler skipped the
trigger — nothing executes and nothing emails, so the miss is silent.

This checks after the fact that a digest actually exists for today and sends
an alert if it does not. Run it from its own scheduled task, an hour or so
after the digest is due.

It never alerts before the digest is actually due, so running it by hand in
the morning reports "not due yet" instead of a false alarm.

Usage:
  python check_digest_ran.py            # check today
  python check_digest_ran.py --quiet    # only print on problems
  python check_digest_ran.py --due 19:30  # override the due time
"""

import argparse
import json
import re
import smtplib
import sys
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from daily_digest import (
    DEFAULT_STATUS_FILE,
    EMAIL_FROM,
    EMAIL_TO,
    GMAIL_APP_PASSWORD,
)
from notify_failure import read_log_tail

PROJECT_DIR = Path(__file__).parent
LOG_PATH = PROJECT_DIR / "digest_run.log"

# The digest is scheduled for 18:30 and takes a few minutes. Before this time
# of day there is nothing to complain about, so the watchdog stays quiet.
DEFAULT_DUE = "19:30"


def parse_due(value):
    """Parse a HH:MM due time. Falls back to DEFAULT_DUE if malformed."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value or "")
    if match:
        hour, minute = (int(part) for part in match.groups())
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    print(f"[WARN] Could not parse due time {value!r}; using {DEFAULT_DUE}.")
    hour, minute = (int(part) for part in DEFAULT_DUE.split(":"))
    return hour, minute


def find_todays_digests(day=None):
    """Return today's digest markdown files, newest first."""
    day = day or datetime.now()
    pattern = f"digest_{day:%Y%m%d}_*.md"
    candidates = []
    for path in PROJECT_DIR.glob(pattern):
        try:
            if path.is_file() and path.stat().st_size > 0:
                if path.read_text(encoding="utf-8", errors="replace").strip():
                    candidates.append(path)
        except OSError:
            continue
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)


def check_run_status(status_path, day):
    """Return (state, message): state is success, failed, or missing."""
    path = Path(status_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "missing", f"status file not found: {path}"
    except (json.JSONDecodeError, OSError) as exc:
        return "failed", f"status file is unreadable: {exc}"

    expected_date = day.date().isoformat()
    if payload.get("date") != expected_date:
        return "missing", f"latest status is for {payload.get('date') or 'an unknown date'}"

    status = payload.get("status")
    if status not in {"completed", "completed_with_warnings"}:
        detail = payload.get("error") or f"run status is {status or 'missing'}"
        return "failed", detail

    digest_value = payload.get("digest_path")
    if not digest_value:
        return "failed", "successful status does not name a digest file"
    digest_path = Path(digest_value)
    if not digest_path.is_absolute():
        digest_path = PROJECT_DIR / digest_path
    try:
        if not digest_path.is_file() or not digest_path.read_text(
            encoding="utf-8", errors="replace"
        ).strip():
            return "failed", f"digest is missing or empty: {digest_path}"
    except OSError as exc:
        return "failed", f"digest cannot be read: {exc}"

    email_status = payload.get("email_status")
    if email_status == "failed":
        return "failed", "digest email delivery failed"
    return "success", f"completed digest: {digest_path.name}"


def send_alert(day, reason="no successful run status was found"):
    """Email a 'digest never ran' alert. Returns True if sent."""
    if not GMAIL_APP_PASSWORD:
        print("[SKIP] No Gmail app password configured — alert not sent.")
        return False

    body = (
        f"No successful digest run was recorded for {day:%B %d, %Y}.\n\n"
        f"Reason: {reason}\n\n"
        f"Things worth checking:\n"
        f"  - Was the machine awake at the scheduled run time?\n"
        f"    The task uses S4U logon, so it runs whether you are signed\n"
        f"    in or not, but the machine must be powered on.\n"
        f"  - Task Scheduler > RedditDailyDigest > Last Run Result.\n"
        f"  - Run it manually:  schtasks /run /tn RedditDailyDigest\n\n"
        f"Last lines of {LOG_PATH.name} (may predate today):\n"
        f"{'-' * 60}\n"
        f"{read_log_tail(LOG_PATH)}\n"
        f"{'-' * 60}\n"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[MISSED] Reddit Digest did not run — {day:%B %d, %Y}"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, GMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"[OK] Missed-run alert emailed to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"[ERROR] Missed-run alert could not be sent: {e}")
        return False


def main(argv=None, now=None):
    parser = argparse.ArgumentParser(description="Check that today's digest succeeded")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--due", default=DEFAULT_DUE, help="Due time in HH:MM")
    parser.add_argument("--status-file", default=str(DEFAULT_STATUS_FILE))
    args = parser.parse_args(argv)
    now = now or datetime.now()
    due_hour, due_minute = parse_due(args.due)

    state, reason = check_run_status(args.status_file, now)
    if state == "success":
        if not args.quiet:
            print(f"[OK] Digest succeeded for {now:%Y-%m-%d}: {reason}")
        return 0

    # Compatibility for installations upgrading from filename-only checks.
    if state == "missing":
        found = find_todays_digests(now)
        if found:
            if not args.quiet:
                print(
                    f"[OK] Legacy digest present for {now:%Y-%m-%d}: "
                    f"{found[0].name} (no current status file)"
                )
            return 0

    # Nothing yet — but say nothing until the digest is actually overdue,
    # otherwise a morning run would report a failure that has not happened.
    if (now.hour, now.minute) < (due_hour, due_minute):
        if not args.quiet:
            print(f"[OK] No digest yet for {now:%Y-%m-%d}, but it is not due "
                  f"until {due_hour:02d}:{due_minute:02d} — no alert.")
        return 0

    print(f"[ALERT] Digest run failed for {now:%Y-%m-%d}: {reason}")
    return 0 if send_alert(now, reason) else 1


if __name__ == "__main__":
    sys.exit(main())
