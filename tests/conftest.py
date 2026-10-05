"""Test path and ShinBot stubs for the LinkParser plugin package.

Mirrors the convention used by sibling plugins (e.g. AstroAssist): unit tests
must run without a ShinBot installation, so any `shinbot.*` symbol imported at
module import time is stubbed here. Framework imports inside plugin functions
are left untouched.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


shinbot_module = types.ModuleType("shinbot")
schema_module = types.ModuleType("shinbot.schema")
elements_module = types.ModuleType("shinbot.schema.elements")


class MessageElement:
    """Small MessageElement stub covering LinkParser test needs."""

    @classmethod
    def text(cls, content: str) -> dict[str, object]:
        return {"type": "text", "attrs": {"content": content}, "children": []}

    @classmethod
    def video(cls, src: str, **kwargs: object) -> dict[str, object]:
        return {"type": "video", "attrs": {"src": src, **kwargs}, "children": []}

    @classmethod
    def img(cls, src: str, **kwargs: object) -> dict[str, object]:
        return {"type": "img", "attrs": {"src": src, **kwargs}, "children": []}

    @classmethod
    def message(
        cls, children: list | None = None, **kwargs: object
    ) -> dict[str, object]:
        return {"type": "message", "attrs": dict(kwargs), "children": children or []}

    @classmethod
    def forward(cls, nodes: list) -> dict[str, object]:
        return {"type": "message", "attrs": {"forward": "true"}, "children": nodes}


elements_module.__dict__["MessageElement"] = MessageElement
sys.modules.setdefault("shinbot", shinbot_module)
sys.modules.setdefault("shinbot.schema", schema_module)
sys.modules.setdefault("shinbot.schema.elements", elements_module)
