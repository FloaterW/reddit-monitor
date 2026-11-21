"""Inspect or explicitly reconcile an uncertain edition; never send email.

Examples:
  python manage_delivery.py status --monitor churning --date 2026-11-04
  python manage_delivery.py reconcile --monitor churning --date 2026-11-04 \
      --expected-token TOKEN --outcome sent --reason "Confirmed message in Gmail"

Only choose retryable after independently establishing that the earlier attempt
was not accepted. Reconciliation permits a later ordinary run; it does not run it.
"""

import argparse
import json
import os
from datetime import date

from delivery_guard import DeliveryGuardError, get_delivery, resolve_delivery
from monitor_config import list_monitors, load_monitor


def _edition(value):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise argparse.ArgumentTypeError("edition date must use YYYY-MM-DD") from exc
    return value


def main(argv=None):
    # Read the same local destination/ledger configuration as the normal sender.
    # Importing its configuration does not call SMTP or run the digest.
    import daily_digest

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("status", "Read the delivery record without changing it"),
                            ("reconcile", "Record an independently verified outcome; do not send")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--monitor", required=True,
                             help="Monitor profile slug, or exact name stored in the ledger")
        command.add_argument("--date", required=True, type=_edition, dest="edition_date")
        command.add_argument("--destination", default=daily_digest.EMAIL_TO,
                             help="Recipient (defaults to the configured digest recipient)")
        command.add_argument("--ledger", default=os.getenv("DIGEST_DELIVERY_LEDGER", str(daily_digest.DEFAULT_DELIVERY_LEDGER)))
        if name == "reconcile":
            command.add_argument("--expected-token", required=True,
                                 help="Exact attempt token shown by the status command")
            command.add_argument("--outcome", required=True, choices=["sent", "retryable"])
            command.add_argument("--reason", required=True,
                                 help="Evidence for the decision; do not include passwords or tokens")
    args = parser.parse_args(argv)
    try:
        monitor = load_monitor(args.monitor)["name"] if args.monitor in list_monitors() else args.monitor
        key = dict(monitor=monitor, edition_date=args.edition_date, destination=args.destination)
        if args.command == "reconcile":
            resolve_delivery(args.ledger, **key, expected_token=args.expected_token,
                             outcome=args.outcome, reason=args.reason)
        record = get_delivery(args.ledger, **key)
        if record is None:
            record = {"monitor": monitor, "edition_date": args.edition_date, "state": "absent"}
        print(json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True))
        if args.command == "reconcile":
            print("Reconciliation recorded. No email was sent and no digest run was started.")
        return 0
    except (DeliveryGuardError, ValueError, OSError) as exc:
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
