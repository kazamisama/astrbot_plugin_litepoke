# 更新日志

litepoke 的所有版本变更记录。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
