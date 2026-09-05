# ShinBot LinkParser — 设计文档

> 状态：**v0.4.0**（136 项单测通过、ruff 干净）。平台：**Bilibili 视频** + **小红书图文/视频**。
> 策略：off/at/always 三档按会话设置；精确 matcher；超限视频 ffmpeg 压缩；图文默认拼长图。
> 真实端到端验证过：B站 HTML5/DASH 下载、压缩、QQ 分享卡片（双重编码）→ BV 解析。

---

## 1. 背景与目标

识别分享内容并回复可消费媒体。参考接口：AstrBot [astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)。
按 ShinBot **Python 主分支契约**实现"档位 → 触发 → 解析 → 消费回复"模型。

### 1.1 范围与行为（已与需求方确认）

- **Bilibili 视频**：`BV`/`av`、`bilibili.com/video?p=N`、`b23.tv`、`bmBV...`、QQ 分享卡片 → 视频直发。
- **小红书**：`xiaohongshu.com/(explore|discovery/item)/<id>`（含 `xsec_token`）、`xhslink.com/.cn`
  短链、QQ 分享卡片 → 图文笔记默认**拼接一张长图**直发（可逐张），视频笔记 HLS 下载直发。
- **三档**（会话级 `/parser`，全局 `default_mode` 兜底，默认 `off`）：
  - `off`：不解析。
  - `at`：仅当消息 @本机器人；解析对象 = 消息自身 +（@消息引用回复时）被引消息内容。
  - `always`：总是解析；引用内容在 `parse_reply=true` 时一并解析。
- **消费语义**：精确 matcher 命中才消费；@+引用经 `message_logs` 核实有链接才匹配，普通提问不被吞。
- 视频超 `max_send_mb` 用 ffmpeg 压缩后直发（不传原画）；发送失败链路绝不抛错、可重试。

### 1.2 Roadmap 未做项

RenderKit 封面信息卡、B站扫码登录、i18n、更多平台、图文"原图无水印"细节调优。

---

## 2. 数据源

- **B站**：`bilibili-api-python`（`Video.get_info()`、`get_download_url`，WBI 签名内置）。实测：匿名
  `html5=True` → 单文件 mp4（最高约 1080P）为默认路径；DASH 匿名仅 480P 需 ffmpeg 合并；CDN 需 Referer。
- **小红书**：无开放 API；抓笔记页 SSR 的 `window.__INITIAL_STATE__=…</script>`（`undefined`→`null`）：
  - explore 布局：`note.noteDetailMap[id].note` → type/title/desc/user/imageList[].urlDefault、
    `video.media.stream.h265|h264|av1|h266[0].masterUrl`（HLS）。
  - discovery 布局：`noteData.data.noteData`（图含水印）+ `normalNotePreloadData`（干净封面）。
  - 需浏览器 UA + HTML Accept + Referer；风控时配 `xiaohongshu_cookie`（a1/web_session 等）重试。
  - 视频 HLS 用 ffmpeg `-headers`（带 Referer/UA/cookie）取流 `-c copy` 合并。

## 3. 架构（模块）

```text
shinbot_plugin_linkparser/
├── __init__.py      # setup：双 client（bili/xhs）+ state/debouncer/matcher + 路由/指令；泛化发送
├── models.py        # LinkCandidate(platform/kind/bvid/note_id/note_url…)、VideoMeta、XHSNoteInfo、outcomes
├── urls.py          # B站+卡片、xhslink/xiaohongshu 扫描、ark 双重解码挖链、supported 收集器、quote 工具
├── parse_policy.py  # 三档判定 + 可见@检测 + quote 解析器 + message_logs 引用读取（纯逻辑）
├── matcher.py       # NORMAL matcher：mode + 精确引用核实（DB 同步）
├── session_state.py # 会话档位持久化（default_mode；旧格式迁移）
├── debounce.py      # 会话级防抖
├── parsers.py       # parse_video(bili) / parse_xhs_note(xhs) 编排 + 压缩 + 缓存
├── bilibili/        # client（bilibili-api 封装）/ download（httpx+ffmpeg 合并+压缩+ffprobe）
└── xiaohongshu/     # client（页面解析/短链/错误）/ media（图+HLS）/ stitch（Pillow 长图）
```

判定链路：matcher（同 policy）命中 → handler 取 `parse_candidates_for` 首个 candidate →
按 `platform` 分发解析 → 产出本地文件 → 按 kind（video 单文件 / images 多图或长图）组 MessageElement 发送；
失败均走 `_safe_send` 文本兜底、绝不外抛；成功记资源级防抖，`delete_after_send` 可选即删。

## 4. 关键决策

- **小红书长图**：`xhs_image_mode=long`（默认）用 Pillow 等比缩放逐图拼接（限 `xhs_stitch_max_height`，
  超限整体缩小）→ 单图直发；Pillow 缺失或 `raw` 才逐张。省消息资源、防刷屏。
- **压缩**：`max_send_mb`（默认 50MB）为直发目标；B站用 meta 时长、xhs 用 ffprobe 时长反推码率
  （libx264+AAC、`compress_max_height` 限高）。ffmpeg 缺失 → 链接文本 + 提示。
- **引用解析**：OneBot `reply` 只带 id → `message_logs.get_by_platform_msg_id` 取 `content_json`
  （MessageElement AST 数组）→ 复用 supported 扫描（文本/卡片/bili/xhs 通吃）。
- **卡片**：OneBot/QQ 官方 `json/miniapp/ark` → `sb:ark`；适配器双重编码 + `\/` 转义 → 重复解码 + 字段 + 递归兜底。

## 5. 配置与指令（定稿）

```python
enabled=True; default_mode="off"; parse_reply=False; fallback_to_text=True
bilibili_cookie=""; prefer_mp4=True; max_quality=80
max_duration_seconds=0; max_size_mb=200
max_send_mb=50; compress=True; compress_max_height=720
debounce_seconds=300; cache_max_files=50; delete_after_send=False
xiaohongshu_cookie=""; xhs_max_images=9
xhs_image_mode="long"; xhs_stitch_max_height=12000
```

指令 `/parser off|at|always|status`（`on`=always；权限 `cmd.linkparser`，admin/owner 默认）。

## 6. 打包 / 发布 / 测试

- 仓库 `NekyuuYa/shinbot_plugin_linkparser` + 市场索引 `NekyuuYa/shinbot-plugins`；发版纪律：
  ruff+pytest → bump metadata/pyproject → 推送插件 → 索引 "Bump … to x.y.z"。
- 依赖：bilibili-api-python、httpx、pydantic、**pillow**（长图）；ffmpeg/ffprobe 运行期需要（压缩/HLS/合并）。
- 测试 136 项全离线：urls（bili/xhs/卡片/边界）、policy/matcher（三档+@+引用+DB）、session_state、
  debounce、parsers/handler（分发/超时/断连/超限/长图 kind）、bilibili client、download/compress
  （真实 ffmpeg）、xiaohongshu（扫描/HTML 布局解析/stitch）、plugin_entry、packaging。

## 7. Roadmap

- **已实现（0.4.x）**：B站视频 + 小红书图文(长图)/视频、三档会话策略、精确 matcher、压缩、防抖、卡片、
  引用解析、失败链路加固、真实卡片回归。
- **Next**：RenderKit 信息卡、扫码登录态（B站）、i18n、多平台、图文原图与去水印策略调优。
