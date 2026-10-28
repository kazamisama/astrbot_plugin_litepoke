# 轻量智能戳一戳（astrbot_plugin_litepoke）

<p align="center">
  <img src="logo.png" width="240" alt="用力戳头的鲸鱼娘">
</p>

一个把"戳一戳"能力做轻的 AstrBot 插件：

- **aiocqhttp（QQ）平台**：发送真实的戳一戳通知
- **其他平台**（webchat / telegram / 飞书等）：没有"戳一戳"这个动作，调用 `poke_user` 会返回失败提示
- **LLM 自主判断**：注册 `poke_user` 工具，模型自行决定何时戳
- **场景引导钩子**：检测关键词 → 以临时 TextPart 注入提示 → 让 LLM 考虑调用
- **群内跟戳**（v1.1+）：监听群内戳一戳事件，按概率跟戳"别人戳别人"，基于滑动窗口的轻量群氛围决策
- **被戳回应**（v1.3+）：bot 被戳时按概率 / 间隔主动发起一次 LLM 回应，让模型按人设处理

代码刻意保持单文件、零依赖、易读易改。

---

## 特性

| 项 | 说明 |
|---|---|
| 平台 | aiocqhttp（真戳）；其他平台**不支持戳一戳**，调用返回失败提示 |
| 调用方式 | LLM tool 自主调用 + 关键词场景引导 + **群内概率跟戳** + **被戳主动回应** |
| 限频 | 引导CD + 跟戳CD + 被戳主动响应CD；`poke_user` 工具本身不再做静默CD |
| 群氛围决策 | 60s 滑动窗口，O(1) 规则调概率，**不调 LLM** |
| PokeLog | JSON 持久化带 `schema_version`，保存采用临时文件原子替换，坏文件会保留为 `.corrupt.<timestamp>` |
| 诊断日志 | 可选 `debug_diagnostics`，排查 CD、概率未命中、私聊禁用、路径不可用等跳过原因 |
| 代码量 | 单文件，零依赖，易读易改 |

---

## 工作原理

```text
LLM 决定戳人 ──> poke_user 工具（aiocqhttp 发真实戳一戳；其他平台返回不支持）
用户发消息 ──> on_llm_request：
   ├─ 自动跟戳解释（临时 TextPart，仅发给 LLM）
   ├─ PokeLog 统计（可选，默认关闭）
   └─ 关键词引导（命中 trigger_keywords 且过 guide_cd）
群内消息 ──> on_group_message：维护每个群的滑动窗口（vibe）
戳一戳事件 ──> on_group_poke：
   ├─ bot 被戳 ──> PokeLog + 写入上下文 + 发起 LLM 回应
   ├─ 别人戳别人 ──> 按 vibe 调整后的概率跟戳
   └─ bot 自己发出的戳 ──> 清理历史中残留的原始 poke 记录
```

- 注入给 LLM 的临时提示统一走 `extra_user_content_parts` + `mark_as_temp()`，不写入会话历史，也不破坏 system prompt 前缀缓存。
- 跟戳（`follow_*`）和群氛围（`vibe_*`）是纯规则动作，不触发 LLM；被戳回应是唯一会主动发起 LLM 请求的路径。
- 主动回应只走 LLM：bot 被戳且命中概率 / CD 后发起一次 LLM 请求；不再发送固定的直发文字。

---

## 安装

把整个 `astrbot_plugin_litepoke/` 文件夹丢到 AstrBot 的 `data/plugins/` 目录下，重启 AstrBot 即可。要求 AstrBot >= 4.13.0（见 `metadata.yaml` 的 `astrbot_version`）。

PokeLog 运行时数据保存在 `data/plugin_data/astrbot_plugin_litepoke/poke_log.json`，首次写盘时自动创建。

---

## 配置项

所有配置都在 AstrBot WebUI 的插件配置页里可调（首次启动后才会显示）。WebUI 会把配置按下面的功能拆成 8 个分组框；配置键名保持不变，插件内部仍按扁平键读取。升级时旧版扁平配置会在插件加载前自动迁移到分组结构，原文件备份为 `..._config.json.legacy.bak`。

### 基础

| 配置项 | 默认 | 说明 |
|---|---|---|
| `only_group` | true | 是否仅在群聊生效；为 true 时私聊 `poke_user` 和私聊被戳主动回应都会关闭 |

### LLM 主动戳

| 配置项 | 默认 | 说明 |
|---|---|---|
| `poke_max_times` | 3 | aiocqhttp 下 LLM 传入的 times 上限 |
| `poke_interval` | 0.5 | 多次戳时每次间隔（秒） |

### 关键词引导

| 配置项 | 默认 | 说明 |
|---|---|---|
| `trigger_keywords` | `["笨蛋","人机","机器人","bot","傻"]` | 命中即注入引导提示的关键词 |
| `guide_cd` | 30 | 同一会话内引导提示最小间隔（秒） |
| `guide_prompt` | 见配置 | 命中关键词时以临时 TextPart 注入的轻量引导；不处理 bot 被戳事件 |

### 被戳回应

| 配置项 | 默认 | 说明 |
|---|---|---|
| `respond_poked_enabled` | true | 主动回应被戳开关 |
| `respond_poked_prob` | 1.0 | 被戳响应概率（0-1） |
| `respond_poked_cd` | 10 | 被戳响应 CD（秒）|
| `respond_poked_write_context` | true | 被戳 notice 是否作为纯文本 user 消息写入官方 conversation；CD 内只写入不即时回应 |
| `respond_poked_prompt` | 见配置 | 被戳响应 prompt 模板；使用 `{poke_event}` 占位符 |

### 群内跟戳

| 配置项 | 默认 | 说明 |
|---|---|---|
| `follow_enabled` | true | 是否启用群内跟戳 |
| `follow_prob` | 0.1 | 跟戳基础概率（0-1），vibe 调整后会变 |
| `follow_cd` | 3 | 两次跟戳之间最小间隔（秒） |
| `follow_trace_inject_enabled` | true | 是否把自动跟戳成功的概率命中原因短期注入给下一次 LLM 请求，用于解释“为什么戳他” |
| `follow_trace_ttl` | 60 | 跟戳解释缓存保留秒数；过期未被消费会丢弃，只保存在内存 |

### 跟戳氛围

| 配置项 | 默认 | 说明 |
|---|---|---|
| `vibe_window` | 60 | 群内滑动窗口秒数 |
| `vibe_active_threshold` | 5 | 窗口内消息数 ≥ 此值算群活跃，跟戳概率 ×1.5 |
| `vibe_quiet_threshold` | 1 | 窗口内消息数 < 此值算群冷清，跟戳概率 ×0.3 |
| `vibe_max_in_window` | 1 | 窗口内最多跟戳几次 |

### 记录与统计

| 配置项 | 默认 | 说明 |
|---|---|---|
| `poke_log_persist` | true | 是否持久化 PokeLog 到 JSON |
| `poke_log_path` | 空 | PokeLog JSON 路径；留空用自带 |
| `poke_log_save_interval` | 60 | 写盘间隔秒数 |
| `poke_log_window` | 60 | 短期窗口秒数（recent 队列） |
| `poke_log_daily_keep_days` | 7 | 日累计保留天数 |
| `poke_log_inject_enabled` | false | 是否把 PokeLog 统计以临时 TextPart 注入，默认关闭 |

### 诊断

| 配置项 | 默认 | 说明 |
|---|---|---|
| `debug_diagnostics` | false | 是否输出 `[litepoke][diag]` 诊断日志；排查问题时开启，平时建议关闭 |

---

## 工具签名（给 LLM 看）

```
poke_user(user_id, times=1)
```

- `user_id`（必填）：目标用户ID
  - aiocqhttp：必须是纯数字 QQ 号；群聊会带 `group_id` 调用，私聊（需 `only_group=false`）会优先使用 OneBot `send_poke`，失败时再尝试 `friend_poke` 兜底
  - 其他平台：调用会返回「当前平台不支持戳一戳」，不会发任何消息
- `times`（可选）：aiocqhttp 下戳的次数，默认 1，上限 `poke_max_times`（默认 3），超出会被截断

**tool 返回值**是中文文本，会作为 tool result 喂回 LLM，用于让 LLM 继续生成总结回复。

---

## 使用建议

### 1. 引导 LLM 调用

在 AstrBot 的 **人设 / system prompt** 里加一句：

> 你可以使用 `poke_user` 工具主动戳用户，但仅在符合当前语境的场景下使用，不要滥用。

`trigger_keywords` 命中普通消息时也会自动注入轻量引导提示（`guide_prompt`）。它只负责提醒 LLM 有 `poke_user` 这个互动工具；bot 被戳后的即时回应由 `respond_poked_prompt` 单独控制。

### 2. 平台差异

- QQ 群：真戳，体验最好
- 私聊（aiocqhttp）：戳好友，效果也不错（需 `only_group=false`，默认关闭）
- webchat / telegram / 飞书等：没有"戳一戳"这个动作，也收不到戳一戳事件；`poke_user` 会返回不支持提示，不会发任何消息

### 3. 跟戳调优

`follow_prob` 是基础概率，**实际跟戳概率会按 vibe 调整**：

| 群内状态 | 调整后概率 |
|---|---|
| 60s 内消息数 ≥ 5（活跃） | base × 1.5（封顶 1.0） |
| 60s 内消息数 < 1（冷清） | base × 0.3 |
| 窗口内已跟戳 ≥ 1 | 0（不连戳） |
| 被戳的人最近没说话 | base × 0.5 |

**建议调参起点**：

- 想要"偶尔跟一戳"：`follow_prob = 0.1`
- 想要"频繁跟戳"：`follow_prob = 0.3`
- 想要"基本不跟戳"：`follow_prob = 0.02`
- 完全关掉跟戳：`follow_enabled = false`

**跟戳解释**：自动跟戳成功后，插件会把一次短期解释记录放进内存缓存，并在下一次同群 LLM 请求前以临时 TextPart 注入。记录包含基础概率、群氛围调整后概率、随机数 roll 和 `vibe_reason`，所以 bot 可以回答“刚才为什么戳他”，而不是凭空编理由。注入后即消费，超过 `follow_trace_ttl` 未消费会丢弃；如果不想让模型感知自动跟戳，关闭 `follow_trace_inject_enabled`。

**调试方法**：排查“为什么没跟戳/没回应”时，先在插件配置里临时打开 `debug_diagnostics`，AstrBot 日志里搜 `[litepoke][diag]`。常见 `reason` 包括 `hit`、`cooldown`、`prob_miss`、`disabled`、`private_disabled`、`path_unavailable`。正常运行时建议关掉，避免日志太碎。

---

## 行为说明

### 4. PokeLog 累积日志（v1.2+）

PokeLog 现在主要作为**统计缓存**保留，不再默认注入 LLM 提示词。v1.3.4+ 已经会把 bot 被戳的 notice 事件写入官方 conversation，通常不需要再用 PokeLog 做延迟感知。

**两个维度**：

| 字段 | 窗口 | 用途 |
|---|---|---|
| `recent` | 60s 短期 | 统计近期被戳次数 |
| `daily` | 24h 长期按 sender 分人 | 统计今天谁戳了多少次 |

**PokeLog 文件位置**：

```text
data/plugin_data/astrbot_plugin_litepoke/poke_log.json
```

**默认行为**：

- `poke_log_persist=true`：继续写盘保留统计
- `poke_log_inject_enabled=false`：默认不注入统计块
- 如确实想恢复旧版“刚被戳 N 次 / 今天总共 M 次”的提示词注入，可手动开启 `poke_log_inject_enabled`

**可选注入示例**：

```text
[戳一戳统计] 刚被戳了 3 次（60秒内），其中 alice 戳了 2 次；
今天总共被戳 8 次，alice 是'主力'（戳了 5 次）。
如果觉得对方过界了，可以考虑用 poke_user 工具戳回去或发个文字吐槽。
```

**消费机制**：只有开启 `poke_log_inject_enabled` 并实际注入时，`recent` 才会被消费清空；`daily` 不清空，跨重启保留 7 天。

### 5. bot 被戳主动回应（v1.3+）

litepoke 自 v1.3 起在 bot 被戳时会**主动**回应：累积 PokeLog → 按 `respond_poked_prob` / `respond_poked_cd` 决定是否触发一次 LLM 调用。v1.6 起去掉 direct 直发模式，统一走 LLM；模型不可用时不会发出固定文字兜底。

**触发流程**：

```text
A 戳了 bot
   ↓
on_group_poke 触发
   ↓ 累积 PokeLog（仅统计，默认不注入提示词）
   ↓ _build_poke_event_text 构造戳一戳事件文本
   ↓ 可选：写入官方 conversation（respond_poked_write_context；不受 respond_poked_cd 影响）
   ↓ 检查 respond_poked_enabled
   ↓ 检查 respond_poked_cd（防刷屏）
   ↓ 检查 respond_poked_prob（概率）
   ↓ 获取官方 conversation → event.request_llm(prompt, conversation) → yield
   ↓
LLM 结合 conversation 中的 poke_event（或 prompt 内联的 poke_event）与人设决定回应；
LLM 请求链抛错时只记录 warning，不再直发固定文字
```

这里不使用 `guide_prompt`；`guide_prompt` 只用于普通消息命中关键词时的轻量工具提醒，被戳事件由 `respond_poked_prompt` 独立控制。

**默认 prompt（`{poke_event}` 替换后的示例）**：

```text
[戳一戳事件]有人戳了你，发起者是 alice(ID:123456)。
请用符合人设的方式简短回应（1-2 句），可以反戳回去（用 poke_user 工具）或吐槽。
```

**调优建议**：

- `respond_poked_prob = 1.0`（默认）— 100% 响应
- `respond_poked_prob = 0.5` — 一半的戳一戳会响应
- `respond_poked_cd = 10`（默认 10s）— 群友狂戳 bot 时 10 秒只回 1 次
- `respond_poked_enabled = false` — 完全关掉被戳主动回应，回到 v1.2.0 行为（只累积 PokeLog）

### 6. 戳一戳事件和 LLM 上下文

- 戳一戳是 OneBot 的 `notice/poke` 事件，平台本身**默认不进** LLM 对话上下文
- `poke_user` tool 是 LLM 在**用户发消息**时自主决定调用的工具
- 跟戳（v1.1+）是**纯规则**动作，不触发 LLM，**不污染**上下文
- PokeLog（v1.2+）通过 `on_llm_request` 钩子以临时 TextPart **延迟注入**统计块，让 LLM 后续响应时能感知被戳统计
- 提示词注入（v1.4.9+）统一走 `extra_user_content_parts` + `mark_as_temp()`：跟戳解释 / PokeLog / 关键词引导只发给 LLM，不写入会话历史，也不污染 system_prompt 前缀缓存
- **主动回应**（v1.3+）：bot 被戳时按概率 / CD 触发一次 LLM 调用
- **写入上下文**（v1.3.4+）：开启 `respond_poked_write_context` 后，bot 被戳的 notice 会作为一条纯文本 `user` 消息写入官方 conversation，后续普通对话也能自然感知这次戳一戳事件
- 为避免 OpenAI 兼容接口的工具调用历史配对问题，litepoke 不会写入 `tool` / `tool_calls`。回应会优先传入官方 conversation；由于戳一戳事件已写入 conversation，prompt 只引用“上文最新的戳一戳事件”，避免同一 `poke_text` 重复出现。若 conversation 获取失败，则把 `poke_event` 直接内联进 prompt 发起 LLM 请求。

---

## 故障排查

**调用 tool 后没收到任何消息**
- 确认当前平台是 aiocqhttp：其他平台没有戳一戳动作，工具会直接返回失败提示
- 看 AstrBot 日志，会打印 `[litepoke]` 前缀的警告
- 确认 `only_group` 没把私聊场景关掉

**LLM 死活不调用 tool**
- 确认模型支持 function calling（DeepSeek-R1、Gemini 2.0 thinking 等不支持）
- 在人设里**显式提到** `poke_user` 这个工具名
- `/tool ls` 看插件是否在 tool 列表里

**每次都戳同一个人**
- `poke_user` 工具本身不再做静默 CD；如果需要减少重复动作，应通过人设约束、`poke_max_times`、平台限频或群内跟戳/被戳响应 CD 控制
- 跟戳分支仍受 `follow_cd` / `vibe_max_in_window` 约束；bot 被戳主动回应仍受 `respond_poked_cd` 约束

---

## 文件结构

```
astrbot_plugin_litepoke/
├── main.py              # 全部逻辑，单文件
├── logo.png             # 插件图标（AstrBot 自动识别）
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # 配置 schema
├── README.md            # 本文件
├── CHANGELOG.md         # 版本变更记录
├── tests/               # 单元测试（stdlib unittest，无需安装 AstrBot）
├── LICENSE              # AGPL-3.0
└── .gitignore
```

数据目录（运行时自动创建）：

```
data/plugin_data/astrbot_plugin_litepoke/
└── poke_log.json        # PokeLog 持久化
```

测试直接跑标准库 unittest（用桩替换 astrbot 依赖，不需要安装 AstrBot）：

```bash
python -m unittest discover -s tests
```

---

## License

Copyright (C) 2026 kazamisama

本项目以 [GNU Affero General Public License v3.0](LICENSE)（AGPL-3.0）发布。
