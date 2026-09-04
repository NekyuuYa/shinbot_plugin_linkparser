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


def _session_id_from_context(match_context: Any) -> str | None:
    """Extract the session id from a ``RouteMatchContext`` if present.

    The message ingress attaches the resolved ``Session`` object to the match
    context before route matching, so custom matchers can gate on the session.
    """
    if match_context is None:
        return None
    session = getattr(match_context, "session", None)
    if session is None:
        return None
    session_id = getattr(session, "id", None)
    return str(session_id) if session_id is not None else None


def build_link_matcher(
    *,
    enabled: bool,
    parse_on_mention: bool,
    parse_reply: bool,
    parse_allowed: Callable[[str | None], bool] | None = None,
) -> Matcher:
    """Build the route custom matcher from the plugin configuration.

    The matcher returns True (→ the NORMAL route consumes the message and the
    parser replies) when all of the following hold:

    - the session is allowed to parse (via *parse_allowed*; the default allows
      everything, keeping the matcher usable as a pure link detector)
    - the message carries a scannable Bilibili link:
      plain text ``BV``/``av``/``bilibili.com/video``/``b23.tv``, or an
      ``sb:ark`` share card whose JSON contains a Bilibili URL, or (when
      ``parse_reply`` is enabled) a link inside a ``quote``
    - the message does not @-mention the bot when ``parse_on_mention`` is False

    Args:
        enabled: Master switch; False disables matching entirely.
        parse_on_mention: Also match messages that mention the bot.
        parse_reply: Also scan links inside quoted messages.
        parse_allowed: Callable(session_id) deciding whether a session may
            parse (e.g. per-session toggle state). Defaults to always True.

    Returns:
        A callable ``(event, message, match_context=None) -> bool``.
    """
    gate = parse_allowed if parse_allowed is not None else (lambda _session_id: True)

    def matches(event: Any, message: Any, match_context: Any = None) -> bool:
        if not enabled:
            return False
        if not gate(_session_id_from_context(match_context)):
            return False
        elements = getattr(message, "elements", None)
        if not elements:
            return False
        if not parse_on_mention and _message_mentions(elements, getattr(event, "self_id", "")):
            return False
        return bool(collect_bilibili_candidates(elements, include_quote=parse_reply))

    return matches
