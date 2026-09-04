# ShinBot LinkParser — 设计文档

> 状态：**M1 已实现**（B站视频解析 v1）。65 项单测通过、ruff 干净；
> 已用真实 B站视频完成端到端验证（HTML5 单文件 mp4 与 DASH+ffmpeg 两条下载路径）。
> 本文件是设计与后续迭代（M2/M3）的依据。

---

## 1. 背景与目标

ShinBot 群聊中用户分享链接时，默认只发送纯文本 URL；部分平台（QQ/OneBot 环境）
不会自动展开为可预览内容。本插件**识别消息中的可解析链接 / 分享卡片，
解析后直接回复可播放/可消费的内容本体**。

参考接口：AstrBot 生态的 [astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)（万能解析器）。
本插件面向 ShinBot 原生插件体系（**Python 主分支契约**，见 §4.1）实现其"消息触发 + 解析 + 消费回复"模型。

### 1.1 v1 范围（已与需求方确认）

- **只解析 Bilibili（B站）视频**：`BV`/`av` 号、`bilibili.com/video/...?p=N`、`b23.tv` 短链、
  `bmBV...`（QQ 卡片常见前缀）与 **QQ 分享卡片**（`sb:ark`）内嵌链接；分 P 参数生效。
  **动态/专栏/直播/歌单一律不做**。
- **只回复视频本体**（下载为本地 mp4 后以视频消息回复），首版不做封面卡片/图文混排。
- **消费语义**：命中即被 NORMAL 路由消费（阻止 Agent fallback 再回复），
  "哪些消息消费"由场景配置控制（`parse_on_mention`/`parse_reply`/`enabled`）。

### 1.2 不在 v1 范围（Roadmap）

- 其他平台解析器（抖音/小红书/微博/X/YouTube/知乎/网易云…）
- 封面卡片渲染（届时引入 `shinbot_plugin_renderkit` 或自绘）
- B站扫码登录态管理、会话级 `/parser on|off` 开关与黑名单持久化

---

## 2. 参考实现接口分析：astrbot_plugin_parser

仓库：https://github.com/Zhalslar/astrbot_plugin_parser（v1.5.7，分析用克隆已清理）。

### 2.1 消息处理模型（`main.py`）

- 每次消息都处理：白/黑名单 → 从消息链取文本；含 `Json` 段（分享卡片）时用
  `extract_json_url()` 挖跳转 URL；可选 `Reply` 引用解析 → "关键词 + 正则"双层匹配
  （所有平台解析器 URL 正则汇总一张表，长关键词优先）→ 防抖/仲裁 → `parse()` →
  `MessageSender` 按发送计划回复。
- 管理命令：`开启解析`/`关闭解析`（会话级，ADMIN）、`登录B站`（扫码）。

### 2.2 解析器扩展模型（`core/parsers/base.py`）

- 每平台一个 `BaseParser` 子类，`__init_subclass__` 自动注册；
- `@handle(keyword, regex_pattern)` 标注"关键词 + URL 正则"，按关键词长度降序聚合；
- 每 parser 持自己的 aiohttp session/headers/proxy，卸载统一关闭。

### 2.3 B站视频实现要点（`core/parsers/bilibili/`）

- 匹配 `BV`/`av`/`b23.tv`/`bmBV`；数据源 `bilibili-api-python`
  （`Video.get_info()` 元数据、playurl 播放流）；下载用自研下载器，DASH 用 ffmpeg 合并；cookie 支持高清。

### 2.4 移植结论

| 机制 | 在 ShinBot 的实现 |
|---|---|
| 关键词+正则 → 解析器分发 | `urls.find_bilibili_candidates` + `parsers.parse_video`（v1 仅 B站；接口为多平台预留） |
| Json 卡片挖 URL | `urls.ark_extract_url`：`sb:ark` 元素 attrs.data 的 `meta.miniapp/detail_1/news` 等路径 |
| 引用回复解析 | `iter_element_texts(include_quote=config.parse_reply)`（v1 尽力而为：仅扫描 quote 子树文本） |
| 链接/资源防抖 | `debounce.Debouncer`（会话级、内存、TTL 惰性清理） |
| 统一 ParseResult + 发送计划 | `models.ParseOutcome`（本地 mp4 路径 + `VideoMeta`）→ 直接发 `MessageElement.video` |
| 卸载清理 | `on_disable` 关闭共享 `BilibiliClient`（httpx 会话） |

---

## 3. v1 行为规格（B站视频）

### 3.1 输入（消息形态）

1. **纯文本 URL / 裸号**：`bilibili.com/video/BV…|av…`（含 `?p=N`）、`BV…`/`av…`、
   `b23.tv/…`（→ 302 展开后取 BV）、`bmBV…`；行尾 `BV… <N>` 页号后缀（QQ 卡片形态）。
2. **QQ 分享卡片**：OneBot `json`/`xml`/`miniapp` 段 → ShinBot `sb:ark`
   （`MessageElement(type="sb:ark", attrs={"data": "<json>"})`，见
   `shinbot/builtin_plugins/shinbot_adapter_onebot_v11/adapter.py` 与 `..._qqofficial`），
   从 JSON 挖跳转 URL。
3. **引用回复**：`quote` 子树（OneBot `reply` 段通常不带内容，v1 仅对携带子内容的 quote 生效；完整引用拉取留 M2）。

### 3.2 处理流程

```
消息进入（message-created）
  └─ matcher（同步、轻量）：元素扫描（文本 + sb:ark；quote 按配置）
  └─ 命中 → NORMAL 路由消费（agent fallback 不再触发）
  └─ handler：collect candidates（文本/BV/av/b23/卡片，去重）
  └─ 会话防抖：链接级命中即跳过（失败则 forget 允许重试）
  └─ parse_video：b23 展开 → fetch_meta（时长上限校验）→ resolve_stream
  └─ download：HTML5 单文件 mp4（默认）或 DASH 双流 + ffmpeg 合并（体积上限）
  └─ 回复：MessageElement.video(本地 mp4)；发送失败降级「标题+链接」文本
  └─ 成功后记资源级防抖（BV:pN）
```

### 3.3 消费语义（ShinBot 路由层，重要）

`shinbot/core/dispatch/routing.py::RouteTable.match` 语义：

- `OBSERVE` 永远伴随执行（被动监听）。
- 有 `EXCLUSIVE` 命中 → 只派发最高优先级那条 EXCLUSIVE + observers；**会挡住其他插件 NORMAL 规则**。
- 有 `NORMAL` 命中 → 所有命中的 NORMAL 规则都派发；**FALLBACK（`agent_entry`）不再触发**。
- `FALLBACK` 只在无 NORMAL/EXCLUSIVE 命中时兜底给 Agent。

**实现结论：注册 `RouteMatchMode.NORMAL` + custom matcher**。消费（阻止 agent 双回复）是
NORMAL 路由的固有结果，不存在"回复但不消费"的中间态；因此**没有 `consume_message` 配置**，
"何时消费"由 matcher 的场景开关决定：

- `enabled`：总开关。
- `parse_on_mention=False`：@机器人 的消息不匹配 → 留给 Agent（默认 True 仍解析）。
- `parse_reply`：是否把 `quote` 子树纳入扫描。
- 预留：@了**别的机器人**时跳过（对齐 astrbot 防多机器人抢答；单机器人部署不生效）。

### 3.4 触发注册

```python
@plg.on_route(
    RouteCondition(event_types=frozenset({"message-created"}), custom_matcher=matcher),
    rule_id="shinbot_plugin_linkparser.parse",
    priority=80,
    match_mode=RouteMatchMode.NORMAL,
)
async def handler(context: RouteDispatchContext, rule: RouteRule) -> None:
    ctx = context.require_message_context()
    ...
```

matcher 在 `RouteTable.match` 同步执行，只做轻量元素/正则/JSON 扫描（无网络）；真实解析在 handler 异步完成。

---

## 4. ShinBot 框架对齐（Python 主分支）

### 4.1 分支说明（防呆）

本机 `ShinBot` checkout 当前在 `experiments/cordis-poc`（TS + Cordis 重写实验），
但 **`shinbot/` 目录 Python 代码与 `master` 逐字节一致**（`git diff master HEAD -- shinbot/` 为空），
`docs/architecture/framework_plugin_contract.md` 亦声明 "master 未改动"。
本插件一切 API 锚点均以 **master（Python）契约为准**，不参考 cordis-poc 的 TS 插件模型。

### 4.2 已核实的 API 锚点（实现已用）

| 锚点 | 位置 |
|---|---|
| 插件入口 `setup(plg: Plugin)` | `shinbot/core/plugins/context.py` |
| 模块元数据 `__plugin_name__/__plugin_description__/__plugin_config_class__` | `context.py` 读取约定 |
| `plg.on_route(cond, rule_id=…, priority=…, match_mode=…)` | `context.py::Plugin.on_route` |
| `RouteCondition(event_types, …, custom_matcher)` | `shinbot/core/dispatch/routing.py` |
| 路由 handler 签名 `(RouteDispatchContext, RouteRule)`、`require_message_context()` | `shinbot/core/dispatch/ingress.py` |
| `MessageContext`：`.session_id` `.message.elements` `.send()` | `shinbot/core/dispatch/message_context.py` |
| 元素 AST `MessageElement`（text/at/img/video/quote/`sb:ark`…） | `shinbot/schema/elements.py`（`EXTENSION_TAGS`） |
| 配置加载 `plugin_config_block(payload, plugin_id)` | `shinbot/core/plugins/config.py`；`DEFAULT_CONFIG_PATH` → `shinbot/core/application/paths.py` |
| 卸载 `on_disable` | 参考 renderkit/minesweeper 约定 |
| 数据目录 `plg.data_dir`；`plg.logger` | `context.py` |

### 4.3 发送视频（已核实 + 平台限制）

- OneBot v11 出站：`MessageElement(type="video")` → `{"type":"video","data":{"file": …}}`
  （`shinbot/builtin_plugins/shinbot_adapter_onebot_v11/adapter.py`）。
- **限制**：该适配器把存在的本地文件整体转 `base64://` 上行（`_format_ob11_file_src`），
  超大视频受平台消息大小限制且内存开销大 → 用 `max_size_mb` 控制体积；
  发送失败由 `fallback_to_text` 降级为「标题+链接」文本。Satori 适配器出站支持 `video`（本地文件走上传）。

### 4.4 卡片（sb:ark）入站样例（OneBot）

```json
{"type": "json", "data": {"data": "{\"app\":\"…\",\"meta\":{\"detail_1\":{\"qqdocurl\":\"https://www.bilibili.com/video/BV1xx…\"}}}"}}
```
→ `MessageElement(type="sb:ark", attrs={"data": "<上面的 data>"})`。挖 URL 字段优先级参考 astrbot
`extract_json_url`：`meta.miniapp.legacyUrl/pcJumpUrl/jumpUrl/sourceUrl/url`、
`meta.detail_1.qqdocurl/jumpUrl/url/shareUrl/targetUrl`、`meta.news.*` 等。

---

## 5. 模块结构（现状）

```text
shinbot_plugin_linkparser/
├── DESIGN.md / README.md / metadata.json / pyproject.toml / .gitignore
├── shinbot_plugin_linkparser/
│   ├── __init__.py          # setup：配置/共享 BilibiliClient/Debouncer/NORMAL 路由注册；on_disable
│   ├── models.py            # LinkCandidate / VideoMeta / SingleFilePlan / DashPlan / ParseOutcome（纯 dataclass）
│   ├── urls.py              # 纯逻辑：BV/av/b23/bm 提取、?p=N、ark 挖链、元素扫描/候选收集
│   ├── matcher.py           # build_link_matcher（同步轻量）、提及检测
│   ├── debounce.py          # 会话级防抖（内存 TTL）
│   ├── parsers.py           # parse_video 编排：b23→meta→guard→plan→下载；错误分类
│   └── bilibili/
│       ├── __init__.py
│       ├── client.py        # bilibili-api-python 封装：fetch_meta / resolve_stream / resolve_short_url
│       └── download.py      # httpx 流式下载（Referer/UA/体积上限/断点清理）+ ffmpeg DASH 合并
└── tests/                   # conftest(shinbot 桩) + packaging/urls/matcher/debounce/parsers/client/download
```

### 5.1 多平台预留

v1 没有独立的 Parser 基类注册表（避免过度设计）：URL 扫描集中在 `urls`，
下载编排在 `parsers.parse_video`，平台判断目前隐含为 bilibili。M3 增加平台时再把
`parse_video` 泛化为按 `LinkCandidate.platform` 分发的注册表即可。

### 5.2 B站数据源（方案 B 已实现 + 实测结论）

- **封装**：`bilibili-api-python`（pin `>=17.4.1,<18.0.0`，与 astrbot 对齐）负责元数据
  （`Video.get_info()`）与播放流（`get_download_url`，自带 WBI 签名）；实际字节下载用 httpx。
- **实测（匿名）**：
  - `get_download_url(html5=True)` → 单文件 muxed mp4（`durl`），**匿名可达 1080P**（quality 80），免 ffmpeg/登录 —— 默认路径；
  - DASH（`fnval=4048`）匿名仅到 480P，需 ffmpeg 合并；带 `SESSDATA` cookie 可更高；
  - CDN 下载必须带 `Referer: https://www.bilibili.com/` + 浏览器 UA（已实现）。
- **策略**：`prefer_mp4=true`（默认）先试 HTML5；失败或 `prefer_mp4=false` 走 DASH
  （AVC、按 `max_quality` 限制、AAC 音轨）→ `ffmpeg -c copy -f mp4` 合并。
  无 ffmpeg 且无 html5 可用时报错并给出提示。

---

## 6. 配置（已定稿，代码即文档）

```python
class LinkParserPluginConfig(BaseModel):
    enabled: bool = True
    parse_on_mention: bool = True      # false → @机器人 消息不消费，留给 Agent
    parse_reply: bool = False          # 是否扫描 quote 子树
    fallback_to_text: bool = True      # 视频发送失败 → 标题+链接文本
    bilibili_cookie: str = ""          # SESSDATA（可选，DASH 高清晰度）
    prefer_mp4: bool = True            # 优先 HTML5 单文件 mp4
    max_quality: int = 80              # DASH 路径 qn 上限（16..127）
    max_duration_seconds: int = 0      # 时长上限（0=不限）
    max_size_mb: int = 200             # 下载体积上限
    debounce_seconds: int = 300        # 会话防抖窗口（0=关闭）
    cache_max_files: int = 50          # videos 缓存保留 mp4 数（0=不清理）
```

写入 ShinBot 配置 `[plugins.shinbot_plugin_linkparser]`。
已下载文件按 `bv_pN.mp4` 缓存复用（重启后免重复下载）；setup 与每次解析后按 `cache_max_files` 修剪。
会话级 `/parser on|off` 与黑名单持久化、`__plugin_locales__` i18n 留 M2。

---

## 7. 打包 / 上传 / 索引（已执行 + 纪律）

- **已执行**：repo-first 建仓（`NekyuuYa/shinbot_plugin_linkparser`，public，main）并推送；
  在 `shinbot-plugins` 写入 `plugins.json` 条目 + README 清单并推送（version 0.1.0）。
- 每次发版纪律：实现/修复 → ruff + pytest → 同步 `metadata.json`/`pyproject.toml` version →
  推送插件仓库 → `shinbot-plugins` 单独提交 "Bump LinkParser marketplace version to x.y.z" 推送 main。

## 8. 测试

- 65 项单测全部离线（无 ShinBot 安装、无网络）：`urls`（候选提取/边界/去重/ark）、
  `matcher`（场景开关/提及/quote）、`debounce`、`parsers`（FakeClient：b23 展开/时长上限/产物/
  **缓存复用**/**目录修剪**）、`bilibili client`（html5/DASH 计划选择、错误码翻译，使用真实
  `VideoDownloadURLDataDetecter`）、`download`（ffmpeg 缺失分支）、`packaging`、
  `plugin_entry`（fake-Plugin 验证 setup 装配：NORMAL 路由注册 + matcher 行为 + 启动修剪）。
- 端到端已验证（开发期手动）：匿名 HTML5 下载 33s/8.8MB 视频成功（ftyp 校验）；
  DASH 480P + ffmpeg 合并成功（ffprobe 检出 h264+aac 双流，时长 32s）。
- 运行：`uv sync` 后 `.venv/bin/python -m pytest tests/ -q`；`uv run` 环境缺失时可 `uvx`。

## 9. Roadmap

- **M1（已实现）**：B站视频解析 + 下载（mp4 优先 / DASH 兜底）+ `video` 回复 + 消费开关（§3）。
- **M2**：引用回复完整解析（拉取被引消息）、`/parser on|off` 会话开关 + 黑名单、
  `__plugin_locales__`、可选 RenderKit 封面/信息卡片、B站扫码登录态。
- **M3**：多平台解析器注册表（抖音/小红书/微博/X/YouTube…）。
- 各阶段对齐 §7 发布/索引纪律。
