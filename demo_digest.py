"""Offline synthetic demo: real pipeline, fixture responses, no external services."""

import json
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "examples" / "job-market"
OUTPUT = ROOT / "data" / "demo-job-market"


def run_demo(output_dir=OUTPUT):
    """Run a fixed synthetic edition, with network/model/delivery boundaries blocked."""
    import daily_digest as digest

    output_dir = Path(output_dir)
    responses = json.loads((FIXTURES / "model-responses.json").read_text(encoding="utf-8"))

    def recorded_model(prompt, **_kwargs):
        if "SOURCE COMMENTS:\n" in prompt:
            return json.dumps({"notes": responses["notes"]})
        if "ORIGINAL SOURCES:\n" in prompt:
            return json.dumps({"sections": responses["sections"]})
        raise RuntimeError("Unexpected demo prompt; no live provider will be called")

    def forbidden(*_args, **_kwargs):
        raise RuntimeError("Offline demo attempted an external or production operation")

    print("SYNTHETIC OFFLINE DEMO: fictional sources and fixture model responses, not live news.")
    with (
        patch.object(digest, "_invoke_llm", side_effect=recorded_model),
        patch.object(digest, "scrape_all", side_effect=forbidden),
        patch.object(digest, "send_email", side_effect=forbidden),
        patch.object(digest, "_save_run_history", side_effect=forbidden),
        patch("socket.socket.connect", side_effect=forbidden),
    ):
        code = digest.main([
            "--monitor", "job-market", "--from-json", str(FIXTURES / "source.json"),
            "--digest-date", "2026-01-01", "--source-safe", "--quality", "strict",
            "--no-email", "--no-db", "--quiet-summary",
            "--save", str(output_dir / "newsletter.md"),
            "--status-file", str(output_dir / "status.json"),
            "--evaluation-report", str(output_dir / "evaluation.json"),
        ])
    if code:
        return code
    markdown = (output_dir / "newsletter.md").read_text(encoding="utf-8")
    label = "Synthetic demonstration only: fictional sources and model responses. Links illustrate citation structure, not real posts."
    preview = digest._wrap_html_email(
        digest._inline_styles(digest._markdown_to_safe_html(label + "\n\n" + markdown)),
        "Job Market Digest — SYNTHETIC DEMO", subreddits=["cscareerquestions", "experienceddevs", "csMajors"],
    )
    digest.atomic_write_text(output_dir / "preview.html", preview)
    print(f"Preview: {output_dir / 'preview.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_demo())
