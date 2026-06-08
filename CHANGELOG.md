# 更新日志

litepoke 的所有版本变更记录。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
