# LinkParser (shinbot_plugin_linkparser)

ShinBot 插件：解析分享内容并回复可消费媒体——

- **Bilibili 视频**（链接 / `BV`/`av` 号 / `b23.tv` 短链 / QQ 分享卡片，含 `?p=N`）→ 下载为可播放 mp4 后直发；
- **小红书笔记**（`xiaohongshu.com`/`xhslink.com` 链接或分享卡片）→ 与 X 相同的输出：**「作者/标题/正文 + 媒体」折叠成
  一条聊天记录**（合并转发）发送；图文**逐张**发送（不拼图，保留原图与图文混排空间），视频优先直链 mp4 下载（HLS 才走 ffmpeg）并按需压缩；
- **X/Twitter 推文**（`x.com`/`twitter.com`/镜像站 `/status/<id>`）→ 文字 + 媒体**折叠成一条聊天记录**（合并转发）
  发送，两者都不丢；纯文字推文直接回文字；多图逐张（不拼图），视频选 ≤720P 直链下载后按需压缩。
  **图片与视频混合、或一条推文含多个视频时按推文原顺序全部发送**（聊天记录里一条一个 node）。

**默认不解析**。解析档位按会话设置（三档），状态持久化：

| 档位 | 行为 |
|---|---|
| `off` | 不解析（默认） |
| `at` | 仅当消息 @了本机器人 时解析：查消息自身文本里的链接；若该 @ 消息是引用回复，则解析其**引用的消息内容**（经 message_logs 精确判定，无链接的 @ 提问不会被吞） |
| `always` | **总是解析**链接消息（含 QQ 分享卡片）；引用回复内容在 `parse_reply=true` 时一并解析 |

## 指令

| 指令 | 权限 | 说明 |
|---|---|---|
| `/parser off` | `cmd.linkparser` | 当前会话不解析 |
| `/parser at` | `cmd.linkparser` | 仅 @机器人 时解析（含 @ 消息引用的内容） |
| `/parser always` | `cmd.linkparser` | 总是解析（`on` 是 `always` 的别名） |
| `/parser status` | `cmd.linkparser` | 查看当前会话档位与全局默认 |

别名：`/linkparser`。`cmd.linkparser` 默认授予 admin/owner 分组；如需群友自行设置，
在权限配置中把该节点授予对应用户/分组即可。

## 安装

通过 ShinBot WebUI 插件市场安装（索引仓库：
[NekyuuYa/shinbot-plugins](https://github.com/NekyuuYa/shinbot-plugins)），
或手动把本仓库放到 `data/plugins/` 后启用：

```bash
curl -X POST http://localhost:3945/api/v1/plugins/rescan
curl -X POST http://localhost:3945/api/v1/plugins/shinbot_plugin_linkparser/enable
```

## 用法

有权限的用户在目标会话设置档位，例如管理员想让该群"发链接就解析"：

```
/parser always
```

之后群友发 B站视频链接、裸 `BV`/`av` 号、`b23.tv` 短链或 **QQ 分享卡片**（含 `?p=N`）：

```
https://www.bilibili.com/video/BV1xx411c7mD?p=2
```

机器人解析并回复内容（B站视频；小红书图文→长图 / 视频；X 推文文字+媒体→折叠聊天记录）。
视频超过 `max_send_mb` 会自动压缩后直发，不必上传原画。
同一会话短时间重复链接不重复解析；
已下载视频按 `bv号_pN.mp4` 缓存复用（重启后不再重新下载，压缩版本也会缓存），目录按 `cache_max_files` 自动清理；
发送成功后不删除缓存，若想零留存把 `delete_after_send` 设为 `true`。
发送失败时自动降级为「标题 + 链接」文本（`fallback_to_text`）。

> 平台限制（OneBot）：适配器会把本地视频整体转 `base64://` 上行，大文件易超过适配器
> `request_timeout`。插件默认把超限视频自动压缩到 `max_send_mb` 内再直发（需 ffmpeg）；
> ffmpeg 缺失或压缩仍超限时回「标题+链接」文本，且所有失败路径绝不抛错。
> 若要调大直发体积：把 `max_send_mb`/`compress_max_height` 调大，并相应把 OneBot
> 适配器配置里的 `request_timeout` 调大（如 300s）。

## 配置

写入 ShinBot 配置文件插件块 `[plugins.shinbot_plugin_linkparser]`：

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 插件总开关（关闭后任何会话都不解析，指令仍可用） |
| `default_mode` | `"off"` | 未用 /parser 设置过的会话的档位：`off`/`at`/`always` |
| `parse_reply` | `false` | `always` 档下同时解析引用回复（quote）里的链接；`at` 档对 @消息引用内容的解析不依赖此项 |
| `fallback_to_text` | `true` | 视频发送失败时降级为「标题+链接」文本 |
| `prefer_mp4` | `true` | 优先单文件 mp4（HTML5，匿名最高约 1080P）；`false` 走 DASH+ffmpeg 合并 |
| `bilibili_cookie` | `""` | B站 SESSDATA（可选，DASH 高清晰度） |
| `max_quality` | `80` | DASH 路径最高清晰度（qn：16=360P … 80=1080P … 127=8K） |
| `max_duration_seconds` | `0` | 视频时长上限（秒），0=不限制 |
| `max_size_mb` | `200` | 下载体积上限 |
| `max_send_mb` | `50` | 直发视频体积上限（MB）：超限先用 ffmpeg 压缩到该体积内再直发 |
| `compress` | `true` | 允许自动压缩（超 max_send_mb 时），无需上传原画 |
| `compress_max_height` | `720` | 压缩输出最大高度 px（宽高比保持）；压缩不可用时才回标题+链接 |
| `debounce_seconds` | `300` | 同一会话同一链接/资源防抖窗口（秒），0=关闭 |
| `cache_max_files` | `50` | videos 缓存目录保留的 mp4 文件数上限，0=不清理 |
| `delete_after_send` | `false` | 发送成功后删除本地缓存文件（不再跨会话/重启复用） |
| `xiaohongshu_cookie` | `""` | 小红书网页 cookie（可选，绕过风控） |
| `xhs_max_images` | `9` | 图文笔记最多取前 N 张 |
| `xhs_image_mode` | `"raw"` | 图文发送方式：`raw`=逐张（默认）；`long`=拼一张长图（可选，待平台图文模板就绪后再考虑） |
| `xhs_stitch_max_height` | `12000` | 长图最大高度 px（超限等比缩小） |
| `xhs_video_max_height` | `720` | 小红书视频下载最大分辨率（优先直链 mp4，避免大体积原片） |
| `x_backend` | `"auto"` | X 数据源：`auto`=官方 syndication 优先、fxtwitter 兜底；或 `syndication`/`fxtwitter` |
| `send_text` | `true` | 回复包含文字说明（X 推文正文 / 小红书作者·标题·正文） |
| `send_forward` | `true` | 「文字+媒体」折叠为一条聊天记录（合并转发，X 与小红书通用），失败自动降级 |
| `x_image_mode` | `"raw"` | X 多图：`raw`=逐张（默认）；`long`=拼长图（可选） |
| `x_video_max_height` | `720` | X 视频下载最大分辨率（避免拉 4K 原片） |

## 开发

```bash
uv sync                                   # 安装依赖（含 bilibili-api-python）
.venv/bin/python -m pytest tests/ -q      # 单测（无需 ShinBot 安装，无网络依赖）
.venv/bin/ruff check .
```

依赖需 ffmpeg 的场景：仅当 html5 单文件不可用或 `prefer_mp4=false` 时，才需要 `ffmpeg` 合并 DASH 音视频流。

## 发布 / 索引

设计、打包与索引纪律见 [DESIGN.md](./DESIGN.md) §7。
