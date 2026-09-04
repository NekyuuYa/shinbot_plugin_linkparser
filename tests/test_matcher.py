"""Route matcher unit tests (fake event/message shapes only)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from shinbot_plugin_linkparser.matcher import build_link_matcher

BV = "BV1xx411c7mD"


def _element(element_type: str, attrs: dict | None = None, children: list | None = None):
    return {"type": element_type, "attrs": attrs or {}, "children": children or []}


def _message(elements: list) -> SimpleNamespace:
    return SimpleNamespace(elements=elements)


def _event(self_id: str = "") -> SimpleNamespace:
    return SimpleNamespace(self_id=self_id)


def _default_matcher(**overrides) -> object:
    options = {
        "enabled": True,
        "parse_on_mention": True,
        "parse_reply": False,
    }
    options.update(overrides)
    return build_link_matcher(**options)


def test_plain_link_text_matches() -> None:
    matcher = _default_matcher()
    message = _message(
        [_element("text", {"content": f"看 https://www.bilibili.com/video/{BV}"})]
    )
    assert matcher(_event(), message) is True


def test_bare_bv_matches() -> None:
    matcher = _default_matcher()
    assert matcher(_event(), _message([_element("text", {"content": BV})])) is True


def test_plain_text_does_not_match() -> None:
    matcher = _default_matcher()
    message = _message([_element("text", {"content": "今天天气不错"})])
    assert matcher(_event(), message) is False


def test_disabled_master_switch() -> None:
    matcher = _default_matcher(enabled=False)
    message = _message([_element("text", {"content": BV})])
    assert matcher(_event(), message) is False


def test_ark_card_matches() -> None:
    matcher = _default_matcher()
    payload = json.dumps({"meta": {"detail_1": {"qqdocurl": "https://b23.tv/xYz12"}}})
    message = _message([_element("sb:ark", {"data": payload})])
    assert matcher(_event(), message) is True


def test_ark_card_without_url_does_not_match() -> None:
    matcher = _default_matcher()
    payload = json.dumps({"meta": {"detail_1": {"title": "无链接"}}})
    message = _message([_element("sb:ark", {"data": payload})])
    assert matcher(_event(), message) is False


def test_mention_excluded_when_parse_on_mention_false() -> None:
    matcher = _default_matcher(parse_on_mention=False)
    elements = [
        _element("at", {"id": "123"}),
        _element("text", {"content": BV}),
    ]
    assert matcher(_event(self_id="123"), _message(elements)) is False
    # other bot mention does not exclude when bot itself is mentioned elsewhere
    elements = [
        _element("at", {"id": "456"}),
        _element("at", {"id": "123"}),
        _element("text", {"content": BV}),
    ]
    assert matcher(_event(self_id="123"), _message(elements)) is False


def test_mention_all_excluded_when_parse_on_mention_false() -> None:
    matcher = _default_matcher(parse_on_mention=False)
    elements = [
        _element("at", {"type": "all"}),
        _element("text", {"content": BV}),
    ]
    assert matcher(_event(self_id="123"), _message(elements)) is False


def test_mention_allowed_when_parse_on_mention_true() -> None:
    matcher = _default_matcher(parse_on_mention=True)
    elements = [
        _element("at", {"id": "123"}),
        _element("text", {"content": BV}),
    ]
    assert matcher(_event(self_id="123"), _message(elements)) is True


def test_quote_excluded_by_default() -> None:
    matcher = _default_matcher(parse_reply=False)
    quote = _element(
        "quote",
        {"id": "msg1"},
        children=[_element("text", {"content": BV})],
    )
    message = _message([quote, _element("text", {"content": "转发一下"})])
    assert matcher(_event(), message) is False


def test_quote_included_when_parse_reply_true() -> None:
    matcher = _default_matcher(parse_reply=True)
    quote = _element(
        "quote",
        {"id": "msg1"},
        children=[_element("text", {"content": BV})],
    )
    message = _message([quote, _element("text", {"content": "转发一下"})])
    assert matcher(_event(), message) is True


def _element_with_children(element_type: str, children: list) -> dict:
    return {"type": element_type, "attrs": {}, "children": children}


def test_session_gate_blocks_disabled_session() -> None:
    matcher = _default_matcher(
        parse_allowed=lambda session_id: session_id == "allowed-session"
    )
    message = _message([_element("text", {"content": BV})])
    context = SimpleNamespace(session=SimpleNamespace(id="other-session"))
    assert matcher(_event(), message, context) is False


def test_session_gate_allows_enabled_session() -> None:
    matcher = _default_matcher(
        parse_allowed=lambda session_id: session_id == "allowed-session"
    )
    message = _message([_element("text", {"content": BV})])
    context = SimpleNamespace(session=SimpleNamespace(id="allowed-session"))
    assert matcher(_event(), message, context) is True


def test_session_gate_defaults_allowed_without_context() -> None:
    matcher = _default_matcher()
    message = _message([_element("text", {"content": BV})])
    assert matcher(_event(), message) is True


def test_ark_card_double_encoded_matches_when_session_allowed() -> None:
    """OneBot double-encodes share-card JSON; matcher must still trigger."""
    matcher = _default_matcher(
        parse_allowed=lambda session_id: session_id == "group:1"
    )
    card = {
        "app": "com.tencent.miniapp",
        "meta": {
            "detail_1": {
                "qqdocurl": "https://www.bilibili.com/video/BV1xx411c7mD"
            }
        },
    }
    double_encoded = json.dumps(json.dumps(card))
    message = _message([_element("sb:ark", {"data": double_encoded})])
    context = SimpleNamespace(session=SimpleNamespace(id="group:1"))
    assert matcher(_event(), message, context) is True
