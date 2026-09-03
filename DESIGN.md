# ShinBot LinkParser — 设计文档（准备阶段）

> 状态：**准备阶段（preparation）**。本文件是后续实现的设计依据，解析逻辑尚未实现。
> 目录结构、打包与加载约定已与 `/home/Nekyuu/Workplace/plugins/` 下其他插件一致；
> GitHub 仓库与插件市场索引已建立（§7），代码骨架已可加载（无害 no-op）。
> §5.2 数据源方案已定：方案 B（bilibili-api-python）。

---

## 1. 背景与目标

ShinBot 群聊中用户分享链接时，默认只发送纯文本 URL；部分平台（QQ/OneBot 环境）
不会自动展开为可预览内容，消息体验差。本插件用于**识别消息中的可解析链接 / 分享卡片，
解析后直接回复可播放/可消费的内容本体**。

参考接口：AstrBot 生态的 [astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)（万能解析器）。
本插件面向 ShinBot 原生插件体系（**Python 主分支契约**，见 §4.1）重新实现其"解析器注册表 + 消息触发 + 消费回复"模型，
而非照搬其 AstrBot 平台 API。

### 1.1 v1 范围（已与需求方确认）

- **只解析 Bilibili（B站）视频链接**（含 `BV` 号/`av` 号/`b23.tv` 短链/`bilibili.com/video/...`）；
  **动态/专栏/直播/歌单等一律不做**。
- **只回复视频本体**（下载后以视频消息回复），首版不做封面图卡片/图文混排。
- **消息消费语义：消费并回复（可配置）** —— 命中后吞掉消息并回复视频，
  阻止 Agent fallback 再回复一次；是否消费、是否在 @机器人 场景也解析由配置控制。
- 支持"卡片信息"输入：QQ 分享卡片（OneBot `json`/`xml`/`miniapp` 段 → ShinBot `sb:ark` 元素）内嵌链接也可解析。

### 1.2 不在 v1 范围（Roadmap）

- 其他平台解析器（抖音/小红书/微博/X/YouTube/知乎/网易云…）
- 封面卡片渲染（届时引入 `shinbot_plugin_renderkit` 或自绘）
- 登录态管理（B站扫码登录高清晰度）、引用回复解析等

---

## 2. 参考实现接口分析：astrbot_plugin_parser

仓库：https://github.com/Zhalslar/astrbot_plugin_parser（cloned 至本地 `/tmp/astrbot_parser_ref` 作对照，v1.5.7）。

### 2.1 消息处理模型（`main.py`）

- 消息统一入口 `on_message`（`filter.event_message_type(ALL)`），每次消息都做如下处理：
  1. 白名单 / 黑名单（按会话 `unified_msg_origin` 过滤）。
  2. 从消息链取文本（`event.message_str`）；若消息链含 `Json` 段（分享卡片），
     用 `extract_json_url()` 从卡片 JSON 里挖跳转 URL，作为待解析文本。
  3. 若启用 `enable_reply_parse` 且消息是 `Reply`（引用回复），把被引用消息的文本/卡片 URL 一并作为解析源。
  4. 用"关键词 + 正则"双层匹配（所有平台解析器的 URL 正则汇总成一张表，
     长关键词优先），命中则进入解析流程。
  5. 防抖/仲裁：链接级防抖（同一会话同一链接窗口内跳过）、资源 ID 级防抖、
     多机器人仲裁（群内多条同类解析机器人只胜出一个）。
  6. `parse()` 得到统一 `ParseResult`（元数据 + 内容清单），再由 `MessageSender`
     组装发送计划（预览卡片图 + 视频/图集/音频分段 + 文本兜底）后回复。
- 管理命令：`开启解析` / `关闭解析`（会话级开关，需 ADMIN 权限）、`登录B站`（扫码登录）。

### 2.2 解析器扩展模型（`core/parsers/base.py`）

- 每个平台一个 `BaseParser` 子类；`__init_subclass__` 自动把子类注册进 `_registry`。
- 每个处理函数用 `@handle(keyword, regex_pattern)` 装饰器标注"关键词 + URL 正则"；
  类初始化时聚合为 `_handlers`/`_key_patterns`（关键词按长度降序）。
- `parse(keyword, match) -> ParseResult` 由关键词分发到具体 handler。
- 每个 parser 实例持有自己的 aiohttp session / headers / proxy；插件卸载时统一关闭。

### 2.3 B站视频实现要点（`core/parsers/bilibili/`）

- 匹配：`BV` 正则、`av` 正则、`b23.tv`/`bili2233.cn` 短链（先重定向展开）、`bmBVxxx`（QQ 卡片常见前缀）。
- 数据源：`bilibili-api-python`（`bilibili_api.video.Video.get_info()` 等）拉元数据；
  播放地址由 `extract_download_urls()` 取 DASH/音视频流；下载用自研下载器（流式或 `yt-dlp`），
  音视频分离时 `merge_av` 合并（依赖 ffmpeg）；带 cookie 支持高清。
- 结果：`VideoContent`（本地文件路径）+ 元数据；发送端把本地视频文件发成平台 `Video` 消息段。

### 2.4 对我们有用的"接口"结论

| 机制 | 移植进 ShinBot 的形态 |
|---|---|
| 关键词+正则注册表 → 解析器分发 | 平台解析器接口 + URL 模式表（v1 先只含 B站） |
| Json 卡片挖 URL（`extract_json_url`） | 处理 ShinBot `sb:ark` 元素（attrs.data 为 JSON 字符串） |
| 引用回复解析 | 可选：遍历 `quote` 子元素文本（v1 可先不做） |
| 链接/资源防抖 | 会话级防抖窗口（内存 + 可选落盘） |
| 会话白/黑名单开关 | 会话级开关（命令 + 配置）；ShinBot 侧用权限节点控制命令 |
| 统一 ParseResult + 发送计划 | v1 简化为 `BiliVideoResult(元数据 + 本地视频路径)`，直接发 `video` 元素 |
| 卸载清理 | `teardown`/`on_disable` 关闭 http 会话、取消后台任务（`plg.cancel_background_tasks`） |

---

## 3. v1 行为规格（B站视频）

### 3.1 输入（消息形态）

1. **纯文本 URL**：消息文本元素含 B站视频链接。
   - `https://www.bilibili.com/video/BVxxxxxxxxxx`
   - `https://b23.tv/xxxx`（短链 → 需 302 展开成 BV 链接）
   - 裸 `BVxxxxxxxxxx` / `avxxxxxxxx`
   - 可选支持 `?p=N` 分 P 参数与 `bm`/QQ 卡片内 `legacyUrl` 形态。
2. **QQ 分享卡片**：OneBot `json`/`xml`/`miniapp` 段被适配器归一为
   `MessageElement(type="sb:ark", attrs={"data": "<json 字符串>"})`（见
   `shinbot/builtin_plugins/shinbot_adapter_onebot_v11/adapter.py` 与 `..._qqofficial/adapter.py`），
   从 JSON 的 `meta`/`detail_1`/`news` 等路径挖跳转 URL。
3. （Roadmap）引用回复：消息 `quote` 元素引用的是机器人/他人发的链接消息时解析。

### 3.2 处理流程

```
消息进入（message-created）
  └─ 会话开关/权限过滤（关闭则不解析）
  └─ 文本扫描 + sb:ark 卡片挖 URL + (可选)quote 展开
  └─ URL 归一：b23.tv 展开 → BV/av 提取 → 页码
  └─ 链接/资源防抖检查（窗口内重复 → 静默跳过）
  └─ 解析（B站 web API 元数据）
  └─ 播放地址获取 + 下载到 plg.data_dir/videos（限长/限体积）
  └─ 回复：MessageElement.video(local file)（可附一行文字：标题/UP主/时长）
  └─ 消费语义：按配置标记消费（阻止 agent fallback）
```

### 3.3 消费语义（ShinBot 路由层，重要）

ShinBot 的消息路由语义（`shinbot/core/dispatch/routing.py::RouteTable.match`）：

- `OBSERVE` 规则永远伴随执行（被动监听）。
- 有 `EXCLUSIVE` 命中 → 只派发**最高优先级那条 EXCLUSIVE** + observers；
  会**挡住其他插件的 NORMAL 规则**（不适合本插件，避免误伤其它功能）。
- 有 `NORMAL` 命中 → 所有命中的 NORMAL 规则都派发；**此时 FALLBACK（`agent_entry`）不再触发**。
- `FALLBACK`（`builtin.agent_entry_fallback`）只在无 NORMAL/EXCLUSIVE 命中时兜底给 Agent。

**结论：本插件注册 `RouteMatchMode.NORMAL` + 自定义 matcher**：
命中 B站链接的消息自然不再落入 Agent fallback（不会"机器人又回一句"），
同时不阻断其他插件的并行规则。行为开关：

- `consume_message = True`（默认）：命中链接即消费（阻止 agent 回复）。
- `parse_on_mention`：消息同时 @ 机器人时仍照常解析并回复视频（默认 True）；
  若设 False，则 @ 机器人的消息跳过解析、留给 Agent。
- 预留 `only_parse_when_not_mentioned_other`：群内消息 @ 了**别的机器人**时跳过
  （对齐 astrbot 行为，防多机器人抢答；单机器人部署时通常不生效）。

### 3.4 触发注册方式

```python
cond = RouteCondition(
    event_types=frozenset({"message-created"}),
    custom_matcher=matcher,  # def matcher(event, message) -> bool，见 §5
)
@plg.on_route(cond, rule_id="shinbot_plugin_linkparser.parse", priority=90,
              match_mode=RouteMatchMode.NORMAL)
async def handler(context: RouteDispatchContext, rule: RouteRule) -> None:
    ctx = context.require_message_context()
    ...
```

matcher 在 `RouteTable.match` 阶段同步执行（按优先级排序、注册序稳定），只做**轻量文本/元素扫描**
（URL 正则 + `sb:ark` 存在性），不发起网络请求；真正解析放在 handler 异步执行。

---

## 4. ShinBot 框架对齐（Python 主分支）

### 4.1 分支说明（防呆）

本机 `ShinBot` checkout 当前在 `experiments/cordis-poc`（TS + Cordis 重写实验），
但 **`shinbot/` 目录 Python 代码与 `master` 逐字节一致**（`git diff master HEAD -- shinbot/` 为空），
`docs/architecture/framework_plugin_contract.md` 亦声明 "master 未改动"。
本插件一切 API 锚点均以 **master（Python）契约为准**，不参考 cordis-poc 的 TS 插件模型。

### 4.2 已核实的 API 锚点（实现时直接使用）

| 锚点 | 位置 |
|---|---|
| 插件入口 `setup(plg: Plugin)` | 插件包 `__init__.py`；`Plugin` 见 `shinbot/core/plugins/context.py` |
| 模块元数据 `__plugin_name__/__plugin_description__/__plugin_config_class__` | `context.py` 读取约定（见 minesweeper/renderkit 实现） |
| 自定义路由 `plg.on_route(cond, rule_id=..., priority=..., match_mode=...)` | `context.py::Plugin.on_route` |
| `RouteCondition(event_types, element_types, platforms, is_private, custom_matcher)` | `shinbot/core/dispatch/routing.py` |
| 路由 handler 签名 `(RouteDispatchContext, RouteRule)`，`require_message_context()` | `shinbot/core/dispatch/ingress.py` |
| `MessageContext`：`.text` `.elements` `.user_id` `.session_id` `.is_private` `.has_permission()` `.send()` `.delete_msg()` | `shinbot/core/dispatch/message_context.py` |
| 元素 AST：`MessageElement`（text/at/img/video/quote/sb:ark…） | `shinbot/schema/elements.py`（`EXTENSION_TAGS` 含 `sb:ark`、`sb:poke`） |
| 配置类：`_load_plugin_config` + `plugin_config_block(payload, plugin_id)` | `shinbot/core/plugins/config.py`；`DEFAULT_CONFIG_PATH` 见 `shinbot/core/application/paths.py` |
| 后台任务 `plg.create_task`；卸载 `plg.cancel_background_tasks` / 模块 `teardown`/`on_disable` | `context.py`；参考 renderkit `on_disable` |
| 数据目录 `plg.data_dir`；`plg.logger`；`plg.plugin_id` | `context.py` |

### 4.3 发送视频（已核实可行）

- OneBot v11 适配器出站映射：`MessageElement(type="video")` → OB11 `{"type":"video","data":{"file": ...}}`
  （`shinbot/builtin_plugins/shinbot_adapter_onebot_v11/adapter.py`），`file` 可为本地路径/URL。
- Satori 适配器出站支持 media 元素 `video`（本地文件走 Satori 上传）。
- 因此 v1 做法：下载到本地 → `ctx.send([MessageElement.video(str(local_path), ...)])`（可先接一行文本元素）。

### 4.4 卡片（sb:ark）入站样例（OneBot）

```json
{"type": "json", "data": {"data": "{\"app\":\"...\",\"meta\":{\"detail_1\":{\"qqdocurl\":\"https://www.bilibili.com/video/BV1xx...\"}}}"}}
```
→ `MessageElement(type="sb:ark", attrs={"data": "<上面的 data 字符串>"})`。
挖 URL 参考 astrbot `extract_json_url` 的路径优先级：
`meta.miniapp.legacyUrl/pcJumpUrl/jumpUrl/sourceUrl/url`、`meta.detail_1.qqdocurl/jumpUrl/url/shareUrl/targetUrl`、`meta.news.*` 等。

---

## 5. 模块结构与文件布局（实现目标）

```text
shinbot_plugin_linkparser/
├── DESIGN.md                       # 本文档
├── README.md
├── metadata.json
├── pyproject.toml
├── .gitignore
├── shinbot_plugin_linkparser/
│   ├── __init__.py                 # setup / 配置类 / 路由注册（当前为骨架）
│   ├── urls.py                     # URL 提取与归一：文本/BV/av/b23 展开、sb:ark 挖链
│   ├── matcher.py                  # 路由 custom_matcher（轻量、同步）
│   ├── debounce.py                 # 会话级链接/资源防抖（内存 + 可选 JSON 落盘）
│   ├── bilibili/
│   │   ├── client.py               # B站 web API：view 元数据 / playurl / 页码解析
│   │   └── download.py             # 视频下载（限长/限体积、referer 头、mp4/DASH 策略）
│   ├── parsers.py                  # 平台解析器注册表（v1: 仅 BilibiliParser，接口为后续平台预留）
│   └── models.py                   # LinkParserConfig 拆分 / ParseResult / BiliVideoResult
└── tests/
    ├── conftest.py                 # 路径 + shinbot 桩（仿 astroassist）
    ├── test_packaging.py           # metadata/pyproject/入口结构校验
    ├── test_urls.py                # BV/av/b23/sb:ark 挖链 纯函数测试
    ├── test_matcher.py             # matcher 不误伤普通消息/不在消息中时返回 False
    └── test_bilibili_client.py     # API 层（httpx MockTransport 假响应）
```

### 5.1 平台解析器接口（预留给后续平台）

```python
class BaseLinkParser(ABC):
    platform: ClassVar[str]
    key_patterns: ClassVar[list[tuple[str, re.Pattern[str]]]]

    def __init__(self, cfg, data_dir): ...
    async def parse(self, url: str, *, page: int | None = None) -> ParseResult: ...
    async def close(self) -> None: ...
```

v1 只有 `BilibiliVideoParser`；后续加平台只需新增子类 + 在 setup 汇总进注册表，
触发 matcher 改为"任一平台模式命中"，分发按平台走（对齐 astrbot 的 `_key_patterns` 汇总法）。

### 5.2 B站数据源方案（已定：**方案 B**）

- **方案 B（已采用）**：`bilibili-api-python`（astrbot_plugin_parser 同款封装，
  版本对齐其 pin：`>=17.4.1,<18.0.0`）。负责元数据（`bilibili_api.video.Video.get_info()`）、
  播放地址/下载流获取、WBI 签名与登录态管理（扫码登录供高清）；
  实际视频字节下载用 `httpx`（带 `Referer`/凭据头）或复用库内下载器，实现时定。
- ~~方案 A：裸 web API（`x/web-interface/view` + `x/player/playurl`，只加 httpx）~~
  —— 高清/WBI 签名细节自维护，可靠性差，已放弃。
- **下载策略**：优先 `durl`（mp4 直链，免 ffmpeg）；DASH（分离音视频）需 ffmpeg 合并，
  默认不合并时降级到 durl 低清档或直接报错提示。首版先保证"能出视频"，
  高清/合并能力作为配置开关。

---

## 6. 配置与命令草案（待实现时定稿）

```python
class LinkParserPluginConfig(BaseModel):
    enabled: bool = True
    consume_message: bool = True       # 命中后消费消息，阻止 agent fallback
    parse_on_mention: bool = True      # 消息同时 @机器人 时仍解析
    parse_reply: bool = False          # 引用回复中的链接（v1 可不开）
    bilibili_cookie: str = ""          # SESSDATA（可选，用于更高清晰度）
    max_duration_seconds: int = 600    # 0=不限；超长不下载
    max_size_mb: int = 300             # 体积上限
    prefer_mp4: bool = True            # 优先 durl/mp4；False 才尝试 DASH+ffmpeg
    debounce_seconds: int = 300        # 同一会话同一链接/资源防抖窗口
```

会话开关命令（对齐 astrbot `开启解析/关闭解析`，ShinBot 用权限节点）：
`/parser on` / `/parser off`（permission `cmd.linkparser`，owner/admin 分组），
会话黑名单持久化到 `plg.data_dir/session_blacklist.json`。
模块元数据带 `zh-CN`/`en-US` 文案（`__plugin_locales__`，参考 minesweeper）。

---

## 7. 打包 / 上传 / 索引（与既有插件一致）

### 7.1 目录约定（已核对 siblings）

- 独立 git 仓库，remote 指向 `git@github.com:NekyuuYa/shinbot_plugin_linkparser.git`
  （已建仓并推送 skeleton，见 §7.3 步骤 1）。
- `metadata.json`：`id/name/version/author/description/entry/role/permissions`；
  打包名 `shinbot-plugin-linkparser`，`requires-python >= 3.12`。
- pyproject 带 ruff（py312, E/F/I/UP/B）与 pytest（`asyncio_mode="auto"`）配置。

### 7.2 索引仓库 shinbot-plugins（`/home/Nekyuu/Workplace/plugins/shinbot-plugins`）

`plugins.json` 顶部按 id 追加条目（字段与既有条目一致）：

```json
"shinbot_plugin_linkparser": {
  "id": "shinbot_plugin_linkparser",
  "name": "LinkParser",
  "version": "0.1.0",
  "author": "NekyuuYa",
  "description": "解析 B 站视频链接/分享卡片，回复可播放视频本体。",
  "entry": "shinbot_plugin_linkparser/__init__.py",
  "role": "logic",
  "permissions": [],
  "repository": "https://github.com/NekyuuYa/shinbot_plugin_linkparser",
  "ref": "main",
  "plugin_path": ""
}
```

版本演进照既有纪律：插件发版 → 在 `shinbot-plugins` 单独提交 "Bump LinkParser marketplace version to x.y.z" 并推送 main。
README.md 的"Current plugin repositories"清单同步补一行。

### 7.3 发布流程（repo-first：骨架仓与索引先落地，实现后随版本迭代推送）

1. **已执行**：`git init`（main）→ 提交 skeleton → `gh repo create NekyuuYa/shinbot_plugin_linkparser --public --source . --push`。
2. **已执行**：在 `shinbot-plugins` 更新 `plugins.json` + README 清单并推送（首条索引，version 0.1.0）。
3. 后续每次发版：实现/修复 → 本地 ruff/pytest → 改 `metadata.json` 与 `pyproject.toml` version →
   commit + push 插件仓库 → `shinbot-plugins` 单独提交 "Bump LinkParser marketplace version to x.y.z" 推送 main。

---

## 8. 测试策略

- **纯单元**：`urls`/`matcher`/`debounce`/`bilibili.client` 均为可脱离框架的纯逻辑模块；
  用 `tests/conftest.py` 的 shinbot 桩（仿 astroassist）保证 `pytest` 不依赖 ShinBot 安装即可跑。
- **网络隔离**：httpx 用 `MockTransport` 假响应；下载器只测"头 + 限长/限体积"分支。
- **打包测试**：`test_packaging.py` 校验 metadata.json 与 pyproject.toml 一致性、入口文件存在、包可导入。
- **手动验收**：OneBot 环境真实发一条 B站链接 → 期望回复视频；重复发同链接 → 防抖静默。
  （v1 代码完成后若有真机群可走；无则用框架级单测覆盖路由注册。）

---

## 9. Roadmap

- **M1（v1）**：B站视频解析 + 下载 + `video` 回复 + 消费开关（本设计 §3）。
- **M2**：sb:ark 卡片 → 完整字段展示（标题/UP主/封面缩略文字），可选 RenderKit 封面图卡片；
  引用回复解析；`/parser on|off` 会话开关 + 会话黑名单。
- **M3**：更多平台解析器（抖音/小红书/微博/X/YouTube…）；多平台关键词汇总触发；登录态（B站扫码）。
- 各阶段对齐 §7 发布/索引纪律。
