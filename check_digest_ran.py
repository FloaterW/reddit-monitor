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

import smtplib
import sys
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from daily_digest import EMAIL_FROM, EMAIL_TO, GMAIL_APP_PASSWORD
from notify_failure import read_log_tail

PROJECT_DIR = Path(__file__).parent
LOG_PATH = PROJECT_DIR / "digest_run.log"

# The digest is scheduled for 18:30 and takes a few minutes. Before this time
# of day there is nothing to complain about, so the watchdog stays quiet.
DEFAULT_DUE = "19:30"


def parse_due(value):
    """Parse a HH:MM due time. Falls back to DEFAULT_DUE if malformed."""
    try:
        hour, _, minute = value.partition(":")
        return int(hour), int(minute)
    except ValueError:
        print(f"[WARN] Could not parse due time {value!r}; "
              f"using {DEFAULT_DUE}.")
        hour, _, minute = DEFAULT_DUE.partition(":")
        return int(hour), int(minute)


def find_todays_digests(day=None):
    """Return today's digest markdown files, newest first."""
    day = day or datetime.now()
    pattern = f"digest_{day:%Y%m%d}_*.md"
    return sorted(PROJECT_DIR.glob(pattern), key=lambda p: p.stat().st_mtime,
                  reverse=True)


def send_alert(day):
    """Email a 'digest never ran' alert. Returns True if sent."""
    if not GMAIL_APP_PASSWORD:
        print("[SKIP] No Gmail app password configured — alert not sent.")
        return False

    body = (
        f"No digest was produced for {day:%B %d, %Y}.\n\n"
        f"The scheduled run appears not to have completed. Because nothing\n"
        f"ran, run_digest.bat could not report the failure itself.\n\n"
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


def main():
    quiet = "--quiet" in sys.argv
    now = datetime.now()

    due = DEFAULT_DUE
    if "--due" in sys.argv:
        idx = sys.argv.index("--due")
        if idx + 1 < len(sys.argv):
            due = sys.argv[idx + 1]
        else:
            print("ERROR: --due requires a HH:MM value")
            return 1
    due_hour, due_minute = parse_due(due)

    found = find_todays_digests(now)
    if found:
        if not quiet:
            print(f"[OK] Digest present for {now:%Y-%m-%d}: {found[0].name}")
        return 0

    # Nothing yet — but say nothing until the digest is actually overdue,
    # otherwise a morning run would report a failure that has not happened.
    if (now.hour, now.minute) < (due_hour, due_minute):
        if not quiet:
            print(f"[OK] No digest yet for {now:%Y-%m-%d}, but it is not due "
                  f"until {due_hour:02d}:{due_minute:02d} — no alert.")
        return 0

    print(f"[ALERT] No digest found for {now:%Y-%m-%d} — sending alert.")
    send_alert(now)
    # Always exit 0: the watchdog reports problems, it is not one itself.
    return 0


if __name__ == "__main__":
    sys.exit(main())
