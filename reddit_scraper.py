"""
Reddit Scraper (HTML Parsing - No API Key Needed)
===================================================
Pulls posts, comments, and search results from Reddit via command-line arguments.
No API credentials required - parses old.reddit.com HTML pages.

SETUP:
1. Install requests:  pip install requests
2. Run any command below.

USAGE EXAMPLES:
  Search all of Reddit (by relevance, all time):
    python reddit_scraper.py search "Tesla Model 3 Canada"

  Search within a specific subreddit:
    python reddit_scraper.py search "Model 3 price" --subreddit teslacanada

  Search with a custom sort/time window and limit:
    python reddit_scraper.py search "EV news" --sort top --time week --limit 20

  Get top posts from a subreddit (last week):
    python reddit_scraper.py posts teslacanada --sort top --time week --limit 15

  Get comments from a post URL (sorted by top):
    python reddit_scraper.py comments https://www.reddit.com/r/teslacanada/comments/xyz/ --sort top

  Save results to a custom file (otherwise a timestamped file is written):
    python reddit_scraper.py search "Tesla" --output my_results.json
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urlparse

import requests

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": os.getenv(
        "REDDIT_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    ),
})

_BASE = "https://old.reddit.com"


def _fetch(path, params=None):
    url = f"{_BASE}{path}"
    for attempt in range(3):
        try:
            resp = _SESSION.get(url, params=params, timeout=20)
        except requests.ConnectionError:
            print("  WARNING: Could not reach Reddit — skipping this request.")
            return None
        except requests.Timeout:
            print("  WARNING: Reddit request timed out — skipping this request.")
            return None

        if resp.status_code == 429:
            wait = min(int(resp.headers.get("Retry-After", 60)), 120)
            print(f"  Rate limited - waiting {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code == 403:
            print("  WARNING: Reddit returned 403 — skipping this request.")
            return None
        if resp.status_code == 404:
            return None

        try:
            resp.raise_for_status()
        except requests.HTTPError:
            print(f"  WARNING: HTTP {resp.status_code} — skipping this request.")
            return None
        return resp.text

    print("  WARNING: Reddit rate limit hit after retries — skipping this request.")
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _format_utc(timestamp) -> str:
    if not timestamp:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _author_name(author) -> str:
    if not author or author == "[deleted]":
        return "[deleted]"
    return str(author)


def _truncate(text: str, length: int) -> str:
    text = text or ""
    return text[:length] + "..." if len(text) > length else text


def _strip_html(html_text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_iso_time(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso_str


# ---------------------------------------------------------------------------
# HTML Parsers
# ---------------------------------------------------------------------------
def _parse_things(html, limit):
    """Parse 'thing' divs from old.reddit listing pages (subreddit posts)."""
    results = []
    for m in re.finditer(r"<div[^>]*data-type=\"link\"[^>]*>", html):
        if len(results) >= limit:
            break

        tag = m.group()

        def attr(name, default=""):
            a = re.search(rf'data-{name}="([^"]*)"', tag)
            return a.group(1) if a else default

        if attr("promoted") == "true":
            continue

        chunk = html[m.end() : m.end() + 2000]
        title_m = re.search(r'class="[^"]*title[^"]*"[^>]*>([^<]+)', chunk)

        timestamp_ms = int(attr("timestamp", "0"))

        entry = {
            "title": unescape(title_m.group(1).strip()) if title_m else "",
            "score": int(attr("score", "0")),
            "num_comments": int(attr("comments-count", "0")),
            "author": _author_name(attr("author")),
            "subreddit": attr("subreddit"),
            "url": attr("url"),
            "permalink": f"https://reddit.com{attr('permalink')}",
            "created": _format_utc(timestamp_ms / 1000) if timestamp_ms else "",
        }
        results.append(entry)

    return results


def _parse_search_results(html, limit):
    """Parse search result blocks from old.reddit search page."""
    results = []
    blocks = re.split(r"search-result\s+search-result-link", html)

    for block in blocks[1:]:
        if len(results) >= limit:
            break

        title_m = re.search(r'class="search-title[^"]*"[^>]*>([^<]+)', block)
        score_m = re.search(r'class="search-score">(\d+)', block)
        comments_m = re.search(
            r'class="[^"]*search-comments[^"]*"[^>]*>(\d+)\s*comment', block
        )
        author_m = re.search(r'class="author[^"]*"[^>]*>([^<]+)', block)
        subreddit_m = re.search(
            r'class="search-subreddit-link[^"]*"[^>]*>r/([^<]+)', block
        )
        time_m = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        permalink_m = re.search(
            r'class="search-title[^"]*"[^>]*href="([^"]+)"', block
        )
        body_m = re.search(
            r'class="search-result-body">(.*?)</div>', block, re.DOTALL
        )

        permalink = permalink_m.group(1) if permalink_m else ""
        permalink = permalink.replace("old.reddit.com", "reddit.com")
        if permalink and not permalink.startswith("http"):
            permalink = f"https://reddit.com{permalink}"

        entry = {
            "title": unescape(title_m.group(1).strip()) if title_m else "",
            "subreddit": subreddit_m.group(1) if subreddit_m else "",
            "score": int(score_m.group(1)) if score_m else 0,
            "num_comments": int(comments_m.group(1)) if comments_m else 0,
            "permalink": permalink,
            "created": _parse_iso_time(time_m.group(1) if time_m else ""),
            "author": _author_name(author_m.group(1) if author_m else None),
            "selftext": _truncate(
                _strip_html(body_m.group(1)) if body_m else "", 300
            ),
        }
        results.append(entry)

    return results


def _parse_comments(html, limit):
    """Parse comment things from old.reddit comment page with depth tracking.

    Uses html.parser to properly track <div> nesting so comment depth and
    parent_id are derived from the actual DOM tree structure, not heuristics.
    """
    from html.parser import HTMLParser

    # Pass 1: walk the DOM to find comment IDs, depths, and parents.
    class _DepthWalker(HTMLParser):
        def __init__(self):
            super().__init__()
            self.div_stack = []
            self.comment_depth = 0
            self.parent_stack = []
            self.results = []

        def handle_starttag(self, tag, attrs):
            if tag != "div":
                return
            a = dict(attrs)
            cls = a.get("class", "")
            div_role = None

            if "sitetable" in cls and a.get("id", "").startswith("siteTable_t1_"):
                div_role = "sitetable"
                parent_id = "t1_" + a["id"][len("siteTable_t1_"):]
                self.comment_depth += 1
                self.parent_stack.append(parent_id)
            elif a.get("data-type") == "comment":
                div_role = "comment"
                self.results.append({
                    "id": a.get("data-fullname", ""),
                    "author": a.get("data-author", ""),
                    "depth": self.comment_depth,
                    "parent_id": self.parent_stack[-1] if self.parent_stack else "",
                    "pos": self.getpos(),
                })

            self.div_stack.append(div_role)

        def handle_endtag(self, tag):
            if tag != "div" or not self.div_stack:
                return
            role = self.div_stack.pop()
            if role == "sitetable":
                self.comment_depth -= 1
                if self.parent_stack:
                    self.parent_stack.pop()

    walker = _DepthWalker()
    walker.feed(html)
    tree_info = {r["id"]: r for r in walker.results}

    # Pass 2: extract body / score / time via regex (faster than parsing).
    results = []
    for m in re.finditer(r'<div[^>]*data-type="comment"[^>]*>', html):
        if len(results) >= limit:
            break

        tag = m.group()
        fullname_m = re.search(r'data-fullname="([^"]*)"', tag)
        comment_id = fullname_m.group(1) if fullname_m else ""
        info = tree_info.get(comment_id, {})

        author_m = re.search(r'data-author="([^"]*)"', tag)
        chunk = html[m.end() : m.end() + 2000]
        score_m = re.search(r'class="[^"]*score[^"]*"[^>]*>(\d+)\s*point', chunk)
        time_m = re.search(r'<time[^>]*datetime="([^"]+)"', chunk)
        body_m = re.search(r'<div class="md">(.*?)</div>', chunk, re.DOTALL)

        entry = {
            "id": comment_id,
            "author": _author_name(author_m.group(1) if author_m else None),
            "score": int(score_m.group(1)) if score_m else 0,
            "body": _truncate(_strip_html(body_m.group(1)) if body_m else "", 500),
            "created": _parse_iso_time(time_m.group(1) if time_m else ""),
            "depth": info.get("depth", 0),
            "parent_id": info.get("parent_id", ""),
        }
        results.append(entry)

    return results


# ---------------------------------------------------------------------------
# 1. SEARCH REDDIT
# ---------------------------------------------------------------------------
def search_reddit(query, subreddit=None, sort="relevance", time_filter="all", limit=10):
    scope = f"r/{subreddit}" if subreddit else "all of Reddit"
    print(f'== Searching {scope} for: "{query}" [sort={sort}, time={time_filter}] ==\n')

    if subreddit:
        path = f"/r/{subreddit}/search"
        params = {
            "q": query,
            "sort": sort,
            "t": time_filter,
            "limit": limit,
            "restrict_sr": "on",
            "type": "link",
        }
    else:
        path = "/search"
        params = {
            "q": query,
            "sort": sort,
            "t": time_filter,
            "limit": limit,
            "type": "link",
        }

    html = _fetch(path, params)
    if not html:
        return []

    results = _parse_search_results(html, limit)

    for entry in results:
        print(f"  [{entry['score']:>5}] r/{entry['subreddit']} - {entry['title'][:70]}")
        print(f"         {entry['num_comments']} comments | {entry['created']}")
        print(f"         {entry['permalink']}\n")

    return results


# ---------------------------------------------------------------------------
# 2. GET SUBREDDIT POSTS
# ---------------------------------------------------------------------------
def get_subreddit_posts(subreddit_name, sort="hot", limit=10, time_filter="day"):
    print(f"== Posts from r/{subreddit_name} [{sort}] ==\n")

    path = f"/r/{subreddit_name}/{sort}/"
    params = {"limit": limit}
    if sort == "top":
        params["t"] = time_filter

    html = _fetch(path, params)
    if not html:
        print(f"ERROR: Subreddit r/{subreddit_name} not found.")
        sys.exit(1)

    results = _parse_things(html, limit)

    for entry in results:
        print(f"  [{entry['score']:>5}] {entry['title'][:80]}")
        print(f"         {entry['num_comments']} comments | {entry['created']}")
        print(f"         {entry['permalink']}\n")

    return results


# ---------------------------------------------------------------------------
# 3. GET POST COMMENTS
# ---------------------------------------------------------------------------
def get_post_comments(post_url, limit=20, sort="top"):
    print(f"== Comments from: {post_url} ==\n")

    parsed = urlparse(post_url)
    path = parsed.path.rstrip("/") + "/"
    params = {"sort": sort, "limit": limit}

    html = _fetch(path, params)
    if not html:
        print("ERROR: Could not fetch comments - check the post URL.")
        sys.exit(1)

    results = _parse_comments(html, limit)

    for entry in results:
        indent = "  " + "  " * entry.get("depth", 0)
        print(f"{indent}u/{entry['author']} [{entry['score']} pts] - {entry['created']}")
        print(f"{indent}{entry['body'][:200]}\n")

    return results


# ---------------------------------------------------------------------------
# 4. DEEP SEARCH (scan comments inside posts)
# ---------------------------------------------------------------------------
def _matches_query(query_lower, text, use_word_boundary=True):
    if use_word_boundary:
        return bool(re.search(r'\b' + re.escape(query_lower) + r'\b', text, re.IGNORECASE))
    return query_lower in text.lower()


def _dedup_key(comment):
    cid = comment.get("id", "")
    if cid:
        return cid
    import hashlib
    parts = (
        comment.get("author", ""),
        comment.get("body", ""),
        comment.get("created", ""),
        comment.get("post_permalink", ""),
    )
    blob = "\0".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(blob).hexdigest()


def _scan_subreddit_comments(subreddit_name, query_lower, sort, time_filter,
                             max_posts, max_results, comment_sorts,
                             use_word_boundary=True):
    """Scan one subreddit's posts and return matching comments."""
    path = f"/r/{subreddit_name}/{sort}/"
    params = {"limit": max_posts}
    if sort == "top":
        params["t"] = time_filter

    html = _fetch(path, params)
    if not html:
        print(f"  WARNING: r/{subreddit_name} not reachable, skipping.")
        return []

    posts = _parse_things(html, max_posts)
    if not posts:
        print(f"  No posts found in r/{subreddit_name}.")
        return []

    matches = []
    seen = set()

    for i, post in enumerate(posts):
        if len(matches) >= max_results:
            break

        print(f"  ({i + 1}/{len(posts)}) r/{subreddit_name}: {post['title'][:55]}...")

        permalink = post["permalink"].replace("https://reddit.com", "")
        post_hits = 0

        for cs in comment_sorts:
            if len(matches) >= max_results:
                break

            comment_html = _fetch(permalink, {"sort": cs, "limit": 500})
            if not comment_html:
                continue

            comments = _parse_comments(comment_html, 500)

            for comment in comments:
                if len(matches) >= max_results:
                    break
                key = _dedup_key(comment)
                if key in seen:
                    continue
                body = comment.get("body", "")
                if _matches_query(query_lower, body, use_word_boundary):
                    seen.add(key)
                    comment["post_title"] = post["title"]
                    comment["post_permalink"] = post["permalink"]
                    comment["subreddit"] = subreddit_name
                    matches.append(comment)
                    post_hits += 1

            if len(comment_sorts) > 1:
                time.sleep(0.5)

        if post_hits:
            print(f"         -> {post_hits} match(es)")

        if i < len(posts) - 1:
            time.sleep(1)

    return matches


def deep_search_comments(subreddits, query, sort="new", time_filter="day",
                         max_posts=10, max_results=50, comment_sorts=None,
                         use_word_boundary=True):
    if comment_sorts is None:
        comment_sorts = ["top", "new"]

    sub_list = subreddits if isinstance(subreddits, list) else [subreddits]
    sorts_label = "+".join(comment_sorts)

    print(f'== Deep searching for: "{query}" ==')
    print(f"   Subreddits: {', '.join('r/' + s for s in sub_list)}")
    print(f"   Posts: {max_posts} per sub | Comment sorts: {sorts_label}\n")

    all_matches = []

    for sub in sub_list:
        hits = _scan_subreddit_comments(
            sub, query.lower(), sort, time_filter,
            max_posts, max_results - len(all_matches), comment_sorts,
            use_word_boundary,
        )
        all_matches.extend(hits)
        if len(all_matches) >= max_results:
            break
        if sub != sub_list[-1]:
            time.sleep(1)

    print(f"\n== Found {len(all_matches)} comment(s) matching \"{query}\" ==\n")

    for m in all_matches:
        depth = m.get("depth", 0)
        reply_tag = f" (reply, depth {depth})" if depth > 0 else ""
        print(f"  [r/{m['subreddit']} | {m['post_title'][:50]}]")
        print(f"  u/{m['author']} [{m['score']} pts] - {m['created']}{reply_tag}")
        print(f"  {m['body'][:200]}")
        print()

    return all_matches


# ---------------------------------------------------------------------------
# SAVE RESULTS TO JSON
# ---------------------------------------------------------------------------
def save_to_json(data, filename) -> bool:
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"ERROR: Could not write results to {filename}: {e}")
        return False
    print(f"[OK] Results saved to: {filename}")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _positive_int(value: str) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"must be an integer, got {value!r}")
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {ivalue}")
    return ivalue


def _default_output(command: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"reddit_{command}_{stamp}.json"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="reddit_scraper",
        description="Pull Reddit posts, comments, or search results (no API key needed).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python reddit_scraper.py search "Tesla Model 3 Canada"
  python reddit_scraper.py search "EV news" --subreddit EVCanada --sort top --time week --limit 20
  python reddit_scraper.py deep-search "Chase" --subreddit churning,CreditCards --posts 10
  python reddit_scraper.py posts teslacanada --sort top --time week --limit 15
  python reddit_scraper.py comments https://www.reddit.com/r/teslacanada/comments/xyz/ --sort top
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    sp = subparsers.add_parser("search", help="Search Reddit by keyword")
    sp.add_argument("query", type=str, help='Search query, e.g. "Tesla Model 3 Canada"')
    sp.add_argument("--subreddit", type=str, default=None,
                    help="Limit search to a subreddit. Omit to search all of Reddit.")
    sp.add_argument("--sort", type=str, default="relevance",
                    choices=["relevance", "hot", "top", "new", "comments"],
                    help="Sort order (default: relevance)")
    sp.add_argument("--time", type=str, default="all",
                    choices=["hour", "day", "week", "month", "year", "all"],
                    dest="time_filter",
                    help="Time window (default: all)")
    sp.add_argument("--limit", type=_positive_int, default=10,
                    help="Max results (default: 10)")
    sp.add_argument("--output", type=str, default=None,
                    help="Output JSON filename (default: reddit_search_<timestamp>.json)")

    sp = subparsers.add_parser("posts", help="Get posts from a subreddit")
    sp.add_argument("subreddit", type=str, help="Subreddit name, e.g. teslacanada")
    sp.add_argument("--sort", type=str, default="hot",
                    choices=["hot", "new", "top", "rising"],
                    help="Sort order (default: hot)")
    sp.add_argument("--time", type=str, default="day",
                    choices=["hour", "day", "week", "month", "year", "all"],
                    dest="time_filter",
                    help="Time window for --sort top (default: day)")
    sp.add_argument("--limit", type=_positive_int, default=10,
                    help="Max posts (default: 10)")
    sp.add_argument("--output", type=str, default=None,
                    help="Output JSON filename (default: reddit_posts_<timestamp>.json)")

    sp = subparsers.add_parser("deep-search",
                               help="Search within comments of recent posts")
    sp.add_argument("query", type=str, help='Keyword to find in comments')
    sp.add_argument("--subreddit", type=str, required=True,
                    help="Comma-separated subreddits, e.g. churning,CreditCards")
    sp.add_argument("--sort", type=str, default="new",
                    choices=["hot", "new", "top", "rising"],
                    help="How to sort posts when scanning (default: new)")
    sp.add_argument("--time", type=str, default="day",
                    choices=["hour", "day", "week", "month", "year", "all"],
                    dest="time_filter",
                    help="Time window for posts (default: day)")
    sp.add_argument("--posts", type=_positive_int, default=10,
                    help="Number of posts to scan per subreddit (default: 10)")
    sp.add_argument("--limit", type=_positive_int, default=50,
                    help="Max matching comments to return (default: 50)")
    sp.add_argument("--substring", action="store_true", default=False,
                    help="Use substring matching instead of word-boundary (e.g. 'ink' would also match 'think')")
    sp.add_argument("--output", type=str, default=None,
                    help="Output JSON filename")

    sp = subparsers.add_parser("comments", help="Get comments from a post URL")
    sp.add_argument("url", type=str, help="Full Reddit post URL")
    sp.add_argument("--limit", type=_positive_int, default=25,
                    help="Max comments (default: 25)")
    sp.add_argument("--sort", type=str, default="top",
                    choices=["top", "new", "controversial", "old", "qa"],
                    help="Comment sort order (default: top)")
    sp.add_argument("--output", type=str, default=None,
                    help="Output JSON filename (default: reddit_comments_<timestamp>.json)")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    output = args.output or _default_output(args.command)

    try:
        if args.command == "search":
            results = search_reddit(
                query=args.query,
                subreddit=args.subreddit,
                sort=args.sort,
                time_filter=args.time_filter,
                limit=args.limit,
            )
            payload = {"query": args.query, "results": results}

        elif args.command == "posts":
            results = get_subreddit_posts(
                subreddit_name=args.subreddit,
                sort=args.sort,
                limit=args.limit,
                time_filter=args.time_filter,
            )
            payload = {"subreddit": args.subreddit, "results": results}

        elif args.command == "deep-search":
            subs = [s.strip() for s in args.subreddit.split(",") if s.strip()]
            results = deep_search_comments(
                subreddits=subs,
                query=args.query,
                sort=args.sort,
                time_filter=args.time_filter,
                max_posts=args.posts,
                max_results=args.limit,
                use_word_boundary=not args.substring,
            )
            payload = {"query": args.query, "subreddits": subs, "results": results}

        elif args.command == "comments":
            results = get_post_comments(
                post_url=args.url,
                limit=args.limit,
                sort=args.sort,
            )
            payload = {"url": args.url, "results": results}

    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except requests.HTTPError as e:
        print(f"ERROR: Reddit request failed ({e}).")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        sys.exit(1)

    if not results:
        print("\nNo results found.")
        if args.command == "search":
            print("Tip: try --sort relevance, widen --time (e.g. all), or simplify the query.")

    save_to_json(payload, output)


if __name__ == "__main__":
    main()
