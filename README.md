# LinkParser (shinbot_plugin_linkparser)

ShinBot 插件：解析消息里的链接 / 分享卡片并回复**内容本体**。

**v1 范围（当前）**：只处理 **Bilibili 视频**链接（`BV`/`av`/`b23.tv`/分享卡片内嵌链接），
解析后下载视频并**以视频消息回复**；命中时默认**消费消息**（阻止 Agent 再回复），行为可配置。

> 状态：准备阶段（骨架已入库并上架市场索引）。解析逻辑见 [DESIGN.md](./DESIGN.md) 待实现；
> B站数据源采用方案 B（bilibili-api-python）。

## 安装

通过 ShinBot WebUI 插件市场安装（插件索引仓库：
[NekyuuYa/shinbot-plugins](https://github.com/NekyuuYa/shinbot-plugins)），
或手动把本仓库放到 `data/plugins/` 后启用：

```bash
# 从源码启用
curl -X POST http://localhost:3945/api/v1/plugins/rescan
curl -X POST http://localhost:3945/api/v1/plugins/shinbot_plugin_linkparser/enable
```

## 用法（v1 目标行为）

群友发一条 B站视频链接（或 QQ 分享卡片）：

```
https://www.bilibili.com/video/BV1xx411c7mD
```

机器人解析后回复该视频（可播放的视频消息）；同一会话短时间内重复链接不重复解析。

## 配置（草案）

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关 |
| `consume_message` | `true` | 命中链接时消费消息（阻止 agent fallback） |
| `parse_on_mention` | `true` | 消息同时 @机器人 时仍解析 |
| `bilibili_cookie` | `""` | SESSDATA（可选，更高清晰度） |
| `max_duration_seconds` | `600` | 视频时长上限（0=不限） |
| `max_size_mb` | `300` | 下载体积上限 |
| `prefer_mp4` | `true` | 优先 mp4 直链（免 ffmpeg 合并） |
| `debounce_seconds` | `300` | 同一会话同一链接防抖窗口 |

配置写入 ShinBot 配置文件插件块 `[plugins.shinbot_plugin_linkparser]`。

## 开发

```bash
uv run --group dev python -m pytest        # 测试（不需安装 ShinBot，见 tests/conftest.py 桩）
uv run --group dev ruff check .
```

## 发布 / 索引

设计、打包与索引纪律见 [DESIGN.md](./DESIGN.md) §7。
