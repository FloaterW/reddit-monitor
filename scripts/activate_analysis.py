"""Guarded one-shot deployment of a tested local release, never a digest run.

The release manifest pins both the tested payload and the existing live tree.
Activation refuses early/late execution, an unsuccessful protected edition,
running digests, changed files/settings or an existing collection task.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inside(root, relative):
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or candidate == root.resolve():
        raise ValueError("Release path escapes its root")
    return candidate


def verify_files(manifest, release):
    root = Path(manifest["live_root"])
    for name, expected in manifest["baseline"].items():
        path = inside(root, name)
        actual = sha(path) if path.is_file() else None
        if actual != expected:
            raise RuntimeError(f"Live file changed since testing: {name}")
    for name, expected in manifest["payload"].items():
        if sha(inside(release / "files", name)) != expected:
            raise RuntimeError(f"Release integrity mismatch: {name}")
        if name not in manifest["baseline"]:
            raise RuntimeError("Payload has no baseline entry")
    if sha(root / ".env") != manifest["environment_sha256"]:
        raise RuntimeError("Local settings changed since testing")


def validate_gate(manifest, status, tasks, now):
    if not datetime.fromisoformat(manifest["not_before"]) <= now < datetime.fromisoformat(manifest["expires"]):
        raise RuntimeError("Outside the approved activation window")
    if (status.get("edition_date") != manifest["protected_edition"]
            or status.get("monitor") != manifest.get("monitor")
            or status.get("status") not in {"completed", "completed_with_warnings"}
            or status.get("email_status") != "sent" or status.get("exit_code") != 0):
        raise RuntimeError("Protected evening edition has not completed successfully")
    if (tasks.get("state") != "Ready" or tasks.get("last_result") != 0
            or tasks.get("last_date") != manifest["protected_edition"]
            or tasks.get("collection_exists") or tasks.get("digest_processes")):
        raise RuntimeError("Task state is unsafe for activation")
    xml = tasks.get("xml", "").strip()
    if hashlib.sha256(xml.encode()).hexdigest() != manifest["task_xml_sha256"]:
        raise RuntimeError("The evening task configuration changed")


def powershell(command):
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError("Windows task inspection or configuration failed")
    return result.stdout.strip()


def task_state():
    return json.loads(powershell("""
$ErrorActionPreference='Stop'
$t=Get-ScheduledTask -TaskName RedditDailyDigest
$i=Get-ScheduledTaskInfo -TaskName RedditDailyDigest
$p=@(Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -match '(?i)(?:daily_digest|collect_snapshot)\\.py' -and $_.Name -match '^python'})
[ordered]@{state=[string]$t.State;last_result=$i.LastTaskResult;last_date=$i.LastRunTime.ToString('yyyy-MM-dd');collection_exists=[bool](Get-ScheduledTask -TaskName RedditMiddayCollection -ErrorAction SilentlyContinue);digest_processes=$p.Count;xml=(Export-ScheduledTask -TaskName RedditDailyDigest)} | ConvertTo-Json -Compress
"""))


def install_files(manifest, release, *, install_task, rollback_task):
    """Back up all targets before the first replacement; roll back on failure."""
    root, backup = Path(manifest["live_root"]), release / "backup"
    if backup.exists():
        raise RuntimeError("Previous activation attempt exists; inspect it before retrying")
    backup.mkdir()
    installed, task_attempted = [], False
    try:
        for name in manifest["payload"]:
            target = inside(root, name)
            actual = sha(target) if target.is_file() else None
            if actual != manifest["baseline"][name]:
                raise RuntimeError(f"Live file changed before backup: {name}")
            if target.exists():
                saved = inside(backup, name)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
                if sha(saved) != manifest["baseline"][name]:
                    raise RuntimeError(f"Backup differs from pinned baseline: {name}")
        for name in manifest["payload"]:
            target, src = inside(root, name), inside(release / "files", name)
            actual = sha(target) if target.is_file() else None
            if actual != manifest["baseline"][name]:
                raise RuntimeError(f"Live file changed before replacement: {name}")
            if sha(src) != manifest["payload"][name]:
                raise RuntimeError(f"Release source changed before replacement: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".upgrade-tmp")
            if temporary.exists():
                raise RuntimeError("Unexpected upgrade temporary file")
            try:
                shutil.copy2(src, temporary)
                if sha(temporary) != manifest["payload"][name]:
                    raise RuntimeError(f"Copied release payload differs: {name}")
                os.replace(temporary, target)
                installed.append(name)
            finally:
                temporary.unlink(missing_ok=True)
        task_attempted = True
        install_task()
        for name, expected in manifest["payload"].items():
            if sha(inside(root, name)) != expected:
                raise RuntimeError("Post-install source verification failed")
    except BaseException as original:
        restored, failures = [], []
        for name in reversed(installed):
            try:
                target, old = inside(root, name), inside(backup, name)
                if old.exists():
                    temporary = target.with_name(target.name + ".rollback-tmp")
                    if temporary.exists():
                        raise RuntimeError("Unexpected rollback temporary file")
                    try:
                        if sha(old) != manifest["baseline"][name]:
                            raise RuntimeError("Backup no longer matches pinned baseline")
                        shutil.copy2(old, temporary)
                        if sha(temporary) != manifest["baseline"][name]:
                            raise RuntimeError("Copied rollback file differs from backup")
                        os.replace(temporary, target)
                    finally:
                        temporary.unlink(missing_ok=True)
                else:
                    if manifest["baseline"][name] is not None:
                        raise RuntimeError("Required backup is missing")
                    target.unlink(missing_ok=True)
                restored.append(name)
            except BaseException as exc:
                failures.append({"path": name, "error": f"{type(exc).__name__}: {exc}"})
        if task_attempted:
            try:
                rollback_task()
            except BaseException as exc:
                failures.append({"path": "task callback", "error": f"{type(exc).__name__}: {exc}"})
        report = {"status": "rollback_failed" if failures else "rolled_back",
                  "completed_at": datetime.now(timezone.utc).isoformat(),
                  "installation_error": f"{type(original).__name__}: {original}",
                  "installed_files": installed, "restored_files": restored, "failures": failures,
                  "backup_preserved": True}
        try:
            # Flush before atomic replacement so a partial JSON file cannot
            # masquerade as a complete restoration report.
            descriptor, filename = tempfile.mkstemp(prefix="rollback-result-", suffix=".tmp", dir=release)
            temporary_report = Path(filename)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(report, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_report, release / "rollback-result.json")
            finally:
                temporary_report.unlink(missing_ok=True)
        except OSError as exc:
            failures.append({"path": "rollback-result.json", "error": f"{type(exc).__name__}: {exc}"})
        if failures:
            raise RuntimeError("Installation failed; rollback is incomplete or its receipt could not be saved. "
                               "Inspect the preserved backup and rollback-result.json before running the program. "
                               + "; ".join(f"{item['path']}: {item['error']}" for item in failures)) from original
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    release = Path(args.release).resolve()
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "blocked"}
    try:
        verify_files(manifest, release)
        if args.verify_only:
            print("Release and live baseline verified; no activation performed.")
            return 0
        root = Path(manifest["live_root"])
        status = json.loads((root / "data" / "last_run_status.json").read_text(encoding="utf-8"))
        validate_gate(manifest, status, task_state(), datetime.now(timezone.utc))

        def install_task():
            script = root / "scripts" / "install_collection_task.ps1"
            completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                        "-File", str(script), "-Root", str(root)], capture_output=True, timeout=60)
            if completed.returncode:
                raise RuntimeError("Midday task installation failed")

        def rollback_task():
            # Never remove a different task that appeared concurrently.
            expected = ('/c ""' + str(root / "run_collection.bat") + '""').replace("'", "''")
            powershell("$t=Get-ScheduledTask -TaskName RedditMiddayCollection -ErrorAction SilentlyContinue; "
                       f"if ($t -and $t.Actions.Arguments -eq '{expected}' -and "
                       "$t.Description -eq 'Collection only: API cache and local analysis; no model or email. Evening digest remains at 18:30.') "
                       "{Unregister-ScheduledTask -TaskName RedditMiddayCollection -Confirm:$false -ErrorAction Stop}")

        install_files(manifest, release, install_task=install_task, rollback_task=rollback_task)
        result.update(status="activated", finished_at=datetime.now(timezone.utc).isoformat())
        print("Validated API analysis release activated; evening schedule unchanged.")
        return 0
    except Exception as exc:
        result["error"] = str(exc)
        print(f"Activation blocked or rolled back: {exc}")
        return 1
    finally:
        if not args.verify_only:
            (release / "activation-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
