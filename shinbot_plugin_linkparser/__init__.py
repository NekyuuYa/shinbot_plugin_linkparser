"""ShinBot plugin: link parser that resolves shared links into playable content.

Scope: Bilibili video links / share cards only — download the video and reply
with it (see DESIGN.md).

Parse policy has three modes, set per session with ``/parser`` (default off):

- ``off``    — never parse.
- ``at``     — parse only when the bot is @-mentioned; for reply/quote messages
               the quoted content is resolved (message_logs) and parsed too.
- ``always`` — parse every message carrying a parseable link (quote content
               additionally when ``parse_reply`` is enabled).

The route matcher applies the mode precisely (including the DB-backed quote
resolution), so disabled/other messages flow to the agent untouched.
"""

from __future__ import annotations

import asyncio
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from shinbot.core.plugins.context import Plugin

__plugin_name__ = "LinkParser"
__plugin_description__ = (
    "解析 B 站视频链接/分享卡片并回复视频；默认不解析，"
    "/parser at|always 按会话开启（off/at/always 三档）。"
)


class LinkParserPluginConfig(BaseModel):
    """Configuration for the link parser plugin (Bilibili video)."""

    enabled: bool = Field(
        default=True,
        description="插件总开关：关闭后任何会话都不解析（指令仍可用）。",
    )
    default_mode: Literal["off", "at", "always"] = Field(
        default="off",
        description=(
            "未用 /parser 设置过的会话使用的解析档位："
            "off=不解析；at=仅 @机器人 时解析（含其引用内容）；always=总是解析。"
        ),
    )
    parse_reply: bool = Field(
        default=False,
        description=(
            "在 always 档下同时解析引用回复（quote）里的链接；"
            "at 档对 @消息所引用内容的解析不依赖此项。"
        ),
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
    max_send_mb: int = Field(
        default=50,
        ge=1,
        le=4096,
        description=(
            "直发视频上限（MB）：超过则不发视频、改发标题+链接（OneBot 以 base64 "
            "上行大文件易超适配器 request_timeout，可按平台实测调大）。"
        ),
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

_USAGE_TEXT = (
    "/parser off|at|always|status —— "
    "off=不解析；at=仅 @机器人 时解析（含其引用的消息）；always=总是解析（on 同 always）。"
)


async def _safe_send(ctx: Any, content: Any, logger: Any, label: str) -> bool:
    """Send *content*, never letting a platform failure escape the handler.

    Args:
        ctx: Message context (or anything with ``async send(content)``).
        content: Payload to send.
        logger: Logger for warnings.
        label: Short description used in log lines.

    Returns:
        True when the send call succeeded.
    """
    try:
        await ctx.send(content)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("LinkParser %s send failed (%s): %s", label, type(exc).__name__, exc)
        return False


def _file_size_mb(path: Path) -> int:
    """Return a file's size in whole MiB (0 on any error)."""
    try:
        return int(path.stat().st_size // (1024 * 1024))
    except OSError:
        return 0


def setup(plg: Plugin) -> None:
    """Register the parser command, route and shared client.

    Framework and third-party imports happen here (not at module import time)
    so the package stays importable in plain unit tests.
    """
    global _client_global

    from shinbot.core.dispatch.routing import RouteCondition, RouteMatchMode

    from .bilibili import BilibiliClient
    from .debounce import Debouncer
    from .matcher import build_link_matcher
    from .parsers import prune_video_cache
    from .session_state import SessionStateStore

    config = _load_plugin_config(plg.plugin_id)

    state = SessionStateStore(
        Path(plg.data_dir) / "session_state.json",
        default_mode=config.default_mode,
    )
    client = BilibiliClient(cookie=config.bilibili_cookie, logger=plg.logger)
    _client_global = client
    debouncer = Debouncer(config.debounce_seconds)
    matcher = build_link_matcher(
        enabled=config.enabled,
        parse_reply=config.parse_reply,
        get_mode=lambda session_id: state.mode(session_id),
        database=plg.database,
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
        await _handle_message(plg, config, state, client, debouncer, context)

    @plg.on_command(
        "parser",
        aliases=["linkparser"],
        description="设置当前会话的 B 站链接解析档位（off/at/always）",
        usage="/parser off|at|always|status",
        permission="cmd.linkparser",
    )
    async def parser_command(ctx: Any, args: str) -> None:
        await _handle_parser_command(ctx, args, state=state, logger=plg.logger)

    plg.logger.info(
        "LinkParser loaded (Bilibili video; default_mode=%s, parse_reply=%s, prefer_mp4=%s)",
        config.default_mode,
        config.parse_reply,
        config.prefer_mp4,
    )


async def _handle_parser_command(
    ctx: Any,
    args: str,
    *,
    state: Any,
    logger: Any,
) -> None:
    """Handle ``/parser off|at|always|status`` for the current session."""
    verb = (args or "").strip().split(None, 1)[0].lower() if (args or "").strip() else ""
    session_id = str(getattr(ctx, "session_id", "") or "")
    if not session_id:
        await ctx.send("无法获取当前会话标识。")
        return

    aliases = {"on": "always", "all": "always"}
    mode = aliases.get(verb, verb)
    if mode in ("off", "at", "always"):
        state.set_mode(session_id, mode)
        labels = {
            "off": "不解析",
            "at": "仅 @本机器人 时解析（含其引用的消息内容）",
            "always": "总是解析链接消息",
        }
        await ctx.send(f"本会话解析已设为 {mode}（{labels[mode]}）。")
    elif verb in ("status", ""):
        current = state.mode(session_id)
        await ctx.send(
            f"本会话解析档位：{current}（全局默认：{state.default_mode}）。\n{_USAGE_TEXT}"
        )
    else:
        await ctx.send(_USAGE_TEXT)


async def _handle_message(
    plg: Plugin,
    config: LinkParserPluginConfig,
    state: Any,
    client: Any,
    debouncer: Any,
    context: Any,
) -> None:
    """Parse the resolved Bilibili link for a matched message and reply."""
    from shinbot.schema.elements import MessageElement

    from .bilibili import BilibiliError
    from .parse_policy import make_db_quote_resolver, parse_candidates_for, visible_mentions_bot
    from .parsers import delete_cached_file, parse_video

    message_context = context.require_message_context()
    elements = message_context.message.elements
    session_id = message_context.session_id
    mode = state.mode(session_id)

    resolver = None
    if mode == "at" or (mode == "always" and config.parse_reply):
        resolver = make_db_quote_resolver(plg.database, session_id)
    candidates = parse_candidates_for(
        elements,
        mode=mode,
        mentions_bot=visible_mentions_bot(elements, message_context.event.self_id),
        parse_reply=config.parse_reply,
        resolve_quote=resolver,
    )
    if not candidates:
        plg.logger.debug("LinkParser: no parseable target in matched message")
        return

    candidate = candidates[0]
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
        await _safe_send(message_context, message, plg.logger, "parse-error")
        return

    # ── reply ─────────────────────────────────────────────────────────
    size_mb = _file_size_mb(outcome.path)
    if config.max_send_mb > 0 and size_mb > config.max_send_mb:
        # Skip the platform video attempt entirely: base64-uploading huge
        # files typically exceeds the adapter's request timeout and can drop
        # the adapter connection. Reply with a link instead.
        title = outcome.meta.display_title or "视频"
        plg.logger.info(
            "LinkParser video %s is %dMB > max_send_mb=%d; sending text fallback",
            outcome.path.name,
            size_mb,
            config.max_send_mb,
        )
        await _safe_send(
            message_context,
            f"{title}（{size_mb}MB，超过直发上限 {config.max_send_mb}MB）\n{outcome.meta.page_url}",
            plg.logger,
            "oversize-fallback",
        )
        debouncer.forget(session_id, link_key)
        return

    resource_key = f"bilibili:video:{outcome.meta.bvid}:p{outcome.meta.page}"
    try:
        await message_context.send([MessageElement.video(str(outcome.path))])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Platform send failed (timeout/disconnect/etc.). Never let this
        # escape: keep the cached file, forget debounce so a re-share can
        # retry, and attempt an informational text reply if possible.
        plg.logger.warning(
            "LinkParser video send failed (%s): %s", type(exc).__name__, exc
        )
        debouncer.forget(session_id, link_key)
        debouncer.forget(session_id, resource_key)
        if config.fallback_to_text:
            title = outcome.meta.display_title or "视频"
            await _safe_send(
                message_context,
                f"{title}\n{outcome.meta.page_url}",
                plg.logger,
                "send-fallback",
            )
        return

    # Send succeeded; remember the canonical resource for dedupe (unless the
    # file was deleted right after sending).
    if config.delete_after_send:
        if delete_cached_file(outcome.path):
            plg.logger.debug("LinkParser removed sent video cache: %s", outcome.path)
        debouncer.forget(session_id, resource_key)
    else:
        debouncer.remember(session_id, resource_key)


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
