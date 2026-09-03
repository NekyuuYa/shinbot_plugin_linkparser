"""ShinBot plugin: link parser that resolves shared links into playable content.

v1 scope: Bilibili video links / share cards only — reply with the downloaded
video and (by default) consume the message. See DESIGN.md for the full design
and roadmap.

NOTE: this module is currently a preparation-stage skeleton. The parsing logic
(URL extraction, sb:ark card mining, Bilibili API client, downloader, route
registration) is not implemented yet; see DESIGN.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from shinbot.core.plugins.context import Plugin

__plugin_name__ = "LinkParser"
__plugin_description__ = "解析 B 站视频链接/分享卡片，回复可播放的视频本体（v1：仅 B 站视频）。"


class LinkParserPluginConfig(BaseModel):
    """Configuration for the link parser plugin (draft — finalise in M1)."""

    enabled: bool = True
    consume_message: bool = Field(
        default=True,
        description="命中链接后消费消息（阻止 agent fallback 再次回复）。",
    )
    parse_on_mention: bool = Field(
        default=True,
        description="消息同时 @机器人 时仍解析并回复。",
    )
    parse_reply: bool = Field(
        default=False,
        description="解析引用回复（quote）中的链接（roadmap）。",
    )
    bilibili_cookie: str = Field(
        default="",
        description="B站 SESSDATA cookie（可选，用于更高清晰度）。",
    )
    max_duration_seconds: int = Field(
        default=600,
        ge=0,
        description="视频时长上限（秒），0=不限制。",
    )
    max_size_mb: int = Field(
        default=300,
        ge=1,
        description="下载体积上限（MB）。",
    )
    prefer_mp4: bool = Field(
        default=True,
        description="优先 mp4 直链（免 ffmpeg 合并 DASH 音视频）。",
    )
    debounce_seconds: int = Field(
        default=300,
        ge=0,
        description="同一会话同一链接/资源的防抖窗口（秒）。",
    )


__plugin_config_class__ = LinkParserPluginConfig


def setup(plg: Plugin) -> None:
    """Register the link parser route and background resources.

    TODO(impl): implement per DESIGN.md — register a NORMAL message-created
    route with a lightweight custom matcher (URL / sb:ark scan), build the
    Bilibili parser registry, and wire config/teardown. Until then the plugin
    loads without registering any behaviour.
    """
    # config = _load_plugin_config(plg.plugin_id)
    # _register_parse_route(plg, config)
    plg.logger.warning(
        "LinkParser loaded as a preparation skeleton; parsing is not implemented yet (see DESIGN.md)."
    )


async def on_disable(_plg: Plugin) -> None:
    """Release parser sessions and cancel background tasks on disable.

    TODO(impl): close http clients / downloader sessions owned by parsers.
    """
    return None
