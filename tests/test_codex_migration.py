"""Background executable discovery and one-shot diagnostic safety."""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import daily_digest as digest
from scheduled_digest_test import consume_request


def test_codex_can_be_found_without_desktop_path(tmp_path):
    old = tmp_path / "OpenAI/Codex/bin/old/codex.exe"
    new = tmp_path / "OpenAI/Codex/bin/new/codex.exe"
    for item in (old, new):
        item.parent.mkdir(parents=True)
        item.write_text("test")
    os.utime(old, (100, 100))
    os.utime(new, (200, 200))
    with patch.object(digest.shutil, "which", return_value=None), patch.dict(
        digest.os.environ, {"LOCALAPPDATA": str(tmp_path)}
    ):
        assert digest._resolve_llm_executable("codex") == str(new)


def test_explicit_provider_path_is_preserved(tmp_path):
    explicit = tmp_path / "codex.exe"
    assert digest._resolve_llm_executable(str(explicit)) == str(explicit.resolve())


@pytest.mark.parametrize("delta,raw,accepted", [
    (300, "digest_20260917_1830.json", True),
    (-1, "digest_20260917_1830.json", False),
    (601, "digest_20260917_1830.json", False),
    (300, "../private.json", False),
])
def test_background_request_is_expiring_scoped_and_single_use(tmp_path, delta, raw, accepted):
    now = datetime.now(timezone.utc)
    path = tmp_path / "test-request.json"
    path.write_text(json.dumps({"expires_at": (now + timedelta(seconds=delta)).isoformat(),
                                "raw_file": raw}), encoding="utf-8")
    assert bool(consume_request(path, now)) == accepted
    assert consume_request(path, now) is None
    assert not path.exists()


def test_malformed_request_does_not_replace_daily_run(tmp_path):
    path = tmp_path / "test-request.json"
    path.write_text("not json", encoding="utf-8")
    assert consume_request(path) is None
