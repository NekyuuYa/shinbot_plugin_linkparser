"""Route matcher construction for the LinkParser plugin.

The route matcher runs synchronously inside ``RouteTable.match`` for every
message, so it must stay cheap: regex scans over text elements and JSON URL
mining for ``sb:ark`` cards only — no network, no parsing work.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .urls import collect_bilibili_candidates

Matcher = Callable[..., bool]


def _message_mentions(elements: list[Any], self_id: str) -> bool:
    """Return True when any ``at`` element points at the bot (or at everyone)."""
    if not self_id:
        return False
    stack = list(elements)
    while stack:
        element = stack.pop()
        element_type = element.get("type") if isinstance(element, dict) else getattr(
            element, "type", ""
        )
        if element_type == "at":
            attrs = (
                element.get("attrs", {})
                if isinstance(element, dict)
                else getattr(element, "attrs", {}) or {}
            )
            at_id = str(attrs.get("id", ""))
            at_type = str(attrs.get("type", ""))
            if at_type == "all" or (at_id and at_id == self_id):
                return True
        children = (
            element.get("children", [])
            if isinstance(element, dict)
            else getattr(element, "children", []) or []
        )
        if children:
            stack.extend(children)
    return False


def build_link_matcher(
    *,
    enabled: bool,
    parse_on_mention: bool,
    parse_reply: bool,
) -> Matcher:
    """Build the route custom matcher from the plugin configuration.

    The matcher returns True (→ the NORMAL route consumes the message and the
    parser replies) when the message carries a scannable Bilibili link:

    - plain text containing ``BV``/``av``/``bilibili.com/video``/``b23.tv``
    - an ``sb:ark`` share card whose JSON contains a Bilibili URL
    - (optionally) links inside a ``quote`` when ``parse_reply`` is enabled

    When ``parse_on_mention`` is False, messages that @-mention the bot are
    excluded so the agent can handle them instead.

    Args:
        enabled: Master switch; False disables matching entirely.
        parse_on_mention: Also match messages that mention the bot.
        parse_reply: Also scan links inside quoted messages.

    Returns:
        A callable ``(event, message) -> bool``.
    """

    def matches(event: Any, message: Any) -> bool:
        if not enabled:
            return False
        elements = getattr(message, "elements", None)
        if not elements:
            return False
        if not parse_on_mention and _message_mentions(elements, getattr(event, "self_id", "")):
            return False
        return bool(collect_bilibili_candidates(elements, include_quote=parse_reply))

    return matches
