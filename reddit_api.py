"""Bounded, read-only Reddit collection using an existing installed OAuth client.

Tokens stay in memory. This adapter never borrows browser cookies, sends private
account requests, or falls back to another transport when Reddit refuses access.
"""

import math
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from reddit_scraper import (
    _request_timeout,
    _retry_sleep,
    _rss_retry_delay,
    collection_warning,
)

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_API_BASE = "https://oauth.reddit.com"
_FULLNAME = re.compile(r"t[13]_[a-z0-9]+\Z")
_SUBREDDIT = re.compile(r"[A-Za-z0-9_]{1,64}\Z")


class RedditAPIClient:
    """One paced client shared across a digest's subreddit collections."""

    def __init__(self, client_id, user_agent, *, session=None, clock=None, sleeper=None):
        if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{5,128}", client_id):
            raise ValueError("Configure a valid REDDIT_CLIENT_ID for API collection")
        if (not isinstance(user_agent, str) or not user_agent.strip()
                or len(user_agent) > 512 or any(ord(c) < 32 or ord(c) > 126 for c in user_agent)
                or user_agent.lower().startswith("mozilla/")):
            raise ValueError("Configure REDDIT_API_USER_AGENT identifying your application")
        self._client_id = client_id
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": user_agent.strip(), "Accept": "application/json"})
        self._clock = clock or time.monotonic
        self._sleep = sleeper or _retry_sleep
        self._next_request = 0.0
        self._token = None
        self._token_expires = 0.0
        self._auth_failed = False
        self._closed = False
        self.request_count = 0

    @classmethod
    def from_environment(cls):
        return cls(os.getenv("REDDIT_CLIENT_ID", "").strip(),
                   os.getenv("REDDIT_API_USER_AGENT", "").strip())

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        """Best-effort revoke the temporary token; cleanup never masks a failure."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._token:
                # Do not violate a server cooldown just to clean up. These
                # app-only tokens have no refresh token and expire naturally.
                delay = max(0, self._next_request - self._clock())
                if delay <= 2:
                    if delay:
                        self._sleep(delay)
                    self._session.post(
                        "https://www.reddit.com/api/v1/revoke_token",
                        auth=(self._client_id, ""),
                        data={"token": self._token, "token_type_hint": "access_token"},
                        timeout=5, allow_redirects=False,
                    )
        except (requests.RequestException, TimeoutError):
            pass
        finally:
            self._token = None
            self._session.close()

    def _warn(self, sub, reason, *, request=False):
        prefix = "API request failed: " if request else "API "
        collection_warning(sub, prefix + reason + "; coverage is incomplete.")

    def _wait(self):
        delay = max(0, self._next_request - self._clock())
        if delay > 120:
            raise TimeoutError("API cooldown exceeds the bounded wait limit")
        if delay:
            self._sleep(delay)
        _request_timeout()  # Check the shared collection budget after waiting.

    def _rate_headers(self, response):
        try:
            remaining = float(response.headers.get("X-Ratelimit-Remaining", "nan"))
            reset = float(response.headers.get("X-Ratelimit-Reset", "nan"))
            if remaining <= 0 and math.isfinite(reset) and reset >= 0:
                self._next_request = max(self._next_request, self._clock() + reset + 1)
        except (TypeError, ValueError):
            pass

    def _transport(self, method, url, sub, **kwargs):
        for attempt in range(3):
            self._wait()
            try:
                self.request_count += 1
                response = self._session.request(
                    method, url, timeout=_request_timeout(), allow_redirects=False, **kwargs,
                )
            except requests.RequestException:
                self._next_request = max(self._next_request, self._clock() + 2 ** (attempt + 1))
                if attempt == 2:
                    self._warn(sub, "network failure after 3 attempts", request=True)
                    return None
                continue
            self._next_request = max(self._next_request, self._clock() + 2)
            self._rate_headers(response)
            status = response.status_code
            if status == 429:
                delay = _rss_retry_delay(response.headers.get("Retry-After"), 60)
                self._next_request = max(self._next_request, self._clock() + delay)
            elif status in {500, 502, 503, 504}:
                self._next_request = max(self._next_request, self._clock() + 2 ** (attempt + 1))
            else:
                return response
            if attempt == 2:
                self._warn(sub, f"HTTP {status} after 3 attempts", request=True)
                return None
        return None

    def _get_token(self, sub):
        if self._auth_failed:
            self._warn(sub, "authentication unavailable", request=True)
            return None
        if self._token and self._clock() < self._token_expires:
            return self._token
        response = self._transport(
            "POST", _TOKEN_URL, sub, auth=(self._client_id, ""),
            data={"grant_type": "https://oauth.reddit.com/grants/installed_client",
                  "device_id": "DO_NOT_TRACK_THIS_DEVICE"},
        )
        if response is None:
            return None
        if response.status_code != 200:
            self._warn(sub, f"authentication returned HTTP {response.status_code}", request=True)
            self._auth_failed = response.status_code in {400, 401, 403}
            return None
        try:
            payload = response.json()
            token = payload.get("access_token")
            expires = float(payload.get("expires_in", 0))
            if (not isinstance(token, str) or not token or any(c.isspace() for c in token)
                    or not math.isfinite(expires) or expires <= 0
                    or payload.get("token_type", "").lower() != "bearer"):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            self._warn(sub, "authentication returned an invalid token document", request=True)
            return None
        self._token = token
        self._token_expires = self._clock() + max(1, expires - min(60, expires / 2))
        return token

    def _get(self, path, sub, params):
        for refresh in range(2):
            token = self._get_token(sub)
            if not token:
                return None
            response = self._transport("GET", _API_BASE + path, sub, params=params,
                                       headers={"Authorization": "bearer " + token})
            if response is None:
                return None
            if response.status_code == 401 and not refresh:
                self._token_expires = 0
                continue
            if response.status_code != 200:
                self._warn(sub, f"HTTP {response.status_code}", request=True)
                return None
            try:
                payload = response.json()
                if (not isinstance(payload, dict) or payload.get("kind") != "Listing"
                        or not isinstance(payload.get("data"), dict)
                        or not isinstance(payload["data"].get("children"), list)):
                    raise ValueError
                return payload["data"]
            except (ValueError, TypeError):
                self._warn(sub, "invalid listing document", request=True)
                return None
        return None

    @staticmethod
    def _timestamp(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None

    @staticmethod
    def _post_path(value, subreddit, post_id):
        if not isinstance(value, str):
            return None
        parsed = urlparse(value)
        if parsed.netloc and (parsed.scheme != "https" or parsed.hostname not in {
                "reddit.com", "www.reddit.com", "old.reddit.com"}):
            return None
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            return None
        match = re.fullmatch(r"/r/([A-Za-z0-9_]+)/comments/([a-z0-9]+)/([\w%.-]+)/?(?:[a-z0-9]+/?)?", parsed.path)
        if not match or match[1].lower() != subreddit.lower() or "t3_" + match[2] != post_id:
            return None
        return f"https://reddit.com/r/{match[1]}/comments/{match[2]}/{match[3]}/"

    def _comment(self, item, sub):
        if not isinstance(item, dict) or item.get("kind") != "t1":
            return None
        data = item.get("data")
        if not isinstance(data, dict):
            return None
        identifier = data.get("name", "t1_" + str(data.get("id", "")))
        link_id = data.get("link_id", "")
        parent = data.get("parent_id", "")
        created = self._timestamp(data.get("created_utc"))
        body = data.get("body")
        if (not isinstance(identifier, str) or not re.fullmatch(r"t1_[a-z0-9]+", identifier)
                or identifier != "t1_" + str(data.get("id", ""))
                or not isinstance(link_id, str) or not re.fullmatch(r"t3_[a-z0-9]+", link_id)
                or not isinstance(parent, str) or not _FULLNAME.fullmatch(parent)
                or (parent.startswith("t3_") and parent != link_id)
                or str(data.get("subreddit", "")).lower() != sub.lower()
                or created is None or not isinstance(body, str) or not body.strip()):
            return None
        permalink = data.get("permalink")
        post_url = self._post_path(permalink, sub, link_id)
        if permalink:
            # A conflicting source identity must not be repaired into a
            # plausible-looking citation to a different comment.
            if post_url is None or urlparse(permalink).path.rstrip("/").split("/")[-1] != identifier[3:]:
                return None
        else:
            post_url = self._post_path(data.get("link_permalink"), sub, link_id)
        if post_url is None:
            return None
        author = data.get("author")
        author = author if isinstance(author, str) and author else "[deleted]"
        score = data.get("score", 0)
        score = score if type(score) is int else 0
        return {"id": identifier, "author": author.removeprefix("/u/").removeprefix("u/"),
                "score": score, "body": body, "created": created.strftime("%Y-%m-%d %H:%M UTC"),
                "depth": 0, "parent_id": parent, "subreddit": sub,
                "post_title": data.get("link_title") if isinstance(data.get("link_title"), str) else "",
                "post_permalink": post_url, "comment_permalink": post_url + identifier[3:] + "/",
                "link_id": link_id, "collection_source": "reddit_api", "source_kind": "comment",
                "created_utc": created.timestamp(), "edited_utc": self._edited(data.get("edited")),
                "content_removed": body.strip().casefold() in {"[removed]", "[deleted]"}}

    def _edited(self, value):
        timestamp = self._timestamp(value)
        return timestamp.timestamp() if timestamp else None

    def _submission(self, item, sub):
        if not isinstance(item, dict) or item.get("kind") != "t3" or not isinstance(item.get("data"), dict):
            return None
        data = item["data"]
        identifier = data.get("name", "")
        created = self._timestamp(data.get("created_utc"))
        title, body = data.get("title"), data.get("selftext", "")
        if (not isinstance(identifier, str) or not re.fullmatch(r"t3_[a-z0-9]+", identifier)
                or identifier != "t3_" + str(data.get("id", "")) or created is None
                or str(data.get("subreddit", "")).casefold() != sub.casefold()
                or not isinstance(title, str) or not title.strip() or not isinstance(body, str)):
            return None
        url = self._post_path(data.get("permalink"), sub, identifier)
        if not url or urlparse(data["permalink"]).path.rstrip("/").split("/")[-1] != urlparse(url).path.rstrip("/").split("/")[-1]:
            return None
        author = data.get("author")
        removed = bool(data.get("removed_by_category")) or body.strip().casefold() in {"[removed]", "[deleted]"}
        return {"id": identifier, "source_kind": "submission", "link_id": identifier,
                "author": author if isinstance(author, str) and author else "[deleted]",
                "body": "[removed]" if removed else title.strip() + ("\n\n" + body if body.strip() else ""),
                "post_title": title, "post_permalink": url, "permalink": url,
                "subreddit": sub, "created": created.strftime("%Y-%m-%d %H:%M UTC"),
                "created_utc": created.timestamp(), "edited_utc": self._edited(data.get("edited")),
                "score": data.get("score") if type(data.get("score")) is int else 0,
                "parent_id": "", "depth": 0, "context_status": "top_level",
                "content_removed": removed, "collection_source": "reddit_api"}

    def fetch_submissions(self, subreddit, cutoff, window_end, title_filter=None, max_pages=5):
        """Bounded /new listing: titles and self text are first-class post evidence."""
        if (not isinstance(subreddit, str) or not _SUBREDDIT.fullmatch(subreddit)
                or type(max_pages) is not int or not 1 <= max_pages <= 30
                or not isinstance(window_end, datetime) or window_end.tzinfo is None
                or cutoff is not None and (not isinstance(cutoff, datetime) or cutoff.tzinfo is None or cutoff > window_end)):
            raise ValueError("Invalid submission collection window or limits")
        pattern = re.compile(title_filter, re.I) if title_filter else None
        records, cursors, cursor = {}, set(), None
        try:
            for _ in range(max_pages):
                params = {"limit": 100, "raw_json": 1}
                if cursor:
                    params["after"] = cursor
                page = self._get(f"/r/{subreddit}/new", subreddit, params)
                if page is None:
                    break
                times, added = [], 0
                for item in page["children"]:
                    row = self._submission(item, subreddit)
                    times.append(self._timestamp(item.get("data", {}).get("created_utc"))
                                 if isinstance(item, dict) and isinstance(item.get("data"), dict) else None)
                    if row is None:
                        self._warn(subreddit, "submission listing contains invalid identity/content")
                    elif row["id"] not in records:
                        records[row["id"]] = row
                        added += 1
                if cutoff is not None and times and all(t is not None and t < cutoff for t in times):
                    break
                cursor = page.get("after")
                if not isinstance(cursor, str) or not re.fullmatch(r"t3_[a-z0-9]+", cursor):
                    # The final mixed page proves the cutoff when sorted /new
                    # includes older posts; an empty/unbounded tail does not.
                    if cutoff is not None and not any(t is not None and t < cutoff for t in times):
                        self._warn(subreddit, "submission listing ended before the requested time boundary")
                    break
                if not added or cursor in cursors:
                    self._warn(subreddit, "submission listing repeated a page or cursor")
                    break
                cursors.add(cursor)
            else:
                self._warn(subreddit, f"submission listing reached the {max_pages}-page limit")
        except TimeoutError:
            self._warn(subreddit, "submission collection budget or cooldown limit reached")
        return [row for row in records.values() if not pattern or pattern.search(row["post_title"])]

    def refresh_sources(self, sources, max_batches=5):
        """Refresh cached source bodies. Transport failure is never a deletion."""
        if type(max_batches) is not int or not 1 <= max_batches <= 30:
            raise ValueError("Invalid refresh budget")
        by_id = {row["id"]: row for row in sources if _FULLNAME.fullmatch(str(row.get("id", "")))}
        updates, unavailable, failed = {}, set(), set()
        identifiers = sorted(by_id)
        for offset in range(0, min(len(identifiers), max_batches * 100), 100):
            batch = identifiers[offset:offset + 100]
            try:
                data = self._get("/api/info", "", {"id": ",".join(batch), "raw_json": 1})
            except TimeoutError:
                data = None
            if data is None:
                failed.update(batch)
                continue
            found = set()
            for item in data["children"]:
                identifier = item.get("data", {}).get("name") if isinstance(item, dict) and isinstance(item.get("data"), dict) else None
                if identifier not in batch:
                    continue
                sub = by_id[identifier]["subreddit"]
                row = self._submission(item, sub) if identifier.startswith("t3_") else self._comment(item, sub)
                if row is None:
                    failed.add(identifier)
                    found.add(identifier)
                else:
                    found.add(identifier)
                    # /api/info comments may omit link_title. Preserve the
                    # known title; it remains labelled context, not evidence.
                    if not row.get("post_title"):
                        row["post_title"] = by_id[identifier].get("post_title", "")
                    updates[identifier] = row
            unavailable.update(set(batch) - found)
        failed.update(identifiers[max_batches * 100:])
        return updates, unavailable, failed

    def _info(self, fullnames, sub):
        result = []
        identifiers = sorted(set(fullnames))
        for offset in range(0, len(identifiers), 100):
            batch = identifiers[offset:offset + 100]
            data = self._get("/api/info", sub, {"id": ",".join(batch), "raw_json": 1})
            if data is None:
                break
            allowed = set(batch)
            result.extend(item for item in data["children"] if isinstance(item, dict)
                          and isinstance(item.get("data"), dict)
                          and item["data"].get("name") in allowed)
        return result

    @staticmethod
    def _ancestry_problem(source, records):
        """Return a chain problem and a missing ID without changing source data."""
        seen = {source["id"]}
        current = source
        for _ in range(65):
            parent_id = current["parent_id"]
            if not parent_id.startswith("t1_"):
                return None, None
            if parent_id in seen:
                return "cycle", None
            seen.add(parent_id)
            if parent_id not in records:
                return "missing", parent_id
            current = records[parent_id]
            if (current["link_id"] != source["link_id"]
                    or current["post_permalink"] != source["post_permalink"]):
                return "thread", None
        return "depth", None

    def fetch_subreddit(self, subreddit, cutoff, window_end, title_filter=None, max_pages=30,
                        include_submissions=False):
        """Fetch recent comments and bounded missing parent context, retaining gaps."""
        if not isinstance(subreddit, str) or not _SUBREDDIT.fullmatch(subreddit):
            raise ValueError("Invalid API subreddit")
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise ValueError("API max_pages must be between 1 and 100")
        if (not isinstance(window_end, datetime) or window_end.tzinfo is None
                or (cutoff is not None and (not isinstance(cutoff, datetime)
                                           or cutoff.tzinfo is None or cutoff > window_end))):
            raise ValueError("API collection requires an ordered timezone-aware window")
        pattern = re.compile(title_filter, re.I) if title_filter else None
        records = {}
        submissions = {}
        cursor = None
        cursors = set()

        def is_current(source):
            created = datetime.strptime(source["created"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
            return created <= window_end and (cutoff is None or created >= cutoff)

        try:
            for _ in range(max_pages):
                params = {"limit": 100, "raw_json": 1}
                if cursor:
                    params["after"] = cursor
                data = self._get(f"/r/{subreddit}/comments", subreddit, params)
                if data is None:
                    break
                page = data["children"]
                page_times, added = [], 0
                for item in page:
                    comment = self._comment(item, subreddit)
                    created = self._timestamp(item.get("data", {}).get("created_utc")) if isinstance(item, dict) and isinstance(item.get("data"), dict) else None
                    page_times.append(created)
                    if comment is None:
                        self._warn(subreddit, "listing contains an invalid comment")
                        continue
                    if comment["id"] not in records:
                        records[comment["id"]] = comment
                        added += 1
                if cutoff is not None and page_times and all(t is not None and t < cutoff for t in page_times):
                    break
                if not page:
                    if cutoff is not None:
                        self._warn(subreddit, "listing ended before the requested time boundary was confirmed")
                    break
                if not added:
                    self._warn(subreddit, "listing repeated a page")
                    break
                cursor = data.get("after")
                if not isinstance(cursor, str) or not re.fullmatch(r"t1_[a-z0-9]+", cursor):
                    if cutoff is not None:
                        self._warn(subreddit, "pagination cursor unavailable before the requested time boundary")
                    break
                if cursor in cursors:
                    self._warn(subreddit, "pagination cursor repeated")
                    break
                cursors.add(cursor)
            else:
                self._warn(subreddit, f"listing reached the {max_pages}-page limit")

            # Actual submission titles supersede incidental listing title fields.
            metadata = {c["link_id"]: (c["post_title"], c["post_permalink"])
                        for c in records.values() if c["post_title"]}
            for item in self._info({c["link_id"] for c in records.values()}, subreddit):
                if include_submissions:
                    submission = self._submission(item, subreddit)
                    if submission:
                        submissions[submission["id"]] = submission
                data = item["data"]
                identifier = data.get("name", "")
                if (item.get("kind") != "t3" or str(data.get("subreddit", "")).lower() != subreddit.lower()
                        or identifier != "t3_" + str(data.get("id", ""))
                        or not isinstance(data.get("title"), str)):
                    self._warn(subreddit, "thread metadata contains an invalid submission")
                    continue
                post_url = self._post_path(data.get("permalink"), subreddit, identifier)
                if post_url:
                    metadata[identifier] = (data["title"], post_url)
            for comment in records.values():
                if comment["link_id"] in metadata:
                    comment["post_title"], comment["post_permalink"] = metadata[comment["link_id"]]
                    comment["comment_permalink"] = comment["post_permalink"] + comment["id"][3:] + "/"
                elif not comment["post_title"]:
                    self._warn(subreddit, "thread title unavailable")
            if pattern:
                records = {sid: c for sid, c in records.items() if pattern.search(c["post_title"])}

            # Only current eligible children need expanded ancestry. The final
            # old listing page can contain many unrelated historical replies.
            def needed_parents():
                missing = set()
                for source in list(records.values()):
                    if not is_current(source):
                        continue
                    _, missing_id = self._ancestry_problem(source, records)
                    if missing_id:
                        missing.add(missing_id)
                return missing

            attempted = set()
            for _ in range(8):
                missing = needed_parents() - attempted
                if not missing:
                    break
                expected = {sid: {c["link_id"] for c in records.values() if c["parent_id"] == sid}
                            for sid in missing}
                attempted.update(missing)
                for item in self._info(missing, subreddit):
                    parent = self._comment(item, subreddit)
                    if parent is None or parent["link_id"] not in expected.get(parent["id"], set()):
                        self._warn(subreddit, "parent context has invalid identity or thread")
                        continue
                    if parent["link_id"] in metadata:
                        parent["post_title"], parent["post_permalink"] = metadata[parent["link_id"]]
                        parent["comment_permalink"] = parent["post_permalink"] + parent["id"][3:] + "/"
                    records[parent["id"]] = parent
        except TimeoutError:
            self._warn(subreddit, "collection budget or cooldown limit reached; keeping collected comments")

        # Apply a title gate even when metadata enrichment hit its budget.
        records = {sid: c for sid, c in records.items()
                   if not pattern or pattern.search(c["post_title"])}
        problems = set()
        for source in records.values():
            if not is_current(source):
                continue
            issue, _ = self._ancestry_problem(source, records)
            if issue:
                source["context_status"] = "partial"
                problems.add(issue)
        if problems:
            detail = " (cycle detected)" if "cycle" in problems else ""
            self._warn(subreddit, "parent context unavailable or depth limit reached" + detail)
        roots = {c["link_id"] for c in records.values() if is_current(c)}
        return list(records.values()) + [r for sid, r in submissions.items() if sid in roots]
