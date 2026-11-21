"""Provider-native schemas: formatting enforcement, not a replacement for fact checks."""

import json


def response_schema(prompt):
    text = {"type": "string"}
    # Pick the earliest stage marker; markers inside source data are untrusted.
    markers = [(prompt.find(m), m) for m in ("OMISSION REVIEW:\n", "EDITORIAL PLAN:\n", "SOURCE COMMENTS:\n", "ORIGINAL SOURCES:\n") if m in prompt]
    if markers and min(markers)[1] == "OMISSION REVIEW:\n":
        payload, _ = json.JSONDecoder().raw_decode(prompt.split("OMISSION REVIEW:\n", 1)[1].lstrip())
        identifiers = [s["source_id"] for s in payload["sources"]]
        reference = {"type": "string", "enum": identifiers}
        note = {"type": "object", "additionalProperties": False, "properties": {
            "topic": text, "text": text,
            "source_ids": {"type": "array", "items": reference, "minItems": 1, "maxItems": 8}},
            "required": ["topic", "text", "source_ids"]}
        decision = {"type": "object", "additionalProperties": False, "properties": {
            "source_id": reference,
            "status": {"type": "string", "enum": ["added", "already_covered", "insufficient_context", "low_value"]},
            "reason": text}, "required": ["source_id", "status", "reason"]}
        return {"type": "object", "additionalProperties": False, "properties": {
            "notes": {"type": "array", "items": note, "maxItems": 15},
            "decisions": {"type": "array", "items": decision, "minItems": len(identifiers), "maxItems": len(identifiers)}},
            "required": ["notes", "decisions"]}
    if markers and min(markers)[1] == "EDITORIAL PLAN:\n":
        payload, _ = json.JSONDecoder().raw_decode(prompt.split("EDITORIAL PLAN:\n", 1)[1].lstrip())
        identifiers = [n["note_id"] for n in payload["notes"]]
        story = {"type": "object", "additionalProperties": False, "properties": {
            "topic": text, "reason": text,
            "note_ids": {"type": "array", "items": {"type": "string", "enum": identifiers}, "minItems": 1},
            "status": {"type": "string", "enum": ["new", "update", "deadline", "repeat", "low_value"]}},
            "required": ["topic", "reason", "note_ids", "status"]}
        return {"type": "object", "additionalProperties": False,
                "properties": {"stories": {"type": "array", "items": story, "maxItems": len(identifiers)}},
                "required": ["stories"]}
    ids = {"type": "array", "items": text, "minItems": 1, "maxItems": 8}
    extraction = prompt.find("SOURCE COMMENTS:\n")
    writing = prompt.find("ORIGINAL SOURCES:\n")
    is_extraction = extraction >= 0 and (writing < 0 or extraction < writing)
    if writing >= 0 and not is_extraction:
        bullet = {"type": "object", "additionalProperties": False,
                  "properties": {"text": text, "source_ids": ids,
                                 "source_position": {"type": "string", "enum": ["start", "end"]},
                                 "indent": {"type": "integer", "enum": [0, 1]}},
                  "required": ["text", "source_ids", "source_position", "indent"]}
        item = {"type": "object", "additionalProperties": False,
                "properties": {"title": text, "bullets": {"type": "array", "items": bullet,
                                                            "minItems": 1, "maxItems": 24}},
                "required": ["title", "bullets"]}
        key, maximum = "sections", 36
    elif is_extraction:
        item = {"type": "object", "additionalProperties": False,
                "properties": {"topic": text, "text": text, "source_ids": ids},
                "required": ["topic", "text", "source_ids"]}
        key, maximum = "notes", 15
    else:
        return None
    # Constrain references at generation time rather than repairing invented IDs.
    # Background source IDs remain available for supporting context; the renderer
    # separately requires a current source in every published section.
    marker = "SOURCE COMMENTS:\n" if is_extraction else "ORIGINAL SOURCES:\n"
    try:
        records, _ = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1].lstrip())
        allowed_ids = list(dict.fromkeys(
            row["source_id"] for row in records
            if isinstance(row, dict) and isinstance(row.get("source_id"), str)
        )) if isinstance(records, list) else []
    except (ValueError, IndexError, TypeError):
        allowed_ids = []
    if allowed_ids:
        ids["items"] = {"type": "string", "enum": allowed_ids}
    return {"type": "object", "additionalProperties": False,
            "properties": {key: {"type": "array", "items": item, "maxItems": maximum}},
            "required": [key]}
