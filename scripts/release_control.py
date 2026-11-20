"""Activate a pinned local release with rollback on installation failure.

This command never edits tasks, credentials or databases, and never sends email.
Activation requires independent inbox verification as well as the sent ledger.
Prepare a manifest/files directory in a private local release folder first.
Run in a quiet window: scheduled starts within ten minutes are blocked, but the
current program imports modules before taking its workflow locks. This first
installation therefore cannot exclude a concurrent manual program launch.
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.activate_analysis import inside, install_files, powershell, sha


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Release timestamps require an explicit timezone")
    return result.astimezone(timezone.utc)


def allowed_payload(name):
    path = Path(name)
    return (bool(path.parts) and not path.is_absolute() and ".." not in path.parts
            and path.parts[0].casefold() not in {".git", "data", ".venv", ".codex", ".agents", ".aws"}
            and (not path.parts[0].casefold().startswith(".env") or name == ".env.example")
            and path.suffix.casefold() not in {".db", ".sqlite", ".pem", ".key", ".pfx", ".p12"})


def verify_release(manifest, release, *, installed=False):
    """Hash-pin source and destination without trusting arbitrary payload paths."""
    if manifest.get("version") != 1 or not manifest.get("payload"):
        raise ValueError("Unsupported or empty release")
    root = Path(manifest["live_root"])
    if not root.is_absolute():
        raise ValueError("Live root must be absolute")
    for name, expected in manifest["payload"].items():
        if not allowed_payload(name) or name not in manifest["baseline"]:
            raise ValueError("Unsafe or unpinned release path")
        if sha(inside(release / "files", name)) != expected:
            raise RuntimeError(f"Tested payload changed: {name}")
    for name, expected in manifest["baseline"].items():
        target = inside(root, name)
        expected = manifest["payload"].get(name, expected) if installed else expected
        actual = sha(target) if target.is_file() else None
        if actual != expected:
            raise RuntimeError(f"Live file changed since release preparation: {name}")
    for name, expected in manifest["protected"].items():
        if sha(inside(root, name)) != expected:
            raise RuntimeError(f"Protected setting changed: {name}")


def validate_candidate(manifest, release):
    """Require an isolated real-provider preview of this exact tested payload."""
    proof = json.loads((release / "candidate-validation.json").read_text(encoding="utf-8"))
    if (not isinstance(proof, dict) or proof.get("commit") != manifest["commit"]
            or proof.get("payload") != manifest["payload"] or proof.get("passed") is not True
            or proof.get("quality_status") != "passed" or proof.get("email_status") != "disabled"
            or proof.get("rendered_format_unchanged") is not True or proof.get("real_provider_used") is not True):
        raise RuntimeError("Exact-payload real-provider no-email candidate validation is required")


def validate_gate(manifest, status, tasks, delivery, inbox, now):
    """No early activation, stale success, running process or inferred delivery."""
    if now.tzinfo is None or not utc(manifest["not_before"]) <= now < utc(manifest["expires"]):
        raise RuntimeError("Outside the explicit activation window")
    edition, monitor = manifest["protected_edition"], manifest["monitor"]
    if (status.get("edition_date") != edition or status.get("monitor") != monitor
            or status.get("status") not in {"completed", "completed_with_warnings"}
            or status.get("email_status") != "sent" or status.get("quality_status") != "passed"
            or status.get("exit_code") != 0):
        raise RuntimeError("Protected edition has not passed quality and delivery")
    if (not delivery or delivery.get("state") != "sent"
            or delivery.get("edition_date") != edition or delivery.get("monitor") != monitor
            or delivery.get("destination_key") != manifest["destination_key"]):
        raise RuntimeError("No matching sent delivery record")
    if (inbox.get("edition_date") != edition or inbox.get("monitor") != monitor
            or inbox.get("inbox_confirmed") is not True or not inbox.get("gmail_id")
            or inbox.get("plain_exact_match") is not True or inbox.get("html_exact_match") is not True
            or inbox.get("destination_key") != manifest["destination_key"]
            or not delivery.get("message_id") or inbox.get("message_id") != delivery["message_id"]
            or inbox.get("content_hash") != delivery.get("content_hash")):
        raise RuntimeError("Independent full-message inbox verification is required")
    if tasks.get("active_processes"):
        raise RuntimeError("Digest or collection process is active")
    observed = {task["name"]: task for task in tasks.get("tasks", [])}
    for expected in manifest["tasks"]:
        task = observed.get(expected["name"], {})
        if (task.get("state") != "Ready" or task.get("enabled") is not True
                or task.get("xml_sha256") != expected["xml_sha256"]):
            raise RuntimeError("Task configuration or state changed")
        if expected.get("protects_edition") and (task.get("last_result") != 0 or task.get("last_date") != edition):
            raise RuntimeError("The normal protected scheduled run did not succeed")
        try:
            next_run = utc(task.get("next_run_time", ""))
        except (TypeError, ValueError, AttributeError) as exc:
            raise RuntimeError("Next scheduled run time is unavailable") from exc
        if next_run - now <= timedelta(minutes=10):
            raise RuntimeError("At least ten minutes of clearance before every scheduled task is required")


def inspect_tasks(manifest):
    names = [t["name"] for t in manifest["tasks"]]
    if not names or any(not re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in names):
        raise ValueError("Unsupported scheduled-task name")
    literals = ",".join("'" + name + "'" for name in names)
    result = json.loads(powershell("""
$ErrorActionPreference='Stop'
$items=@(foreach($n in @(""" + literals + """)) {
$t=Get-ScheduledTask -TaskName $n
$i=Get-ScheduledTaskInfo -TaskName $n
[ordered]@{name=$n;state=[string]$t.State;enabled=[bool]$t.Settings.Enabled;last_date=$i.LastRunTime.ToString('yyyy-MM-dd');last_result=$i.LastTaskResult;next_run_time=$i.NextRunTime.ToUniversalTime().ToString('o');xml=(Export-ScheduledTask -TaskName $n)}
})
$p=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^python' -and $_.CommandLine -match '(?i)(?:daily_digest|digest_recovery|collect_snapshot)(?:\\.py|\\b)'})
[ordered]@{tasks=$items;active_processes=$p.Count} | ConvertTo-Json -Depth 5 -Compress
"""))
    for task in result["tasks"]:
        task["xml_sha256"] = hashlib.sha256(task.pop("xml").strip().encode()).hexdigest()
    return result


def read_delivery(manifest):
    path = inside(Path(manifest["live_root"]), manifest["delivery_ledger"])
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM delivery_guard WHERE monitor=? AND edition_date=? AND destination_key=?",
            (manifest["monitor"], manifest["protected_edition"], manifest["destination_key"]),
        ).fetchone()
        return dict(row) if row else None


def observe_gate(manifest, release, now):
    root = Path(manifest["live_root"])
    status = json.loads(inside(root, manifest["status_file"]).read_text(encoding="utf-8"))
    inbox = json.loads((release / "protected-inbox.json").read_text(encoding="utf-8"))
    delivery, tasks = read_delivery(manifest), inspect_tasks(manifest)
    validate_gate(manifest, status, tasks, delivery, inbox, now)
    digest = Path(status["digest_path"])
    if not digest.is_absolute():
        digest = root / digest
    # Only the configured root may provide the protected edition's artifact.
    digest = digest.resolve()
    if not digest.is_relative_to(root.resolve()):
        raise RuntimeError("Protected digest is outside its configured project")
    content_hash = hashlib.sha256(digest.read_text(encoding="utf-8").encode()).hexdigest()
    if content_hash != delivery["content_hash"]:
        raise RuntimeError("Protected digest differs from the delivered content")
    return tasks


def activate(manifest, release, *, observe=observe_gate, now=None):
    supplied_now = now
    now = now or datetime.now(timezone.utc)
    verify_release(manifest, release)
    validate_candidate(manifest, release)
    observe(manifest, release, now)
    # Task and inbox inspection may take time. Never overwrite a local change
    # made after the initial pin check; the installer also checks its backups.
    verify_release(manifest, release)

    def post_install():
        verify_release(manifest, release, installed=True)
        validate_candidate(manifest, release)
        observe(manifest, release, supplied_now or datetime.now(timezone.utc))

    # The existing tested installer backs up before its first atomic replacement
    # and restores installed targets if any verification callback fails.
    install_files(manifest, release, install_task=post_install, rollback_task=lambda: None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--verify-only", action="store_true", help="Check hashes only; never bypass the activation gate")
    args = parser.parse_args(argv)
    release = Path(args.release).resolve()
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    if args.verify_only:
        verify_release(manifest, release)
        print("Pinned release and unchanged live baseline verified. No files installed.")
        return 0
    try:
        activate(manifest, release)
    except (OSError, ValueError, RuntimeError, KeyError, sqlite3.Error) as exc:
        print(f"Activation blocked or rolled back: {exc}")
        return 1
    result = {"status": "activated", "completed_at": datetime.now(timezone.utc).isoformat(),
              "commit": manifest["commit"], "tasks_changed": False}
    try:
        (release / "activation-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"Release IS installed, but writing its activation receipt failed: {exc}")
        return 1
    print("Verified release installed; task schedules and protected settings unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
