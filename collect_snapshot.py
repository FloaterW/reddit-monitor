"""Collection-only entry point. Never calls a model, SMTP or the delivery ledger."""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from analysis_report import write_report
from daily_digest import atomic_write_json
from enhanced_collection import collect_enhanced
from intelligence_store import IntelligenceStore, scope_key
from monitor_config import load_monitor


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--cleanup-only", action="store_true", help="Expire stale cache and rebuild the local report, without network access")
    args = parser.parse_args(argv)
    monitor = load_monitor(args.monitor)
    now = datetime.now(timezone.utc)
    folder = Path(args.store).parent
    status = {"started_at": now.isoformat(), "mode": "cleanup" if args.cleanup_only else "collection_only"}
    try:
        # Rewrite diagnostics from the remaining cache first, even if a later
        # API call fails. HTML/JSON copies must not preserve expired evidence.
        with IntelligenceStore(args.store) as store:
            report = store.report(scope_key(monitor), now)
        write_report(folder / "analysis.html", report)
        if not args.cleanup_only:
            stats, audit = {}, {}
            sources, _, report = collect_enhanced(monitor, args.store, window_end=now, stats=stats, collection_audit=audit)
            write_report(folder / "analysis.html", report)
            # Only aggregates are exported; raw sources live in the bounded DB.
            status.update(sources=len(sources), api_requests=stats["api_requests"], warnings=stats.get("collection_warnings", []))
            if not sources:
                raise RuntimeError("No matching sources collected; evening collection will retry the full window")
        status.update(status="completed", finished_at=datetime.now(timezone.utc).isoformat())
        atomic_write_json(folder / "snapshot-status.json", status)
        return 0
    except Exception as exc:
        status.update(status="failed", error=str(exc)[:500], finished_at=datetime.now(timezone.utc).isoformat())
        atomic_write_json(folder / "snapshot-status.json", status)
        print(f"[ERROR] Collection snapshot: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
