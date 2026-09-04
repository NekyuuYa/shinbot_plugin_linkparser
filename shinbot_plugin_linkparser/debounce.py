"""Per-session link/resource debounce for the LinkParser plugin.

Prevents repeated parsing (and re-downloading) of the same link or resource
within a short window — e.g. when a group re-posts the same video, or the bot
restarts mid-window. State is held in memory; entries expire lazily.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Mapping


class Debouncer:
    """In-memory session-scoped debounce keyed by link/resource strings."""

    def __init__(self, window_seconds: float = 300.0) -> None:
        """Initialize the debouncer.

        Args:
            window_seconds: Debounce window length. ``<= 0`` disables it.
        """
        self._window = max(0.0, float(window_seconds))
        self._entries: dict[str, dict[str, float]] = defaultdict(dict)

    @property
    def window_seconds(self) -> float:
        """Return the configured debounce window."""
        return self._window

    def hit(self, session_id: str, key: str) -> bool:
        """Return True when *key* is inside the debounce window for a session."""
        if self._window <= 0:
            return False
        expiry = self._entries.get(session_id, {}).get(key)
        if expiry is None:
            return False
        if expiry > time.monotonic():
            return True
        # expired — drop lazily
        self._entries[session_id].pop(key, None)
        if not self._entries[session_id]:
            self._entries.pop(session_id, None)
        return False

    def remember(self, session_id: str, key: str) -> None:
        """Record *key* now so subsequent calls hit until the window passes."""
        if self._window <= 0:
            return
        self._entries[session_id][key] = time.monotonic() + self._window

    def forget(self, session_id: str, key: str) -> None:
        """Remove a recorded key (used to allow retries after a failure)."""
        if session_id in self._entries:
            self._entries[session_id].pop(key, None)
            if not self._entries[session_id]:
                self._entries.pop(session_id, None)

    def clear_session(self, session_id: str) -> None:
        """Drop all debounce state for one session."""
        self._entries.pop(session_id, None)

    def snapshot(self) -> Mapping[str, Mapping[str, float]]:
        """Return a copy of current state (for tests / debugging)."""
        return {session: dict(keys) for session, keys in self._entries.items()}
