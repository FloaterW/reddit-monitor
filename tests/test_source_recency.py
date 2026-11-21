"""Old parent context can support a development, but cannot be the whole news."""

import json

from digest_schema import response_schema
from editorial_profiles import resolve_editorial
from verified_digest import _source_records, render_checked, summarize_verified


def sources():
    return [
        {"id": "t1_old", "author": "older_reporter", "body": "The reward is 125k after 8k.",
         "created": "2026-10-01 10:00 UTC", "context_only": True,
         "post_permalink": "https://reddit.com/r/example/comments/thread/topic/"},
        {"id": "t1_new", "author": "current_reporter", "body": "The offer is still visible today.",
         "created": "2026-11-03 11:00 UTC",
         "post_permalink": "https://reddit.com/r/example/comments/thread/topic/"},
    ]


def section(*source_ids):
    return {"title": "Offer report", "bullets": [
        {"text": "A user reports the reward is 125k after 8k.", "source_ids": list(source_ids),
         "source_position": "end", "indent": 0}
    ]}


def test_old_parent_alone_cannot_be_a_current_news_section():
    rendered, issues = render_checked({"sections": [section("t1_old")]}, sources())
    assert rendered is None
    assert any("background-only" in issue for issue in issues)


def test_background_evidence_can_support_a_section_with_a_current_report():
    rendered, issues = render_checked({"sections": [section("t1_old", "t1_new")]}, sources())
    assert not issues
    assert "125k" in rendered


def test_current_source_in_another_section_does_not_validate_an_old_only_section():
    current = {"title": "Current report", "bullets": [
        {"text": "A user reports the offer is still visible.", "source_ids": ["t1_new"],
         "source_position": "end", "indent": 0}
    ]}
    rendered, issues = render_checked({"sections": [current, section("t1_old")]}, sources())
    assert rendered is None
    assert sum("background-only" in issue for issue in issues) == 1


def test_current_parent_bullet_allows_older_supporting_detail():
    current = {"text": "A user reports the offer is still visible.", "source_ids": ["t1_new"],
               "source_position": "end", "indent": 0}
    draft = section("t1_old")
    draft["bullets"][0]["indent"] = 1
    draft["bullets"].insert(0, current)
    rendered, issues = render_checked({"sections": [draft]}, sources())
    assert rendered and not issues


def test_prompt_records_preserve_known_and_unknown_source_dates():
    rows = sources()
    rows[1]["parent_context"] = [rows[0]]
    records = _source_records(rows)
    assert records[0]["created"] == "2026-10-01 10:00 UTC"
    assert records[1]["created"] == "2026-11-03 11:00 UTC"
    assert records[1]["parent_context"][0]["created"] == "2026-10-01 10:00 UTC"
    assert records[1]["parent_context"][0]["background_only"]
    unknown = _source_records([{"id": "t1_unknown", "body": "Tomorrow."}])
    assert unknown[0]["created"] == ""


def test_native_schema_keeps_background_ids_for_citation_provenance():
    schema = response_schema("SOURCE COMMENTS:\n" + json.dumps(_source_records(sources())))
    ids = schema["properties"]["notes"]["items"]["properties"]["source_ids"]["items"]["enum"]
    assert ids == ["t1_old", "t1_new"]


def test_extractor_and_writer_receive_recency_instructions_and_timestamps():
    prompts = []

    def invoke(prompt, **kwargs):
        prompts.append(prompt)
        if response_schema(prompt)["required"] == ["notes"]:
            return json.dumps({"notes": [{"topic": "Offer report", "text": "The offer is still visible.",
                                          "source_ids": ["t1_new", "t1_old"]}]})
        return json.dumps({"sections": [section("t1_new", "t1_old")]})

    result = summarize_verified(sources(), lambda rows: [rows], invoke,
                                editorial_profile=resolve_editorial())
    assert result and len(prompts) == 2
    for prompt in prompts:
        assert "2026-10-01 10:00 UTC" in prompt and "2026-11-03 11:00 UTC" in prompt
        assert "INTERNAL chronology context" in prompt and "timezone is unknown" in prompt
        assert "background_only are older context" in prompt
