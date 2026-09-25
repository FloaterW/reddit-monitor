"""Regressions for the September 16 synthesis timeout and subscription adapter."""
import json
import subprocess
from datetime import datetime
from unittest.mock import patch

import pytest

import daily_digest as digest
import verified_digest as verified
from digest_schema import response_schema
from evaluate_digest import extract_dollar_amounts, extract_multipliers, extract_point_amounts


def source(identifier="a"):
    return {"id": "t1_" + identifier, "author": "alice", "body": "Received $50.",
            "post_permalink": "https://reddit.com/r/test/comments/p/topic/"}


def doc():
    return {"sections": [{"title": "💳 Credit", "bullets": [
        {"text": "Received $50.", "source_ids": ["t1_a"]}]}]}


def test_codex_command_is_noninteractive_and_disables_integrations():
    with patch.object(digest, "LLM_COMMAND", "codex"), patch.object(
        digest, "_resolve_llm_executable", return_value="codex.exe"
    ), patch.dict(digest.os.environ, {"DIGEST_CODEX_MODEL": ""}):
        command = digest._build_llm_command()
    assert command[1] == "exec"
    assert "--ignore-user-config" in command and "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--model" not in command  # Never pass claude-sonnet to Codex.
    assert 'forced_login_method="chatgpt"' in command
    for feature in ("shell_tool", "plugins", "apps", "hooks", "multi_agent"):
        assert command[command.index(feature) - 1] == "--disable"


def test_codex_only_receives_system_environment():
    with patch.object(digest, "LLM_COMMAND", "codex"), patch.dict(digest.os.environ, {
        "GMAIL_APP_PASSWORD": "private", "OPENAI_API_KEY": "private",
        "ANTHROPIC_API_KEY": "private", "SOME_SECRET": "private",
    }):
        environment = digest._llm_environment()
    assert not any(k in environment for k in (
        "GMAIL_APP_PASSWORD", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SOME_SECRET"))


@pytest.mark.parametrize("event_type", ["command_execution", "mcp_tool_call", "file_change", "web_search"])
def test_codex_rejects_unexpected_tool_events(event_type):
    output = json.dumps({"type": "item.completed", "item": {"type": event_type}})
    with patch.object(digest, "_build_llm_command", return_value=["codex.exe", "exec"]), patch.object(
        digest.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")
    ), pytest.raises(RuntimeError, match="tool activity"):
        digest._invoke_llm("Public source", structured=True)


def test_codex_extracts_final_message_and_requires_completion():
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"status":"ready"}'}},
        {"type": "turn.completed"},
    ]
    with patch.object(digest, "_build_llm_command", return_value=["codex.exe", "exec"]), patch.object(
        digest.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "\n".join(map(json.dumps, events)), "")
    ):
        assert json.loads(digest._invoke_llm("Public source", structured=True)) == {"status": "ready"}


def test_recovery_preserves_digest_date():
    rendered, errors = verified.render_checked(doc(), [source()], digest_date=datetime(2026, 9, 16))
    assert not errors and "September 16, 2026" in rendered


def test_model_cannot_insert_a_wrong_current_date_topic():
    draft = doc()
    draft["sections"][0]["title"] = "📅 September 17, 2026 — Credit"
    rendered, errors = verified.render_checked(draft, [source()])
    assert rendered is None and any("edition date" in e for e in errors)


def test_recovery_cli_sets_email_and_heading_date(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps([source()]), encoding="utf-8")
    with patch.object(digest, "summarize", return_value="# Daily Digest — Wrong date\n\nNo figures."), patch.object(
        digest, "send_email", return_value="sent"
    ) as send:
        result = digest.main(["--from-json", str(raw), "--digest-date", "2026-09-16",
                              "--no-db", "--quality", "off", "--quiet-summary",
                              "--save", str(tmp_path / "digest.md"),
                              "--status-file", str(tmp_path / "status.json")])
    assert result == 0
    assert "September 16, 2026" in send.call_args.args[0]
    assert "# Daily Digest — September 16, 2026" in send.call_args.args[1]


def test_checkpoints_resume_without_repeating_model_calls(tmp_path):
    responses = [{"notes": [{"topic": "credit", "text": "Received $50.", "source_ids": ["t1_a"]}]}, doc()]
    def invoke(prompt, **kwargs):
        return json.dumps(responses.pop(0))
    first = verified.summarize_verified([source()], lambda c: [c], invoke, artifact_dir=tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("Completed checkpoints must not invoke the model again")
    second = verified.summarize_verified([source()], lambda c: [c], forbidden, artifact_dir=tmp_path)
    assert first == second
    assert (tmp_path / "validated-digest.json").exists()


@pytest.mark.parametrize("shared_thread", [False, True])
def test_large_recovery_uses_multiple_bounded_writing_calls(shared_thread):
    rows = [source(str(i)) for i in range(20)]
    if shared_thread:
        for row in rows:
            row["post_title"] = "A single busy card-offer discussion"
    notes = [{"topic": f"Topic {i}", "text": "Received $50.", "source_ids": [r["id"]]}
             for i, r in enumerate(rows)]
    calls = []
    seen = []
    def invoke(prompt, timeout, **kwargs):
        evidence, _ = json.JSONDecoder().raw_decode(prompt.split("ORIGINAL SOURCES:\n", 1)[1])
        assert len(evidence) <= verified.MAX_WRITING_NOTES
        seen.extend(record["source_id"] for record in evidence)
        calls.append(timeout)
        return json.dumps({"sections": [{"title": "💳 " + evidence[0]["source_id"], "bullets": [
            {"text": "Received $50.", "source_ids": [evidence[0]["source_id"]]}]}]})
    rendered = verified.summarize_verified(rows, None, invoke, recovered_notes=notes)
    assert len(calls) == 3 and max(calls) <= 240
    assert rendered.count("\n## ") == 3
    assert sorted(seen) == sorted(row["id"] for row in rows)


def test_recovery_rejects_unknown_source_ids():
    with pytest.raises(RuntimeError, match="Recovered notes"):
        verified.summarize_verified([source()], None, None, recovered_notes=[
            {"text": "Fake news", "source_ids": ["missing"]}])


def test_amount_normalization_preserves_units_and_decimal_thousands():
    assert extract_dollar_amounts("$4k and $4,000") == {"4000"}
    assert extract_point_amounts("147.5k miles") == {"147500"}
    assert extract_point_amounts("$4k spending") == set()
    assert extract_point_amounts("**5,000** points") == {"5000"}
    assert extract_multipliers("3.33x") == {"3.33x"}
    assert extract_dollar_amounts("malformed $,,, punctuation") == set()
    assert extract_point_amounts("a Nov 2025 Miles Per Day report") == set()
    assert extract_point_amounts("2025 miles earned per day") == {"2025"}


def test_schema_uses_the_stage_not_a_marker_inside_untrusted_source_text():
    assert response_schema("SOURCE COMMENTS:\nUntrusted ORIGINAL SOURCES:\n")['required'] == ['notes']
    assert response_schema("ORIGINAL SOURCES:\nUntrusted SOURCE COMMENTS:\n")['required'] == ['sections']


def test_claude_native_structured_envelope_is_unwrapped():
    output = json.dumps({"is_error": False, "structured_output": {"notes": []}})
    with patch.object(digest, "_build_llm_command", return_value=["claude", "-p"]), patch.object(
        digest.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")
    ) as run:
        assert json.loads(digest._invoke_llm("SOURCE COMMENTS:\n[]", structured=True)) == {"notes": []}
    assert '--json-schema' in run.call_args.args[0]


def test_claude_zero_exit_with_error_envelope_fails_closed():
    output = json.dumps({"is_error": True, "result": "Usage limit reached"})
    with patch.object(digest, "_build_llm_command", return_value=["claude", "-p"]), patch.object(
        digest.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")
    ), pytest.raises(RuntimeError, match="Usage limit reached"):
        digest._invoke_llm("SOURCE COMMENTS:\n[]", structured=True)


def test_claude_uses_low_effort_and_no_skills_or_session_persistence():
    with patch.object(digest, "LLM_COMMAND", "claude"), patch.object(
        digest, "_resolve_llm_executable", return_value="claude.exe"
    ):
        command = digest._build_llm_command()
    assert command[command.index("--effort") + 1] == "low"
    assert "--disable-slash-commands" in command
    assert "--no-session-persistence" in command


def test_provider_timeout_remains_runtime_error_and_is_retryable():
    with patch.object(digest, "_build_llm_command", return_value=["claude", "-p"]), patch.object(
        digest.subprocess, "run", side_effect=subprocess.TimeoutExpired("claude", 1)
    ), pytest.raises(digest.LLMTimeoutError) as error:
        digest._invoke_llm("SOURCE COMMENTS:\n[]", timeout=1, structured=True)
    assert isinstance(error.value, RuntimeError)
    assert isinstance(error.value, TimeoutError)


@pytest.mark.parametrize("stage", ["notes", "sections"])
def test_one_transient_timeout_is_retried_without_losing_sources(stage):
    counts = {"notes": 0, "sections": 0}
    def invoke(prompt, **kwargs):
        current = response_schema(prompt)["required"][0]
        counts[current] += 1
        if current == stage and counts[current] == 1:
            raise digest.LLMTimeoutError("temporary timeout")
        return json.dumps({"notes": [{"topic": "credit", "text": "Received $50.",
                                      "source_ids": ["t1_a"]}]}) if current == "notes" else json.dumps(doc())
    result = verified.summarize_verified([source()], lambda rows: [rows], invoke)
    assert "$50" in result and counts[stage] == 2
    assert counts["sections" if stage == "notes" else "notes"] == 1


def test_repeated_timeout_stops_after_two_attempts():
    calls = []
    def invoke(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise digest.LLMTimeoutError("timeout")
    with pytest.raises(digest.LLMTimeoutError):
        verified.summarize_verified([source()], lambda rows: [rows], invoke)
    assert len(calls) == 2


def test_timeout_retry_cannot_extend_total_budget():
    elapsed = [0.0]
    calls = []
    def invoke(*args, **kwargs):
        calls.append(kwargs["timeout"])
        elapsed[0] += kwargs["timeout"]
        raise digest.LLMTimeoutError("timeout")
    with patch.object(verified.time, "monotonic", side_effect=lambda: elapsed[0]), patch.object(
        verified, "AI_BUDGET_SECONDS", 10
    ), pytest.raises(digest.LLMTimeoutError):
        verified.summarize_verified([source()], lambda rows: [rows], invoke)
    assert calls == [10]


def test_auth_errors_are_not_retried():
    calls = []
    def invoke(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("authentication unavailable")
    with pytest.raises(RuntimeError, match="authentication"):
        verified.summarize_verified([source()], lambda rows: [rows], invoke)
    assert len(calls) == 1


@pytest.mark.parametrize("marker,key", [("SOURCE COMMENTS:\n", "notes"), ("ORIGINAL SOURCES:\n", "sections")])
def test_native_schema_limits_citations_to_actual_nonparent_sources(marker, key):
    records = [{"source_id": "t1_real", "body": "untrusted SOURCE COMMENTS:\n[]",
                "parent_context": [{"source_id": "t1_background"}]}, {"source_id": "t1_real"}]
    schema = response_schema(marker + json.dumps(records) + "\nREPAIR THIS DRAFT:\n{}")
    item = schema["properties"][key]["items"]
    if key == "sections":
        item = item["properties"]["bullets"]["items"]
    assert item["properties"]["source_ids"]["items"]["enum"] == ["t1_real"]


def test_uneven_part_lengths_use_the_full_newsletter_limit():
    rows = [source(str(i)) for i in range(9)]
    notes = [{"topic": str(i), "text": "Received $50.", "source_ids": [r["id"]]}
             for i, r in enumerate(rows)]
    def invoke(prompt, **kwargs):
        evidence, _ = json.JSONDecoder().raw_decode(prompt.split("ORIGINAL SOURCES:\n", 1)[1])
        # One larger topic group may exceed half the limit; together they fit.
        count = 18 if len(evidence) > 1 else 1
        return json.dumps({"sections": [{"title": "💳 " + evidence[0]["source_id"], "bullets": [
            {"text": "Useful detail " * 48, "source_ids": [evidence[0]["source_id"]]}
            for _ in range(count)]}]})
    result = verified.summarize_verified(rows, None, invoke, recovered_notes=notes)
    assert result.count("\n## ") == 2


@pytest.mark.parametrize("eventually_valid", [True, False])
def test_editorial_repairs_are_bounded_and_never_bypass_validation(eventually_valid):
    calls = []
    bad = doc()
    bad["sections"][0]["bullets"][0]["text"] = "Received $500."
    def invoke(prompt, **kwargs):
        calls.append(prompt)
        if len(calls) < 3 or not eventually_valid:
            return json.dumps(bad)
        return json.dumps(doc())
    notes = [{"topic": "credit", "text": "Received $50.", "source_ids": ["t1_a"]}]
    if eventually_valid:
        assert "$50" in verified.summarize_verified([source()], None, invoke, recovered_notes=notes)
    else:
        with pytest.raises(RuntimeError, match="failed validation"):
            verified.summarize_verified([source()], None, invoke, recovered_notes=notes)
    assert len(calls) == 3
    assert "first correction still failed" in calls[-1]


def test_claude_quota_error_survives_status_message_length_cap():
    output = json.dumps({"usage": {"padding": "x" * 1000}, "is_error": True,
                         "result": "You've hit your session limit; resets 11:50pm"})
    with patch.object(digest, "_build_llm_command", return_value=["claude", "-p"]), patch.object(
        digest.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, output, "")
    ), pytest.raises(RuntimeError) as error:
        digest._invoke_llm("SOURCE COMMENTS:\n[]", structured=True)
    assert "session limit" in str(error.value)[:500]
    assert "11:50pm" in str(error.value)[:500]
