"""SessionStateStore unit tests."""

from __future__ import annotations

from shinbot_plugin_linkparser.session_state import SessionStateStore


def test_default_off_when_parse_by_default_false(tmp_path) -> None:
    store = SessionStateStore(tmp_path / "state.json", parse_by_default=False)
    assert store.is_enabled("g:1") is False


def test_enable_then_is_enabled(tmp_path) -> None:
    store = SessionStateStore(tmp_path / "state.json", parse_by_default=False)
    store.enable("g:1")
    assert store.is_enabled("g:1") is True
    assert store.is_enabled("g:2") is False


def test_disable_overrides_parse_by_default(tmp_path) -> None:
    store = SessionStateStore(tmp_path / "state.json", parse_by_default=True)
    assert store.is_enabled("g:1") is True
    store.disable("g:1")
    assert store.is_enabled("g:1") is False
    store.enable("g:1")
    assert store.is_enabled("g:1") is True


def test_state_persists_across_reload(tmp_path) -> None:
    path = tmp_path / "state.json"
    store = SessionStateStore(path, parse_by_default=False)
    store.enable("g:1")
    store.disable("g:2")

    reloaded = SessionStateStore(path, parse_by_default=False)
    assert reloaded.is_enabled("g:1") is True
    assert reloaded.is_enabled("g:2") is False


def test_session_isolation(tmp_path) -> None:
    store = SessionStateStore(tmp_path / "state.json", parse_by_default=False)
    store.enable("g:1")
    assert store.is_enabled("g:2") is False


def test_empty_session_id(tmp_path) -> None:
    store = SessionStateStore(tmp_path / "state.json", parse_by_default=False)
    assert store.is_enabled(None) is False
    store.enable("")
    # "" is treated as a valid (degenerate) session key once stored
    assert store.is_enabled("") is True
