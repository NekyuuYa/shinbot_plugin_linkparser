"""Per-session parse toggle state for the LinkParser plugin.

By default the plugin does not parse anything; a user enables parsing for the
*current session* with ``/parser on`` (and disables with ``/parser off``).
State is persisted as JSON under the plugin data dir so toggles survive bot
restarts.

Effective state for a session:
- explicitly enabled (``enabled`` set) → always parse
- otherwise → parse only when ``parse_by_default`` is on AND the session is
  not explicitly disabled
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("shinbot_plugin_linkparser.session_state")


class SessionStateStore:
    """Persisted per-session on/off state."""

    def __init__(self, path: Path, *, parse_by_default: bool = False) -> None:
        """Initialize the store.

        Args:
            path: JSON file used for persistence.
            parse_by_default: Whether sessions without an explicit toggle parse.
        """
        self._path = path
        self._parse_by_default = bool(parse_by_default)
        self._enabled: set[str] = set()
        self._disabled: set[str] = set()
        self._load()

    # ── queries ──────────────────────────────────────────────────────

    @property
    def parse_by_default(self) -> bool:
        """Return whether sessions default to parsing."""
        return self._parse_by_default

    def is_enabled(self, session_id: str | None) -> bool:
        """Return whether parsing is enabled for a session."""
        session = session_id or ""
        if session in self._enabled:
            return True
        if self._parse_by_default:
            return session not in self._disabled
        return False

    def is_explicitly_disabled(self, session_id: str | None) -> bool:
        """Return whether a session was explicitly disabled with ``/parser off``."""
        return (session_id or "") in self._disabled

    # ── mutations ────────────────────────────────────────────────────

    def enable(self, session_id: str) -> None:
        """Turn parsing on for a session and persist."""
        session_id = session_id or ""
        self._disabled.discard(session_id)
        added = session_id not in self._enabled
        self._enabled.add(session_id)
        if added:
            self._save()

    def disable(self, session_id: str) -> None:
        """Turn parsing off for a session and persist."""
        session_id = session_id or ""
        removed = session_id in self._enabled
        self._enabled.discard(session_id)
        added = session_id not in self._disabled
        self._disabled.add(session_id)
        if removed or added:
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
        enabled = payload.get("enabled")
        disabled = payload.get("disabled")
        if isinstance(enabled, list):
            self._enabled = {str(value) for value in enabled if value}
        if isinstance(disabled, list):
            self._disabled = {str(value) for value in disabled if value}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "enabled": sorted(self._enabled),
                "disabled": sorted(self._disabled),
            }
            partial = self._path.with_name(f"{self._path.name}.tmp")
            partial.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            partial.replace(self._path)
        except OSError:
            logger.exception("LinkParser failed to persist session state: %s", self._path)
