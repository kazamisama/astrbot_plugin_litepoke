# 更新日志

litepoke 的所有版本变更记录。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [v1.3.16] - 2026-06-09

### Fixed
- 新增 conversation history 自修复：清理已残留的空 `assistant` 消息，以及没有匹配 `assistant.tool_calls` 的孤立 `tool` 消息，避免 OpenAI 兼容接口持续报 `Invalid assistant message: content or tool_calls must be set`。
- bot 被戳写入戳一戳事件时会顺手清理当前 conversation 中的非法 LLM history，避免旧污染继续影响后续请求。
- 普通 `on_llm_request` 前也会尝试清理当前 conversation，修复已经写进数据库的历史脏记录导致的持续复发。

### Changed
- `metadata.yaml` version：v1.3.15 → v1.3.16

---
## [v1.3.15] - 2026-06-09

### Fixed
- aiocqhttp 私聊收到别人戳 bot 的 notice 时不再因 `group_id` 为空被提前忽略；在 `only_group=false` 时会写入戳一戳上下文并主动触发 LLM 回应。
- bot 私聊主动戳别人产生的 outgoing poke notice 也会进入清理分支，避免原始空 Poke 消息残留到 conversation。

### Changed
- 戳一戳接管日志从 `group=` 改为 `scope=`，私聊场景会显示 `_private`，方便排查。
- `metadata.yaml` version：v1.3.14 → v1.3.15

---
## [v1.3.14] - 2026-06-09

### Fixed
- aiocqhttp 私聊 `poke_user` 优先改用 OneBot `send_poke` 原始 API，兼容 NapCat/新版实现中 `friend_poke` 封装存在但无实际效果的情况。
- 私聊 `send_poke` 失败时保留 `friend_poke` 兜底，并在返回值中带出两条调用链的错误，方便排查平台兼容性。

### Changed
- `metadata.yaml` version：v1.3.13 → v1.3.14

---
## [v1.3.13] - 2026-06-09

### Fixed
- PokeLog 写盘和记录时会按 `poke_log_window` 裁剪 `recent`，避免默认关闭统计注入时 recent 长期累积到 JSON。
- `_get_llm_tooling()` 不再原地删除 inactive 工具，避免主动 notice LLM 链路污染全局 ToolSet。
- incoming/outgoing 戳一戳 history 增加延迟清理补偿，降低 AstrBot 原始 Poke 写入时序导致的重复上下文/残留 outgoing 空消息风险。
- 自定义 `poke_log_path` 现在会尝试创建父目录。
- meme 索引缓存会感知 emotion 子目录 mtime，运行中往已有标签目录加图也能刷新。
- 配置数字项读取增加容错和范围夹取，配置损坏时降级默认值而不是让事件处理链抛异常。

### Changed
- 拆分工具戳、概率跟戳、bot 被戳主动回应的 CD 时间戳，避免 `_last_any_poke` 混用导致不同动作互相压制。
- `metadata.yaml` version：v1.3.12 → v1.3.13

---
## [v1.3.12] - 2026-06-09

### Changed
- 整合服务器 patch：bot 被戳后的主动 `request_llm` 现在优先传入当前 conversation，使即时回复也能读取官方 conversation 中刚写入的戳一戳事件和最近上下文。
- conversation 路线下 `respond_poked_prompt` 不再重复塞入完整 `poke_text`，而是引用“上文最新的戳一戳事件”，避免一次戳一戳同时出现在 prompt 和 conversation 中导致模型误判被戳两次。
- 获取 conversation 失败时仍降级为 `poke_event` prompt + 当前 persona `system_prompt`。
- `metadata.yaml` version：v1.3.11 → v1.3.12

---
## [v1.3.11] - 2026-06-09

### Changed
- 调整默认 `guide_prompt`：从“戳回去”改为普通消息场景下的轻量工具提醒，避免和 `respond_poked_prompt` 的被戳事件回应职责重叠。
- `_conf_schema.json` 和 README 明确 `guide_prompt` 只用于普通消息命中关键词时的引导；bot 被戳后的即时回应由 `respond_poked_prompt` 独立控制。
- `metadata.yaml` version：v1.3.10 → v1.3.11

---
## [v1.3.10] - 2026-06-08

### Removed
- 删除被戳主动回应时手动读取并拼接最近 user/assistant 文本上下文的逻辑。
- 删除配置项 `respond_poked_context_enabled` / `respond_poked_context_messages`。

### Changed
- bot 被戳的即时主动回应现在只向 LLM 传入 `poke_event` prompt、当前 persona `system_prompt` 和工具集；写入 conversation 的戳一戳事件仅供后续普通对话使用。
- `respond_poked_prompt` 默认文案不再要求“结合当前聊天上下文”。
- `metadata.yaml` version：v1.3.9 → v1.3.10

---
## [v1.3.9] - 2026-06-08

### Fixed
- 主动回应 bot 被戳时，当前戳一戳事件仍会写入 conversation，但即时 `request_llm` 构造 recent contexts 时会跳过同一条 `poke_text`，避免该事件同时出现在 prompt 和 contexts 中导致模型误判被戳了两次。

### Changed
- `metadata.yaml` version：v1.3.8 → v1.3.9

---
## [v1.3.8] - 2026-06-08

### Fixed
- 机器人自己通过 `poke_user` 或概率跟戳发出的 outgoing 戳一戳 notice 不再作为新的 `user` 输入留在 conversation 中，避免下一轮模型误判为“用户刚刚戳了 bot”。
- 戳一戳 notice 的 `self_id` / `user_id` / `target_id` / `group_id` 统一转为字符串后再比较，避免平台返回 int/string 混用导致方向判断失效。

### Changed
- `follow_enabled` 现在只控制“别人戳别人时概率跟戳”，不再影响“bot 被戳写入上下文”和“清理 bot outgoing 原始 Poke history”。
- `metadata.yaml` version：v1.3.7 → v1.3.8

---
## [v1.3.7] - 2026-06-08

### Fixed
- 写入戳一戳事件到 conversation 时，优先替换最近由 AstrBot 原始 Poke 组件产生的空 `user` 消息；找不到可替换项时才追加新消息。
- 避免一次戳一戳在上下文中表现为“原始空 Poke 消息 + litepoke 文本事件”两条记录，导致模型误判被戳了两次。

### Changed
- `metadata.yaml` version：v1.3.6 → v1.3.7

---

## [v1.3.6] - 2026-06-08

### Fixed
- 主动戳一戳回应的 `event.request_llm()` 现在显式传入 `func_tool_manager` / `tool_set`，确保该 notice 触发链路也能看到 `poke_user` 等 LLM 工具，避免模型只能用 `send_message_to_user` 假装“反戳”。
- `respond_poked_cd` 现在只控制是否主动调 LLM，不再阻止戳一戳事件写入 conversation。CD 内的戳一戳也会被记录为文本上下文。

### Changed
- `metadata.yaml` version：v1.3.5 → v1.3.6

---

## [v1.3.5] - 2026-06-08

### Changed
- PokeLog 统计默认不再注入 `system_prompt`。新增 `poke_log_inject_enabled`（默认 false），需要旧版统计提示时可手动开启。
- 删除 `poke_user` 工具级全局/单用户 CD 静默分支；LLM 调用工具时不再出现“工具看似调用成功但实际没有反馈”的空返回。
- 移除 `global_cd` / `user_cd` 配置项；防刷屏保留在跟戳 `follow_cd`、被戳主动响应 `respond_poked_cd` 和平台侧限制中。
- `metadata.yaml` version：v1.3.4 → v1.3.5

---

## [v1.3.4] - 2026-06-08

### Added
- 新增 `respond_poked_write_context`（默认 true）：bot 被戳的 notice 事件会作为一条纯文本 `user` 消息写入官方 conversation。

### Changed
- 主动戳一戳事件从“只作为本次 prompt/contexts 使用”改为“写入上下文历史”，后续普通对话、上下文构造和记忆链路都能自然感知该事件。
- 写入内容只包含纯文本事件描述和轻量 metadata，不写入 `tool` / `tool_calls`，避免污染 OpenAI 工具调用链。
- `metadata.yaml` version：v1.3.3 → v1.3.4

---

## [v1.3.3] - 2026-06-08

### Fixed
- 修复 v1.3.2 主动戳一戳回应虽然保留人格，但仍缺少最近聊天上下文，导致回应和当前话题联动不足、显得像独立触发器的问题。
- 主动戳一戳回应现在会读取当前 conversation，但只提取最近 user/assistant 的纯文本消息作为 `contexts`，过滤 `tool`、`tool_calls`、`_checkpoint`、think 等内容，避免重新触发 OpenAI 兼容接口的 tool 配对 400。

### Added
- 新增 `respond_poked_context_enabled`：控制被戳主动回应是否携带清洗后的最近上下文，默认 true。
- 新增 `respond_poked_context_messages`：控制携带最近上下文条数，默认 6。

### Changed
- `metadata.yaml` version：v1.3.2 → v1.3.3
- `respond_poked_prompt` 默认文案增加“结合当前聊天上下文”。

---

## [v1.3.2] - 2026-06-08

### Fixed
- 修复 v1.3.1 为避免 `conversation` 中历史 tool 消息导致 400 后，只传短 `prompt + session_id` 造成主动戳一戳回应丢失人格、表现像“换了一个人”的问题。
- 主动戳一戳回应现在会通过 `persona_manager.get_default_persona_v3(event.unified_msg_origin)` 单独获取当前会话人格 prompt，并作为 `system_prompt` 传入 `event.request_llm()`。

### Changed
- `metadata.yaml` version：v1.3.1 → v1.3.2
- 继续保持不传完整 `conversation`，避免重新引入 OpenAI 兼容接口的 tool/tool_calls 历史配对问题。

---

## [v1.3.1] - 2026-06-08

### Fixed
- 修复 bot 被戳后主动调用 LLM 时携带完整 `conversation`，在部分 OpenAI 兼容接口上可能因历史 `tool` 消息与 `tool_calls` 被裁剪/重组而触发 400：`Messages with role 'tool' must be a response to a preceding message with 'tool_calls'`。
- 被戳主动回应改为只传当前短 `prompt` 与 `session_id`，避免把历史工具调用消息原样带入 notice 事件触发的主动请求。

### Changed
- `metadata.yaml` version：v1.3.0 → v1.3.1
- 部署约定更新：本地仓库作为唯一开发仓库；服务器仅同步已发布代码，不再作为开发仓库使用。

---

## [v1.3.0] - 2026-06-07

### Added
- **接管 chat_plus 的戳一戳响应**（v1.3+）：
  - `_build_poke_event_text` 静态方法（移植自 chat_plus 的 `build_persistent_poke_event_text`）
  - `_get_conversation` 异步方法（移植自 pokepro 的 `LLMService.get_conversation`）
  - `on_group_poke` 的"bot 被戳"分支重写：累积 PokeLog + **主动调用 `event.request_llm()`** 让 LLM 立刻回应
- **与 chat_plus 完全解耦**：
  - 不再依赖 chat_plus 的 `bot_only` 模式构造伪消息
  - 跟 chat_plus 设 `ignore` / `bot_only` / `all` 都兼容
  - chat_plus 设为 `ignore` 时，litepoke 仍能主动响应戳一戳
- **4 个新配置项**：
  - `respond_poked_enabled`（默认 true）
  - `respond_poked_prob`（默认 1.0，范围 0-1）
  - `respond_poked_cd`（默认 10 秒）
  - `respond_poked_prompt`（默认 prompt 模板）

### Changed
- `on_group_poke` 函数：从纯 `await` 改为 `async def` + `yield`（async generator），可以 yield `event.request_llm()` 响应
- `on_group_poke` 的"bot 被戳"分支：从"PokeLog 累积 + return" 改为 "PokeLog 累积 + 主动调 LLM + yield 响应"
- `metadata.yaml` version：v1.2.0 → v1.3.0

### Design Principles
- **零依赖**：依然不调任何外部插件，`_build_poke_event_text` 和 `_get_conversation` 都是**移植代码**到 litepoke 内部
- **可降级**：CD 期间、概率未命中、conversation 拿不到 → 静默 return，不影响 PokeLog 累积
- **防刷屏**：`respond_poked_cd` 默认 10 秒，群友狂戳 bot 不会烧 token
- **跟 chat_plus 协同**：chat_plus 正常工作，litepoke 只**接管**戳一戳响应这一块

### Notes
- 每次 bot 被戳 → 1 次 LLM 调用（约 200-500 tokens）
- 100% 概率（默认）+ 10s CD：群友狂戳 bot 时，10 秒内只回应 1 次
- 日志会打印 `[litepoke] 接管戳一戳：group=... sender=... -> 主动调 LLM` 方便追踪

---

## [v1.2.0] - 2026-06-07

### Added
- **PokeLog 数据类**（双维度累积日志）：
  - `recent: deque[(sender, time)]` — 短期窗口（默认 60s）的戳一戳事件
  - `daily: dict[date, dict[sender, count]]` — 按日累计按 sender 分人
  - 方法：`record` / `prune_recent` / `prune_daily` / `consume_recent` / `top_sender_today` / `total_today` / `to_dict` / `from_dict`
- **PokeLog JSON 持久化**：
  - 默认路径 `<plugin_data>/astrbot_plugin_litepoke/poke_log.json`
  - 启动时 `_load_poke_log()` 恢复数据
  - `terminate()` 写盘
  - 间隔写盘：每 60s 自动保存
- **延迟反馈机制**（CD 期间静默累积）：
  - `poke_user` CD 期间不返回错误，改返回 `""` 静默累积到 PokeLog
  - `on_group_poke` 检测到 bot 自己被戳 → 累积 PokeLog，**不调 LLM**、**不污染 conversation**
- **`on_llm_request` 双通道注入**：
  - 通道 1（PokeLog 统计）：总是执行。LLM 消费 recent 后清空
  - 通道 2（关键词引导）：保持原行为
- **`_build_poke_log_block` 方法**：构造"刚被戳 N 次 / 今天总共 M 次 / 主力是谁"自然语言块
- **5 个新配置项**：
  - `poke_log_persist`（默认 true）
  - `poke_log_path`（默认空，用自带路径）
  - `poke_log_window`（默认 60 秒）
  - `poke_log_daily_keep_days`（默认 7 天）
  - `poke_log_save_interval`（默认 60 秒）

### Changed
- `poke_user` CD 期间行为：从"返回错误"改为"静默累积 + 返回空串"
- `on_group_poke` bot 自己被戳：从"直接 return"改为"累积 PokeLog"
- `on_llm_request`：从"单通道（仅关键词）"改为"双通道（总是 PokeLog + 条件关键词）"
- `terminate`：增加 `_save_poke_log()`

### Design Principles
- **零依赖**：不调 livingmemory，litepoke 自己存 JSON
- **不污染 conversation**：通过 on_llm_request 注入 system_prompt，不是写历史消息
- **延迟反馈**：戳一戳是 notice 事件，**默认不进** LLM 上下文；通过 PokeLog 累积 + 用户发消息时 LLM 消费，间接让 LLM 知道
- **精确 vs 模糊**：精确数字用 PokeLog 自己存（count 是整数）；语义偏好才适合用 livingmemory

### Notes
- 一个活跃群今天被戳 50 次的 PokeLog 增量：~50 entries + JSON 写盘 < 1KB
- 跨重启：数据保留最近 7 天（`poke_log_daily_keep_days` 可调）
- LLM 看不到戳一戳事件本身（notice 事件），但能看到 PokeLog 累积后的统计

---

## [v1.1.0] - 2026-06-07

## [v1.1.0] - 2026-06-07

### Added
- **群内戳一戳事件监听器 (`on_group_poke`)**：监听 aiocqhttp 群内戳一戳事件（`notice/notify/poke`），按概率跟戳"别人戳别人"
- **`GroupVibe` 滑动窗口**：轻量版群氛围追踪，仅记录时间戳和最近一次事件的发送者（O(1) deque）
- **`on_group_message` 钩子**：群内每条消息进来维护滑动窗口（消息数 / 说话人）
- **vibe 概率调整规则**（`_vibe_adjust`）：
  - 窗口内消息数 ≥ `vibe_active_threshold` → 跟戳概率 ×1.5（封顶 1.0）
  - 窗口内消息数 < `vibe_quiet_threshold` → 跟戳概率 ×0.3
  - 窗口内已跟戳次数 ≥ `vibe_max_in_window` → 概率 = 0
  - 被戳的人不是最近说话的人 → 概率 ×0.5
- **跟戳配置项**：
  - `follow_enabled`（默认 true）
  - `follow_prob`（默认 0.1，范围 0-1）
  - `follow_cd`（默认 3 秒）
  - `vibe_window`（默认 60 秒）
  - `vibe_active_threshold`（默认 5 条）
  - `vibe_quiet_threshold`（默认 1 条）
  - `vibe_max_in_window`（默认 1 次）

### Notes
- vibe 决策**不调 LLM**，纯规则，O(1) 开销
- 跟戳走 `_last_any_poke` 全局CD，与 `poke_user` 共享，不会冲突
- 戳一戳事件是 `notice` 类型，**不会**进 LLM 对话上下文（默认 AstrBot 行为）
- 跟戳决策 reason 写到 `logger.debug`，可在 AstrBot 日志里看 `reason=active(7),target_silent` 之类

---

## [v1.0.0] - 2026-06-06

### 🎉 首个稳定版本

litepoke 正式可用的第一个版本，包含完整的戳一戳自主决策能力 + 跨平台表情回退。

### Added
- **LLM 工具 `poke_user`**：模型可自主决定调用，支持 aiocqhttp 真戳和其他平台表情回退
- **`emotion` 参数**：LLM 可指定表情标签（baka/angry/sad/...）
- **三级回退链**：meme 表情包 → QQ face → 文字
- **`on_llm_request` 场景引导钩子**：关键词触发 + 引导CD，防止刷屏
- **CD 控制**：全局CD + per-user CD + 引导CD 三层限频
- **meme 索引缓存**：目录 mtime 检测，目录未变不重扫
- **`meme_dir` 配置项**：可覆盖默认路径，默认走插件自带数据目录
- **首次启动自动建目录**：`<plugin_data>/astrbot_plugin_litepoke/memes/`

### Documentation
- `README.md` — 安装、配置、使用、跨平台部署、故障排查
- `EMOTION_GUIDE.md` — 26标签体系在litepoke场景下的推荐配置
- `CHANGELOG.md` — 本文件

---

## [v0.3.0] - 2026-06-06（开发期）

### Changed
- **表情包目录完全独立**：移除对 meme_manager 插件的依赖推断
- **默认 meme_dir 改为插件自带**：`data/plugin_data/astrbot_plugin_litepoke/memes`
- **自动建目录机制**：插件初始化时 `mkdir(parents=True, exist_ok=True)`
- **`meme_dir` 配置项语义调整**：留空 = 用自带路径，填了才覆盖

### Documentation
- `EMOTION_GUIDE.md` 起草，列出必装/推荐/可选/不推荐四组标签

---

## [v0.2.0] - 2026-06-06（开发期）

### Added
- **跨平台支持**：webchat / telegram / 飞书等非 aiocqhttp 平台的表情回退
- **`emotion` 参数**：可选标签名，控制表情包选择
- **`fallback_face_id` 配置**：QQ face 兜底
- **`fallback_text` 配置**：纯文字兜底
- **`enable_meme_fallback` 配置**：表情回退开关
- **meme 索引系统**：扫描子目录建立 `emotion → [paths]` 映射

### Fixed
- **MessageChain 导入错误**：`MessageChain` 来自 `astrbot.core.message.message_event_result`，不在 `components` 里
- **user_id 校验过严**：原本固定校验 `isdigit()`，导致 webchat 字符串 ID（"chiriu"）被拒绝
  - 改为：仅 aiocqhttp 校验纯数字，其他平台宽松接受
- **私聊开关在 aiocqhttp 下被错误拦截**：`only_group` 逻辑调整

### Changed
- **`poke_user` 签名扩展**：`poke_user(user_id, times=1, emotion=None)`
- **作用域 key 调整**：私聊用 `_private` 作为 scope key

---

## [v0.1.0] - 2026-06-06（开发期）

### 🎉 初版

### Added
- **基础 LLM 工具 `poke_user(user_id, times)`**：仅支持 aiocqhttp 真戳
- **aiocqhttp 群聊戳一戳**：`group_poke` 多次发送支持
- **aiocqhttp 私聊戳一戳**：`friend_poke` 单次发送
- **CD 控制**：
  - 全局CD（`global_cd`，默认5秒）
  - per-user CD（`user_cd`，默认60秒）
- **`on_llm_request` 场景引导**：
  - `trigger_keywords` 关键词列表（默认：笨蛋/人机/机器人/bot/傻）
  - `guide_cd` 引导CD（默认30秒）
  - `guide_prompt` 引导文本注入到 `system_prompt` 末尾
- **配置 schema**：
  - `global_cd`, `user_cd`, `poke_max_times`, `poke_interval`
  - `trigger_keywords`, `guide_cd`, `guide_prompt`, `only_group`
- **meme_manager 集成**（v0.1.0 早期版本）：默认从 `data/plugin_data/meme_manager/memes/` 推断路径
  - 后在 v0.3.0 移除该依赖

### Notes
- 整个插件单文件实现，~150 行 Python
- 依赖：仅 AstrBot 核心 API
- 平台：仅 `aiocqhttp`
- metadata.yaml 标识 v1.0.0（开发过程使用同一版本号）

---

## 版本演进图

```
v0.1.0 ──→ v0.2.0 ──→ v0.3.0 ──→ v1.0.0
基础功能    跨平台回退   表情包独立   文档完善
   │           │           │          │
  QQ真戳     meme/face   不再依赖   README
  +CD       三级降级     meme_mgr   EMOTION_GUIDE
  +引导     修两bug     自带目录     CHANGELOG
```

---

## 未发布的计划

> 这些是讨论中提到但还没落地的方向，仅作记录。

### 考虑中
- **被戳反戳**：检测到别人戳 bot 时自动反戳（pokepro 有，litepoke 暂未实现）
- **表情回执计数**：记录每个标签被选中的次数，便于调优
- **批量同步脚本**：从 meme_manager 同步标签的独立工具脚本
- **mtime 热加载**：检测到 meme_dir 新增图片时自动刷新索引（目前只在第一次或目录变化时刷新）

### 设计原则
- **保持单文件**：除非必要不拆模块
- **零硬依赖**：不依赖 meme_manager，不依赖外部插件
- **失败优雅**：任何环节失败都要降级，不能让 tool 抛异常
- **文档同步**：CHANGELOG / README / EMOTION_GUIDE 跟代码同步更新

---

## 反馈

发现 bug 或有功能建议，可以在 AstrBot 插件管理页面查看日志，或在对应的 issue 页面反馈。
