"""Route matcher unit tests (fake event/message shapes only)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from shinbot_plugin_linkparser.matcher import build_link_matcher

BV = "BV1xx411c7mD"
SELF = "123"


def _element(element_type: str, attrs: dict | None = None, children: list | None = None):
    return {"type": element_type, "attrs": attrs or {}, "children": children or []}


def _message(elements: list) -> SimpleNamespace:
    return SimpleNamespace(elements=elements)


def _event(self_id: str = SELF) -> SimpleNamespace:
    return SimpleNamespace(self_id=self_id)


def _context(session_id: str) -> SimpleNamespace:
    return SimpleNamespace(session=SimpleNamespace(id=session_id))


def _at() -> dict:
    return _element("at", {"id": SELF})


def _link_elements() -> list[dict]:
    return [_element("text", {"content": "看 https://b23.tv/abC12"})]


class _FakeLogs:
    def __init__(self, records: dict) -> None:
        self._records = records

    def get_by_platform_msg_id(self, session_id: str, platform_msg_id: str):
        return self._records.get((session_id, platform_msg_id))


class _FakeDB:
    def __init__(self, records: dict) -> None:
        self.message_logs = _FakeLogs(records)


def _quote(quote_id: str, children: list | None = None) -> dict:
    return _element("quote", {"id": quote_id}, children or [])


def test_default_matcher_detects_link() -> None:
    matcher = build_link_matcher(enabled=True, parse_reply=False)
    assert matcher(_event(), _message(_link_elements())) is True


def test_plain_text_does_not_match() -> None:
    matcher = build_link_matcher(enabled=True, parse_reply=False)
    message = _message([_element("text", {"content": "今天天气不错"})])
    assert matcher(_event(), message) is False


def test_off_mode_never_matches() -> None:
    matcher = build_link_matcher(
        enabled=True,
        parse_reply=False,
        get_mode=lambda sid: "off",
    )
    assert matcher(_event(), _message(_link_elements())) is False


def test_disabled_master_switch() -> None:
    matcher = build_link_matcher(enabled=False, parse_reply=False)
    assert matcher(_event(), _message(_link_elements())) is False


def test_always_mode_matches_without_mention() -> None:
    matcher = build_link_matcher(enabled=True, parse_reply=False, get_mode=lambda sid: "always")
    assert matcher(_event(), _message(_link_elements())) is True


# ── at mode ───────────────────────────────────────────────────────────


def test_at_mode_requires_mention() -> None:
    matcher = build_link_matcher(enabled=True, parse_reply=False, get_mode=lambda sid: "at")
    # link without mention → not matched (agent keeps it)
    assert matcher(_event(), _message(_link_elements()), _context("g:1")) is False


def test_at_mode_matches_mention_with_link() -> None:
    matcher = build_link_matcher(enabled=True, parse_reply=False, get_mode=lambda sid: "at")
    elements = [_at(), *_link_elements()]
    assert matcher(_event(), _message(elements), _context("g:1")) is True


def test_at_mode_mention_without_link_or_quote_not_matched() -> None:
    matcher = build_link_matcher(enabled=True, parse_reply=False, get_mode=lambda sid: "at")
    elements = [_at(), _element("text", {"content": "在吗"})]
    assert matcher(_event(), _message(elements), _context("g:1")) is False


def test_at_mode_resolves_quote_via_db() -> None:
    logs = {("g:1", "q1"): {"content_json": json.dumps(_link_elements())}}
    matcher = build_link_matcher(
        enabled=True,
        parse_reply=False,
        get_mode=lambda sid: "at",
        database=_FakeDB(logs),
    )
    elements = [_at(), _quote("q1")]
    # quote exists and DB shows the quoted message contains a link → match
    assert matcher(_event(), _message(elements), _context("g:1")) is True


def test_at_mode_quote_without_link_not_matched() -> None:
    logs = {
        ("g:1", "q1"): {
            "content_json": json.dumps([_element("text", {"content": "无链接"})])
        }
    }
    matcher = build_link_matcher(
        enabled=True,
        parse_reply=False,
        get_mode=lambda sid: "at",
        database=_FakeDB(logs),
    )
    elements = [_at(), _quote("q1")]
    assert matcher(_event(), _message(elements), _context("g:1")) is False


def test_at_mode_unknown_quote_not_matched() -> None:
    matcher = build_link_matcher(
        enabled=True,
        parse_reply=False,
        get_mode=lambda sid: "at",
        database=_FakeDB({}),
    )
    elements = [_at(), _quote("q-unknown")]
    assert matcher(_event(), _message(elements), _context("g:1")) is False


def test_at_mode_quote_children_with_link_match_without_db() -> None:
    matcher = build_link_matcher(enabled=True, parse_reply=False, get_mode=lambda sid: "at")
    elements = [_at(), _quote("q1", _link_elements())]
    assert matcher(_event(), _message(elements), _context("g:1")) is True


# ── always + parse_reply ──────────────────────────────────────────────


def test_always_mode_quote_requires_parse_reply() -> None:
    matcher = build_link_matcher(enabled=True, parse_reply=False, get_mode=lambda sid: "always")
    elements = [_quote("q1", _link_elements())]
    assert matcher(_event(), _message(elements), _context("g:1")) is False

    matcher_reply = build_link_matcher(
        enabled=True, parse_reply=True, get_mode=lambda sid: "always"
    )
    assert matcher_reply(_event(), _message(elements), _context("g:1")) is True


def test_always_mode_quote_resolved_via_db() -> None:
    logs = {("g:1", "q1"): {"content_json": json.dumps(_link_elements())}}
    matcher = build_link_matcher(
        enabled=True,
        parse_reply=True,
        get_mode=lambda sid: "always",
        database=_FakeDB(logs),
    )
    elements = [_quote("q1")]
    assert matcher(_event(), _message(elements), _context("g:1")) is True
