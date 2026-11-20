"""Offline continuity planning; prior delivery and original source windows matter."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from coverage_continuity import resolve_daily_window

END = datetime(2026, 11, 4, 23, 30, tzinfo=timezone.utc)


def prior(tmp_path, *, elapsed=25, status="completed_with_warnings", email="sent",
          monitor="credit-card-churning", coverage=None):
    raw = tmp_path / "digest_arbitrary_filename.json"
    sidecar = raw.with_suffix(".coverage.json")
    previous_end = END - timedelta(hours=elapsed)
    sidecar.write_text(json.dumps(coverage if coverage is not None else {
        "window_start": (previous_end - timedelta(hours=24)).isoformat(),
        "window_end": previous_end.isoformat(), "replay_unknown": False,
    }), encoding="utf-8")
    path = tmp_path / "status.json"
    path.write_text(json.dumps({
        "status": status, "email_status": email, "monitor": monitor,
        "raw_json_path": str(raw), "started_at": "2000-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    return path, sidecar


def test_one_hour_shift_bridges_the_actual_saved_boundary_read_only(tmp_path):
    status, sidecar = prior(tmp_path)
    before = [p.read_bytes() for p in (status, sidecar)]
    window = resolve_daily_window(END, status, "credit-card-churning")
    assert window.start == datetime(2026, 11, 3, 22, 30, tzinfo=timezone.utc)
    assert window.end == END and window.extended
    assert "not exhaustive" in window.warnings[0]["reason"]
    assert [p.read_bytes() for p in (status, sidecar)] == before


@pytest.mark.parametrize("elapsed", [23, 24])
def test_no_extension_when_normal_window_already_reaches_previous_end(tmp_path, elapsed):
    status, _ = prior(tmp_path, elapsed=elapsed)
    window = resolve_daily_window(END, status, "credit-card-churning")
    assert window.start == END - timedelta(hours=24)
    assert not window.extended and not window.warnings


@pytest.mark.parametrize("elapsed", [25.01, 30, 48])
def test_larger_gap_is_disclosed_without_unbounded_backfill(tmp_path, elapsed):
    status, _ = prior(tmp_path, elapsed=elapsed)
    window = resolve_daily_window(END, status, "credit-card-churning")
    assert window.start == END - timedelta(hours=24)
    assert not window.extended
    assert "continuity gap" in window.warnings[0]["reason"]
    assert "was not collected" in window.warnings[0]["reason"]


@pytest.mark.parametrize("fields", [
    {"status": "failed"}, {"status": "running"}, {"status": []},
    {"email": "disabled"}, {"email": "failed"}, {"email": None},
    {"monitor": "another-monitor"},
])
def test_unsent_failed_or_other_monitor_status_cannot_extend_window(tmp_path, fields):
    status, _ = prior(tmp_path, **fields)
    window = resolve_daily_window(END, status, "credit-card-churning")
    assert not window.extended and not window.warnings


@pytest.mark.parametrize("coverage", [
    {}, [], {"replay_unknown": True},
    {"window_start": "2026-11-02T22:30:00", "window_end": "2026-11-03T22:30:00"},
    {"window_start": "2026-11-03T22:30:00Z", "window_end": "2026-11-03T22:30:00Z"},
    {"window_start": "2026-11-03T22:30:00Z", "window_end": "2026-11-05T22:30:00Z"},
])
def test_missing_or_invalid_boundaries_do_not_infer_time_from_filename(tmp_path, coverage):
    status, _ = prior(tmp_path, coverage=coverage)
    window = resolve_daily_window(END, status, "credit-card-churning")
    assert not window.extended and window.start == END - timedelta(hours=24)
    assert "could not be checked" in window.warnings[0]["reason"]


def test_first_run_uses_normal_window(tmp_path):
    window = resolve_daily_window(END, tmp_path / "missing.json", "credit-card-churning")
    assert window.start == END - timedelta(hours=24)
    assert not window.extended and not window.warnings


def test_malformed_status_is_not_a_successful_history_record(tmp_path):
    status = tmp_path / "status.json"
    status.write_bytes(b"\xff")
    window = resolve_daily_window(END, status, "credit-card-churning")
    assert not window.extended and not window.warnings


def test_timezone_conversion_and_minute_boundary(tmp_path):
    status, _ = prior(tmp_path)
    local_end = datetime(2026, 11, 4, 18, 30, 59,
                         tzinfo=timezone(timedelta(hours=-5)))
    window = resolve_daily_window(local_end, status, "credit-card-churning")
    assert window.end == END and window.extended


def test_naive_new_window_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_daily_window(datetime(2026, 11, 4, 18, 30), tmp_path / "status.json", "test")
