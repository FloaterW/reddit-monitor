"""Read-only continuity planning for a bounded daily collection window."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class CoverageWindow:
    start: datetime
    end: datetime
    extended: bool = False
    warnings: list[dict[str, str]] = field(default_factory=list)


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("Coverage timestamp must be text")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Coverage timestamp must include its timezone")
    return result.astimezone(timezone.utc)


def resolve_daily_window(window_end, previous_status_path, monitor_name):
    """Bridge at most one hour after the last successfully sent daily edition.

    Call before replacing the previous status with a running status. Only the
    previous edition's saved coverage boundary is evidence of its collection
    window; neither its filename nor its delivery time establishes that boundary.
    This proposes dates only and never changes network limits or production state.
    """
    if not isinstance(window_end, datetime) or window_end.tzinfo is None:
        raise ValueError("New coverage end must be a timezone-aware datetime")
    end = window_end.astimezone(timezone.utc).replace(second=0, microsecond=0)
    normal = CoverageWindow(start=end - timedelta(hours=24), end=end)
    if not previous_status_path:
        return normal
    status_path = Path(previous_status_path)
    try:
        previous = json.loads(status_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return normal
    except (OSError, UnicodeError, ValueError):
        return normal
    if (not isinstance(previous, dict)
            or previous.get("monitor") != monitor_name
            or previous.get("status") not in ("completed", "completed_with_warnings")
            or previous.get("email_status") != "sent"):
        return normal

    unknown = [{"subreddit": "", "reason": (
        "Previous delivered coverage boundary is unavailable; continuity could not be checked."
    )}]
    raw_path = previous.get("raw_json_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return CoverageWindow(normal.start, end, warnings=unknown)
    # Relative production artifact paths have historically been rooted at the
    # project working directory, as in daily_digest.py and its scheduled wrapper.
    try:
        coverage_path = Path(raw_path).with_suffix(".coverage.json")
        coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
        if not isinstance(coverage, dict) or coverage.get("replay_unknown"):
            raise ValueError("No usable saved collection boundary")
        previous_start = _utc(coverage.get("window_start"))
        previous_end = _utc(coverage.get("window_end"))
        if previous_start >= previous_end or previous_end > end:
            raise ValueError("Invalid saved collection boundary")
    except (OSError, UnicodeError, ValueError):
        return CoverageWindow(normal.start, end, warnings=unknown)

    elapsed = end - previous_end
    if timedelta(hours=24) < elapsed <= timedelta(hours=25):
        return CoverageWindow(previous_end, end, extended=True, warnings=[{
            "subreddit": "", "reason": (
                "Daily collection window extended to the prior delivered boundary "
                f"({previous_end.isoformat()}); the window is at most 25 hours. "
                "Existing request, page and time limits still apply; recovery is not exhaustive."
            ),
        }])
    if elapsed > timedelta(hours=25):
        gap = (normal.start - previous_end).total_seconds() / 3600
        return CoverageWindow(normal.start, end, warnings=[{
            "subreddit": "", "reason": (
                f"Coverage continuity gap: {gap:g} hours between the prior delivered "
                f"boundary ({previous_end.isoformat()}) and this window's start "
                f"({normal.start.isoformat()}). The gap exceeds the bounded one-hour "
                "extension and was not collected by this run."
            ),
        }])
    return normal
