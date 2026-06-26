"""
Daily Reddit Digest — scrapes configured subreddits for keywords,
then produces a themed summary via LLM summarization.

Usage:
  python daily_digest.py                     # run with defaults
  python daily_digest.py --posts 15          # scan more posts per sub
  python daily_digest.py --time week         # wider time window
  python daily_digest.py --save digest.md    # save summary to file
"""

import os, sys, json, time, re, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Load .env file if present (no extra dependency needed)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ---------------------------------------------------------------------------
# Config — edit these to match your interests
# ---------------------------------------------------------------------------
SUBREDDITS = ["churning", "CreditCards", "awardtravel", "churningcanada"]

# Per-subreddit post title filters (regex). Only posts matching the pattern
# are scraped. Omit a subreddit to scrape all its posts.
POST_TITLE_FILTERS = {
    "churningcanada": r"(?i)data\s*point\s*weekly|US\s*churning\s*discussion",
}

KEYWORDS = [
    "chase", "amex", "citi", "capital one",
    "sapphire", "ink",
    "CIP", "CIC", "CSR", "CSP", "CIU",
    "c1", "vx", "bonvoy",
    "SUB", "bonus", "retention",
    "paze", "bilt", "palladium",
    "hyatt", "hilton", "marriott",
    "boa", "alaska", "summit",
    "5/24",
]

POSTS_PER_SUB = 10
MAX_RESULTS_PER_KEYWORD = 30
POST_SORT = "new"
TIME_FILTER = "day"

LLM_MODEL = "claude-sonnet-4-6"

# Email — leave GMAIL_APP_PASSWORD empty to skip emailing.
# Generate an app password at https://myaccount.google.com/apppasswords
EMAIL_TO = os.getenv("DIGEST_EMAIL_TO", "your_email@gmail.com")
EMAIL_FROM = os.getenv("DIGEST_EMAIL_FROM", "your_email@gmail.com")
GMAIL_APP_PASSWORD = ""

_pw_file = Path(__file__).parent / ".gmail_app_password"
if _pw_file.exists():
    GMAIL_APP_PASSWORD = _pw_file.read_text().strip()


# ---------------------------------------------------------------------------
# Scraping — fetch once, filter locally for all keywords
# ---------------------------------------------------------------------------
from reddit_scraper import (
    _fetch, _parse_things, _parse_comments, _dedup_key, _matches_query,
)


def _fetch_all_comments(subreddits, posts_per_sub, post_sort, time_filter):
    """Fetch comments from recent posts across subreddits (one pass)."""
    all_comments = []

    for sub in subreddits:
        print(f"\n  Fetching r/{sub} posts...")
        path = f"/r/{sub}/{post_sort}/"
        params = {"limit": posts_per_sub}
        if post_sort == "top":
            params["t"] = time_filter

        html = _fetch(path, params)
        if not html:
            print(f"    WARNING: r/{sub} not reachable, skipping.")
            continue

        posts = _parse_things(html, posts_per_sub)

        title_pat = POST_TITLE_FILTERS.get(sub)
        if title_pat:
            posts = [p for p in posts if re.search(title_pat, p["title"])]
            print(f"    Found {len(posts)} posts (filtered for title pattern)")
        else:
            print(f"    Found {len(posts)} posts")

        for i, post in enumerate(posts):
            print(f"    ({i + 1}/{len(posts)}) {post['title'][:55]}...")
            permalink = post["permalink"].replace("https://reddit.com", "")
            seen_in_post = set()

            for sort_order in ["top", "new"]:
                comment_html = _fetch(permalink, {"sort": sort_order, "limit": 500})
                if not comment_html:
                    continue

                comments = _parse_comments(comment_html, 500)
                for c in comments:
                    key = _dedup_key(c)
                    if key not in seen_in_post:
                        seen_in_post.add(key)
                        c["subreddit"] = sub
                        c["post_title"] = post["title"]
                        c["post_permalink"] = post["permalink"]
                        all_comments.append(c)

                time.sleep(1)

            if i < len(posts) - 1:
                time.sleep(1.5)

        if sub != subreddits[-1]:
            time.sleep(2)

    return all_comments


TIME_WINDOW_HOURS = {"hour": 1, "day": 24, "week": 168, "month": 720, "year": 8760}


def _comment_in_window(created_str, cutoff_dt):
    """Return True if comment timestamp is after cutoff."""
    if not created_str:
        return True
    try:
        dt = datetime.strptime(created_str, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        return dt >= cutoff_dt
    except ValueError:
        return True


def scrape_all(keywords, subreddits, posts_per_sub, time_filter):
    """Fetch all comments once, then filter for any matching keyword."""
    raw = _fetch_all_comments(subreddits, posts_per_sub, POST_SORT, time_filter)
    print(f"\n  Total comments fetched: {len(raw)}")

    hours = TIME_WINDOW_HOURS.get(time_filter, 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent = [c for c in raw if _comment_in_window(c.get("created", ""), cutoff)]
    print(f"  Comments within last {hours}h: {len(recent)} (filtered {len(raw) - len(recent)} older)")

    matched = []
    seen = set()

    for c in recent:
        body = c.get("body", "")
        hits = [kw for kw in keywords if _matches_query(kw.lower(), body)]
        if hits:
            key = _dedup_key(c)
            if key not in seen:
                seen.add(key)
                c["matched_keywords"] = hits
                matched.append(c)

    print(f"  Comments matching keywords: {len(matched)}")

    kw_counts = {}
    for c in matched:
        for kw in c["matched_keywords"]:
            kw_counts[kw] = kw_counts.get(kw, 0) + 1
    for kw, count in sorted(kw_counts.items(), key=lambda x: -x[1]):
        print(f"    {kw}: {count}")

    return matched


# ---------------------------------------------------------------------------
# Summarisation
# ---------------------------------------------------------------------------
SUMMARY_PROMPT = """\
You are a credit card churning and award travel analyst. Below are Reddit comments \
scraped from r/churning, r/CreditCards, r/awardtravel, and r/churningcanada in the \
last {time_window}.

Your job: produce a **Daily Digest** that someone in the churning community would \
find valuable. Organize by theme, not by subreddit or keyword.

Rules:
- Lead with the most actionable or time-sensitive items (new offers, expiring deals, \
  policy changes).
- Use specific numbers: point values, dollar amounts, dates, ratios.
- Attribute claims to usernames as clickable markdown links using the permalink \
  provided for each comment. Format: [u/name](permalink). This lets the reader \
  click through to the original comment thread.
- Flag anything that's a single unconfirmed data point vs. widely corroborated.
- If comments contradict each other, note both sides.
- Do NOT fabricate details that aren't in the source comments.
- Do NOT pad with generic advice — only summarize what was actually discussed.
- Do NOT collapse multiple distinct topics into a single combined section. Give each \
  distinct topic its own section header (e.g. separate "Chase Portal Update" and \
  "CSP Hotel Credit Claw-back DP" instead of one combined "Chase Updates" section).
- Include EVERY piece of information from the source comments. Do not skip or omit \
  data points, tips, warnings, or discussions — completeness is critical.

Formatting:
- Start with a markdown H1 date header.
- Use H2 (##) for each themed section with an emoji prefix that signals urgency or \
  topic type. Examples: 🚨 for time-sensitive, ⏰ for deadlines, 💳 for card-specific, \
  🆕 for new offers/partners, 📊 for data/tracking, 📉 for negative DPs, 🏦 for bank \
  bonuses, 🌍 for award travel, 🔧 for policy/misc.
- Use horizontal rules (---) between sections for visual separation.
- When listing credits or benefits by deadline, break into clear sub-groups \
  (e.g. "Q2 quarterly credits" vs "Semi-annual H1 credits") with bold labels.
- Use bold for card names, dollar amounts, dates, and key terms.
- Keep bullet points detailed but scannable — each should stand alone as useful info.

---
SOURCE COMMENTS ({count} total):

{comments}
---

Write the digest now."""


def format_comments_for_prompt(comments):
    lines = []
    for c in comments:
        sub = c.get("subreddit", "?")
        post = c.get("post_title", "?")
        author = c.get("author", "?")
        score = c.get("score", 0)
        created = c.get("created", "?")
        body = c.get("body", "")
        keywords = ", ".join(c.get("matched_keywords", []))
        depth = c.get("depth", 0)
        parent = c.get("parent_id", "")
        depth_tag = f" [reply depth={depth}, parent={parent[-7:]}]" if depth > 0 else ""
        permalink = c.get("post_permalink", "")
        comment_id = c.get("id", "").replace("t1_", "")
        if permalink and comment_id:
            comment_link = permalink.rstrip("/") + "/" + comment_id + "/"
        else:
            comment_link = permalink
        lines.append(
            f"[r/{sub} | {post}] u/{author} ({score} pts, {created})"
            f"{depth_tag} [kw:{keywords}]\nPermalink: {comment_link}\n{body}\n"
        )
    return "\n".join(lines)


def summarize(comments, time_window="24 hours"):
    prompt_text = SUMMARY_PROMPT.format(
        time_window=time_window,
        count=len(comments),
        comments=format_comments_for_prompt(comments),
    )

    print(f"\nSending {len(comments)} comments for summarization...\n")

    try:
        result = subprocess.run(
            ["claude", "-p", "--model", LLM_MODEL],
            input=prompt_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Summarization timed out after 10 minutes")

    if result.returncode != 0:
        raise RuntimeError(f"Summarization failed: {result.stderr.strip()}")

    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def _preprocess_md(md_text):
    """Preprocess markdown for the markdown lib: ensure blank lines before lists,
    and double indentation on nested list items (the lib requires 4-space nesting)."""
    lines = md_text.split("\n")
    out = []
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        is_list = stripped.startswith("- ") or re.match(r"^\d+\.\s", stripped)
        if is_list:
            indent = len(line) - len(stripped)
            if indent > 0:
                line = " " * (indent * 2) + stripped
            if i > 0 and out and out[-1].strip() != "":
                prev = out[-1].lstrip()
                prev_is_list = prev.startswith("- ") or re.match(r"^\d+\.\s", prev)
                if not prev_is_list and prev != "---":
                    out.append("")
        out.append(line)
    return "\n".join(out)


def _wrap_html_email(inner_html, title):
    """Wrap converted markdown in a styled email template."""
    return f"""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#f0f0f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;-webkit-text-size-adjust:100%">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f0f0f5">
    <tr><td align="center" style="padding:24px 16px">
      <table role="presentation" width="680" cellpadding="0" cellspacing="0"
             style="max-width:680px;width:100%;background:#ffffff;border-radius:8px;
                    box-shadow:0 2px 8px rgba(0,0,0,0.08);overflow:hidden">
        <!-- Header -->
        <tr><td style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:28px 32px">
          <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:600;letter-spacing:-0.3px">
            {title}
          </h1>
          <p style="margin:6px 0 0;color:#a0aec0;font-size:13px">
            Auto-generated from r/churning, r/CreditCards, r/awardtravel, r/churningcanada
          </p>
        </td></tr>
        <!-- Body -->
        <tr><td style="padding:32px 32px;color:#1a1a1a;font-size:15px;line-height:1.7">
          {inner_html}
        </td></tr>
        <!-- Footer -->
        <tr><td style="padding:20px 32px;border-top:1px solid #e2e8f0;
                       font-size:12px;color:#a0aec0;text-align:center">
          Reddit Digest &middot; Delivered daily at 6:30 PM
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _inline_styles(html):
    """Inject inline CSS into HTML tags for email client compatibility."""
    styles = {
        "h1": "font-size:24px;color:#1a1a1a;margin:0 0 4px 0;padding:0;font-weight:700;line-height:1.3",
        "h2": "font-size:19px;color:#1a1a1a;margin:28px 0 12px 0;padding:0;font-weight:700;line-height:1.3",
        "h3": "font-size:16px;color:#1a1a1a;margin:20px 0 8px 0;font-weight:600",
        "p": "margin:10px 0;color:#1a1a1a;line-height:1.7;font-size:15px",
        "ul": "margin:10px 0 14px 0;padding-left:28px;line-height:1.7",
        "ol": "margin:10px 0 14px 0;padding-left:28px;line-height:1.7",
        "li": "margin:6px 0;color:#1a1a1a;line-height:1.7;font-size:15px",
        "strong": "color:#1a1a1a;font-weight:700",
        "em": "font-style:italic",
        "a": "color:#4183c4;text-decoration:none",
        "hr": "border:none;border-top:1px solid #e2e8f0;margin:24px 0",
        "blockquote": "margin:14px 0;padding:8px 16px;border-left:4px solid #3182ce;background:#f7fafc;color:#555;font-style:italic;border-radius:0 4px 4px 0",
    }
    for tag, style in styles.items():
        html = re.sub(
            rf"<{tag}(?![^>]*style=)(\s|>)",
            f'<{tag} style="{style}"\\1',
            html,
        )
    html = re.sub(
        r"(<li[^>]*>(?:(?!</li>).)*?)<(ul|ol)\s+style=\"([^\"]*)\"",
        lambda m: m.group(1) + f'<{m.group(2)} style="{m.group(3)};padding-left:24px;margin:4px 0 4px 0"',
        html,
        flags=re.DOTALL,
    )
    return html


def send_email(subject, body_md):
    """Send the digest as an HTML email via Gmail SMTP."""
    if not GMAIL_APP_PASSWORD:
        print("[SKIP] No Gmail app password configured — email not sent.")
        return False

    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    processed_md = _preprocess_md(body_md)
    try:
        import markdown
        inner = markdown.markdown(
            processed_md,
            extensions=["tables", "fenced_code", "sane_lists"],
        )
    except ImportError:
        inner = f"<pre style='font-family:sans-serif;white-space:pre-wrap'>{body_md}</pre>"

    inner = _inline_styles(inner)
    html_body = _wrap_html_email(inner, subject)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(body_md, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, GMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"[OK] Digest emailed to {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"[ERROR] Email failed: {e}")
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Daily Reddit digest — scrape + summarize"
    )
    parser.add_argument("--posts", type=int, default=POSTS_PER_SUB,
                        help=f"Posts to scan per subreddit (default: {POSTS_PER_SUB})")
    parser.add_argument("--time", type=str, default=TIME_FILTER,
                        choices=["hour", "day", "week", "month", "year", "all"],
                        dest="time_filter",
                        help="Time window (default: day)")
    parser.add_argument("--save", type=str, default=None,
                        help="Save summary to a markdown file")
    parser.add_argument("--save-raw", type=str, default=None,
                        help="Also save raw scraped comments to JSON")
    parser.add_argument("--subreddits", type=str, default=None,
                        help="Override subreddits (comma-separated)")
    parser.add_argument("--keywords", type=str, default=None,
                        help="Override keywords (comma-separated)")
    args = parser.parse_args()

    subs = [s.strip() for s in args.subreddits.split(",")] if args.subreddits else SUBREDDITS
    kws = [k.strip() for k in args.keywords.split(",")] if args.keywords else KEYWORDS

    print("=" * 60)
    print(f"  DAILY REDDIT DIGEST — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Subreddits: {', '.join('r/' + s for s in subs)}")
    print(f"  Keywords: {', '.join(kws)}")
    print(f"  Time window: {args.time_filter}")
    print("=" * 60)

    comments = scrape_all(kws, subs, args.posts, args.time_filter)

    if not comments:
        print("\nNo comments found. Nothing to summarize.")
        return

    print(f"\n{'=' * 60}")
    print(f"  Scraped {len(comments)} unique comments. Summarizing...")
    print(f"{'=' * 60}")

    if args.save_raw:
        with open(args.save_raw, "w", encoding="utf-8") as f:
            json.dump(comments, f, indent=2, ensure_ascii=False)
        print(f"[OK] Raw comments saved to: {args.save_raw}")

    time_label = {"hour": "hour", "day": "24 hours", "week": "week",
                  "month": "month", "year": "year", "all": "all time"}
    try:
        summary = summarize(comments, time_label.get(args.time_filter, "24 hours"))
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        print("Raw data was saved — rerun with a longer timeout or fewer posts.")
        sys.exit(1)

    print("\n" + summary)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(summary)
        print(f"\n[OK] Summary saved to: {args.save}")
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        default_path = f"digest_{stamp}.md"
        with open(default_path, "w", encoding="utf-8") as f:
            f.write(summary)
        print(f"\n[OK] Summary saved to: {default_path}")

    date_str = datetime.now().strftime("%B %d, %Y")
    send_email(f"Churning Digest — {date_str}", summary)


if __name__ == "__main__":
    main()
