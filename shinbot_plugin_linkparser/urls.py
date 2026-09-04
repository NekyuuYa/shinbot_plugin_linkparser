"""Pure URL / card extraction helpers for the LinkParser plugin.

This module never imports ShinBot or Bilibili libraries, so all functions can
be unit-tested without a framework or network. Message elements are handled by
duck typing (objects exposing ``type``/``attrs``/``children``, or plain dicts).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from typing import Any
from urllib.parse import parse_qs, urlparse

from .models import LinkCandidate

_BVID_RE = re.compile(r"(?<![0-9A-Za-z])BV[0-9A-Za-z]{10}(?![0-9A-Za-z])")
_AVID_RE = re.compile(r"(?<![0-9A-Za-z])av(?P<avid>\d{4,})(?![0-9A-Za-z])", re.IGNORECASE)
_BILI_VIDEO_URL_RE = re.compile(
    r"(?<![0-9A-Za-z.])"
    r"(?:https?://)?(?:www\.|m\.)?bilibili\.com/video/"
    r"(?P<id>BV[0-9A-Za-z]{10}|av\d{4,})",
    re.IGNORECASE,
)
_B23_RE = re.compile(
    r"(?<![0-9A-Za-z.])"
    r"(?:https?://)?b23\.tv/(?P<code>[0-9A-Za-z]+)",
    re.IGNORECASE,
)
_BM_BV_RE = re.compile(r"(?<![0-9A-Za-z])bm(?P<bvid>BV[0-9A-Za-z]{10})", re.IGNORECASE)

_URL_END = r"[\s\u3000<>]"
_TRAILING_PUNCT = "，。；、！？!?,.:：)）】」』]}\"'‘’“”"


def is_bilibili_video_id(text: str) -> bool:
    """Return True when *text* looks like a Bilibili video id or share URL."""
    return bool(find_bilibili_candidates(text))


def _token_end(text: str, start: int) -> int:
    """Bound a URL-ish token starting at *start* by whitespace/angle brackets."""
    match = re.search(_URL_END, text[start:])
    end = start + match.start() if match else len(text)
    # strip trailing punctuation that is not part of the URL
    while end > start and text[end - 1] in _TRAILING_PUNCT:
        end -= 1
    return end


def _page_from_token(token: str) -> int:
    """Read the ``p`` query parameter from a URL token (1-based, default 1)."""
    parsed = urlparse(token)
    try:
        values = parse_qs(parsed.query).get("p")
        if values:
            return max(1, int(values[0]))
    except (ValueError, TypeError):
        pass
    return 1


def _add_candidate(
    candidates: dict[tuple[str, str, int], LinkCandidate],
    *,
    bvid: str | None,
    avid: int | None,
    page: int,
    matched: str,
    short_code: str | None = None,
) -> None:
    """Insert a candidate unless an identical resource already exists."""
    key_id = bvid or (f"av{avid}" if avid else f"short:{short_code or matched}")
    key = ("bilibili", "video", key_id, page)
    if key in candidates:
        return
    candidates[key] = LinkCandidate(
        platform="bilibili",
        kind="video",
        bvid=bvid,
        avid=avid,
        page=page,
        matched=matched,
        short_code=short_code,
    )


def find_bilibili_candidates(text: str) -> list[LinkCandidate]:
    """Scan *text* for Bilibili video links and return deduplicated candidates.

    Handles:
    - full ``bilibili.com/video/BV...`` / ``.../av...`` URLs (with ``?p=N``)
    - bare ``BV...`` / ``av...`` tokens (with optional ``N`` page suffix)
    - ``bmBV...`` prefixed tokens used by QQ share cards
    - ``b23.tv/...`` short links (flag for redirect resolution)
    """
    if not text:
        return []
    candidates: dict[tuple[str, str, str, int], LinkCandidate] = {}

    # Full video URLs first (their spans shadow bare ids inside them).
    url_spans: list[tuple[int, int]] = []
    for match in _BILI_VIDEO_URL_RE.finditer(text):
        url_spans.append((match.start(), match.end()))
        token = text[match.start() : _token_end(text, match.start())]
        raw_id = match.group("id")
        page = _page_from_token(token)
        if raw_id.upper().startswith("BV"):
            _add_candidate(
                candidates,
                bvid=raw_id,
                avid=None,
                page=page,
                matched=token,
            )
        else:
            avid_value = int(raw_id[2:])
            _add_candidate(
                candidates,
                bvid=None,
                avid=avid_value,
                page=page,
                matched=token,
            )

    def inside_url_span(position: int) -> bool:
        return any(start <= position < end for start, end in url_spans)

    # b23.tv short links (not already covered above).
    for match in _B23_RE.finditer(text):
        if inside_url_span(match.start()):
            continue
        token = text[match.start() : _token_end(text, match.start())]
        _add_candidate(
            candidates,
            bvid=None,
            avid=None,
            page=1,
            matched=token,
            short_code=match.group("code"),
        )

    # Bare / bm-prefixed BV tokens.
    for match in _BM_BV_RE.finditer(text):
        if inside_url_span(match.start()):
            continue
        page = _bare_page_suffix(_line_tail(text, match.end()))
        _add_candidate(
            candidates,
            bvid=match.group("bvid"),
            avid=None,
            page=page,
            matched=match.group(0),
        )
    for match in _BVID_RE.finditer(text):
        if inside_url_span(match.start()):
            continue
        if match.group(0).lower().startswith("bm"):
            continue  # handled above (case-insensitive bm prefix)
        page = _bare_page_suffix(_line_tail(text, match.end()))
        _add_candidate(
            candidates,
            bvid=match.group(0),
            avid=None,
            page=page,
            matched=match.group(0),
        )

    # Bare av tokens.
    for match in _AVID_RE.finditer(text):
        if inside_url_span(match.start()):
            continue
        page = _bare_page_suffix(_line_tail(text, match.end()))
        _add_candidate(
            candidates,
            bvid=None,
            avid=int(match.group("avid")),
            page=page,
            matched=match.group(0),
        )

    return [candidate for candidate in candidates.values()]


def _line_tail(text: str, start: int) -> str:
    """Return the remainder of the current line starting at *start*."""
    newline = text.find("\n", start)
    return text[start:] if newline < 0 else text[start:newline]


def _bare_page_suffix(tail: str) -> int:
    """Parse an optional end-of-line ``<space>N`` page suffix (QQ card style).

    Args:
        tail: The remaining text of the current line after the id token.

    Returns:
        The 1-based page number (default 1).
    """
    match = re.match(r"[ \t]*(\d{1,3})[ \t]*$", tail)
    if match:
        try:
            return max(1, int(match.group(1)))
        except ValueError:
            return 1
    return 1


# ── QQ share-card (sb:ark) URL mining ─────────────────────────────────────

_ARK_URL_FIELDS = (
    "legacyUrl",
    "pcJumpUrl",
    "jumpUrl",
    "sourceUrl",
    "shareUrl",
    "targetUrl",
    "qqdocurl",
    "url",
    "mqqurl",
    "videoUrl",
)

_ARK_META_FAMILIES = ("miniapp", "detail_1", "detail", "detail_2", "news", "link")

_URL_PREFIXES = ("http://", "https://")
_MAX_DECODE_DEPTH = 4


def _decode_payload(data: object) -> object:
    """Decode possibly double-encoded JSON card payloads.

    The OneBot adapter stores card JSON with ``json.dumps`` on top of the
    already-serialized card string, producing a JSON *string* whose value is
    again JSON. Decode repeatedly (bounded) until a dict is reached or the
    payload is no longer decodable JSON.
    """
    payload = data
    for _ in range(_MAX_DECODE_DEPTH):
        if not isinstance(payload, str):
            return payload
        try:
            decoded = json.loads(payload)
        except (ValueError, TypeError):
            return payload
        if isinstance(decoded, dict):
            return decoded
        payload = decoded
    return payload


def _url_candidates(payload: object) -> list[str]:
    """Collect every http(s) URL string nested anywhere in the card JSON."""
    found: list[str] = []
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            if node.startswith(_URL_PREFIXES):
                found.append(node)
            continue
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return found


def _prefer_bilibili(urls: list[str]) -> str | None:
    """Return the first Bilibili-ish URL, falling back to the first URL."""
    for url in urls:
        if "bilibili.com" in url or "b23.tv" in url or re.search(r"/?BV[0-9A-Za-z]{10}", url):
            return url
    return urls[0] if urls else None


def ark_extract_url(data: str | dict | None) -> str | None:
    """Extract the first usable URL from a QQ share-card JSON payload.

    QQ cards (OneBot ``json`` segment → ShinBot ``sb:ark`` element) nest jump
    targets inside ``meta.miniapp`` / ``meta.detail_1`` / ``meta.news`` etc.
    The payload may be double-encoded by the adapter, which is handled here.

    Priority:
    1. Structured fields in astrbot-style priority order (Bilibili first).
    2. Any http(s) URL found anywhere in the payload (Bilibili preferred).

    Args:
        data: The card payload: a JSON string or an already-parsed dict.

    Returns:
        The extracted URL, or ``None`` when nothing usable is found.
    """
    payload = _decode_payload(data)
    if not isinstance(payload, dict):
        return None

    urls: list[str] = []
    meta = payload.get("meta")
    if isinstance(meta, dict):
        for family in _ARK_META_FAMILIES:
            node = meta.get(family)
            if not isinstance(node, dict):
                continue
            for key in _ARK_URL_FIELDS:
                value = node.get(key)
                if isinstance(value, str) and value.startswith(_URL_PREFIXES):
                    urls.append(value)
    # Some cards place jump URLs at the top level of the payload.
    for key in ("url", "jumpUrl", "qqdocurl", "link", "shareUrl", "sourceUrl"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith(_URL_PREFIXES):
            urls.append(value)

    structured = _prefer_bilibili(urls)
    if structured is not None:
        return structured
    return _prefer_bilibili(_url_candidates(payload))


# ── Message element scanning (duck-typed) ────────────────────────────────


def _el_type(element: Any) -> str:
    if isinstance(element, dict):
        return str(element.get("type", ""))
    return str(getattr(element, "type", ""))


def _el_attrs(element: Any) -> dict[str, Any]:
    if isinstance(element, dict):
        attrs = element.get("attrs")
        return attrs if isinstance(attrs, dict) else {}
    attrs = getattr(element, "attrs", None)
    return attrs if isinstance(attrs, dict) else {}


def _el_children(element: Any) -> Sequence[Any]:
    if isinstance(element, dict):
        children = element.get("children")
        return children if isinstance(children, list) else []
    children = getattr(element, "children", None)
    return children if isinstance(children, list) else []


def iter_element_texts(
    elements: Sequence[Any],
    *,
    include_quote: bool = False,
) -> Iterator[tuple[str, str]]:
    """Yield ``(kind, payload)`` pairs for scannable message content.

    Kinds:
    - ``"text"``: plain text content from ``text`` elements.
    - ``"ark"``: the raw JSON ``data`` attribute of ``sb:ark`` cards.

    Args:
        elements: Message element list (duck-typed or plain dicts).
        include_quote: When False, ``quote`` subtrees are skipped entirely so
            links inside quoted messages never trigger parsing.
    """
    stack: list[tuple[Any, bool]] = [(element, False) for element in reversed(list(elements))]
    while stack:
        element, in_quote = stack.pop()
        element_type = _el_type(element)
        if element_type == "quote":
            in_quote = True
        if in_quote and not include_quote:
            continue
        if element_type == "text":
            content = _el_attrs(element).get("content", "")
            if content:
                yield "text", str(content)
        elif element_type == "sb:ark":
            payload = _el_attrs(element).get("data", "")
            if payload:
                yield "ark", str(payload)
        # Recurse into containers (quote / message / forward / etc.).
        children = _el_children(element)
        if children:
            stack.extend((child, in_quote) for child in reversed(children))


def collect_bilibili_candidates(
    elements: Sequence[Any],
    *,
    include_quote: bool = False,
) -> list[LinkCandidate]:
    """Collect deduplicated Bilibili candidates from a message element list.

    Scans plain text elements and ``sb:ark`` share cards (whose JSON may embed
    a jump URL). Duplicates found across text and cards collapse into one.

    Args:
        elements: Message element list (duck-typed or plain dicts).
        include_quote: Whether to include links inside quoted messages.

    Returns:
        The collected candidates, in scanning order.
    """
    collected: dict[tuple[str, str, str, int], LinkCandidate] = {}
    for kind, payload in iter_element_texts(elements, include_quote=include_quote):
        if kind == "text":
            candidates = find_bilibili_candidates(payload)
        else:
            url = ark_extract_url(payload)
            candidates = find_bilibili_candidates(url) if url else []
        for candidate in candidates:
            key = (
                candidate.platform,
                candidate.kind,
                candidate.bvid
                or (f"av{candidate.avid}" if candidate.avid else f"b23:{candidate.short_code}"),
                candidate.page,
            )
            collected.setdefault(key, candidate)
    return list(collected.values())


def iter_quote_elements(elements: Sequence[Any]) -> Iterator[tuple[str | None, Sequence[Any]]]:
    """Yield every ``quote`` element found at any depth as ``(id, children)``.

    Args:
        elements: Message element list (duck-typed or plain dicts).

    Yields:
        Tuples of the quote's ``id`` attribute (may be ``None``) and its
        children (the quoted message content when the adapter attached it).
    """
    stack = list(elements)
    while stack:
        element = stack.pop()
        element_type = _el_type(element)
        if element_type == "quote":
            quote_id = str(_el_attrs(element).get("id", "") or "") or None
            yield quote_id, _el_children(element)
            continue
        children = _el_children(element)
        if children:
            stack.extend(children)


def collect_bilibili_candidates_in_quotes(elements: Sequence[Any]) -> list[LinkCandidate]:
    """Collect candidates from quoted-message content only (quote subtrees).

    Args:
        elements: Message element list (duck-typed or plain dicts).

    Returns:
        Deduplicated candidates found inside any ``quote`` element children.
    """
    collected: dict[tuple[str, str, str, int], LinkCandidate] = {}
    for _quote_id, children in iter_quote_elements(elements):
        for candidate in collect_bilibili_candidates(children):
            key = (
                candidate.platform,
                candidate.kind,
                candidate.bvid
                or (f"av{candidate.avid}" if candidate.avid else f"b23:{candidate.short_code}"),
                candidate.page,
            )
            collected.setdefault(key, candidate)
    return list(collected.values())


def merge_candidates(*groups: Sequence[LinkCandidate]) -> list[LinkCandidate]:
    """Merge candidate groups, deduplicating by resource key (first wins)."""
    collected: dict[tuple[str, str, str, int], LinkCandidate] = {}
    for group in groups:
        for candidate in group:
            key = (
                candidate.platform,
                candidate.kind,
                candidate.bvid
                or (f"av{candidate.avid}" if candidate.avid else f"b23:{candidate.short_code}"),
                candidate.page,
            )
            collected.setdefault(key, candidate)
    return list(collected.values())
