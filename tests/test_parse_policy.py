"""Parse-mode policy unit tests (pure, no framework)."""

from __future__ import annotations

import json

from shinbot_plugin_linkparser.parse_policy import (
    make_db_quote_resolver,
    normalize_mode,
    parse_candidates_for,
    visible_mentions_bot,
)

BV = "BV1xx411c7mD"
SELF = "123"


def _element(element_type: str, attrs: dict | None = None, children: list | None = None):
    return {"type": element_type, "attrs": attrs or {}, "children": children or []}


def _text(content: str) -> dict:
    return _element("text", {"content": content})


def _link_text() -> list[dict]:
    return [_text(f"看 https://www.bilibili.com/video/{BV}")]


def _quote(quote_id: str, children: list | None = None) -> dict:
    return _element("quote", {"id": quote_id}, children or [])


def _at() -> dict:
    return _element("at", {"id": SELF})


def test_normalize_mode() -> None:
    assert normalize_mode("off") == "off"
    assert normalize_mode("at") == "at"
    assert normalize_mode("always") == "always"
    assert normalize_mode("bogus") == "off"


# ── mentions ──────────────────────────────────────────────────────────


def test_visible_mentions_detects_at_self() -> None:
    assert visible_mentions_bot([_at(), _text("hi")], SELF) is True


def test_visible_mentions_ignores_other_at() -> None:
    assert visible_mentions_bot([_element("at", {"id": "999"})], SELF) is False


def test_visible_mentions_ignores_quote_content() -> None:
    # an @ inside the quoted message is not a mention of the current message
    quoted = _quote("q1", [_at()])
    assert visible_mentions_bot([quoted], SELF) is False


# ── parse_candidates_for: off ─────────────────────────────────────────


def test_off_never_parses() -> None:
    assert (
        parse_candidates_for(_link_text(), mode="off", mentions_bot=False, parse_reply=False)
        == []
    )


# ── parse_candidates_for: always ──────────────────────────────────────


def test_always_parses_visible_link() -> None:
    candidates = parse_candidates_for(
        _link_text(), mode="always", mentions_bot=False, parse_reply=False
    )
    assert len(candidates) == 1
    assert candidates[0].bvid == BV


def test_always_no_link_no_parse() -> None:
    assert (
        parse_candidates_for(
            [_text("今天天气不错")], mode="always", mentions_bot=True, parse_reply=True
        )
        == []
    )


def test_always_quote_requires_parse_reply() -> None:
    elements = [_quote("q1", _link_text())]
    # parse_reply off → quote links are not parsed in always mode
    assert (
        parse_candidates_for(elements, mode="always", mentions_bot=False, parse_reply=False)
        == []
    )
    candidates = parse_candidates_for(
        elements, mode="always", mentions_bot=False, parse_reply=True
    )
    assert len(candidates) == 1


def test_always_quote_content_via_resolver() -> None:
    def resolve(quote_id: str):
        assert quote_id == "q1"
        return _link_text()

    elements = [_quote("q1")]
    candidates = parse_candidates_for(
        elements, mode="always", mentions_bot=False, parse_reply=True, resolve_quote=resolve
    )
    assert len(candidates) == 1
    assert candidates[0].bvid == BV


# ── parse_candidates_for: at ──────────────────────────────────────────


def test_at_requires_mention() -> None:
    # link present but bot not mentioned → nothing
    assert (
        parse_candidates_for(_link_text(), mode="at", mentions_bot=False, parse_reply=False)
        == []
    )


def test_at_parses_visible_link_when_mentioned() -> None:
    candidates = parse_candidates_for(
        _link_text(), mode="at", mentions_bot=True, parse_reply=False
    )
    assert len(candidates) == 1


def test_at_mention_without_link_or_quote_returns_empty() -> None:
    elements = [_at(), _text("在吗")]
    assert parse_candidates_for(elements, mode="at", mentions_bot=True, parse_reply=False) == []


def test_at_parses_quoted_content_even_without_parse_reply() -> None:
    """at-mode quote handling is decoupled from parse_reply."""
    elements = [_at(), _quote("q1", _link_text())]
    candidates = parse_candidates_for(
        elements, mode="at", mentions_bot=True, parse_reply=False
    )
    assert len(candidates) == 1


def test_at_resolves_quote_by_id() -> None:
    def resolve(quote_id: str):
        assert quote_id == "q1"
        return _link_text()

    elements = [_at(), _quote("q1")]
    candidates = parse_candidates_for(
        elements, mode="at", mentions_bot=True, parse_reply=False, resolve_quote=resolve
    )
    assert len(candidates) == 1
    assert candidates[0].bvid == BV


def test_at_quote_resolver_finds_nothing_returns_empty() -> None:
    elements = [_at(), _quote("q1")]
    assert (
        parse_candidates_for(
            elements,
            mode="at",
            mentions_bot=True,
            parse_reply=False,
            resolve_quote=lambda _qid: None,
        )
        == []
    )


# ── db quote resolver ─────────────────────────────────────────────────


def test_make_db_quote_resolver_returns_none_without_database() -> None:
    assert make_db_quote_resolver(None, "g:1") is None


class _FakeMessageLogs:
    def __init__(self, records: dict) -> None:
        self._records = records

    def get_by_platform_msg_id(self, session_id: str, platform_msg_id: str):
        return self._records.get((session_id, platform_msg_id))


def test_make_db_quote_resolver_reads_content_json(tmp_path) -> None:
    logs = _FakeMessageLogs(
        {("g:1", "q1"): {"content_json": json.dumps(_link_text())}}
    )
    database = type("DB", (), {"message_logs": logs})()
    resolver = make_db_quote_resolver(database, "g:1")
    assert resolver is not None
    elements = resolver("q1")
    assert elements is not None
    candidates = parse_candidates_for(
        [{"type": "quote", "attrs": {"id": "q1"}, "children": []}],
        mode="at",
        mentions_bot=True,
        parse_reply=False,
        resolve_quote=resolver,
    )
    assert len(candidates) == 1
    assert candidates[0].bvid == BV


def test_make_db_quote_resolver_missing_record_returns_none() -> None:
    logs = _FakeMessageLogs({})
    database = type("DB", (), {"message_logs": logs})()
    resolver = make_db_quote_resolver(database, "g:1")
    assert resolver("q-unknown") is None
