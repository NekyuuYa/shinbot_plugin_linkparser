"""Debouncer unit tests."""

from __future__ import annotations

import time

from shinbot_plugin_linkparser.debounce import Debouncer


def test_no_hit_before_remember() -> None:
    debouncer = Debouncer(window_seconds=60)
    assert debouncer.hit("s1", "link") is False


def test_hit_after_remember() -> None:
    debouncer = Debouncer(window_seconds=60)
    debouncer.remember("s1", "link")
    assert debouncer.hit("s1", "link") is True


def test_session_isolation() -> None:
    debouncer = Debouncer(window_seconds=60)
    debouncer.remember("s1", "link")
    assert debouncer.hit("s2", "link") is False


def test_forget_allows_retry() -> None:
    debouncer = Debouncer(window_seconds=60)
    debouncer.remember("s1", "link")
    debouncer.forget("s1", "link")
    assert debouncer.hit("s1", "link") is False


def test_expiry_after_window() -> None:
    debouncer = Debouncer(window_seconds=0.05)
    debouncer.remember("s1", "link")
    assert debouncer.hit("s1", "link") is True
    time.sleep(0.08)
    assert debouncer.hit("s1", "link") is False


def test_disabled_window_never_hits() -> None:
    debouncer = Debouncer(window_seconds=0)
    debouncer.remember("s1", "link")
    assert debouncer.hit("s1", "link") is False


def test_clear_session() -> None:
    debouncer = Debouncer(window_seconds=60)
    debouncer.remember("s1", "a")
    debouncer.remember("s1", "b")
    debouncer.clear_session("s1")
    assert debouncer.snapshot() == {}


def test_snapshot_exposes_state() -> None:
    debouncer = Debouncer(window_seconds=60)
    debouncer.remember("s1", "a")
    snapshot = debouncer.snapshot()
    assert "a" in snapshot["s1"]
