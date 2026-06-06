# 轻量智能戳一戳（astrbot_plugin_litepoke）

一个只做一件事的 AstrBot 插件：**把"戳一戳"的能力交给 LLM 自主判断**。

- **aiocqhttp（QQ）平台**：发送真实的戳一戳通知
- **其他平台**（webchat / telegram / 飞书 / 企业微信等）：用表情包 / QQ face / 文字模拟"戳"这个动作
- 配套场景引导钩子：检测关键词 → 往 system_prompt 注入提示 → 让 LLM 考虑调用

代码刻意保持单文件、零依赖、易读易改。

---

## 特性

| 项 | 说明 |
|---|---|
| 平台 | aiocqhttp（真戳）+ 其他平台（表情回退） |
| 调用方式 | LLM tool 自主调用 + 关键词场景引导 |
| 表情来源 | 插件自带数据目录，**不依赖** meme_manager |
| 限频 | 全局CD + per-user CD + 引导CD |
| 失败回退 | meme → QQ face → 文字，三级降级 |
| 代码量 | 单文件约 280 行 |

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
└── README.md            # 本文件
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
