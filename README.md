# LinkParser (shinbot_plugin_linkparser)

ShinBot 插件：解析消息里的链接 / 分享卡片并回复**内容本体**。

**v1 范围**：处理 **Bilibili 视频**（`BV`/`av`/`b23.tv` 短链/QQ 分享卡片内嵌链接，含 `?p=N` 分 P），
下载为可播放 mp4 后**以视频消息回复**；命中时**消费消息**（阻止 Agent 再回复），行为可配置。

> 状态：M1 已实现（65 单测通过 + 真机端到端验证）。B站数据源：方案 B（bilibili-api-python）；
> 默认走 HTML5 单文件 mp4（匿名最高约 1080P，免 ffmpeg），失败自动降级 DASH+ffmpeg 合并。

## 安装

通过 ShinBot WebUI 插件市场安装（插件索引仓库：
[NekyuuYa/shinbot-plugins](https://github.com/NekyuuYa/shinbot-plugins)），
或手动把本仓库放到 `data/plugins/` 后启用：

```bash
# 从源码启用
curl -X POST http://localhost:3945/api/v1/plugins/rescan
curl -X POST http://localhost:3945/api/v1/plugins/shinbot_plugin_linkparser/enable
```

## 用法

群友发一条 B站视频链接、裸 `BV`/`av` 号、`b23.tv` 短链或 QQ 分享卡片：

```
https://www.bilibili.com/video/BV1xx411c7mD?p=2
```

机器人解析后回复可播放的视频消息；同一会话短时间内重复链接不重复解析。
已下载的视频按 `bv号_pN.mp4` 缓存复用（重启后同视频不再重新下载），目录按 `cache_max_files` 自动清理。
视频消息发送失败时自动降级为「标题 + 链接」文本（`fallback_to_text`）。

> 平台限制：OneBot 适配器以 base64 上行本地视频文件，超大视频受平台消息大小限制；
> 发送失败即走文本兜底。可用 `max_size_mb` 控制下载体积。

## 配置

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关 |
| `parse_on_mention` | `true` | 消息同时 @机器人 时也解析回复；`false` 时此类消息留给 Agent |
| `parse_reply` | `false` | 同时解析引用回复中的链接 |
| `fallback_to_text` | `true` | 视频发送失败时降级为「标题+链接」文本 |
| `prefer_mp4` | `true` | 优先单文件 mp4（HTML5，匿名最高约 1080P）；`false` 走 DASH+ffmpeg 合并 |
| `bilibili_cookie` | `""` | B站 SESSDATA（可选，DASH 高清晰度） |
| `max_quality` | `80` | DASH 路径最高清晰度（qn：16=360P … 80=1080P … 127=8K） |
| `max_duration_seconds` | `0` | 视频时长上限（秒），0=不限制 |
| `max_size_mb` | `200` | 下载体积上限 |
| `debounce_seconds` | `300` | 同一会话同一链接/资源防抖窗口（秒），0=关闭 |
| `cache_max_files` | `50` | videos 缓存目录保留的 mp4 文件数上限，0=不清理 |

配置写入 ShinBot 配置文件插件块 `[plugins.shinbot_plugin_linkparser]`。

## 开发

```bash
uv sync                                   # 安装依赖（含 bilibili-api-python）
.venv/bin/python -m pytest tests/ -q      # 单测（无需 ShinBot 安装，无网络依赖）
.venv/bin/ruff check .
```

依赖需 ffmpeg 的场景：仅当 html5 单文件不可用或 `prefer_mp4=false` 时，才需要 `ffmpeg` 合并 DASH 音视频流。

## 发布 / 索引

设计、打包与索引纪律见 [DESIGN.md](./DESIGN.md) §7。
