"""Typed Reddit source identities; submission links must never become comment links."""

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit


def source_url_key(source):
    """Validate identity and thread, not just the hostname, for typed API sources."""
    kind = source.get("source_kind")
    identifier = source.get("id", "")
    prefix = {"comment": "t1_", "submission": "t3_"}.get(kind)
    if not prefix or not isinstance(identifier, str) or not re.fullmatch(prefix + r"[a-z0-9]+", identifier):
        return None
    value = (source.get("comment_permalink") if kind == "comment" else source.get("post_permalink")) or source.get("permalink")
    try:
        url = urlsplit(value or "")
        if (url.scheme != "https" or url.hostname not in {"reddit.com", "www.reddit.com", "old.reddit.com"}
                or url.username or url.password or url.port not in {None, 443} or url.query or url.fragment):
            return None
        match = re.fullmatch(r"/r/([A-Za-z0-9_]+)/comments/([a-z0-9]+)/([\w%.-]+)/?(?:([a-z0-9]+)/?)?", url.path)
        if not match or match[1].casefold() != str(source.get("subreddit", "")).casefold():
            return None
        if kind == "submission":
            if match[4] or "t3_" + match[2] != identifier:
                return None
        elif match[4] != identifier[3:] or "t3_" + match[2] != source.get("link_id"):
            return None
        return url.path.rstrip("/").lower()
    except (TypeError, ValueError):
        return None


def source_time(source):
    value = source.get("created_utc")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        return datetime.strptime(source["created"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def unavailable(source):
    return bool(source.get("content_removed")) or str(source.get("body", "")).strip().casefold() in {"[removed]", "[deleted]"}
