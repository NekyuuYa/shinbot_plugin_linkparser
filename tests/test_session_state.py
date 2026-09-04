"""SessionStateStore (parse-mode levels) unit tests."""

from __future__ import annotations

import json

from shinbot_plugin_linkparser.session_state import SessionStateStore


def test_default_mode_off(tmp_path) -> None:
    store = SessionStateStore(tmp_path / "state.json", default_mode="off")
    assert store.mode("g:1") == "off"
    assert store.default_mode == "off"


def test_set_mode_and_query(tmp_path) -> None:
    store = SessionStateStore(tmp_path / "state.json", default_mode="off")
    assert store.mode("g:1") == "off"
    store.set_mode("g:1", "at")
    assert store.mode("g:1") == "at"
    assert store.mode("g:2") == "off"  # isolated session falls back to default


def test_custom_default_mode(tmp_path) -> None:
    store = SessionStateStore(tmp_path / "state.json", default_mode="always")
    assert store.mode("g:1") == "always"
    store.set_mode("g:1", "off")
    assert store.mode("g:1") == "off"
    assert store.mode("g:2") == "always"


def test_persists_across_reload(tmp_path) -> None:
    path = tmp_path / "state.json"
    store = SessionStateStore(path, default_mode="off")
    store.set_mode("g:1", "at")
    store.set_mode("g:2", "always")

    reloaded = SessionStateStore(path, default_mode="off")
    assert reloaded.mode("g:1") == "at"
    assert reloaded.mode("g:2") == "always"


def test_invalid_mode_normalized_to_off(tmp_path) -> None:
    store = SessionStateStore(tmp_path / "state.json", default_mode="nonsense")
    assert store.default_mode == "off"
    store.set_mode("g:1", "weird")
    assert store.mode("g:1") == "off"


def test_legacy_v020_format_migrates(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"enabled": ["g:1"], "disabled": ["g:2"]}),
        encoding="utf-8",
    )
    store = SessionStateStore(path, default_mode="off")
    # v0.2.0 "enabled" meant parse-always; "disabled" meant off.
    assert store.mode("g:1") == "always"
    assert store.mode("g:2") == "off"
    assert store.mode("g:3") == "off"


def test_snapshot_lists_explicit_levels(tmp_path) -> None:
    store = SessionStateStore(tmp_path / "state.json", default_mode="off")
    store.set_mode("g:1", "at")
    assert store.snapshot() == {"g:1": "at"}
