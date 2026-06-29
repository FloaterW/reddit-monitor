"""
Monitor config loader — reads JSON monitor profiles from config/monitors/.

A monitor profile defines subreddits, keywords, post settings, title filters,
and digest metadata. CLI arguments override config values, and config values
override code defaults.
"""

import json
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
    return sorted(p.stem for p in _CONFIG_DIR.glob("*.json"))


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

    missing = _REQUIRED_FIELDS - set(config.keys())
    if missing:
        raise ValueError(f"Monitor '{name}' missing required fields: {', '.join(sorted(missing))}")

    if not isinstance(config["subreddits"], list) or not config["subreddits"]:
        raise ValueError(f"Monitor '{name}': 'subreddits' must be a non-empty list")

    if not isinstance(config["keywords"], list) or not config["keywords"]:
        raise ValueError(f"Monitor '{name}': 'keywords' must be a non-empty list")

    for key, default in _DEFAULTS.items():
        config.setdefault(key, default)

    return config
