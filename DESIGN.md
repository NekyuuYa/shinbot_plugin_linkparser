# ShinBot LinkParser — 设计文档

> 状态：**B站视频解析 v0.3.0**（110 项单测通过、ruff 干净；HTML5/DASH 两条下载路径与真实 QQ 卡片
> 端到端验证过）。v0.3.0 引入三档解析策略：**off / at / always**，按会话设置（`/parser`），
> matcher 为精确程序化判定（@+引用 时经 message_logs 核实引用内容，无链接的 @ 不吞）。

---

## 1. 背景与目标

ShinBot 群聊分享链接/卡片默认只是文本或无法预览占位；本插件**识别可解析的 B站视频链接与
QQ 分享卡片，解析后回复可播放的视频本体**。

参考接口：AstrBot [astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)。
本插件按 ShinBot **Python 主分支契约**（§4.1）实现"按会话设置档位 → 消息触发 → 解析 → 消费回复"模型。

### 1.1 范围与行为（已与需求方确认）

- **只处理 Bilibili 视频**：`BV`/`av`、`bilibili.com/video/...?p=N`、`b23.tv` 短链、`bmBV...`、
  QQ 分享卡片（`sb:ark`，含双重编码）。动态/专栏/直播等不做。
- **只回复视频本体**；不做封面卡片/图文混排。
- **三档策略**（会话级，`/parser` 设置；全局 `default_mode` 兜底，默认 `off`）：
  - `off`：不解析。
  - `at`：仅当消息 @本机器人 才解析；解析对象 = 消息自身文本 +（@消息是引用回复时）被引消息内容。
  - `always`：总是解析链接消息；引用内容在 `parse_reply=true` 时一并解析。
- **消费语义**：matcher 精确匹配才消费（阻止 Agent fallback 双回复）；未命中消息零干扰、落入 Agent。
  - `at` 档用 **程序化 matcher**：@ 且带引用时同步查 `message_logs` 确认被引消息确有链接才匹配——
    普通 @提问、@+引用无链接都不会被吞。

### 1.2 Roadmap 未做项

- 其他平台解析器（抖音/小红书/微博/X/YouTube…）
- RenderKit 封面信息卡、B站扫码登录态、i18n（`__plugin_locales__`）
- 命令可配置为普通成员可执行（当前 `cmd.linkparser` 默认 admin/owner）

---

## 2. 参考实现接口分析（要点）

astrbot_plugin_parser：消息统一入口扫描文本 + `Json` 卡片（`extract_json_url`）+ Reply 引用 →
"关键词+正则"匹配 → 防抖/仲裁 → `parse()` → 发送计划回复；含 `开启解析/关闭解析` 指令（ADMIN）。
每平台一个 `BaseParser`；B站用 `bilibili-api-python` + 自研下载器（DASH ffmpeg 合并）。

移植结论：`urls`（候选提取/卡片双重解码挖链）↔ `extract_json_url`；`parse_policy` 三档判定 ↔
会话开关；`debounce` ↔ 链接/资源防抖；`/parser` 指令 ↔ 会话开关（ADMIN 权限化）；卸载 `on_disable` 关 client。

---

## 3. 行为规格（B站视频）

### 3.1 输入（消息形态）

1. **纯文本 URL / 裸号**：`bilibili.com/video/BV…|av…`（`?p=N`）、`BV…`/`av…`、`b23.tv/…`、`bmBV…`；
   行尾 `BV… <N>` 页号后缀。
2. **QQ 分享卡片**：OneBot/QQ 官方 `json`/`xml`/`miniapp`/`ark` → `sb:ark`（attrs.data）。
   适配器对字符串卡片**再 json.dumps（双重编码）**，且内部常用 `\/` 转义 → `ark_extract_url`
   重复解码（≤4 层）到 dict；按 `meta.miniapp/detail_1/detail/news` 等字段
   （qqdocurl/jumpUrl/url/…）取链，再递归扫全 JSON 任意 http(s) URL 兜底（B站优先）。
3. **引用回复**：`quote`/`reply` 元素（OneBot 通常只带 id 无子内容）→ 通过
   `message_logs.get_by_platform_msg_id(session, quote_id)` 取回记录 `content_json`
   （MessageElement AST 数组，JSON）后复用同一套候选扫描。

### 3.2 处理流程

```
消息进入（message-created）
  └─ matcher（同步、精确、程序化）：
       档位=state.mode(session via RouteMatchContext.session.id)
       off → False
       at  → @本bot?（可见区，不含 quote 内旧@）
             ├─ 有自身链接 → True
             └─ 带引用 → 子树内容 或 message_logs 取回被引消息 → 确有链接才 True
       always → 有链接?（引用仅在 parse_reply 时）→ True/False
  └─ 命中 → NORMAL 消费（agent fallback 不触发）
  └─ handler：同策略取 candidates → 会话防抖 → parse_video
       （b23 展开→meta→时长上限→resolve_stream→下载/缓存复用；体积上限；修剪/删除策略）
  └─ 回复 MessageElement.video；失败按 fallback_to_text 降级文本
```

### 3.3 消费与会话档位（ShinBot 路由层）

`RouteTable.match` 语义：`OBSERVE` 永远伴随；`EXCLUSIVE` 只派发那条+observers（挡其他 NORMAL）；
`NORMAL` 命中派发全部 NORMAL 且 **FALLBACK(`agent_entry`) 不触发**；`FALLBACK` 仅无命中时兜底。

实现结论：注册 `RouteMatchMode.NORMAL` + **精确自定义 matcher**：

- 消息路径 `RouteMatchContext` 携带已解析 `session`（ingress），matcher 第三参可读
  `match_context.session.id`（与 `ctx.session_id` 同键）→ `SessionStateStore.mode(session)`。
- `SessionStateStore`（`data_dir/session_state.json`）：`{"sessions":{sid:mode}}`，
  会话缺省取 `default_mode`；自动迁移 v0.2.0 `enabled/disabled` 旧格式（enabled→always，disabled→off）。
- matcher 的引用核实走 **同步** `message_logs` 仓库（sqlite）+ `content_json`（纯 JSON），
  DB 由 `plg.database` 提供；不可用/查无 → 按无链接处理（False），保证 @ 消息不被误吞。

### 3.4 指令与权限

`/parser off|at|always|status`（别名 `/linkparser`、`on`→always；权限 `cmd.linkparser`，
admin/owner 默认拥有）直接读写 store；`status` 显示当前档位与全局默认。

---

## 4. ShinBot 框架对齐（Python 主分支）

### 4.1 分支说明（防呆）

本机 `ShinBot` checkout 在 `experiments/cordis-poc`（TS/Cordis 实验），但 `shinbot/` Python 代码与
`master` 逐字节一致。所有 API 锚点以 **master（Python）契约为准**。

### 4.2 已核实的 API 锚点

| 锚点 | 位置 |
|---|---|
| `setup(plg)`、`plg.on_route/on_command`、`plg.data_dir/logger/database` | `shinbot/core/plugins/context.py`（manager 传 `database=self._database`） |
| `RouteCondition`/`RouteMatchMode`；matcher `(event,message,match_context?)` | `shinbot/core/dispatch/routing.py`（`_call_route_matcher` 按签名适配第三参） |
| 消息路径 `RouteMatchContext(session=…)` | `shinbot/core/dispatch/ingress.py` |
| handler `(RouteDispatchContext, RouteRule)`、`require_message_context()` | `shinbot/core/dispatch/ingress.py` |
| `MessageContext.session_id/.message.elements/.event.self_id/.send()` | `shinbot/core/dispatch/message_context.py` |
| `MessageElement`（text/at/video/quote/`sb:ark`） | `shinbot/schema/elements.py` |
| 引用内容：`message_logs.get_by_platform_msg_id(session_id, platform_msg_id)`，记录 `content_json` = MessageElement AST 数组 | `shinbot/persistence/repositories/messages.py`、`records.py` |
| 配置 `plugin_config_block` | `shinbot/core/plugins/config.py` |
| 权限 `cmd.*`：owner=`*`、admin=`cmd.*`；命令按 `permission` 过滤 | `shinbot/core/security/permission.py` |

### 4.3 发送视频与平台限制

OneBot v11 出站 `MessageElement(type="video")` → `{"type":"video","data":{"file":…}}`，本地文件整体转
`base64://` 上行（大视频受限 → `max_size_mb` + `fallback_to_text` 兜底）。Satori 适配器支持 `video`（上传）。

---

## 5. 模块结构（现状）

```text
shinbot_plugin_linkparser/
├── DESIGN.md / README.md / metadata.json / pyproject.toml / .gitignore
├── shinbot_plugin_linkparser/
│   ├── __init__.py      # setup：config/state/client/debouncer/matcher/路由+指令；on_disable
│   ├── models.py        # LinkCandidate/VideoMeta/SingleFilePlan/DashPlan/ParseOutcome
│   ├── urls.py          # BV/av/b23/bm 提取、?p=N、ark 挖链、元素扫描、quote 遍历/子树收集/合并
│   ├── parse_policy.py  # 三档判定核心（纯逻辑）+ 可见@检测 + quote 解析器 + DB 引用读取
│   ├── matcher.py       # NORMAL 路由 matcher：mode + 精确引用核实（DB 同步）
│   ├── session_state.py # /parser 档位存储（JSON；default_mode；旧格式迁移）
│   ├── debounce.py      # 会话级防抖
│   ├── parsers.py       # parse_video 编排 + 缓存复用/修剪 + delete_cached_file
│   └── bilibili/
│       ├── client.py    # bilibili-api-python 封装 + 错误翻译
│       └── download.py  # httpx 下载 + ffmpeg DASH 合并
└── tests/               # packaging/urls/parse_policy/matcher/session_state/parsers/client/download/plugin_entry
```

## 6. 配置与指令（已定稿）

```python
class LinkParserPluginConfig(BaseModel):
    enabled: bool = True                    # 插件总开关
    default_mode: Literal["off","at","always"] = "off"
    parse_reply: bool = False               # always 档下解析引用（at 档引用不依赖此项）
    fallback_to_text: bool = True
    bilibili_cookie: str = ""
    prefer_mp4: bool = True
    max_quality: int = 80
    max_duration_seconds: int = 0
    max_size_mb: int = 200
    debounce_seconds: int = 300
    cache_max_files: int = 50
    delete_after_send: bool = False
```

指令（`cmd.linkparser`，admin/owner 默认拥有）：`/parser off|at|always|status`（`on`=always，别名 `/linkparser`）。
档位写入 `data_dir/session_state.json`（v0.3.0 格式 `{"sessions":{...}}`，自动迁移旧版）。

## 7. 打包 / 上传 / 索引（已执行 + 纪律）

- 仓库 `NekyuuYa/shinbot_plugin_linkparser`（public, main）+ 市场索引 `NekyuuYa/shinbot-plugins`。
- 发版纪律：实现/修复 → ruff + pytest → 同步 `metadata.json`/`pyproject.toml` version →
  推送插件仓库 → `shinbot-plugins` 提交 "Bump LinkParser marketplace version to x.y.z" 推送 main。

## 8. 测试

110 项单测全部离线：`urls`（候选/边界/去重/ark 单双编码/真实卡片回归）、`parse_policy`
（三档矩阵：off/at 需 @、at 引用经 resolver/children、always 引用受 parse_reply 约束且与 at 解耦、
DB resolver）、`matcher`（三档 + @/引用精确性：@无链接不匹配、引用经 DB 有链接才匹配）、
`session_state`（档位/默认/持久化/非法值/旧格式迁移）、`debounce`、`parsers`、`bilibili client`、
`download`、`packaging`、`plugin_entry`（fake-Plugin：默认 off/各档 matcher/指令 toggling/修剪）。
端到端（开发期手动）：匿名 HTML5 下载 33s/8.8MB 成功；DASH 480P+ffmpeg 合并成功；
真实 QQ 卡片（OneBot 双重编码，`b23.tv/WD4aMAF`）→ `BV1h38C6SEfh` → 元数据正常。

## 9. Roadmap

- **已实现（0.3.x）**：B站视频解析下载、卡片（sb:ark 双重编码）解析、三档策略（off/at/always）会话化、
  精确 matcher（@+引用经 message_logs 核实）、文件缓存/清理、文本兜底、真实卡片回归测试。
- **Next**：RenderKit 封面信息卡、B站扫码登录态、i18n、多平台解析器注册表、`cmd.linkparser` 权限细粒度化。
- 各阶段对齐 §7 发布/索引纪律。
