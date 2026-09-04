"""ShinBot plugin: link parser that resolves shared links into playable content.

v1 scope: Bilibili video links / share cards only — download the video and
reply with it, consuming the message by default (see DESIGN.md).

Trigger model: a NORMAL ``message-created`` route with a lightweight custom
matcher (text URL / ``sb:ark`` card scan). Matching consumes the message so the
``agent_entry`` fallback does not also reply; which messages match is
configurable (``parse_on_mention``, ``parse_reply``, ``enabled``).
"""

from __future__ import annotations

import asyncio
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from shinbot.core.plugins.context import Plugin

__plugin_name__ = "LinkParser"
__plugin_description__ = "解析 B 站视频链接/分享卡片，回复可播放的视频本体（v1：仅 B 站视频）。"


class LinkParserPluginConfig(BaseModel):
    """Configuration for the link parser plugin (M1 / Bilibili video)."""

    enabled: bool = Field(
        default=True,
        description="总开关：关闭后不再解析任何链接。",
    )
    parse_on_mention: bool = Field(
        default=True,
        description="消息同时 @机器人 时也解析并回复；关闭后此类消息交给 Agent。",
    )
    parse_reply: bool = Field(
        default=False,
        description="同时解析引用回复（quote）中的链接。",
    )
    fallback_to_text: bool = Field(
        default=True,
        description="视频消息发送失败时降级为文本（标题+链接）回复。",
    )
    bilibili_cookie: str = Field(
        default="",
        description="B站 SESSDATA cookie（可选，用于更高清晰度的 DASH 流）。",
    )
    prefer_mp4: bool = Field(
        default=True,
        description="优先单文件 mp4（HTML5 直链，免 ffmpeg 免登录，最高约 1080P）。",
    )
    max_quality: int = Field(
        default=80,
        ge=16,
        le=127,
        description="DASH 合并路径允许的最高清晰度（qn：16=360P…80=1080P…）。",
    )
    max_duration_seconds: int = Field(
        default=0,
        ge=0,
        description="视频时长上限（秒），0=不限制。",
    )
    max_size_mb: int = Field(
        default=200,
        ge=1,
        le=4096,
        description="下载体积上限（MB）。",
    )
    debounce_seconds: int = Field(
        default=300,
        ge=0,
        description="同一会话同一链接/资源的防抖窗口（秒），0=关闭。",
    )
    cache_max_files: int = Field(
        default=50,
        ge=0,
        description="videos 缓存目录保留的 mp4 文件数上限（0=不清理）。",
    )
    delete_after_send: bool = Field(
        default=False,
        description="发送成功后删除本地缓存文件（不再跨会话/重启复用）。",
    )


__plugin_config_class__ = LinkParserPluginConfig

_client_global: Any | None = None
"""Shared BilibiliClient kept for teardown; replaced on every setup()."""


def setup(plg: Plugin) -> None:
    """Register the Bilibili video parser route and shared client.

    Framework and third-party imports happen here (not at module import time)
    so the package stays importable in plain unit tests.
    """
    global _client_global

    from shinbot.core.dispatch.routing import RouteCondition, RouteMatchMode

    from .bilibili import BilibiliClient
    from .debounce import Debouncer
    from .matcher import build_link_matcher
    from .parsers import prune_video_cache

    config = _load_plugin_config(plg.plugin_id)

    client = BilibiliClient(cookie=config.bilibili_cookie, logger=plg.logger)
    _client_global = client
    debouncer = Debouncer(config.debounce_seconds)
    matcher = build_link_matcher(
        enabled=config.enabled,
        parse_on_mention=config.parse_on_mention,
        parse_reply=config.parse_reply,
    )

    if config.cache_max_files > 0:
        try:
            prune_video_cache(Path(plg.data_dir) / "videos", keep=config.cache_max_files)
        except Exception:
            plg.logger.debug("LinkParser video cache prune failed", exc_info=True)

    @plg.on_route(
        RouteCondition(
            event_types=frozenset({"message-created"}),
            custom_matcher=matcher,
        ),
        rule_id="shinbot_plugin_linkparser.parse",
        priority=80,
        match_mode=RouteMatchMode.NORMAL,
    )
    async def linkparser_route(context: Any, _rule: Any) -> None:
        await _handle_message(plg, config, client, debouncer, context)

    plg.logger.info(
        "LinkParser loaded (Bilibili video v1; consume=%s, prefer_mp4=%s)",
        config.enabled,
        config.prefer_mp4,
    )


async def _handle_message(
    plg: Plugin,
    config: LinkParserPluginConfig,
    client: Any,
    debouncer: Any,
    context: Any,
) -> None:
    """Parse the first scannable Bilibili link and reply with the video."""
    from shinbot.schema.elements import MessageElement

    from .bilibili import BilibiliError
    from .parsers import delete_cached_file, parse_video
    from .urls import collect_bilibili_candidates

    message_context = context.require_message_context()
    candidates = collect_bilibili_candidates(
        message_context.message.elements,
        include_quote=config.parse_reply,
    )
    if not candidates:
        return

    candidate = candidates[0]
    session_id = message_context.session_id
    link_key = candidate.matched or candidate.resource_key()
    if debouncer.hit(session_id, link_key):
        plg.logger.debug("LinkParser debounce hit: %s", link_key)
        return
    debouncer.remember(session_id, link_key)

    try:
        outcome = await parse_video(
            client,
            candidate,
            data_dir=Path(plg.data_dir),
            max_duration_seconds=config.max_duration_seconds,
            max_size_mb=config.max_size_mb,
            prefer_mp4=config.prefer_mp4,
            max_quality=config.max_quality,
            cache_max_files=config.cache_max_files,
        )
    except asyncio.CancelledError:
        debouncer.forget(session_id, link_key)
        raise
    except Exception as exc:
        debouncer.forget(session_id, link_key)
        if isinstance(exc, BilibiliError):
            message = str(exc)
        else:
            plg.logger.exception("LinkParser parse failure")
            message = "解析视频时发生未知错误，请稍后再试。"
        await message_context.send(message)
        return

    # Reply with the produced mp4; remember the canonical resource for dedupe
    # (unless the file was deleted right after sending).
    resource_key = f"bilibili:video:{outcome.meta.bvid}:p{outcome.meta.page}"
    try:
        await message_context.send([MessageElement.video(str(outcome.path))])
        if config.delete_after_send:
            if delete_cached_file(outcome.path):
                plg.logger.debug("LinkParser removed sent video cache: %s", outcome.path)
            debouncer.forget(session_id, resource_key)
        else:
            debouncer.remember(session_id, resource_key)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        plg.logger.warning("LinkParser video send failed: %s", exc)
        if config.fallback_to_text:
            title = outcome.meta.display_title or "视频"
            await message_context.send(f"{title}\n{outcome.meta.page_url}")


async def on_disable(_plg: Plugin) -> None:
    """Close the shared Bilibili client when the plugin is disabled."""
    global _client_global
    client = _client_global
    _client_global = None
    if client is not None:
        try:
            await client.close()
        except Exception:
            pass


def _resolve_config_path(argv: Sequence[str] | None = None) -> Path:
    """Resolve the ShinBot ``--config`` path from the process arguments."""
    from shinbot.core.application.paths import DEFAULT_CONFIG_PATH

    args = list(sys.argv[1:] if argv is None else argv)
    for index, value in enumerate(args):
        if value == "--config" and index + 1 < len(args):
            return Path(args[index + 1])
        if value.startswith("--config="):
            return Path(value.split("=", 1)[1])
    return DEFAULT_CONFIG_PATH


def _load_plugin_config(plugin_id: str) -> LinkParserPluginConfig:
    """Load the ``[plugins.<id>]`` TOML block, falling back to defaults."""
    from shinbot.core.plugins.config import plugin_config_block

    path = _resolve_config_path()
    raw: dict[str, Any] = {}
    try:
        if path.exists():
            with path.open("rb") as file_obj:
                payload = tomllib.load(file_obj)
            raw = plugin_config_block(payload, plugin_id)
    except Exception:
        raw = {}
    try:
        return LinkParserPluginConfig.model_validate(raw)
    except Exception:
        return LinkParserPluginConfig()
