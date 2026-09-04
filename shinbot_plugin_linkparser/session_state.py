"""Per-session parse-mode state for the LinkParser plugin.

Parsing is off by default; a session's parse mode is set with the ``/parser``
command to one of three levels:

- ``off``    — never parse in this session.
- ``at``     — parse only when the bot is @-mentioned (checking the message's
               own text and, for reply/quote messages, the quoted content).
- ``always`` — parse every message carrying a parseable link.

State is persisted as JSON under the plugin data dir so levels survive bot
restarts. Sessions without an explicit level fall back to the configured
``default_mode``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .parse_policy import ParseMode, normalize_mode

logger = logging.getLogger("shinbot_plugin_linkparser.session_state")


class SessionStateStore:
    """Persisted per-session parse-mode state."""

    def __init__(self, path: Path, *, default_mode: str = "off") -> None:
        """Initialize the store.

        Args:
            path: JSON file used for persistence.
            default_mode: Mode used for sessions without an explicit level.
        """
        self._path = path
        self._default_mode = normalize_mode(default_mode)
        self._modes: dict[str, str] = {}
        self._load()

    # ── queries ──────────────────────────────────────────────────────

    @property
    def default_mode(self) -> ParseMode:
        """Return the mode used when a session has no explicit level."""
        return self._default_mode  # type: ignore[return-value]

    def mode(self, session_id: str | None) -> ParseMode:
        """Return the effective parse mode for a session."""
        session = session_id or ""
        return normalize_mode(self._modes.get(session, self._default_mode))

    def snapshot(self) -> dict[str, str]:
        """Return a copy of the explicit per-session levels."""
        return dict(self._modes)

    # ── mutations ────────────────────────────────────────────────────

    def set_mode(self, session_id: str, mode: str) -> None:
        """Set the parse mode for a session and persist."""
        session_id = session_id or ""
        normalized = normalize_mode(mode)
        current = self._modes.get(session_id)
        if current == normalized:
            return
        self._modes[session_id] = normalized
        self._save()

    # ── persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("LinkParser session state unreadable, ignoring: %s", self._path)
            return
        if not isinstance(payload, dict):
            return
        # v0.3.0 format: {"sessions": {"<session>": "<mode>"}}
        sessions = payload.get("sessions")
        if isinstance(sessions, dict):
            for session_id, mode in sessions.items():
                self._modes[str(session_id)] = normalize_mode(str(mode))
            return
        # Legacy v0.2.0 format: {"enabled": [...], "disabled": [...]}
        # enabled sessions mapped to "always", disabled sessions to "off".
        enabled = payload.get("enabled")
        if isinstance(enabled, list):
            for session_id in enabled:
                self._modes[str(session_id)] = "always"
        disabled = payload.get("disabled")
        if isinstance(disabled, list):
            for session_id in disabled:
                self._modes[str(session_id)] = "off"

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "sessions": dict(sorted(self._modes.items())),
            }
            partial = self._path.with_name(f"{self._path.name}.tmp")
            partial.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            partial.replace(self._path)
        except OSError:
            logger.exception("LinkParser failed to persist session state: %s", self._path)
