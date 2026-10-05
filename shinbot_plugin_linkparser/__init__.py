"""ShinBot plugin: link parser that resolves shared links into playable content.

Scope: Bilibili video links/cards and Xiaohongshu (小红书) notes — reply with
the video, or with image galleries stitched into one long image (see DESIGN.md).

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
    "解析 B 站视频 / 小红书图文与视频并回复；默认不解析，"
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
            "直发视频体积上限（MB）。超限时用 ffmpeg 自动压缩到该体积内再直发；"
            "压缩不可用时才回标题+链接（OneBot base64 大文件易超 request_timeout）。"
        ),
    )
    compress: bool = Field(
        default=True,
        description="超过 max_send_mb 时用 ffmpeg 压缩后再直发（无需上传原画）。",
    )
    compress_max_height: int = Field(
        default=720,
        ge=144,
        le=2160,
        description="压缩输出最大高度（px），宽高比保持。",
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
    xiaohongshu_cookie: str = Field(
        default="",
        description="小红书网页 cookie 串（可选，用于绕过风控；从浏览器复制 a1/web_session 等）。",
    )
    xhs_max_images: int = Field(
        default=9,
        ge=1,
        le=30,
        description="小红书图文笔记最多取前 N 张。",
    )
    xhs_image_mode: Literal["long", "raw"] = Field(
        default="long",
        description="long=拼成一张长图发送（省消息资源）；raw=逐张发送。",
    )
    xhs_stitch_max_height: int = Field(
        default=12000,
        ge=2000,
        le=60000,
        description="长图拼接最大高度（px），超限会整体等比缩小。",
    )
    xhs_video_max_height: int = Field(
        default=720,
        ge=144,
        le=2160,
        description="小红书视频下载的最大分辨率（优先直链 mp4，避免大体积原片）。",
    )
    x_backend: Literal["auto", "syndication", "fxtwitter"] = Field(
        default="auto",
        description=(
            "X/Twitter 数据源：auto=官方 syndication 优先、fxtwitter 兜底；"
            "也可固定为其中之一。"
        ),
    )
    send_text: bool = Field(
        default=True,
        description="回复中包含文字说明（X 推文正文 / 小红书标题正文与作者）。",
    )
    send_forward: bool = Field(
        default=True,
        description=(
            "「文字+媒体」折叠为一条聊天记录（合并转发）发送，两者都不丢"
            "（适用 X 与小红书）；适配器不支持或发送失败时自动降级为分别发送。"
        ),
    )
    x_image_mode: Literal["long", "raw"] = Field(
        default="long",
        description="X 多图：long=拼成一张长图；raw=逐张发送。",
    )
    x_video_max_height: int = Field(
        default=720,
        ge=144,
        le=2160,
        description="X 视频下载的最大分辨率（避免直接拉 4K 原片）。",
    )


__plugin_config_class__ = LinkParserPluginConfig

_client_global: Any | None = None
"""Shared BilibiliClient kept for teardown; replaced on every setup()."""

_xhs_client_global: Any | None = None
"""Shared Xiaohongshu client kept for teardown; replaced on every setup()."""

_x_client_global: Any | None = None
"""Shared X/Twitter client kept for teardown; replaced on every setup()."""

_FORWARD_NAME = "LinkParser"

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
    """Register the parser command, route and shared clients.

    Framework and third-party imports happen here (not at module import time)
    so the package stays importable in plain unit tests.
    """
    global _client_global, _xhs_client_global, _x_client_global

    from shinbot.core.dispatch.routing import RouteCondition, RouteMatchMode

    from .bilibili import BilibiliClient
    from .debounce import Debouncer
    from .matcher import build_link_matcher
    from .parsers import prune_video_cache
    from .session_state import SessionStateStore
    from .twitter import XClient
    from .xiaohongshu import XHSClient

    config = _load_plugin_config(plg.plugin_id)

    state = SessionStateStore(
        Path(plg.data_dir) / "session_state.json",
        default_mode=config.default_mode,
    )
    client = BilibiliClient(cookie=config.bilibili_cookie, logger=plg.logger)
    _client_global = client
    xhs_client = XHSClient(cookie=config.xiaohongshu_cookie, logger=plg.logger)
    _xhs_client_global = xhs_client
    x_client = XClient(backend=config.x_backend, logger=plg.logger)
    _x_client_global = x_client
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
        await _handle_message(
            plg, config, state, client, xhs_client, x_client, debouncer, context
        )

    @plg.on_command(
        "parser",
        aliases=["linkparser"],
        description="设置当前会话的 B 站/小红书解析档位（off/at/always）",
        usage="/parser off|at|always|status",
        permission="cmd.linkparser",
    )
    async def parser_command(ctx: Any, args: str) -> None:
        await _handle_parser_command(ctx, args, state=state, logger=plg.logger)

    plg.logger.info(
        "LinkParser loaded (bilibili+xhs+x; default_mode=%s, parse_reply=%s, x_backend=%s)",
        config.default_mode,
        config.parse_reply,
        config.x_backend,
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


def _supports_forward(message_context: Any) -> bool:
    """Return True when the session adapter supports folded (forward) sends."""
    adapter = getattr(message_context, "adapter", None)
    candidates = {
        str(getattr(adapter, "platform", "") or "").lower(),
        str(getattr(message_context, "platform", "") or "").lower(),
    }
    if candidates & {"onebot_v11", "onebot", "qq"}:
        return True
    adapter_type = type(adapter)
    return (
        "onebot" in adapter_type.__name__.lower()
        or "shinbot_adapter_onebot_v11" in adapter_type.__module__.lower()
    )


async def _send_folded(
    message_context: Any,
    caption: str,
    files: list[Path],
    is_video: bool,
    logger: Any,
) -> bool:
    """Send caption + media as one collapsed chat-record (forward) message.

    Returns False when the adapter rejects the folded send so the caller can
    fall back to separate text/media messages.
    """
    try:
        from shinbot.schema.elements import MessageElement

        nodes = []
        if caption:
            nodes.append(
                MessageElement.message(
                    [MessageElement.text(caption)], nickname=_FORWARD_NAME
                )
            )
        media = [
            MessageElement.video(str(path)) if is_video else MessageElement.img(str(path))
            for path in files
        ]
        if media:
            nodes.append(MessageElement.message(media, nickname=_FORWARD_NAME))
        if not nodes:
            return False
        await message_context.send([MessageElement.forward(nodes)])
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "LinkParser folded send failed (%s): %s", type(exc).__name__, exc
        )
        return False


async def _handle_message(
    plg: Plugin,
    config: LinkParserPluginConfig,
    state: Any,
    client: Any,
    xhs_client: Any,
    x_client: Any,
    debouncer: Any,
    context: Any,
) -> None:
    """Parse the resolved link for a matched message and reply with media."""
    from shinbot.schema.elements import MessageElement

    from .bilibili import BilibiliError, ffmpeg_available
    from .parse_policy import make_db_quote_resolver, parse_candidates_for, visible_mentions_bot
    from .parsers import delete_cached_file, parse_video, parse_x_post, parse_xhs_note
    from .twitter import XError
    from .xiaohongshu import XHSError

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

    is_video = True
    files: list[Path] = []
    title = "视频"
    page_url = ""
    caption: str | None = None
    resolved_resource: str | None = None

    try:
        if candidate.platform == "xiaohongshu":
            outcome = await parse_xhs_note(
                xhs_client,
                candidate,
                data_dir=Path(plg.data_dir),
                image_mode=config.xhs_image_mode,
                max_images=config.xhs_max_images,
                stitch_max_height=config.xhs_stitch_max_height,
                video_max_height=config.xhs_video_max_height,
                max_size_mb=config.max_size_mb,
                max_send_mb=config.max_send_mb,
                compress=config.compress,
                compress_max_height=config.compress_max_height,
            )
            files = [Path(path) for path in outcome.files]
            is_video = outcome.kind == "video"
            title = outcome.info.display_title or "小红书笔记"
            page_url = outcome.info.page_url
            resolved_resource = f"xiaohongshu:post:{outcome.info.note_id}"
            if config.send_text:
                caption = outcome.info.caption
        elif candidate.platform == "x":
            outcome = await parse_x_post(
                x_client,
                candidate,
                data_dir=Path(plg.data_dir),
                image_mode=config.x_image_mode,
                stitch_max_height=config.xhs_stitch_max_height,
                video_max_height=config.x_video_max_height,
                max_size_mb=config.max_size_mb,
                max_send_mb=config.max_send_mb,
                compress=config.compress,
                compress_max_height=config.compress_max_height,
            )
            files = [Path(path) for path in outcome.files]
            is_video = outcome.kind == "video"
            title = outcome.info.display_title or "X 推文"
            page_url = outcome.info.url
            resolved_resource = f"x:post:{outcome.info.status_id}"
            if config.send_text:
                caption = outcome.info.caption
        else:
            outcome = await parse_video(
                client,
                candidate,
                data_dir=Path(plg.data_dir),
                max_duration_seconds=config.max_duration_seconds,
                max_size_mb=config.max_size_mb,
                prefer_mp4=config.prefer_mp4,
                max_quality=config.max_quality,
                cache_max_files=config.cache_max_files,
                max_send_mb=config.max_send_mb,
                compress=config.compress,
                compress_max_height=config.compress_max_height,
            )
            files = [outcome.path]
            title = outcome.meta.display_title or "视频"
            page_url = outcome.meta.page_url
            resolved_resource = (
                f"bilibili:video:{outcome.meta.bvid}:p{outcome.meta.page}"
            )
    except asyncio.CancelledError:
        debouncer.forget(session_id, link_key)
        raise
    except Exception as exc:
        debouncer.forget(session_id, link_key)
        if isinstance(exc, (BilibiliError, XHSError, XError)):
            message = str(exc)
        else:
            plg.logger.exception("LinkParser parse failure")
            message = "解析内容时发生未知错误，请稍后再试。"
        await _safe_send(message_context, message, plg.logger, "parse-error")
        return

    # ── reply ─────────────────────────────────────────────────────────
    if not files:
        # Text-only post (e.g. a text tweet): reply with the caption.
        if caption:
            await _safe_send(message_context, caption, plg.logger, "text-post")
        if resolved_resource:
            debouncer.remember(session_id, resolved_resource)
        return

    if is_video:
        size_mb = _file_size_mb(files[0])
        if config.max_send_mb > 0 and size_mb > config.max_send_mb:
            # Still over the send cap after (attempted) compression — reply
            # with a link. Usually ffmpeg is missing or compression disabled.
            hint = ""
            if config.compress and not ffmpeg_available():
                hint = "\n（安装 ffmpeg 后本插件可自动压缩后直发）"
            plg.logger.info(
                "LinkParser video %s is %dMB > max_send_mb=%d; text fallback",
                files[0].name,
                size_mb,
                config.max_send_mb,
            )
            await _safe_send(
                message_context,
                f"{title}（{size_mb}MB，超过直发上限 {config.max_send_mb}MB）\n{page_url}{hint}",
                plg.logger,
                "oversize-fallback",
            )
            debouncer.forget(session_id, link_key)
            return

    # Preferred: collapse caption + media into one chat record (OneBot
    # forward) so neither the text nor the media is lost.
    sent = False
    if caption and config.send_forward and _supports_forward(message_context):
        sent = await _send_folded(
            message_context, caption, files, is_video, plg.logger
        )
    if not sent:
        if caption:
            await _safe_send(message_context, caption, plg.logger, "post-caption")
        elements_payload = []
        for path in files:
            if is_video:
                elements_payload.append(MessageElement.video(str(path)))
            else:
                elements_payload.append(MessageElement.img(str(path)))
        try:
            await message_context.send(elements_payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Platform send failed (timeout/disconnect/etc.). Never let this
            # escape: keep the cached files, forget debounce so a re-share can
            # retry, and attempt an informational text reply if possible.
            plg.logger.warning(
                "LinkParser media send failed (%s): %s", type(exc).__name__, exc
            )
            debouncer.forget(session_id, link_key)
            if resolved_resource:
                debouncer.forget(session_id, resolved_resource)
            if config.fallback_to_text:
                await _safe_send(
                    message_context,
                    f"{title}\n{page_url}",
                    plg.logger,
                    "send-fallback",
                )
            return

    # Send succeeded; remember the canonical resource for dedupe (unless the
    # files were deleted right after sending).
    if config.delete_after_send:
        for path in files:
            if delete_cached_file(path):
                plg.logger.debug("LinkParser removed sent cache: %s", path)
        if resolved_resource:
            debouncer.forget(session_id, resolved_resource)
    elif resolved_resource:
        debouncer.remember(session_id, resolved_resource)


async def on_disable(_plg: Plugin) -> None:
    """Close the shared platform clients when the plugin is disabled."""
    global _client_global, _xhs_client_global, _x_client_global
    clients = [_client_global, _xhs_client_global, _x_client_global]
    _client_global = None
    _xhs_client_global = None
    _x_client_global = None
    for client in clients:
        if client is None:
            continue
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
