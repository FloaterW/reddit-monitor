"""Deterministic pacing simulations; no real Reddit traffic or sleep."""
from unittest.mock import Mock, patch

import pytest
import requests

import reddit_scraper as scraper


def response(code=200, content=b'<feed xmlns="http://www.w3.org/2005/Atom"/>', retry="30"):
    return Mock(status_code=code, content=content, headers={"Retry-After": retry})


@pytest.fixture
def clock():
    now = [0.0]
    def sleep(seconds):
        now[0] += seconds
    with patch.object(scraper.time, "monotonic", side_effect=lambda: now[0]), patch.object(scraper.time, "sleep", side_effect=sleep):
        yield now


def test_recovery_requires_three_valid_feeds_and_is_gradual(clock):
    with scraper.collection_diagnostics(), patch.object(scraper._RSS_SESSION, "get", return_value=response()):
        pacing = scraper._RSS_PACING.get()
        pacing.update(interval=60.0, next=60.0)
        for expected in (60, 60, 45):
            scraper._fetch_rss("/test")
            assert pacing["interval"] == expected
        assert clock[0] == 180  # Existing waits were not shortened retroactively.
        for _ in range(30):
            scraper._fetch_rss("/test")
        assert pacing["interval"] == 15


@pytest.mark.parametrize("failure", [response(503), response(content=b"not xml"), requests.ConnectionError()])
def test_failures_reset_success_streak(clock, failure):
    with scraper.collection_diagnostics(), patch.object(scraper._RSS_SESSION, "get", return_value=response()) as get:
        pacing = scraper._RSS_PACING.get()
        pacing.update(interval=60.0)
        scraper._fetch_rss("/test")
        scraper._fetch_rss("/test")
        get.side_effect = [failure] * 3 if isinstance(failure, Exception) else [failure]
        scraper._fetch_rss("/test")
        get.side_effect = None
        scraper._fetch_rss("/test")
        assert pacing["interval"] == 60


def test_renewed_rate_limit_resets_recovery_and_preserves_server_wait(clock):
    with scraper.collection_diagnostics(), patch.object(scraper._RSS_SESSION, "get", return_value=response()) as get:
        pacing = scraper._RSS_PACING.get()
        pacing.update(interval=45.0)
        scraper._fetch_rss("/first")
        scraper._fetch_rss("/first")
        before = clock[0]
        get.side_effect = [response(429, retry="120"), response()]
        scraper._fetch_rss("/second")
        assert clock[0] - before >= 165
        assert pacing["interval"] == 60


def test_recovery_does_not_extend_feed_deadline(clock):
    with scraper.collection_diagnostics(), scraper.request_budget(300), patch.object(scraper._RSS_SESSION, "get", return_value=response()) as get:
        pacing = scraper._RSS_PACING.get()
        pacing.update(interval=60.0, next=60.0)
        with pytest.raises(TimeoutError):
            while True:
                scraper._fetch_rss("/creditcards")
        # Previously four calls fit (60,120,180,240); now five fit without
        # extending the deadline (60,120,180,225,270).
        assert get.call_count == 5
        assert clock[0] < 300


def test_normal_pacing_and_new_run_remain_unaffected(clock):
    with scraper.collection_diagnostics(), patch.object(scraper._RSS_SESSION, "get", return_value=response()):
        for _ in range(10):
            scraper._fetch_rss("/test")
        assert scraper._RSS_PACING.get()["interval"] == 6
    assert scraper._RSS_PACING.get() is None
    with scraper.collection_diagnostics():
        assert scraper._RSS_PACING.get()["interval"] == 6
