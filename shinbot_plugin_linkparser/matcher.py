"""Route matcher construction for the LinkParser plugin.

The matcher runs synchronously inside ``RouteTable.match`` and is the single
place that decides whether a message is consumed (a NORMAL-route match) or
left untouched for the agent. It is deliberately precise:

- the session's parse mode (``off``/``at``/``always``) is resolved from the
  ``RouteMatchContext.session`` the ingress attaches before matching;
- for ``at`` mode, only messages that @-mention the bot are candidates, and a
  reply/quote is only matched after its quoted content is resolved through the
  message_logs repository — so plain @-questions are never swallowed;
- for ``always`` mode only link-bearing messages match.

All work stays cheap: regex/JSON scans plus (only when a quote must be checked)
a synchronous message_logs lookup.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .parse_policy import (
    make_db_quote_resolver,
    parse_candidates_for,
    visible_mentions_bot,
)

Matcher = Callable[..., bool]


def _session_id_from_context(match_context: Any) -> str | None:
    """Extract the session id from a ``RouteMatchContext`` if present."""
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
    parse_reply: bool,
    get_mode: Callable[[str | None], str] | None = None,
    database: Any = None,
) -> Matcher:
    """Build the route custom matcher.

    Args:
        enabled: Master switch; False disables matching entirely.
        parse_reply: In ``always`` mode, also consider quoted content.
        get_mode: Resolver ``session_id -> parse mode`` (off/at/always).
            Defaults to ``always`` (pure link detection).
        database: ShinBot DatabaseManager used to resolve quoted messages.

    Returns:
        A callable ``(event, message, match_context=None) -> bool``.
    """
    mode_for = get_mode if get_mode is not None else (lambda _session_id: "always")

    def matches(event: Any, message: Any, match_context: Any = None) -> bool:
        if not enabled:
            return False
        elements = getattr(message, "elements", None)
        if not elements:
            return False
        session_id = _session_id_from_context(match_context)
        mode = str(mode_for(session_id))
        if mode == "off":
            return False

        self_id = str(getattr(event, "self_id", "") or "")
        mentions_bot = mode == "at" and visible_mentions_bot(elements, self_id)
        resolver = None
        if mode == "always" and parse_reply or mode == "at":
            resolver = make_db_quote_resolver(database, session_id or "")
        candidates = parse_candidates_for(
            elements,
            mode=mode,
            mentions_bot=mentions_bot,
            parse_reply=parse_reply,
            resolve_quote=resolver,
        )
        return bool(candidates)

    return matches
