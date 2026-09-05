"""Parse-mode policy: decide what to parse for a message under off/at/always.

Pure logic — no ShinBot imports, fully unit-testable. The policy answers two
questions:

- *which messages parse* — gated by the session's parse mode:
  - ``off``: nothing parses.
  - ``at``: only messages that @-mention the bot parse. The parse target is
    the message's own text, plus (when the message quotes another message)
    the quoted message's content.
  - ``always``: any message carrying a parseable link parses; quoted content
    is additionally considered when ``parse_reply`` is enabled.
- *what the parse target is* — visible text / ``sb:ark`` cards, and quoted
  content supplied through the quote resolver.

Quotes are only ever resolved lazily through ``resolve_quote`` so the caller
can back it with synchronous DB lookups (message_logs) without any framework
coupling here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any, Literal

from .models import LinkCandidate
from .urls import (
    collect_supported_candidates,
    collect_supported_candidates_in_quotes,
    iter_quote_elements,
    merge_candidates,
)

logger = logging.getLogger("shinbot_plugin_linkparser.policy")

ParseMode = Literal["off", "at", "always"]
PARSE_MODES: tuple[ParseMode, ...] = ("off", "at", "always")

QuoteResolver = Callable[[str], Sequence[Any] | None]
"""``resolve(quote_id) -> elements`` for one quoted message (or None)."""


def normalize_mode(value: str) -> ParseMode:
    """Coerce any value to a valid parse mode (fallback ``off``)."""
    if value in PARSE_MODES:
        return value  # type: ignore[return-value]
    return "off"


def visible_mentions_bot(elements: Sequence[Any], self_id: str) -> bool:
    """Return True when the *visible* (non-quoted) part @-mentions the bot.

    Mentions inside a quote belong to the quoted message, not the current one,
    so they never count.

    Args:
        elements: Message element list (duck-typed or plain dicts).
        self_id: The bot's own platform id.

    Returns:
        True when an ``at`` element outside any quote targets the bot directly.
    """
    if not self_id:
        return False
    stack: list[tuple[Any, bool]] = [(element, False) for element in reversed(list(elements))]
    while stack:
        element, in_quote = stack.pop()
        element_type = element.get("type") if isinstance(element, dict) else getattr(
            element, "type", ""
        )
        if element_type == "quote":
            in_quote = True
        if in_quote:
            continue
        if element_type == "at":
            attrs = (
                element.get("attrs", {})
                if isinstance(element, dict)
                else getattr(element, "attrs", {}) or {}
            )
            at_id = str(attrs.get("id", ""))
            if at_id and at_id == self_id:
                return True
        children = (
            element.get("children", [])
            if isinstance(element, dict)
            else getattr(element, "children", []) or []
        )
        if children:
            stack.extend((child, in_quote) for child in reversed(children))
    return False


def _collect_quoted(
    elements: Sequence[Any],
    resolve_quote: QuoteResolver | None,
) -> list[LinkCandidate]:
    """Collect candidates from quote subtrees plus DB-resolved quote content."""
    groups: list[list[LinkCandidate]] = [collect_supported_candidates_in_quotes(elements)]
    if resolve_quote is not None:
        for quote_id, _children in iter_quote_elements(elements):
            if not quote_id:
                continue
            try:
                resolved = resolve_quote(quote_id)
            except Exception:
                logger.debug("quote resolve failed for %s", quote_id, exc_info=True)
                continue
            if resolved:
                groups.append(collect_supported_candidates(resolved))
    return merge_candidates(*groups)


def parse_candidates_for(
    elements: Sequence[Any],
    *,
    mode: str,
    mentions_bot: bool,
    parse_reply: bool,
    resolve_quote: QuoteResolver | None = None,
) -> list[LinkCandidate]:
    """Return the candidates that should be parsed for a message.

    Args:
        elements: Message element list (duck-typed or plain dicts).
        mode: Session parse mode (``off``/``at``/``always``).
        mentions_bot: Whether the visible part of the message @-mentions the bot.
        parse_reply: In ``always`` mode, also parse quoted content.
        resolve_quote: Resolver for a quoted message's elements by id (lazy).

    Returns:
        Candidates to parse; empty means the message must not be consumed.
    """
    effective = normalize_mode(mode)
    if effective == "off":
        return []
    visible = collect_supported_candidates(elements, include_quote=False)
    if effective == "always":
        if visible:
            return visible
        if parse_reply:
            return _collect_quoted(elements, resolve_quote)
        return []
    # effective == "at"
    if not mentions_bot:
        return []
    if visible:
        return visible
    return _collect_quoted(elements, resolve_quote)


def make_db_quote_resolver(
    database: Any,
    session_id: str,
) -> QuoteResolver | None:
    """Build a quote resolver backed by the message_logs repository.

    The repository query and ``content_json`` (a serialised MessageElement
    array) are both plain JSON/dict data, so no ShinBot import is needed.

    Args:
        database: ShinBot ``DatabaseManager`` (or any object exposing
            ``.message_logs.get_by_platform_msg_id``).
        session_id: Session owning the quote.

    Returns:
        A resolver ``(quote_id) -> list[elements] | None``, or None when the
        database is unavailable.
    """
    if database is None:
        return None
    message_logs = getattr(database, "message_logs", None)
    if message_logs is None or not hasattr(message_logs, "get_by_platform_msg_id"):
        return None

    def resolve(quote_id: str) -> Sequence[Any] | None:
        try:
            record = message_logs.get_by_platform_msg_id(session_id, quote_id)
        except Exception:
            logger.debug("message_logs lookup failed for %s", quote_id, exc_info=True)
            return None
        if not record:
            return None
        raw = record.get("content_json") or "[]"
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, list) else None

    return resolve
