"""Run one edition with at most one bounded, frozen-input synthesis retry."""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import daily_digest as digest

MAX_JOB_SECONDS = 3000
MIN_RETRY_SECONDS = 120


def source_fingerprints(raw_path):
    """Pin raw data and coverage before synthesis; absent provenance blocks retry."""
    if not raw_path:
        return None
    raw = Path(raw_path).resolve()
    coverage = raw.with_suffix(".coverage.json")
    try:
        metadata = json.loads(coverage.read_text(encoding="utf-8"))
        if (not isinstance(metadata, dict)
                or not isinstance(metadata.get("window_start"), str)
                or not isinstance(metadata.get("window_end"), str)):
            return None
        start = datetime.fromisoformat(metadata["window_start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(metadata["window_end"].replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None or start >= end or metadata.get("replay_unknown"):
            return None
        paths = [raw, coverage]
        audit = raw.with_suffix(".collection-audit.json")
        if audit.exists():
            paths.append(audit)
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _replace_option(argv, flag, value=None):
    result, index = [], 0
    while index < len(argv):
        item = argv[index]
        if item == flag:
            index += 2
            continue
        if item.startswith(flag + "="):
            index += 1
            continue
        result.append(item)
        index += 1
    if value is not None:
        result.extend([flag, value])
    return result


def main(argv=None, *, runner=None, clock=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    runner, clock = runner or digest.main, clock or time.monotonic
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--save")
    parser.add_argument("--save-raw")
    parser.add_argument("--from-json")
    selected, _ = parser.parse_known_args(argv)
    deadline = clock() + MAX_JOB_SECONDS
    state, attempts = {}, []
    result = runner(argv, job_deadline=deadline, recovery_state=state)
    attempts.append({"exit_code": result, "error_type": state.get("error_type")})
    original = state.get("source_fingerprints")
    eligible = (result == 1 and state.get("retryable") and state.get("error_type") == "LLMTimeoutError"
                and selected.save and original and state.get("raw_json_path")
                and clock() + MIN_RETRY_SECONDS < deadline)
    if eligible and source_fingerprints(state["raw_json_path"]) == original:
        retry = _replace_option(argv, "--save-raw")
        retry = _replace_option(retry, "--from-json", str(Path(state["raw_json_path"]).resolve()))
        retry = _replace_option(retry, "--digest-date", state["edition_date"])
        print("[RECOVERY] One bounded retry using the same saved sources, edition and checkpoints.", flush=True)
        result = runner(retry, job_deadline=deadline, recovery_state=state)
        attempts.append({"exit_code": result, "error_type": state.get("error_type")})
    elif eligible:
        print("[RECOVERY] Source provenance changed; automatic retry blocked.")
    if selected.save:
        try:
            digest.atomic_write_json(Path(selected.save).with_suffix(".recovery.json"), {
                "version": 1, "attempts": attempts, "source_fingerprints": original,
                "edition_date": state.get("edition_date"), "exit_code": result,
                "deadline_remaining_seconds": max(0, round(deadline-clock(), 3)),
                "maximum_attempts": 2})
        except OSError as exc:
            print(f"[WARN] Recovery diagnostics could not be saved ({type(exc).__name__}).")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
