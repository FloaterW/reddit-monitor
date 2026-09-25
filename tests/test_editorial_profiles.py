"""Profile isolation, legacy compatibility, and a service-free second use case."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import daily_digest as digest
import demo_digest
import verified_digest as verified
from editorial_profiles import resolve_editorial
from evaluate_digest import evaluate
from monitor_config import load_monitor


def profile(name):
    metadata = load_monitor(name)["digest"]
    return resolve_editorial(metadata.get("editorial"), audience=metadata.get("audience"))


def test_churning_prompts_are_byte_identical_to_pre_refactor_release():
    selected = profile("churning")
    # SHA-256 of the four original prompt literals in release 9abb365.
    fingerprints = {
        "rules": "3d2eabeb1baafe23d110a1a1f4b8ac2308213b265fcb563449651ff17b1356b8",
        "style": "f42303dc5ef083e82913b2f59b8dab4b8d3ac192f4b1dd27a4c7168b278146f9",
        "extraction": "ef863e5bbc3c344266b3017f65f6333d740fcaacf12e526498f9bf1eb765859b",
        "writing": "c0cfc7d17428749bf6bec8f0be616f0efdb9be36f79a6a8acd1e5299237c82e4",
    }
    for field, expected in fingerprints.items():
        assert hashlib.sha256(getattr(selected, field).encode()).hexdigest() == expected
    assert selected.financial_checks is True


@pytest.mark.parametrize("settings", [
    [], "general", False, {"preset": "missing"}, {"preset": []},
    {"financial_checks": "false"}, {"financial_checks": 0},
    {"style": ""}, {"style": []}, {"priorities": None},
    {"priorities": "x" * 8001}, {"tools": True},
])
def test_invalid_editorial_settings_are_rejected(settings):
    with pytest.raises(ValueError, match="editorial"):
        resolve_editorial(settings)


def test_monitor_loader_rejects_explicit_null_editorial(tmp_path, monkeypatch):
    import monitor_config
    monkeypatch.setattr(monitor_config, "_CONFIG_DIR", tmp_path)
    (tmp_path / "bad.json").write_text(json.dumps({
        "name": "bad", "subreddits": ["test"], "keywords": ["test"],
        "digest": {"editorial": None},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="editorial"):
        load_monitor("bad")


def test_presets_do_not_leak_between_calls():
    assert not resolve_editorial().financial_checks
    custom = resolve_editorial({"priorities": "database releases", "style": "short headings"},
                               audience="database engineers")
    assert "database engineers" in custom.rules and "database releases" in custom.rules
    assert "short headings" in custom.style
    assert "database releases" not in resolve_editorial().rules
    assert "database engineers" not in profile("churning").rules


def fixtures():
    return (
        json.loads((demo_digest.FIXTURES / "source.json").read_text(encoding="utf-8")),
        json.loads((demo_digest.FIXTURES / "model-responses.json").read_text(encoding="utf-8")),
    )


def test_job_market_actual_requests_are_topic_neutral_and_profile_aware():
    rows, replies = fixtures()
    prompts = []

    def invoke(prompt, **kwargs):
        prompts.append(prompt)
        key = "notes" if "SOURCE COMMENTS:\n" in prompt else "sections"
        return json.dumps({key: replies[key]})

    with patch.object(digest, "_invoke_llm", side_effect=invoke):
        result = digest.summarize(rows, source_safe=True, editorial_profile=profile("job-market"))
    assert len(prompts) == 2 and "ExampleCo" in result
    for prompt in prompts:
        assert "software engineers and CS students" in prompt
        assert "Hiring and recruiting changes" in prompt
        for unwanted in ("churning", "Bilt", "award travel", "card names", "bank bonuses"):
            assert unwanted not in prompt
    assert "descriptive headings" in prompts[1]


def test_general_markdown_mode_also_uses_profile_rules():
    rows, _ = fixtures()
    with patch.object(digest, "_invoke_llm", return_value="# Demo") as invoke:
        digest.summarize(rows, source_safe=False, editorial_profile=profile("job-market"))
    prompt = invoke.call_args.args[0]
    assert "software engineers and CS students" in prompt
    assert "Hiring and recruiting changes" in prompt and "Chase" not in prompt


def test_custom_multibatch_markdown_does_not_reintroduce_churning_audience():
    rows, _ = fixtures()
    with (
        patch.object(digest, "_chunk_comments", return_value=[[rows[0]], [rows[1]]]),
        patch.object(digest, "_invoke_llm", return_value="# Demo") as invoke,
    ):
        digest.summarize(rows, editorial_profile=resolve_editorial())
    assert invoke.call_count == 3
    for call in invoke.call_args_list:
        assert "churning" not in call.args[0]
        assert "credit card" not in call.args[0]


def test_financial_guard_is_optional_but_citation_and_numeric_guards_are_not():
    text = "$250 SUB plus $300 Rakuten bonus: $650 return."
    rows = [{"id": "t1_a", "author": "demo", "body": text,
             "post_permalink": "https://reddit.com/r/test/comments/demo/topic/"}]
    document = {"sections": [{"title": "Example", "bullets": [
        {"text": text, "source_ids": ["t1_a"], "source_position": "end", "indent": 0},
    ]}]}
    assert verified.render_checked(document, rows, financial_checks=True)[1]
    rendered, errors = verified.render_checked(document, rows, financial_checks=False)
    assert not errors
    assert not evaluate(rendered, rows, financial_checks=True)["arithmetic_consistency"]["passed"]
    general = evaluate(rendered, rows, financial_checks=False)
    assert general["arithmetic_consistency"]["required"] is False
    assert general["citation_integrity"]["passed"]
    document["sections"][0]["bullets"][0]["text"] = "Received $999."
    assert verified.render_checked(document, rows, financial_checks=False)[1]
    document["sections"][0]["bullets"][0]["source_ids"] = ["invented"]
    assert verified.render_checked(document, rows, financial_checks=False)[1]


@pytest.mark.parametrize("monitor,preset,financial,title", [
    ("churning", "churning", True, "Churning Digest"),
    ("job-market", "general", False, "Job Market Digest"),
])
def test_cli_carries_profile_to_generation_evaluation_and_email(
    tmp_path, monitor, preset, financial, title
):
    rows, replies = fixtures()
    rendered, errors = verified.render_checked({"sections": replies["sections"]}, rows)
    assert not errors
    with (
        patch.object(digest, "summarize", return_value=rendered) as summarize,
        patch.object(digest, "send_email", return_value="sent") as email,
        patch("evaluate_digest.evaluate", wraps=evaluate) as evaluation,
    ):
        code = digest.main([
            "--monitor", monitor, "--from-json", str(demo_digest.FIXTURES / "source.json"),
            "--digest-date", "2026-01-01", "--source-safe", "--quality", "strict",
            "--no-db", "--quiet-summary", "--save", str(tmp_path / "newsletter.md"),
            "--status-file", str(tmp_path / "status.json"),
        ])
    assert code == 0
    assert summarize.call_args.kwargs["editorial_profile"].preset == preset
    assert evaluation.call_args.kwargs["financial_checks"] is financial
    assert email.call_args.args[0] == title + " — January 01, 2026"
    if preset == "general":
        assert email.call_args.args[1].startswith("# Job Market Digest — January 01, 2026")


def test_offline_demo_produces_validated_preview_without_changing_production(tmp_path):
    root = Path(digest.__file__).parent
    protected = [root / "data/last_run_status.json", root / "run_digest.bat", root / "run_watchdog.bat"]
    before = {p: p.read_bytes() if p.exists() else None for p in protected}
    assert demo_digest.run_demo(tmp_path) == 0
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["email_status"] == "disabled" and status["quality_status"] == "passed"
    assert json.loads((tmp_path / "evaluation.json").read_text())["passed"]
    preview = (tmp_path / "preview.html").read_text(encoding="utf-8")
    assert "SYNTHETIC DEMO" in preview and "fictional" in preview and "<html>" in preview
    assert not (tmp_path / "monitor.db").exists()
    assert before == {p: p.read_bytes() if p.exists() else None for p in protected}


@pytest.mark.parametrize("options,expected", [
    ([], "churning"),
    (["--subreddits", "programming", "--keywords", "release"], "general"),
])
def test_cli_preserves_legacy_default_but_custom_topics_use_general(tmp_path, options, expected):
    with patch.object(digest, "summarize", return_value="# Demo\n\nContent.") as summarize:
        code = digest.main([
            "--from-json", str(demo_digest.FIXTURES / "source.json"),
            "--no-email", "--no-db", "--quality", "off", "--quiet-summary",
            "--save", str(tmp_path / "newsletter.md"),
            "--status-file", str(tmp_path / "status.json"), *options,
        ])
    assert code == 0
    assert summarize.call_args.kwargs["editorial_profile"].preset == expected


def test_checkpoints_are_separated_by_editorial_profile(tmp_path):
    rows, responses = fixtures()
    prompts = []
    def invoke(prompt, **kwargs):
        prompts.append(prompt)
        key = "notes" if "SOURCE COMMENTS:\n" in prompt else "sections"
        return json.dumps({key: responses[key]})
    for name in ("churning", "job-market"):
        verified.summarize_verified(rows, lambda rows: [rows], invoke,
                                    artifact_dir=tmp_path, editorial_profile=profile(name))
    assert len(prompts) == 4
    assert len(list(tmp_path.glob("checkpoint-*.json"))) == 4
