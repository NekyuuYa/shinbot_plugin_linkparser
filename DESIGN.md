# ShinBot LinkParser — 设计文档

> 状态：**B站视频解析 v0.2.0**（89 项单测通过、ruff 干净；真实视频 HTML5/DASH 两条下载路径端到端验证过）。
> v0.2.0 行为：**默认不解析**，`/parser on|off|status` 按会话开关（持久化）；修复 QQ 分享卡片（`sb:ark`
> 双重编码）解析。本文件是设计与后续迭代的依据。

---

## 1. 背景与目标

ShinBot 群聊中用户分享链接/卡片时默认只是文本或无法预览的占位；本插件**识别可解析的 B站视频链接与
QQ 分享卡片，解析后回复可播放的视频本体**。

参考接口：AstrBot 生态的 [astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)。
本插件按 ShinBot **Python 主分支契约**（§4.1）实现其"指令开启 → 消息触发 → 解析 → 消费回复"模型。

### 1.1 范围与行为（已与需求方确认）

- **只处理 Bilibili 视频**：`BV`/`av`、`bilibili.com/video/...?p=N`、`b23.tv` 短链、`bmBV...` 前缀、
  **QQ 分享卡片**（`sb:ark` 内嵌链接，含双重编码）。动态/专栏/直播等不做。
- **只回复视频本体**；不做封面卡片/图文混排。
- **默认不解析**：只有对某会话执行 `/parser on` 后，该会话中的链接才被解析（`/parser off` 关闭，状态持久化）。
- **消费语义**：已开启解析的会话中，命中 NORMAL 路由即消费（阻止 Agent fallback 双回复）；
  未开启的会话消息不匹配、正常落入 Agent，插件零干扰。

### 1.2 Roadmap 未做项

- 其他平台解析器（抖音/小红书/微博/X/YouTube…）
- RenderKit 封面信息卡、B站扫码登录态、i18n（`__plugin_locales__`）
- 引用回复完整拉取（被引消息经 DB 取回；v1 仅扫 `quote` 自带子树文本）

---

## 2. 参考实现接口分析：astrbot_plugin_parser（要点）

- 消息统一入口扫描：文本 + `Json` 卡片段（`extract_json_url` 挖跳转 URL）+ 可选 Reply 引用 → "关键词+正则"
  匹配 → 防抖/仲裁 → `parse()` → `MessageSender` 按计划回复；含 `开启解析/关闭解析` 会话级指令（ADMIN）。
- 每平台一个 `BaseParser`，`@handle(keyword, regex)` 注册；卸载统一关 session。
- B站：`bilibili-api-python` 元数据 + 播放流；DASH 用 ffmpeg 合并；cookie 支持高清。

移植结论：`urls`（候选提取/卡片挖链）↔ `extract_json_url`；`debounce` ↔ 链接/资源防抖；
`/parser on|off` ↔ 会话开关；卸载用 `on_disable` 关共享 client。

---

## 3. 行为规格（B站视频）

### 3.1 输入（消息形态）

1. **纯文本 URL / 裸号**：`bilibili.com/video/BV…|av…`（含 `?p=N`）、`BV…`/`av…`、`b23.tv/…`、`bmBV…`；
   行尾 `BV… <N>` 页号后缀（QQ 卡片形态）。
2. **QQ 分享卡片**：OneBot `json`/`xml`/`miniapp` 段 → `sb:ark`（attrs.data）。
   ⚠ **适配器会把卡片 JSON 再 `json.dumps` 一次（双重编码）**，`ark_extract_url` 需重复解码
   （≤4 层）到 dict；再按 `meta.miniapp/detail_1/detail/news` 等结构化字段（qqdocurl/jumpUrl/url/…）
   取链，最后递归扫全 JSON 任意 http(s) URL 兜底（B站优先）。
3. **引用回复**：`quote` 子树（`parse_reply` 配置；OneBot `reply` 段通常不带内容，完整拉取留 Roadmap）。

### 3.2 处理流程

```
消息进入（message-created）
  └─ matcher（同步、轻量）：[会话已开启解析？] → 元素扫描（文本 + sb:ark + quote按配置）
  └─ 命中 → NORMAL 路由消费（agent fallback 不触发）
  └─ handler：collect candidates（文本/BV/av/b23/卡片去重）
  └─ 会话防抖（失败 forget 允许重试）→ parse_video：b23 展开→meta（时长上限）→resolve_stream
  └─ download：HTML5 单文件 mp4（默认）或 DASH+ffmpeg 合并（体积上限）；缓存复用/修剪
  └─ 回复 MessageElement.video；失败按 fallback_to_text 降级文本；delete_after_send 时发送后即删
```

### 3.3 消费与会话开关（ShinBot 路由层）

`RouteTable.match` 语义：`OBSERVE` 永远伴随；有 `EXCLUSIVE` 命中只派发那条 + observers（挡其他 NORMAL）；
有 `NORMAL` 命中派发全部 NORMAL 且 **FALLBACK(`agent_entry`) 不再触发**；`FALLBACK` 只在无命中时兜底。

实现结论：注册 `RouteMatchMode.NORMAL` + custom matcher；**matcher 拿会话做门控**：
- 消息路径的 `RouteMatchContext` 携带已解析的 `session`（ingress 传入），matcher 第三参可读
  `match_context.session.id`（与 handler 的 `ctx.session_id` 同键）→ `SessionStateStore.is_enabled(session)`。
- `SessionStateStore`（`data_dir/session_state.json`）：`enabled`/`disabled` 集合 + 配置 `parse_by_default`。
  有效状态 = 显式开启 或（默认开启 且 未显式关闭）。默认 `parse_by_default=false` → **默认不解析**。
- matcher 未拿到 session 时按默认策略放行（守旧的纯链接检测用法）；生产 message 路径必有 session。

指令（`/parser on|off|status`，`cmd.linkparser` 权限，admin/owner 默认拥有）直接读写 store。

### 3.4 触发注册

```python
@plg.on_route(
    RouteCondition(event_types=frozenset({"message-created"}), custom_matcher=matcher),
    rule_id="shinbot_plugin_linkparser.parse", priority=80, match_mode=RouteMatchMode.NORMAL,
)
@plg.on_command("parser", aliases=["linkparser"], permission="cmd.linkparser", ...)
```

matcher 同步轻量（正则 + JSON 扫描），真实解析在 handler 异步执行。

---

## 4. ShinBot 框架对齐（Python 主分支）

### 4.1 分支说明（防呆）

本机 `ShinBot` checkout 在 `experiments/cordis-poc`（TS/Cordis 实验），但 `shinbot/` Python 代码与
`master` 逐字节一致（`git diff master HEAD -- shinbot/` 为空）。所有 API 锚点以 **master（Python）契约为准**。

### 4.2 已核实的 API 锚点

| 锚点 | 位置 |
|---|---|
| `setup(plg: Plugin)`、`plg.on_route/on_command`、`plg.data_dir`/`plg.logger` | `shinbot/core/plugins/context.py` |
| `RouteCondition`/`RouteMatchMode`、matcher `(event, message, match_context?)` | `shinbot/core/dispatch/routing.py`（`_call_route_matcher` 按签名适配第三参） |
| 消息路径 `RouteMatchContext(session=…)` | `shinbot/core/dispatch/ingress.py`（匹配前已解析 Session） |
| handler `(RouteDispatchContext, RouteRule)`、`require_message_context()` | `shinbot/core/dispatch/ingress.py` |
| `MessageContext.session_id/.message.elements/.send()` | `shinbot/core/dispatch/message_context.py` |
| `MessageElement`（text/at/video/quote/`sb:ark`） | `shinbot/schema/elements.py` |
| 配置 `plugin_config_block` | `shinbot/core/plugins/config.py` |
| 权限节点 `cmd.*`；owner=`*`、admin=`cmd.*`；命令按 `permission` 过滤 | `shinbot/core/security/permission.py` |

### 4.3 发送视频与平台限制

OneBot v11 出站 `MessageElement(type="video")` → `{"type":"video","data":{"file":…}}`，本地文件会被
整体转 `base64://` 上行（大视频受限 → `max_size_mb` + `fallback_to_text` 兜底）。
Satori 适配器支持 `video`（本地文件上传）。

---

## 5. 模块结构（现状）

```text
shinbot_plugin_linkparser/
├── DESIGN.md / README.md / metadata.json / pyproject.toml / .gitignore
├── shinbot_plugin_linkparser/
│   ├── __init__.py      # setup：config/state/client/debouncer/matcher/路由+指令；on_disable
│   ├── models.py        # LinkCandidate/VideoMeta/SingleFilePlan/DashPlan/ParseOutcome
│   ├── urls.py          # BV/av/b23/bm 提取、?p=N、ark 挖链（双重解码+字段+递归兜底）、元素扫描/候选收集
│   ├── matcher.py       # build_link_matcher：会话门控 + 提及/quote 场景 + 同步轻量扫描
│   ├── session_state.py # /parser 会话开关状态（JSON 持久化，enabled/disabled + parse_by_default）
│   ├── debounce.py      # 会话级链接/资源防抖（内存 TTL）
│   ├── parsers.py       # parse_video 编排 + 缓存复用/修剪 + delete_cached_file + 错误分类
│   └── bilibili/
│       ├── client.py    # bilibili-api-python：fetch_meta / resolve_stream / resolve_short_url / 错误翻译
│       └── download.py  # httpx 流式下载（Referer/UA/体积上限/清理）+ ffmpeg DASH 合并
└── tests/               # conftest(shinbot 桩) + packaging/urls/matcher/session_state/parsers/
                         #   client/download/plugin_entry
```

## 6. 配置与指令（已定稿）

```python
class LinkParserPluginConfig(BaseModel):
    enabled: bool = True             # 插件总开关
    parse_by_default: bool = False   # 未 /parser on 的会话是否默认解析（默认不解析）
    parse_on_mention: bool = True    # 已开启会话中 @机器人 也解析
    parse_reply: bool = False        # 扫描 quote 子树链接
    fallback_to_text: bool = True    # 发送失败 → 标题+链接文本
    bilibili_cookie: str = ""        # SESSDATA（可选）
    prefer_mp4: bool = True          # HTML5 单文件优先
    max_quality: int = 80            # DASH qn 上限
    max_duration_seconds: int = 0    # 时长上限（0=不限）
    max_size_mb: int = 200           # 体积上限
    debounce_seconds: int = 300      # 会话防抖窗口（0=关闭）
    cache_max_files: int = 50        # 缓存保留 mp4 数（0=不清理）
    delete_after_send: bool = False  # 发送成功即删本地缓存
```

指令（权限 `cmd.linkparser`，admin/owner 默认拥有，可在权限配置中授予群友）：
`/parser on` 开启当前会话解析；`/parser off` 关闭；`/parser status` 查看状态与全局默认；别名 `/linkparser`。

## 7. 打包 / 上传 / 索引（已执行 + 纪律）

- 仓库 `NekyuuYa/shinbot_plugin_linkparser`（public, main）+ 市场索引 `NekyuuYa/shinbot-plugins`。
- 发版纪律：实现/修复 → ruff + pytest → 同步 `metadata.json`/`pyproject.toml` version →
  推送插件仓库 → `shinbot-plugins` 提交 "Bump LinkParser marketplace version to x.y.z" 推送 main。

## 8. 测试

89 项单测全部离线：`urls`（候选提取/边界/去重/ark 单双编码/真实字段族/递归兜底）、`matcher`
（会话门控/提及/quote/卡片双编码命中）、`session_state`（默认关/显式开关/持久化/隔离/默认开启时显式关）、
`debounce`、`parsers`（FakeClient：b23/时长上限/产物/缓存复用/修剪/删除）、`bilibili client`
（html5/DASH 计划选择、错误码翻译，用真实 `VideoDownloadURLDataDetecter`）、`download`、
`packaging`、`plugin_entry`（fake-Plugin：默认不解析/parse_by_default 开启/路由+指令注册/指令 toggling/修剪）。
端到端（开发期手动）：匿名 HTML5 下载 33s/8.8MB 视频成功（ftyp 校验）；DASH 480P+ffmpeg 合并成功
（ffprobe：h264+aac，32s）。

## 9. Roadmap

- **已实现（0.2.x）**：B站视频解析与下载、卡片（sb:ark 双重编码）解析、文件缓存/清理、
  `/parser on|off|status` 会话开关（默认不解析）、文本兜底。
- **Next**：引用回复完整拉取、`__plugin_locales__`、RenderKit 封面信息卡、B站扫码登录态、多平台解析器注册表。
- 各阶段对齐 §7 发布/索引纪律。
