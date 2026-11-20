"""Deployment refuses unsafe timing/state and restores files on install failure."""

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from analysis_batches import chunk_sources
from scripts import activate_analysis
from scripts.activate_analysis import inside, install_files, sha, validate_gate, verify_files
from verified_digest import _source_records


@pytest.fixture
def release(tmp_path):
    root, folder = tmp_path/"live", tmp_path/"release"
    root.mkdir()
    (folder/"files").mkdir(parents=True)
    (root/"old.py").write_text("old source")
    (root/".env").write_text("private setting")
    for name in ["old.py", "new.py"]:
        (folder/"files"/name).write_text("new source")
    manifest = {"live_root": str(root), "baseline": {"old.py": sha(root/"old.py"), "new.py": None},
        "payload": {name: sha(folder/"files"/name) for name in ("old.py", "new.py")},
        "environment_sha256": sha(root/".env"), "protected_edition": "2026-11-18",
        "not_before": "2026-11-19T14:00:00+00:00", "expires": "2026-11-19T15:00:00+00:00",
        "task_xml_sha256": hashlib.sha256(b"expected task").hexdigest()}
    return manifest, folder, root


def gate_inputs():
    return ({"edition_date": "2026-11-18", "status": "completed", "email_status": "sent", "exit_code": 0},
            {"state": "Ready", "last_result": 0, "last_date": "2026-11-18", "collection_exists": False,
             "digest_processes": 0, "xml": "expected task"})


@pytest.mark.parametrize("hour,day", [(13, 19), (15, 19), (14, 18), (14, 20)])
def test_never_activates_tonight_or_outside_release_window(release, hour, day):
    manifest, _, _ = release
    status, tasks = gate_inputs()
    with pytest.raises(RuntimeError, match="activation window"):
        validate_gate(manifest, status, tasks, datetime(2026, 11, day, hour, tzinfo=timezone.utc))


@pytest.mark.parametrize("key,value", [("status", "running"), ("status", "failed"), ("email_status", "disabled"),
                                        ("edition_date", "2026-11-17"), ("exit_code", 1)])
def test_requires_protected_evening_success(release, key, value):
    manifest, _, _ = release
    status, tasks = gate_inputs()
    status[key] = value
    with pytest.raises(RuntimeError):
        validate_gate(manifest, status, tasks, datetime(2026, 11, 19, 14, tzinfo=timezone.utc))


@pytest.mark.parametrize("key,value", [("state", "Running"), ("last_result", 1), ("collection_exists", True),
                                        ("digest_processes", 1), ("last_date", "2026-11-17"), ("xml", "changed")])
def test_refuses_changed_or_active_tasks(release, key, value):
    manifest, _, _ = release
    status, tasks = gate_inputs()
    tasks[key] = value
    with pytest.raises(RuntimeError):
        validate_gate(manifest, status, tasks, datetime(2026, 11, 19, 14, tzinfo=timezone.utc))


def test_successful_gates_and_install(release):
    manifest, folder, root = release
    verify_files(manifest, folder)
    validate_gate(manifest, *gate_inputs(), datetime(2026, 11, 19, 14, tzinfo=timezone.utc))
    install, rollback = Mock(), Mock()
    install_files(manifest, folder, install_task=install, rollback_task=rollback)
    assert (root/"old.py").read_text() == (root/"new.py").read_text() == "new source"
    assert (folder/"backup"/"old.py").read_text() == "old source"
    install.assert_called_once()
    rollback.assert_not_called()


@pytest.mark.parametrize("target", ["old.py", ".env", "new.py"])
def test_live_edits_are_never_overwritten(release, target):
    manifest, folder, root = release
    (root/target).write_text("user edit")
    with pytest.raises(RuntimeError):
        verify_files(manifest, folder)


def test_failed_task_install_rolls_back_existing_and_new_files(release):
    manifest, folder, root = release
    rollback = Mock()
    with pytest.raises(RuntimeError, match="simulated"):
        install_files(manifest, folder, install_task=Mock(side_effect=RuntimeError("simulated")), rollback_task=rollback)
    assert (root/"old.py").read_text() == "old source"
    assert not (root/"new.py").exists()
    rollback.assert_called_once()


def test_one_failed_restore_does_not_prevent_other_restores_or_durable_report(release, monkeypatch):
    manifest, folder, root = release
    (root/"last.py").write_text("old last")
    (folder/"files/last.py").write_text("new last")
    manifest["baseline"]["last.py"] = sha(root/"last.py")
    manifest["payload"]["last.py"] = sha(folder/"files/last.py")
    copy = activate_analysis.shutil.copy2
    def locked_restore(src, dest, *args, **kwargs):
        if src == folder/"backup/last.py":
            raise PermissionError("locked last file")
        return copy(src, dest, *args, **kwargs)
    monkeypatch.setattr(activate_analysis.shutil, "copy2", locked_restore)
    rollback = Mock(side_effect=RuntimeError("task rollback failed"))
    with pytest.raises(RuntimeError, match="rollback is incomplete"):
        install_files(manifest, folder, install_task=Mock(side_effect=RuntimeError("install failed")), rollback_task=rollback)
    assert (root/"last.py").read_text() == "new last"
    assert (root/"old.py").read_text() == "old source"
    assert not (root/"new.py").exists()
    assert (folder/"backup/last.py").read_text() == "old last"
    result = json.loads((folder/"rollback-result.json").read_text(encoding="utf-8"))
    assert result["status"] == "rollback_failed" and result["backup_preserved"]
    assert set(result["restored_files"]) == {"old.py", "new.py"}
    assert {f["path"] for f in result["failures"]} == {"last.py", "task callback"}
    rollback.assert_called_once()


@pytest.mark.parametrize("change", ["live", "backup"])
def test_backup_and_immediate_prewrite_hash_checks_prevent_unreviewed_replacement(release, monkeypatch, change):
    manifest, folder, root = release
    copy = activate_analysis.shutil.copy2
    def changed_copy(src, dest, *args, **kwargs):
        result = copy(src, dest, *args, **kwargs)
        if src == root/"old.py" and dest == folder/"backup/old.py":
            (src if change == "live" else dest).write_text("concurrent edit")
        return result
    monkeypatch.setattr(activate_analysis.shutil, "copy2", changed_copy)
    with pytest.raises(RuntimeError, match="before replacement|Backup differs"):
        install_files(manifest, folder, install_task=Mock(), rollback_task=Mock())
    assert (root/"old.py").read_text() == ("concurrent edit" if change == "live" else "old source")
    assert not (root/"new.py").exists()


def test_payload_tampering_and_path_escape_rejected(release):
    manifest, folder, root = release
    (folder/"files"/"old.py").write_text("tampered")
    with pytest.raises(RuntimeError):
        verify_files(manifest, folder)
    with pytest.raises(ValueError):
        inside(root, "../outside.py")


def test_shared_context_is_preserved_once_and_chunks_stay_bounded():
    parent = {"id": "t3_post", "source_kind": "submission", "body": "unique parent evidence "*100,
              "author": "parent", "created": "2026-11-18 12:00 UTC"}
    rows = [{"id": f"t1_{i}", "body": "reply evidence "*30, "parent_context": [parent]} for i in range(20)]
    encoded = json.dumps(_source_records(rows))
    assert encoded.count(parent["body"]) == 1
    assert encoded.count("reply evidence ") == 600
    chunks = chunk_sources(rows, max_chars=6000, max_sources=8)
    assert [r["id"] for c in chunks for r in c] == [r["id"] for r in rows]
    assert all(len(c) <= 8 and len(json.dumps(_source_records(c), ensure_ascii=False)) <= 6000 for c in chunks)
    encoded = json.dumps(_source_records([parent, *rows]))
    assert encoded.count(parent["body"]) == 1
