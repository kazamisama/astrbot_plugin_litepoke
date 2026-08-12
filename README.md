# 轻量智能戳一戳（astrbot_plugin_litepoke）

一个把"戳一戳"能力做轻的 AstrBot 插件：

- **aiocqhttp（QQ）平台**：发送真实的戳一戳通知
- **其他平台**（webchat / telegram / 飞书 / 企业微信等）：用表情包 / QQ face / 文字模拟"戳"这个动作
- **LLM 自主判断**：注册 `poke_user` 工具，模型自行决定何时戳
- **场景引导钩子**：检测关键词 → 以临时 TextPart 注入提示 → 让 LLM 考虑调用
- **群内跟戳**（v1.1+）：监听群内戳一戳事件，按概率跟戳"别人戳别人"，基于滑动窗口的轻量群氛围决策
- **被戳回应**（v1.4+）：bot 被戳时把 poke notice 伪造成普通文字消息重投递，群聊/私聊统一走 AstrBot 标准消息链路

代码刻意保持单文件、零依赖、易读易改。

---

## 特性

| 项 | 说明 |
|---|---|
| 平台 | aiocqhttp（真戳）+ 其他平台（表情回退） |
| 调用方式 | LLM tool 自主调用 + 关键词场景引导 + **群内概率跟戳** + **被戳伪消息重投递** |
| 表情来源 | 插件自带数据目录，**不依赖** meme_manager |
| 限频 | 引导CD + 跟戳CD + 被戳主动响应CD；`poke_user` 工具本身不再做静默CD |
| 失败回退 | meme → QQ face → 文字，三级降级 |
| 群氛围决策 | 60s 滑动窗口，O(1) 规则调概率，**不调 LLM** |
| PokeLog | JSON 持久化带 `schema_version`，保存采用临时文件原子替换，坏文件会保留为 `.corrupt.<timestamp>` |
| 诊断日志 | 可选 `debug_diagnostics`，排查 CD、概率未命中、私聊禁用、路径不可用等跳过原因 |
| 代码量 | 单文件，零依赖，易读易改 |

---

## 安装

把整个 `astrbot_plugin_litepoke/` 文件夹丢到 AstrBot 的 `data/plugins/` 目录下，重启 AstrBot 即可。

首次启动会**自动创建**表情根目录：

```
data/plugin_data/astrbot_plugin_litepoke/memes/
```

---

## 表情包怎么放

**目录结构**：子目录名 = emotion 标签名，里面放图片。

```
data/plugin_data/astrbot_plugin_litepoke/memes/
├── baka/        # 吐槽、可爱的嘟嘴
│   ├── 1.jpg
│   └── 2.png
├── sad/         # 低落失意
├── angry/       # 抱怨反驳
├── happy/       # 开心庆祝
├── cry/         # 委屈带泪
├── shy/         # 害羞
└── ...          # 想加几个标签就建几个目录
```

**支持的文件格式**：`.jpg` `.jpeg` `.png` `.gif` `.webp` `.bmp`

**默认行为**：

- LLM 传了 `emotion`（如 `baka`） → 从 `memes/baka/` 随机选一张
- LLM 没传 / 标签不存在 → 用配置的 `default_emotion`（默认 `baka`）
- 标签目录都没有 → 随机抽一个存在的标签
- meme 全空 → 降级到 QQ face → 再降级到文字

**也可以指向其他目录**：在配置里填 `meme_dir` 覆盖默认路径。

---

## 配置项

所有配置都在 AstrBot WebUI 的插件配置页里可调（首次启动后才会显示）。WebUI 里会按下方功能顺序展示；配置名保持不变，只调整顺序和说明前缀，兼容旧配置。

### 基础作用域

| 配置项 | 默认 | 说明 |
|---|---|---|
| `only_group` | true | 是否仅在群聊生效；为 true 时私聊 `poke_user` 和私聊被戳主动回应都会关闭 |

### LLM 主动戳

| 配置项 | 默认 | 说明 |
|---|---|---|
| `poke_max_times` | 3 | aiocqhttp 下 LLM 传入的 times 上限 |
| `poke_interval` | 0.5 | 多次戳时每次间隔（秒） |

### 普通消息引导

| 配置项 | 默认 | 说明 |
|---|---|---|
| `trigger_keywords` | `["笨蛋","人机","机器人","bot","傻"]` | 命中即注入引导提示的关键词 |
| `guide_cd` | 30 | 同一会话内引导提示最小间隔（秒） |
| `guide_prompt` | 见配置 | 普通消息命中关键词时以临时 TextPart 注入的轻量引导；不处理 bot 被戳事件 |

### 表情回退

| 配置项 | 默认 | 说明 |
|---|---|---|
| `enable_meme_fallback` | true | 非 aiocqhttp 平台是否启用表情回退 |
| `meme_dir` | 空 | 自定义表情根目录；留空 = 插件自带目录 |
| `default_emotion` | `baka` | 默认 emotion 标签 |
| `fallback_face_id` | 0 | meme 不可用时的 QQ face ID；0=关闭 |
| `fallback_text` | `（戳了你一下）` | 所有回退都失败时的文字 |

### bot 被戳回应

| 配置项 | 默认 | 说明 |
|---|---|---|
| `respond_poked_enabled` | true | 主动回应被戳开关 |
| `respond_poked_mode` | `direct` | `direct`=直接表情/文字回退，最稳定；`llm`/`replay`=走 AstrBot 原生 LLM 请求链 |
| `respond_poked_prob` | 1.0 | 被戳响应概率（0-1） |
| `respond_poked_cd` | 10 | 被戳响应 CD（秒）|
| `respond_poked_write_context` | true | 被戳 notice 是否作为纯文本 user 消息写入官方 conversation；CD 内只写入不即时回应 |
| `respond_poked_prompt` | 见配置 | 被戳响应 prompt 模板；`llm`/`replay` 模式下使用 `{poke_event}` 占位符 |

### 群内跟戳

| 配置项 | 默认 | 说明 |
|---|---|---|
| `follow_enabled` | true | 是否启用群内跟戳 |
| `follow_prob` | 0.1 | 跟戳基础概率（0-1），vibe 调整后会变 |
| `follow_cd` | 3 | 两次跟戳之间最小间隔（秒） |
| `follow_trace_inject_enabled` | true | 是否把自动跟戳成功的概率命中原因短期注入给下一次 LLM 请求，用于解释“为什么戳他” |
| `follow_trace_ttl` | 60 | 跟戳解释缓存保留秒数；过期未被消费会丢弃，只保存在内存 |

### 群氛围决策

| 配置项 | 默认 | 说明 |
|---|---|---|
| `vibe_window` | 60 | 群内滑动窗口秒数 |
| `vibe_active_threshold` | 5 | 窗口内消息数 ≥ 此值算群活跃，跟戳概率 ×1.5 |
| `vibe_quiet_threshold` | 1 | 窗口内消息数 < 此值算群冷清，跟戳概率 ×0.3 |
| `vibe_max_in_window` | 1 | 窗口内最多跟戳几次 |

### PokeLog 统计

| 配置项 | 默认 | 说明 |
|---|---|---|
| `poke_log_persist` | true | 是否持久化 PokeLog 到 JSON |
| `poke_log_path` | 空 | PokeLog JSON 路径；留空用自带 |
| `poke_log_window` | 60 | 短期窗口秒数（recent 队列） |
| `poke_log_daily_keep_days` | 7 | 日累计保留天数 |
| `poke_log_save_interval` | 60 | 写盘间隔秒数 |
| `poke_log_inject_enabled` | false | 是否把 PokeLog 统计以临时 TextPart 注入，默认关闭 |

### 诊断

| 配置项 | 默认 | 说明 |
|---|---|---|
| `debug_diagnostics` | false | 是否输出 `[litepoke][diag]` 诊断日志；排查问题时开启，平时建议关闭 |

---

## 工具签名（给 LLM 看）

```
poke_user(user_id, times=1, emotion=None)
```

- `user_id`（必填）：目标用户ID
  - aiocqhttp：必须是纯数字 QQ 号；群聊会带 `group_id` 调用，私聊会优先使用 OneBot `send_poke`，失败时再尝试 `friend_poke` 兜底
  - 其他平台：任意字符串（webchat 用 sender_id 即可）
- `times`（可选）：aiocqhttp 下戳的次数，1-3
- `emotion`（可选）：表情标签名，如 `baka` / `angry` / `sad`。仅非 aiocqhttp 生效

**tool 返回值**是中文文本，会作为 tool result 喂回 LLM，用于让 LLM 继续生成总结回复。

---

## 使用建议

### 1. 引导 LLM 调用

在 AstrBot 的 **人设 / system prompt** 里加一句：

> 你可以使用 `poke_user` 工具主动戳用户，但仅在符合当前语境的场景下使用，不要滥用。

`trigger_keywords` 命中普通消息时也会自动注入轻量引导提示（`guide_prompt`）。它只负责提醒 LLM 有 `poke_user` 这个互动工具；bot 被戳后的即时回应由 `respond_poked_prompt` 单独控制。

### 2. 表情选择策略

- 让 LLM **每次都传 `emotion`**：表达更精准，但 LLM 偶尔会瞎传
- 让 LLM **不传 `emotion`**：完全用默认标签（`baka`），简单稳定
- 折中：在引导 prompt 里**强烈建议**传 emotion，但不强制

### 3. 跨平台部署

- QQ 群：真戳，体验最好
- 私聊（aiocqhttp）：戳好友，效果也不错
- webchat / 其他：自动降级到表情包，推荐先放 5-10 个常用标签的表情

### 4. 跟戳调优

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

### 5. PokeLog 累积日志（v1.2+）

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

### 6. bot 被戳主动回应（v1.3+）

litepoke 自 v1.3 起在 bot 被戳时会**主动**走完整响应链：累积 PokeLog → 调 LLM（或回退）→ 发出可见消息。早期版本（≤ v1.2）只静默累积 PokeLog，**不会**主动回应被戳；从 v1.3 起默认会主动回应，让被戳变得“有触感”。

**触发流程**：

```text
A 戳了 bot
   ↓
on_group_poke 触发
   ↓ 累积 PokeLog（仅统计，默认不注入提示词）
   ↓ _build_poke_event_text 构造戳一戳事件文本
   ↓ 可选：写入官方 conversation（respond_poked_write_context；不受 respond_poked_cd 影响）
   ↓ 检查 respond_poked_enabled
   ↓ 检查 respond_poked_cd（仅控制是否主动调 LLM，防刷屏）
   ↓ 检查 respond_poked_prob（概率）
   ↓ 优先获取当前 conversation
   ↓ 获取全局 LLM 工具集（确保 poke_user 可用）
   ↓ conversation 可用：event.request_llm(prompt, conversation, func_tool_manager, tool_set) → yield
   ↓ conversation 不可用：获取当前 persona system_prompt 后降级 request_llm(prompt, session_id, system_prompt, func_tool_manager, tool_set) → yield
   ↓
LLM 结合 conversation 中的 poke_event、最近上下文和人格决定回应；降级路线只结合人格和 poke_event 文本
```

这里不使用 `guide_prompt`；`guide_prompt` 只用于普通消息命中关键词时的轻量工具提醒，被戳事件由 `respond_poked_prompt` 独立控制。

**默认 prompt**：

```text
[戳一戳事件]有人戳了你，发起者是 alice(ID:123456)。
请用符合人设的方式简短回应（1-2 句），可以反戳回去（用 poke_user 工具）或吐槽。
```

**调优建议**：

- `respond_poked_prob = 1.0`（默认）— 100% 响应
- `respond_poked_prob = 0.5` — 一半的戳一戳会响应
- `respond_poked_cd = 10`（默认 10s）— 群友狂戳 bot 时 10 秒只回 1 次
- `respond_poked_enabled = false` — 完全关掉被戳主动回应，回到 v1.2.0 行为（只累积 PokeLog）

### 7. 戳一戳事件和 LLM 上下文

- 戳一戳是 OneBot 的 `notice/poke` 事件，平台本身**默认不进** LLM 对话上下文
- `poke_user` tool 是 LLM 在**用户发消息**时自主决定调用的工具
- 跟戳（v1.1+）是**纯规则**动作，不触发 LLM，**不污染**上下文
- PokeLog（v1.2+）通过 `on_llm_request` 钩子以临时 TextPart **延迟注入**统计块，让 LLM 后续响应时能感知被戳统计
- 提示词注入（v1.4.9+）统一走 `extra_user_content_parts` + `mark_as_temp()`：跟戳解释 / PokeLog / 关键词引导只发给 LLM，不写入会话历史，也不污染 system_prompt 前缀缓存
- **主动回应**（v1.3+）：bot 被戳时**主动**触发一次 LLM 调用，让 LLM 立刻回应
- **写入上下文**（v1.3.4+）：开启 `respond_poked_write_context` 后，bot 被戳的 notice 会作为一条纯文本 `user` 消息写入官方 conversation，后续普通对话也能自然感知这次戳一戳事件
- 为避免 OpenAI 兼容接口的工具调用历史配对问题，litepoke 不会写入 `tool` / `tool_calls`。主动回应时会优先传入官方 conversation；由于当前戳一戳事件已写入 conversation，prompt 只引用“上文最新的戳一戳事件”，避免同一 `poke_text` 重复出现。若 conversation 获取失败，则降级为只传 `poke_event` prompt + 当前 persona `system_prompt`

---

## 故障排查

**插件加载失败：cannot import name 'MessageChain'**
- 这是导入路径问题。`MessageChain` 在 `astrbot.core.message.message_event_result`，不在 `components`。
- 本插件已修正。如果自定义时报这个错，对照 `main.py` 顶部的 import。

**调用 tool 后没收到任何消息**
- 检查 `meme_dir` 是否有图
- 看 AstrBot 日志，会打印 `[litepoke]` 前缀的警告
- 确认 `enable_meme_fallback` 没被关掉

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
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # 配置 schema
├── README.md            # 本文件
├── EMOTION_GUIDE.md     # 表情分类建议
├── CHANGELOG.md         # 版本变更记录
├── LICENSE              # AGPL-3.0
└── .gitignore
```

数据目录（运行时自动创建）：

```
data/plugin_data/astrbot_plugin_litepoke/
└── memes/               # 表情根目录
    ├── baka/
    ├── sad/
    └── ...
```

---

## License

AGPL-3.0
