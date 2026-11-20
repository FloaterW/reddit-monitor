"""Local source provenance; accounting is not a semantic completeness score."""

from collections import Counter


def build_source_trace(comments, notes, document, *, plan=None, payload=None, review=None):
    """Explain observable source selection stages without inventing drop reasons."""
    review = review or {}
    decisions = {d["source_id"]: d for d in review.get("decisions", [])}
    note_rows = (payload or {}).get("notes") or [{**n, "note_id": f"n{i}"} for i, n in enumerate(notes)]
    source_notes = {}
    for note in note_rows:
        for sid in note["source_ids"]:
            source_notes.setdefault(sid, []).append(note["note_id"])
    note_decisions = {}
    for story in (plan or {}).get("stories", []):
        for nid in story["note_ids"]:
            note_decisions[nid] = {key: story.get(key) for key in ("topic", "status", "reason")}
    published = {}
    for section in document.get("sections", []):
        for bullet in section.get("bullets", []):
            for sid in bullet.get("source_ids", []):
                published.setdefault(sid, set()).add(section["title"])
    rows = []
    for source in comments:
        sid = source.get("id")
        ids = source_notes.get(sid, [])
        selections = [dict(note_id=nid, **note_decisions[nid]) for nid in ids if nid in note_decisions]
        selected = any(s["status"] in {"new", "update", "deadline"} for s in selections)
        if sid in published:
            outcome = "published"
        elif selected or (ids and plan is None):
            outcome = "selected_not_cited"
        elif selections:
            outcome = "planner_excluded"
        else:
            outcome = "not_extracted"
        rows.append({"source_id": sid, "subreddit": source.get("subreddit"),
                     "background_only": bool(source.get("context_only")),
                     "note_ids": ids, "planner_decisions": selections,
                     "omission_review": decisions.get(sid), "outcome": outcome,
                     "published_sections": sorted(published.get(sid, []))})
    return {"version": 1, "counts": dict(Counter(r["outcome"] for r in rows)),
            "source_count": len(rows), "note_count": len(notes),
            "review": {k: v for k, v in review.items() if k != "decisions"},
            "meaning": "Published means included in the candidate document, not email delivery. Source IDs track provenance, not retention of every fact. Not-extracted sources have no inferred rejection reason; omission-review reasons are model judgments.",
            "sources": rows}
