"""The reviewed release cannot affect its protected scheduled edition."""

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from scripts import release_control as release


@pytest.fixture
def gate():
    now = datetime(2026, 11, 21, 14, tzinfo=timezone.utc)
    manifest = {"protected_edition": "2026-11-20", "monitor": "sample", "destination_key": "recipient-hash",
                "not_before": (now-timedelta(hours=1)).isoformat(), "expires": (now+timedelta(hours=1)).isoformat(),
                "tasks": [{"name": "Digest", "xml_sha256": "xml-a", "protects_edition": True},
                          {"name": "Collection", "xml_sha256": "xml-b"}]}
    status = {"edition_date": "2026-11-20", "monitor": "sample", "status": "completed_with_warnings",
              "email_status": "sent", "quality_status": "passed", "exit_code": 0}
    tasks = {"active_processes": 0, "tasks": [
        {"name": "Digest", "xml_sha256": "xml-a", "state": "Ready", "enabled": True, "last_result": 0, "last_date": "2026-11-20", "next_run_time": (now+timedelta(hours=8)).isoformat()},
        {"name": "Collection", "xml_sha256": "xml-b", "state": "Ready", "enabled": True, "next_run_time": (now+timedelta(hours=3)).isoformat()}]}
    delivery = {"state": "sent", "monitor": "sample", "edition_date": "2026-11-20",
                "destination_key": "recipient-hash", "content_hash": "content", "message_id": "<delivery@example.invalid>"}
    inbox = {"edition_date": "2026-11-20", "monitor": "sample", "inbox_confirmed": True,
             "gmail_id": "observed-message", "plain_exact_match": True, "html_exact_match": True, "content_hash": "content",
             "destination_key": "recipient-hash", "message_id": "<delivery@example.invalid>"}
    return manifest, status, tasks, delivery, inbox, now


def test_protected_success_must_include_full_inbox_delivery(gate):
    release.validate_gate(*gate)


@pytest.mark.parametrize("field,value", [
    ("edition_date", "2026-11-19"), ("monitor", "other"), ("status", "running"),
    ("status", "failed"), ("email_status", "disabled"), ("quality_status", "failed"), ("exit_code", 1),
])
def test_preview_failed_or_old_edition_never_activates(gate, field, value):
    gate[1][field] = value
    with pytest.raises(RuntimeError):
        release.validate_gate(*gate)


@pytest.mark.parametrize("field,value", [
    ("inbox_confirmed", False), ("inbox_confirmed", "true"), ("gmail_id", None),
    ("edition_date", "2026-11-19"), ("content_hash", "different"),
    ("plain_exact_match", False), ("html_exact_match", False),
    ("destination_key", "other-recipient"), ("destination_key", None),
    ("message_id", "<another-message@example.invalid>"), ("message_id", None),
])
def test_smtp_acceptance_alone_never_authorizes_switch(gate, field, value):
    gate[4][field] = value
    with pytest.raises(RuntimeError, match="inbox verification"):
        release.validate_gate(*gate)


@pytest.mark.parametrize("state", ["pending", "uncertain", "retryable"])
def test_non_sent_ledger_blocks_activation(gate, state):
    gate[3]["state"] = state
    with pytest.raises(RuntimeError, match="delivery record"):
        release.validate_gate(*gate)


@pytest.mark.parametrize("hours", [-24, -2, 1, 24])
def test_activation_window_protects_tonight_and_stale_releases(gate, hours):
    args = list(gate)
    args[-1] += timedelta(hours=hours)
    with pytest.raises(RuntimeError, match="activation window"):
        release.validate_gate(*args)


@pytest.mark.parametrize("field,value", [("state", "Running"), ("enabled", False), ("xml_sha256", "changed"),
                                         ("last_result", 1), ("last_date", "2026-11-19")])
def test_normal_task_must_succeed_without_configuration_changes(gate, field, value):
    gate[2]["tasks"][0][field] = value
    with pytest.raises(RuntimeError):
        release.validate_gate(*gate)


def test_background_collector_blocks_file_replacement(gate):
    gate[2]["active_processes"] = 1
    with pytest.raises(RuntimeError, match="process is active"):
        release.validate_gate(*gate)


@pytest.mark.parametrize("minutes", [-1, 0, 1, 9, 10])
@pytest.mark.parametrize("task_index", [0, 1])
def test_every_task_requires_ten_minutes_of_quiet_clearance(gate, minutes, task_index):
    gate[2]["tasks"][task_index]["next_run_time"] = (gate[-1]+timedelta(minutes=minutes)).isoformat()
    with pytest.raises(RuntimeError, match="clearance"):
        release.validate_gate(*gate)


@pytest.mark.parametrize("value", [None, "", "2026-11-21T20:00:00"])
def test_unknown_next_run_cannot_be_assumed_safe(gate, value):
    gate[2]["tasks"][0]["next_run_time"] = value
    with pytest.raises(RuntimeError, match="unavailable"):
        release.validate_gate(*gate)


def test_module_and_script_launches_are_both_detected_by_inspection(monkeypatch):
    commands = []
    def fake_powershell(command):
        commands.append(command)
        return json.dumps({"tasks": [{"xml": "pinned"}], "active_processes": 0})
    monkeypatch.setattr(release, "powershell", fake_powershell)
    release.inspect_tasks({"tasks": [{"name": "Digest"}]})
    pattern = re.search(r"CommandLine -match '([^']+)'", commands[0])[1]
    for command in ('python -m daily_digest --monitor sample', 'python -m digest_recovery',
                    'python -u collect_snapshot.py', 'python "C:\\project\\daily_digest.py"'):
        assert re.search(pattern, command)
    assert not re.search(pattern, "python another_program.py")


@pytest.mark.parametrize("name", ["../outside.py", ".env", ".env.backup", "data/status.json", ".git/config", ".venv/bin/python", "token.key"])
def test_runtime_credentials_and_internal_paths_are_not_payload(name):
    assert not release.allowed_payload(name)


@pytest.fixture
def package(tmp_path):
    live = tmp_path / "live"
    folder = tmp_path / "release"
    live.mkdir()
    (folder/"files").mkdir(parents=True)
    (live/"app.py").write_text("old\n", encoding="utf-8")
    (live/".env").write_text("SAMPLE_SETTING=unchanged\n", encoding="utf-8")
    (folder/"files/app.py").write_text("new\n", encoding="utf-8")
    (folder/"files/new.py").write_text("new module\n", encoding="utf-8")
    manifest = {"version": 1, "live_root": str(live), "commit": "sample-commit",
                "baseline": {"app.py": release.sha(live/"app.py"), "new.py": None},
                "payload": {n: release.sha(folder/"files"/n) for n in ("app.py", "new.py")},
                "protected": {".env": release.sha(live/".env")}}
    proof = {"commit": manifest["commit"], "payload": manifest["payload"], "passed": True,
             "quality_status": "passed", "email_status": "disabled", "rendered_format_unchanged": True,
             "real_provider_used": True}
    (folder/"candidate-validation.json").write_text(json.dumps(proof), encoding="utf-8")
    return manifest, folder, live


@pytest.mark.parametrize("field,value", [
    ("commit", "different"), ("payload", {}), ("passed", False), ("passed", "true"),
    ("quality_status", "failed"), ("email_status", "sent"),
    ("rendered_format_unchanged", False), ("real_provider_used", False), ("real_provider_used", "true"),
])
def test_exact_payload_real_provider_candidate_evidence_is_required(package, field, value):
    manifest, folder, live = package
    path = folder/"candidate-validation.json"
    proof = json.loads(path.read_text(encoding="utf-8"))
    proof[field] = value
    path.write_text(json.dumps(proof), encoding="utf-8")
    with pytest.raises(RuntimeError, match="candidate validation"):
        release.activate(manifest, folder, observe=lambda *a: pytest.fail("Do not inspect or install"))
    assert (live/"app.py").read_text() == "old\n" and not (folder/"backup").exists()


def test_absent_candidate_preview_blocks_activation_but_not_hash_verification(package):
    manifest, folder, live = package
    (folder/"candidate-validation.json").unlink()
    release.verify_release(manifest, folder)
    with pytest.raises(FileNotFoundError):
        release.activate(manifest, folder, observe=lambda *a: pytest.fail("Do not inspect or install"))
    assert (live/"app.py").read_text() == "old\n" and not (folder/"backup").exists()


def test_pinned_install_preserves_backup_and_calls_gate_twice(package):
    manifest, folder, live = package
    calls = []
    release.activate(manifest, folder, observe=lambda *a: calls.append(a))
    assert len(calls) == 2
    assert (folder/"backup/app.py").read_text() == "old\n"
    assert (live/"app.py").read_text() == "new\n"
    assert (live/"new.py").exists()
    release.verify_release(manifest, folder, installed=True)


def test_failed_postinstall_restores_old_files_and_removes_new_files(package):
    manifest, folder, live = package
    calls = []
    def gate(*args):
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("Post-install verification failed")
    with pytest.raises(RuntimeError, match="Post-install"):
        release.activate(manifest, folder, observe=gate)
    assert (live/"app.py").read_text() == "old\n"
    assert not (live/"new.py").exists()
    assert release.sha(live/".env") == manifest["protected"][".env"]
    result = json.loads((folder/"rollback-result.json").read_text(encoding="utf-8"))
    assert result["status"] == "rolled_back" and not result["failures"]
    assert set(result["restored_files"]) == {"app.py", "new.py"}


@pytest.mark.parametrize("target", ["payload", "live", "protected"])
def test_changed_files_stop_before_first_replacement(package, target):
    manifest, folder, live = package
    path = {"payload": folder/"files/app.py", "live": live/"app.py", "protected": live/".env"}[target]
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError):
        release.activate(manifest, folder, observe=lambda *a: pytest.fail("Gate should not run"))
    assert not (folder/"backup").exists()


def test_live_edit_during_observation_stops_before_backup(package):
    manifest, folder, live = package
    def observe(*args):
        (live/"app.py").write_text("concurrent local edit", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Live file changed"):
        release.activate(manifest, folder, observe=observe)
    assert (live/"app.py").read_text() == "concurrent local edit"
    assert not (folder/"backup").exists()


def test_failed_activation_receipt_never_claims_installed_files_were_rolled_back(package, monkeypatch, capsys):
    manifest, folder, live = package
    (folder/"manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    activate = release.activate
    monkeypatch.setattr(release, "activate", lambda m, r: activate(m, r, observe=lambda *a: None))
    write_text = type(folder).write_text
    def failed_receipt(path, *args, **kwargs):
        if path.name == "activation-result.json":
            raise OSError("receipt unavailable")
        return write_text(path, *args, **kwargs)
    monkeypatch.setattr(type(folder), "write_text", failed_receipt)
    assert release.main(["--release", str(folder)]) == 1
    assert (live/"app.py").read_text() == "new\n"
    assert "Release IS installed" in capsys.readouterr().out


def test_verify_only_does_not_install_or_require_a_delivery(package, monkeypatch):
    manifest, folder, live = package
    (folder/"manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(release, "observe_gate", lambda *a: pytest.fail("No activation expected"))
    assert release.main(["--release", str(folder), "--verify-only"]) == 0
    assert (live/"app.py").read_text() == "old\n"


def test_observed_gate_requires_delivered_file_hash(package, gate, monkeypatch):
    manifest, folder, live = package
    configured, status, tasks, delivery, inbox, now = copy.deepcopy(gate)
    manifest.update(configured, status_file="status.json")
    (live/"digest.md").write_text("reviewed\n", encoding="utf-8")
    status["digest_path"] = str(live/"digest.md")
    digest_hash = hashlib.sha256(b"reviewed\n").hexdigest()
    delivery["content_hash"] = inbox["content_hash"] = digest_hash
    (live/"status.json").write_text(json.dumps(status), encoding="utf-8")
    (folder/"protected-inbox.json").write_text(json.dumps(inbox), encoding="utf-8")
    monkeypatch.setattr(release, "read_delivery", lambda m: delivery)
    monkeypatch.setattr(release, "inspect_tasks", lambda m: tasks)
    release.observe_gate(manifest, folder, now)
    (live/"digest.md").write_text("different", encoding="utf-8")
    with pytest.raises(RuntimeError, match="differs"):
        release.observe_gate(manifest, folder, now)
