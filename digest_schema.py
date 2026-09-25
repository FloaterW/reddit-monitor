"""Provider-native schemas: formatting enforcement, not a replacement for fact checks."""

import json


def response_schema(prompt):
    text = {"type": "string"}
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
    # Constrain references at generation time, rather than spending another
    # model call repairing invented IDs or citations to background-only parents.
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
