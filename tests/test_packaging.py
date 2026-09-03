"""Packaging metadata tests for LinkParser."""

from __future__ import annotations

import importlib
import json
import tomllib
from pathlib import Path


def test_metadata_json_matches_pyproject() -> None:
    """metadata.json (marketplace) must agree with pyproject.toml (package)."""
    root = Path(__file__).resolve().parents[1]
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["version"] == pyproject["project"]["version"]
    assert metadata["id"] == "shinbot_plugin_linkparser"
    assert pyproject["project"]["name"] == "shinbot-plugin-linkparser"
    assert pyproject["project"]["requires-python"] == ">=3.12"


def test_entry_point_exists_and_is_importable() -> None:
    """The declared plugin entry must exist and import without ShinBot installed."""
    root = Path(__file__).resolve().parents[1]
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))

    entry = root / metadata["entry"]
    assert entry.is_file(), f"entry not found: {metadata['entry']}"

    module = importlib.import_module("shinbot_plugin_linkparser")
    assert callable(getattr(module, "setup", None))
    assert hasattr(module, "__plugin_config_class__")
