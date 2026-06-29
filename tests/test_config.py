"""Tests for monitor_config.py — config loading, validation, and defaults."""

import json

import pytest

from monitor_config import list_monitors, load_monitor


class TestListMonitors:
    def test_finds_builtin_monitors(self):
        monitors = list_monitors()
        assert "churning" in monitors
        assert "job-market" in monitors

    def test_returns_sorted(self):
        monitors = list_monitors()
        assert monitors == sorted(monitors)


class TestLoadMonitor:
    def test_loads_churning(self):
        config = load_monitor("churning")
        assert config["name"] == "credit-card-churning"
        assert isinstance(config["subreddits"], list)
        assert len(config["subreddits"]) > 0
        assert isinstance(config["keywords"], list)
        assert len(config["keywords"]) > 0

    def test_loads_job_market(self):
        config = load_monitor("job-market")
        assert config["name"] == "job-market-watch"
        assert "cscareerquestions" in config["subreddits"]

    def test_has_defaults(self):
        config = load_monitor("churning")
        assert "post_sort" in config
        assert "time_filter" in config
        assert "posts_per_subreddit" in config
        assert "title_filters" in config

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="Invalid monitor name"):
            load_monitor("../../etc/passwd")

    def test_backslash_traversal_rejected(self):
        with pytest.raises(ValueError, match="Invalid monitor name"):
            load_monitor("..\\..\\secrets")

    def test_nonexistent_raises(self):
        with pytest.raises(ValueError, match="not found"):
            load_monitor("__nonexistent__")

    def test_nonexistent_lists_available(self):
        with pytest.raises(ValueError, match="churning"):
            load_monitor("__nonexistent__")

    def test_invalid_json_raises(self, tmp_path, monkeypatch):
        bad_dir = tmp_path / "monitors"
        bad_dir.mkdir()
        (bad_dir / "bad.json").write_text("{invalid json", encoding="utf-8")

        import monitor_config
        monkeypatch.setattr(monitor_config, "_CONFIG_DIR", bad_dir)

        with pytest.raises(ValueError, match="Invalid JSON"):
            load_monitor("bad")

    def test_missing_required_fields_raises(self, tmp_path, monkeypatch):
        bad_dir = tmp_path / "monitors"
        bad_dir.mkdir()
        (bad_dir / "incomplete.json").write_text(
            json.dumps({"name": "test"}), encoding="utf-8"
        )

        import monitor_config
        monkeypatch.setattr(monitor_config, "_CONFIG_DIR", bad_dir)

        with pytest.raises(ValueError, match="missing required fields"):
            load_monitor("incomplete")

    def test_empty_subreddits_raises(self, tmp_path, monkeypatch):
        bad_dir = tmp_path / "monitors"
        bad_dir.mkdir()
        (bad_dir / "empty.json").write_text(
            json.dumps({"name": "test", "subreddits": [], "keywords": ["a"]}),
            encoding="utf-8",
        )

        import monitor_config
        monkeypatch.setattr(monitor_config, "_CONFIG_DIR", bad_dir)

        with pytest.raises(ValueError, match="non-empty list"):
            load_monitor("empty")

    def test_empty_keywords_raises(self, tmp_path, monkeypatch):
        bad_dir = tmp_path / "monitors"
        bad_dir.mkdir()
        (bad_dir / "empty.json").write_text(
            json.dumps({"name": "test", "subreddits": ["a"], "keywords": []}),
            encoding="utf-8",
        )

        import monitor_config
        monkeypatch.setattr(monitor_config, "_CONFIG_DIR", bad_dir)

        with pytest.raises(ValueError, match="non-empty list"):
            load_monitor("empty")

    def test_defaults_applied(self, tmp_path, monkeypatch):
        minimal_dir = tmp_path / "monitors"
        minimal_dir.mkdir()
        (minimal_dir / "minimal.json").write_text(
            json.dumps({"name": "test", "subreddits": ["python"], "keywords": ["fastapi"]}),
            encoding="utf-8",
        )

        import monitor_config
        monkeypatch.setattr(monitor_config, "_CONFIG_DIR", minimal_dir)

        config = load_monitor("minimal")
        assert config["post_sort"] == "new"
        assert config["time_filter"] == "day"
        assert config["posts_per_subreddit"] == 10
        assert config["title_filters"] == {}

    def test_config_values_not_overridden_by_defaults(self):
        config = load_monitor("churning")
        assert "churningcanada" in config["title_filters"]


class TestListMonitorsEmptyDir:
    def test_empty_dir(self, tmp_path, monkeypatch):
        empty_dir = tmp_path / "monitors"
        empty_dir.mkdir()

        import monitor_config
        monkeypatch.setattr(monitor_config, "_CONFIG_DIR", empty_dir)

        assert list_monitors() == []

    def test_nonexistent_dir(self, tmp_path, monkeypatch):
        import monitor_config
        monkeypatch.setattr(monitor_config, "_CONFIG_DIR", tmp_path / "nope")

        assert list_monitors() == []
