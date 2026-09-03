"""
Monitor config loader — reads JSON monitor profiles from config/monitors/.

A monitor profile defines subreddits, keywords, post settings, title filters,
and digest metadata. CLI arguments override config values, and config values
override code defaults.
"""

import copy
import json
import re
from pathlib import Path

_CONFIG_DIR = Path(__file__).parent / "config" / "monitors"

_REQUIRED_FIELDS = {"name", "subreddits", "keywords"}

_DEFAULTS = {
    "post_sort": "new",
    "time_filter": "day",
    "posts_per_subreddit": 10,
    "title_filters": {},
    "digest": {},
}


def list_monitors():
    """Return list of available monitor names (without .json extension)."""
    if not _CONFIG_DIR.exists():
        return []
    try:
        return sorted(p.stem for p in _CONFIG_DIR.glob("*.json"))
    except OSError:
        return []


def load_monitor(name):
    """Load a monitor config by name. Raises ValueError on validation failure."""
    if "/" in name or "\\" in name or name != Path(name).name.removesuffix(".json"):
        raise ValueError(f"Invalid monitor name: '{name}'")
    path = _CONFIG_DIR / f"{name}.json"
    if not path.exists():
        available = list_monitors()
        msg = f"Monitor '{name}' not found at {path}"
        if available:
            msg += f". Available monitors: {', '.join(available)}"
        raise ValueError(msg)

    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}") from e
    except (OSError, UnicodeError) as e:
        raise ValueError(f"Could not read monitor config {path}: {e}") from e

    if not isinstance(config, dict):
        raise ValueError(f"Monitor '{name}' must contain a JSON object")

    missing = _REQUIRED_FIELDS - set(config.keys())
    if missing:
        raise ValueError(f"Monitor '{name}' missing required fields: {', '.join(sorted(missing))}")

    if not isinstance(config["name"], str) or not config["name"].strip():
        raise ValueError(f"Monitor '{name}': 'name' must be a non-empty string")

    for field in ("subreddits", "keywords"):
        values = config[field]
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            raise ValueError(
                f"Monitor '{name}': '{field}' must be a non-empty list "
                "of non-empty strings"
            )
        config[field] = [value.strip() for value in values]

    for key, default in _DEFAULTS.items():
        config.setdefault(key, copy.deepcopy(default))

    if config["post_sort"] not in {"hot", "new", "top", "rising"}:
        raise ValueError(
            f"Monitor '{name}': 'post_sort' must be hot, new, top, or rising"
        )
    if config["time_filter"] not in {
        "hour", "day", "week", "month", "year", "all",
    }:
        raise ValueError(f"Monitor '{name}': invalid 'time_filter'")
    if (
        isinstance(config["posts_per_subreddit"], bool)
        or not isinstance(config["posts_per_subreddit"], int)
        or config["posts_per_subreddit"] < 1
    ):
        raise ValueError(
            f"Monitor '{name}': 'posts_per_subreddit' must be a positive integer"
        )

    title_filters = config["title_filters"]
    if not isinstance(title_filters, dict) or any(
        not isinstance(subreddit, str)
        or not subreddit.strip()
        or not isinstance(pattern, str)
        for subreddit, pattern in title_filters.items()
    ):
        raise ValueError(
            f"Monitor '{name}': 'title_filters' must map subreddit names to regex strings"
        )
    for subreddit, pattern in title_filters.items():
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(
                f"Monitor '{name}': invalid title filter for {subreddit}: {exc}"
            ) from exc

    digest = config["digest"]
    if not isinstance(digest, dict) or any(
        key not in {"title", "audience"}
        or not isinstance(value, str)
        or not value.strip()
        for key, value in digest.items()
    ):
        raise ValueError(
            f"Monitor '{name}': 'digest' must contain non-empty title/audience strings"
        )

    return copy.deepcopy(config)
