# ShinBot LinkParser — 设计文档

> 状态：**v0.5.7**（198 项单测通过、ruff 干净）。平台：**Bilibili 视频** + **小红书图文/视频** + **X/Twitter 推文**。
> 策略：off/at/always 三档按会话设置；精确 matcher；超限视频 ffmpeg 压缩；图文默认拼长图；
> X 的「文字+媒体」默认折叠为一条聊天记录（合并转发）。真实端到端验证：B站（HTML5/DASH/压缩/卡片）、
> X（官方 syndication + fxtwitter 兜底、图片与 mp4 下载）、小红书（HTML 解析/图片/长图/HLS）。

---

## 1. 背景与目标

识别分享内容并回复可消费媒体。参考接口：AstrBot [astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)。
按 ShinBot **Python 主分支契约**实现"档位 → 触发 → 解析 → 消费回复"模型。

### 1.1 范围与行为（已与需求方确认）

- **Bilibili 视频**：`BV`/`av`、`bilibili.com/video?p=N`、`b23.tv`、`bmBV...`、QQ 分享卡片 → 视频直发。
- **小红书**：`xiaohongshu.com/(explore|discovery/item)/<id>`（含 `xsec_token`）、`xhslink.com/.cn` 短链、
  QQ 分享卡片 → **与 X 相同输出**：「作者/标题/正文 + 媒体」折叠成一条聊天记录；图文默认**逐张**（不拼图），
  视频优先直链 mp4 下载（HLS 才走 ffmpeg）并按需压缩。
- **X/Twitter**：`x.com`/`twitter.com`（含 `mobile.`、`/i/status`、`/i/web/status`、legacy `/statuses/`）、
  镜像站 `fxtwitter/vxtwitter/fixupx/fixvx/twittpr` 的 `/status/<id>` → 文字+媒体**折叠聊天记录**；
  纯文字推文回文字；多图逐张（默认不拼图）；视频选 ≤`x_video_max_height` 的 mp4 变体后按需压缩；
  **图片与视频混合 / 多视频按推文原顺序全部发送**（模型为保序 `XTweetInfo.media: list[XMedia]`）。
- **三档**（会话级 `/parser`，全局 `default_mode` 兜底，默认 `off`）：
  - `off`：不解析。
  - `at`：仅当消息 @本机器人；解析对象 = 消息自身 +（@消息引用回复时）被引消息内容。
  - `always`：总是解析；引用内容在 `parse_reply=true` 时一并解析。
- **消费语义**：精确 matcher 命中才消费；@+引用经 `message_logs` 核实有链接才匹配，普通提问不被吞。
- 视频超 `max_send_mb` 用 ffmpeg 压缩后直发（不传原画）；发送失败链路绝不抛错、可重试。

### 1.2 Roadmap 未做项

RenderKit 封面信息卡、B站扫码登录、i18n、更多平台、X 线程（thread）合并、小红书原图去水印调优。

---

## 2. 数据源

- **B站**：`bilibili-api-python`（`Video.get_info()`、`get_download_url`，WBI 签名内置）。实测：匿名
  `html5=True` → 单文件 mp4（最高约 1080P）为默认路径；DASH 匿名仅 480P 需 ffmpeg 合并；CDN 需 Referer。
- **小红书**：无开放 API；抓笔记页 SSR 的 `window.__INITIAL_STATE__=…</script>`（`undefined`→`null`）：
  explore（`note.noteDetailMap[id].note`）与 discovery（`noteData.data.noteData` + `normalNotePreloadData`）两种布局；
  图片 `imageList`、视频 `video.media.stream.{h265,h264,av1,h266}[]`（每项含 `masterUrl`/`format`/`size`/`width`/`height`）。
  **实测多为 `format=mp4` 直链**（非 HLS）：直接 HTTP 下载；仅 HLS 才走 ffmpeg。需浏览器 UA + Referer；风控时配 `xiaohongshu_cookie`。
- **X/Twitter**（两条免登录通道，实测本机可用）：
  - **官方 syndication** `cdn.syndication.twimg.com/tweet-result?id=<id>&lang=en&token=…`：返回
    `text`/`user{name,screen_name}`/`created_at`/`possibly_sensitive`/`mediaDetails[]`；视频含 `video_info.variants`
    （HLS + 多档 **直链 mp4** 480P–4K，URL 内含 `/1280x720/` 尺寸）与 `duration_millis`。
  - **fxtwitter** `api.fxtwitter.com/i/status/<id>`：`tweet.author/text/possibly_sensitive/media.photos|videos`，
    视频给直链 mp4 + `duration` + `thumbnail_url`；作为官方接口限流/失败时的兜底。
  - vxtwitter 被 Cloudflare 拦截（实测 403），故不采用；oEmbed 仅文本，未采用。

## 3. 架构（模块）

```text
shinbot_plugin_linkparser/
├── __init__.py      # setup：三 client（bili/xhs/x）+ state/debouncer/matcher + 路由/指令；泛化发送（含折叠转发）
├── models.py        # LinkCandidate(platform/kind/bvid/note_id/note_url/status_id)、VideoMeta、XHSNoteInfo、XTweetInfo、*Outcome
├── urls.py          # B站/卡片、xhslink/xiaohongshu、x/twitter/镜像 status 扫描；ark 双重解码挖链；supported 收集器；quote 工具
├── parse_policy.py  # 三档判定 + 可见@检测 + quote 解析器 + message_logs 引用读取（纯逻辑）
├── matcher.py       # NORMAL matcher：mode + 精确引用核实（DB 同步）
├── session_state.py # 会话档位持久化（default_mode；旧格式迁移）
├── debounce.py      # 会话级防抖
├── parsers.py       # parse_video(bili) / parse_xhs_note(xhs) / parse_x_post(x) 编排 + 压缩 + 缓存
├── imagestitch.py   # 共享长图拼接（Pillow）：等比限宽 + 总高上限整体缩放
├── ffmpeg_media.py  # 共享 HLS→mp4（ffmpeg -headers -c copy）
├── bilibili/        # client（bilibili-api 封装）/ download（httpx + 合并 + 压缩 + ffprobe）
├── xiaohongshu/     # client（页面解析/短链/错误）/ media（图 + HLS）/ stitch（兼容 shim）
└── twitter/         # client（syndication + fxtwitter + 变体选择）/ media（图 + mp4/HLS）
```

判定链路：matcher（同 policy）命中 → handler 取 `parse_candidates_for` 首个 candidate →
按 `platform` 分发解析 → 产出本地文件 → 发送（X 优先折叠聊天记录）→ 失败 `_safe_send` 文本兜底、
绝不外抛；成功记资源级防抖，`delete_after_send` 可选即删。

## 4. 关键决策

- **账号标签**：`author_label` 读取 syndication `user.verified_type` 与 fxtwitter `author.verification.type`，
  映射为短标签（政府/商业；普通蓝标不显示），写进 caption 与 fallback 文案的作者行。
- **敏感内容标记**：两个后端都返回 `possibly_sensitive`（实测字段存在），解析进 `XTweetInfo.sensitive`；
  按 `x_sensitive_policy` 处理：`allow`（默认）照常、`text` 只回文字并追加提示、`skip` 整条不回复（仅日志）。
  ⚠ X 的 UI 敏感分类（发帖 flag：**Nudity / Violence / Sensitive**，见 help.x.com/en/rules-and-policies/media-settings）
  是**按媒体存在内部 GraphQL 数据**里的；我们用的公开接口（syndication / fxtwitter / API v2）只有推文级布尔
  `possibly_sensitive`（API v2 数据字典亦仅此字段）。要读分类需另找通道（登录态 GraphQL / 页内嵌 Relay 数据），
  待真实敏感推文样本验证后再决定是否实现；
  可读的其它标注：媒体 `ext_media_availability`（已用于跳过被限制媒体并给出准确原因）、
  媒体 `ext_alt_text`、用户 `verified_type`/`is_blue_verified`、fxtwitter `community_note`（社区笔记，非敏感类型）、
  推文 `withheld_in_countries`（地区屏蔽，出现时才有）。
- **保序媒体列表**：`XTweetInfo.media` 按推文顺序保存每个附件（photo/video/gif）；`parse_x_post` 逐条下载并产出
  `MediaItem` 列表（`kind` = video/images/mixed/text），不再"有视频就丢图"；多视频各自选档、各自压缩。
  折叠记录里一条媒体一个 node；非折叠降级时先发视频消息再发图片消息。
- **统一「文字+媒体」折叠**：小红书与 X 共用一条发送路径——caption 由各自 info 提供
  （`XTweetInfo.caption` / `XHSNoteInfo.caption`），`send_text`/`send_forward` 开关与折叠/降级逻辑平台无关；
  仅 B站保持"只回视频"（无 caption）。命名从 `x_send_text`/`x_send_forward` 去平台化为 `send_text`/`send_forward`。
- **X 折叠聊天记录**：用 ShinBot `MessageElement.forward(nodes)`（type=message、`forward=true`）→ OneBot
  `send_group_forward_msg`/`send_private_forward_msg`；节点内可放 text/img/video。优先「文字节点 + 媒体节点」一条卡片；
  适配器不支持（非 OneBot 类）或发送失败 → 降级「文字一条 + 媒体一条」；纯文字推文直接回文字。
- **小红书不拉原画 + 不进 ffmpeg**：`pick_video_variant` 优先 `format=mp4` 且高度 ≤`xhs_video_max_height` 的直链
  （全部超限取最小档），直接 httpx 下载；HLS 才交 ffmpeg。修复了"直链 mp4 被当 HLS 交给 ffmpeg、且输出名 `*.part`
  导致无法推断容器"的失败（`ffmpeg_media` 现显式 `-f mp4`）。
- **X 不拉原画**：`pick_video_variant` 优先「≤`x_video_max_height` 的最高档 mp4」，全部超限则取最小档（省流量），
  无 mp4 才用 HLS（ffmpeg）；仍超 `max_send_mb` 再压缩。图片统一取 `?name=large`（原图 5568px 无必要）。
- **不拼图（默认）**：`xhs_image_mode`/`x_image_mode` 默认 `raw` —— 逐张发送，保留原图与"图文混排"的可能性；
  折叠记录里**每张图一个 node**（相册式，单条 payload 更小）。Pillow 拼接（`long` + `*_stitch_max_height`）
  实现保留为可选项，待各平台图文模板能混排后再评估默认开启。
- **压缩**：`max_send_mb`（默认 50MB）为直发目标；B站用 meta 时长、xhs/x 用 ffprobe 或接口时长反推码率
  （libx264+AAC、`compress_max_height` 限高）。ffmpeg 缺失 → 链接文本 + 提示。
- **引用解析**：OneBot `reply` 只带 id → `message_logs.get_by_platform_msg_id` 取 `content_json`
  （MessageElement AST 数组）→ 复用 supported 扫描（文本/卡片/bili/xhs/x 通吃）。
- **卡片**：OneBot/QQ 官方 `json/miniapp/ark` → `sb:ark`；适配器双重编码 + `\/` 转义 → 重复解码 + 字段 + 递归兜底。

## 5. 配置与指令（定稿）

```python
enabled=True; default_mode="off"; parse_reply=False; fallback_to_text=True
bilibili_cookie=""; prefer_mp4=True; max_quality=80
max_duration_seconds=0; max_size_mb=200
max_send_mb=50; compress=True; compress_max_height=720
debounce_seconds=300; cache_max_files=50; delete_after_send=False
xiaohongshu_cookie=""; xhs_max_images=9
xhs_image_mode="raw"; xhs_stitch_max_height=12000; xhs_video_max_height=720
send_text=True; send_forward=True; x_backend="auto"
x_image_mode="raw"; x_video_max_height=720
x_sensitive_policy="allow"        # allow | text | skip
```

指令 `/parser off|at|always|status`（`on`=always；权限 `cmd.linkparser`，admin/owner 默认）。

## 6. 打包 / 发布 / 测试

- 仓库 `NekyuuYa/shinbot_plugin_linkparser` + 市场索引 `NekyuuYa/shinbot-plugins`；发版纪律：
  ruff+pytest → bump metadata/pyproject → 推送插件 → 索引 "Bump … to x.y.z"。
- 依赖：bilibili-api-python、httpx、pydantic、**pillow**；ffmpeg/ffprobe 运行期需要（压缩/HLS/合并）。
- 测试 198 项全离线（X 用 `httpx.MockTransport`）：urls（bili/xhs/x/卡片/边界）、policy/matcher（三档+@+引用+DB）、
  session_state、debounce、parsers/handler（分发/超时/断连/超限/长图/X 折叠与降级/纯文字）、bilibili client、
  download/compress（真实 ffmpeg）、xiaohongshu（扫描/HTML/stitch）、twitter（扫描/双通道解析/选档/后端降级/下载）、
  plugin_entry、packaging；XHS 选档/直链下载/ffmpeg `-f mp4` 回归；XHS 与 X 一致的
  caption+媒体折叠（含降级、caption 可关、视频/图文/混合三种 kind 与保序）；
  `possibly_sensitive` 解析与三种策略（allow/text/skip）、媒体可用性（withheld）跳过与准确报错、
  账号标签映射与 caption 呈现。
  实测端到端：真实小红书视频笔记（720P 直链 18.5MB）下载成功，压缩到 5MB 用时 5s。

## 7. Roadmap

- **已实现（0.5.x）**：B站视频、小红书图文(长图)/视频、X 推文(折叠聊天记录/长图/视频)、三档会话策略、
  精确 matcher、压缩、防抖、卡片、引用解析、失败链路加固、真实接口端到端验证。
- **Next**：各平台图文混排模板（就绪后再评估拼长图默认值）、RenderKit 信息卡、扫码登录态（B站）、i18n、
  更多平台、X 线程合并、图文原图与去水印策略。
