# LinkParser (shinbot_plugin_linkparser)

ShinBot 插件：解析 **Bilibili 视频**（链接 / `BV`/`av` 号 / `b23.tv` 短链 / QQ 分享卡片，含 `?p=N` 分 P），
下载为可播放 mp4 后**以视频消息回复**。

**默认不解析**。解析档位按会话设置（三档），状态持久化：

| 档位 | 行为 |
|---|---|
| `off` | 不解析（默认） |
| `at` | **仅当消息 @了本机器人** 时解析：查消息自身文本里的链接；若该 @ 消息是引用回复，则解析其**引用的消息内容**（经 message_logs 精确判定，无链接的 @ 提问不会被吞） |
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

机器人解析并回复可播放的视频消息。同一会话短时间重复链接不重复解析；
已下载视频按 `bv号_pN.mp4` 缓存复用（重启后不再重新下载），目录按 `cache_max_files` 自动清理；
发送成功后不删除缓存，若想零留存把 `delete_after_send` 设为 `true`。
视频消息发送失败时自动降级为「标题 + 链接」文本（`fallback_to_text`）。

> 平台限制：OneBot 适配器以 base64 上行本地视频文件，超大视频受平台消息大小限制；
> 发送失败即走文本兜底。可用 `max_size_mb` 控制下载体积。

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
| `debounce_seconds` | `300` | 同一会话同一链接/资源防抖窗口（秒），0=关闭 |
| `cache_max_files` | `50` | videos 缓存目录保留的 mp4 文件数上限，0=不清理 |
| `delete_after_send` | `false` | 发送成功后删除本地缓存文件（不再跨会话/重启复用） |

## 开发

```bash
uv sync                                   # 安装依赖（含 bilibili-api-python）
.venv/bin/python -m pytest tests/ -q      # 单测（无需 ShinBot 安装，无网络依赖）
.venv/bin/ruff check .
```

依赖需 ffmpeg 的场景：仅当 html5 单文件不可用或 `prefer_mp4=false` 时，才需要 `ffmpeg` 合并 DASH 音视频流。

## 发布 / 索引

设计、打包与索引纪律见 [DESIGN.md](./DESIGN.md) §7。
