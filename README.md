# 轻量智能戳一戳（astrbot_plugin_litepoke）

一个把"戳一戳"能力做轻的 AstrBot 插件：

- **aiocqhttp（QQ）平台**：发送真实的戳一戳通知
- **其他平台**（webchat / telegram / 飞书 / 企业微信等）：用表情包 / QQ face / 文字模拟"戳"这个动作
- **LLM 自主判断**：注册 `poke_user` 工具，模型自行决定何时戳
- **场景引导钩子**：检测关键词 → 往 system_prompt 注入提示 → 让 LLM 考虑调用
- **群内跟戳**（v1.1+）：监听群内戳一戳事件，按概率跟戳"别人戳别人"，基于滑动窗口的轻量群氛围决策

代码刻意保持单文件、零依赖、易读易改。

---

## 特性

| 项 | 说明 |
|---|---|
| 平台 | aiocqhttp（真戳）+ 其他平台（表情回退） |
| 调用方式 | LLM tool 自主调用 + 关键词场景引导 + **群内概率跟戳** |
| 表情来源 | 插件自带数据目录，**不依赖** meme_manager |
| 限频 | 全局CD + per-user CD + 引导CD + 跟戳CD |
| 失败回退 | meme → QQ face → 文字，三级降级 |
| 群氛围决策 | 60s 滑动窗口，O(1) 规则调概率，**不调 LLM** |
| 代码量 | 单文件约 540 行 |

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

所有配置都在 AstrBot WebUI 的插件配置页里可调（首次启动后才会显示）。

| 配置项 | 默认 | 说明 |
|---|---|---|
| `global_cd` | 5 | 任意两次戳一戳之间的最小间隔（秒） |
| `user_cd` | 60 | 同一用户被戳后冷却（秒） |
| `poke_max_times` | 3 | aiocqhttp 下 LLM 传入的 times 上限 |
| `poke_interval` | 0.5 | 多次戳时每次间隔（秒） |
| `trigger_keywords` | `["笨蛋","人机","机器人","bot","傻"]` | 命中即注入引导提示的关键词 |
| `guide_cd` | 30 | 同一会话内引导提示最小间隔（秒） |
| `guide_prompt` | 见配置 | 注入到 system_prompt 的引导文本 |
| `only_group` | true | 是否仅在群聊生效 |
| `enable_meme_fallback` | true | 非 aiocqhttp 平台是否启用表情回退 |
| `meme_dir` | 空 | 自定义表情根目录；留空 = 插件自带目录 |
| `default_emotion` | `baka` | 默认 emotion 标签 |
| `fallback_face_id` | 0 | meme 不可用时的 QQ face ID；0=关闭 |
| `fallback_text` | `（戳了你一下）` | 所有回退都失败时的文字 |
| `follow_enabled` | true | 是否启用群内跟戳 |
| `follow_prob` | 0.1 | 跟戳基础概率（0-1），vibe 调整后会变 |
| `follow_cd` | 3 | 两次跟戳之间最小间隔（秒） |
| `vibe_window` | 60 | 群内滑动窗口秒数 |
| `vibe_active_threshold` | 5 | 窗口内消息数 ≥ 此值算群活跃，跟戳概率 ×1.5 |
| `vibe_quiet_threshold` | 1 | 窗口内消息数 < 此值算群冷清，跟戳概率 ×0.3 |
| `vibe_max_in_window` | 1 | 窗口内最多跟戳几次 |
| `poke_log_persist` | true | 是否持久化 PokeLog 到 JSON |
| `poke_log_path` | 空 | PokeLog JSON 路径；留空用自带 |
| `poke_log_window` | 60 | 短期窗口秒数（recent 队列） |
| `poke_log_daily_keep_days` | 7 | 日累计保留天数 |
| `poke_log_save_interval` | 60 | 写盘间隔秒数 |

---

## 工具签名（给 LLM 看）

```
poke_user(user_id, times=1, emotion=None)
```

- `user_id`（必填）：目标用户ID
  - aiocqhttp：必须是纯数字 QQ 号
  - 其他平台：任意字符串（webchat 用 sender_id 即可）
- `times`（可选）：aiocqhttp 下戳的次数，1-3
- `emotion`（可选）：表情标签名，如 `baka` / `angry` / `sad`。仅非 aiocqhttp 生效

**tool 返回值**是中文文本，会作为 tool result 喂回 LLM，用于让 LLM 继续生成总结回复。

---

## 使用建议

### 1. 引导 LLM 调用

在 AstrBot 的 **人设 / system prompt** 里加一句：

> 你可以使用 `poke_user` 工具主动戳用户，但仅在符合当前语境的场景下使用，不要滥用。

`trigger_keywords` 命中时也会自动注入引导提示（`guide_prompt`）。

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

**调试方法**：AstrBot 日志里搜 `[litepoke]`，DEBUG 级别会打印 `跟戳 group=... target=... reason=active(7),target_silent` 这种日志。修改 `astrbot_config.yaml` 把 litepoke 的日志级别调到 DEBUG。

### 5. PokeLog 累积日志（v1.2+）

戳一戳是 `notice` 事件，**默认不进** LLM 对话上下文。litepoke 用 PokeLog 把戳一戳事件**累积**起来，等用户发消息时再通过 `on_llm_request` 注入统计。

**两个维度**：

| 字段 | 窗口 | 用途 |
|---|---|---|
| `recent` | 60s 短期 | "刚被戳了 N 次" |
| `daily` | 24h 长期按 sender 分人 | "今天 A 戳了你 M 次" |

**PokeLog 文件位置**：

```text
data/plugin_data/astrbot_plugin_litepoke/poke_log.json
```

**CD 期间被戳**：静默累积到 PokeLog，**不发任何消息**。等用户说话时 LLM 一次性看到。

**示例 LLM 引导注入**：

```text
[戳一戳统计] 刚被戳了 3 次（60秒内），其中 alice 戳了 2 次；
今天总共被戳 8 次，alice 是'主力'（戳了 5 次）。
如果觉得对方过界了，可以考虑用 poke_user 工具戳回去或发个文字吐槽。
```

**消费机制**：LLM 看到 PokeLog 统计后，**recent 被清空**（防止下次重复注入）。`daily` 不清空，跨重启保留 7 天。

### 6. 戳一戳事件和 LLM 上下文

- 戳一戳是 OneBot 的 `notice/poke` 事件，**默认不进** LLM 的对话上下文
- `poke_user` tool 是 LLM 在**用户发消息**时自主决定调用的工具
- 跟戳（v1.1+）是**纯规则**动作，不触发 LLM，**不污染**上下文
- PokeLog（v1.2+）通过 `on_llm_request` 钩子**延迟注入**统计块，让 LLM 后续响应时能感知

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
- `user_cd` 默认 60 秒，会拦住重复戳同一人
- 但不同用户之间不受这个限制

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
