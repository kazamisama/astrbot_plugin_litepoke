# 表情包分类建议清单

litepoke 的 emotion 参数可以用任何标签名，但**不是所有 26 个标签都适合戳一戳场景**。本文档基于已有的 26 标签体系（meme_manager 已沿用），给出 litepoke 专属的推荐配置。

---

## 一、戳一戳场景的特点

戳一戳的语义是「**轻量互动**」：

- 用户调侃、挑衅、撒娇 → 需要回应
- 表达不满、反驳、吐槽 → 需要回应
- 表达开心、害羞、震惊 → 需要回应
- 不需要：时间状态、任务流、消息回复

所以重点放在**情绪表达 + 社交互动**两类。

---

## 二、标签推荐分组

### 🔴 必装（5 个）— 起步用这五个就够

| 标签 | 视觉标准 | 触发场景 |
|---|---|---|
| **baka** | 可爱嘟嘴、小抱怨 | 用户调侃、玩笑、互怼 |
| **angry** | 强烈反对、激愤表情 | 用户真惹毛了、严肃反驳 |
| **sad** | 低落嘟嘴、无泪 | 用户失落、道歉、不舍 |
| **cry** | 带泪、委屈、抽泣 | 用户伤心、抱怨不公 |
| **happy** | X 形眯眼（>‿<） | 用户开心、调侃成功、互动愉快 |

**起步清单**（最少配置）：

```
data/plugin_data/astrbot_plugin_litepoke/memes/
├── baka/
├── angry/
├── sad/
├── cry/
└── happy/
```

### 🟡 推荐（6 个）— 让表达更细腻

| 标签 | 视觉标准 | 触发场景 |
|---|---|---|
| **surprised** | 空心眼（高光消失）、惊呆 | 用户说怪话、出乎意料 |
| **confused** | 螺旋云朵眼、被动困惑 | 听不懂、用户讲的事太绕 |
| **shy** | 闭眼 + 脸红 | 被夸、被打趣到害羞 |
| **like** | 喜爱、温柔 | 表达欣赏、友好态度 |
| **heart** | 心形眼睛 | 表达喜欢、被打动 |
| **hi** | 打招呼、挥手 | 互动开场、欢迎回来 |

### 🟢 可选（5 个）— 看人设和场景决定

| 标签 | 视觉标准 | 触发场景 |
|---|---|---|
| **think** | 手指抵唇、主动思考 | 认真考虑用户的话 |
| **sigh** | 螺旋瞳、无奈 | 不想争辩、叹气 |
| **meow** | 卖萌、动物化 | 撒娇、装可爱 |
| **fool** | 自嘲、滑稽 | 嘲笑自己失误 |
| **excited** | 期待激动、>happy 强度 | 用户答应某事、好消息 |

### ⚪ 不推荐（10 个）— 戳一戳场景不匹配

| 标签 | 不推荐原因 |
|---|---|
| morning | 时间相关（UTC 6-10），戳一戳不需要 |
| night | 时间相关（UTC 20-2），同上 |
| work | 任务流标签，跟互动无关 |
| reply | 等待回复的状态 |
| givemoney | 需要金额参数 |
| cpu | 思维卡顿，多用于知识问答 |
| color | 调情专用，限制 ≤1 次 |
| cheer | 庆祝欢呼，与 happy 重复 |
| sleep | 作息状态标签 |
| see | 偷瞄关注，需要其他状态联动 |

---

## 三、视觉判断标准速查

### 嘴部特征

| 嘴型 | 对应标签 |
|---|---|
| 可爱嘟嘴 | baka |
| 低落嘟嘴（无泪） | sad |
| 张大嘴惊讶 | surprised |
| 抿嘴/紧闭 | angry |

### 眼睛特征

| 眼睛 | 对应标签 |
|---|---|
| 高光消失、空心 | surprised |
| 螺旋纹 | confused |
| 螺旋瞳（无云） | sigh |
| X 形眯眼 | happy |
| 心形 | heart |
| 闭眼 + 脸红 | shy |
| 闭眼（无红晕） | sleep |
| 带泪光 | cry |

### 附加特征

| 附加 | 对应标签 |
|---|---|
| 手指抵唇 | think |
| 眼泪流下 | cry |
| 红晕 | shy / heart |
| 握拳、爆炸符 | angry |

---

## 四、易混标签区分

### 同为嘟嘴

- **baka vs sad**：
  - baka = 嘴微微嘟起，表情可爱，整体**明亮、轻松**
  - sad = 嘴明显下垂，眉眼低垂，整体**阴郁、低沉**

### 同为闭眼

- **shy vs sleep**：
  - shy = 闭眼 + **明显红晕**，是害羞
  - sleep = 闭眼（无表情/带 Zzz），是困倦
- **shy vs baka**：
  - shy = 闭眼，**不**嘟嘴
  - baka = 嘟嘴，**不**闭眼

### 同为眼部变化

- **surprised vs shy**：
  - surprised = 眼睛**有变化**（空心/瞪大），可能张嘴
  - shy = 眼睛**闭上**，脸红
- **confused vs sigh**：
  - confused = 螺旋**云朵**眼，被动困惑
  - sigh = 螺旋**瞳**（无云），主动无奈

### 强度递进（同一情绪系）

- **sad < cry < angry**：
  - sad = 低落失意
  - cry = 带泪委屈
  - angry = 强烈反对
- **happy < excited**：
  - happy = 确认成就，眯眼笑
  - excited = 期待中，夸张动作

### 主动 vs 被动

- **think vs confused**：
  - think = 主动思考（手指抵唇）
  - confused = 被动困惑（螺旋眼、晕眩）

---

## 五、和 meme_manager 标签的关系

litepoke 的 emotion 参数**不强制要求和 meme_manager 标签一致**，但建议沿用同一套标签名，这样：

- 整理表情时一套标准走两边
- 后续如果想让 litepoke 直接读 meme_manager 目录（修改 `meme_dir` 配置），可以无缝切换
- LLM 学到一次标签语义，多个插件通用

**冲突情况处理**：

如果 litepoke 的 `default_emotion` 设置的标签在插件数据目录里不存在，litepoke 会自动**回退到任意存在的标签**。所以即使某个标签暂时没图，也不会让 LLM 调用失败。

---

## 六、起步配置脚本（可选）

如果你想批量从 meme_manager 复制表情到 litepoke 目录，可以这样：

```python
import shutil
from pathlib import Path

src = Path(r"C:\Users\chiriu\.astrbot\data\plugin_data\meme_manager\memes")
dst = Path(r"C:\Users\chiriu\.astrbot\data\plugin_data\astrbot_plugin_litepoke\memes")

# 起步：复制必装5个标签
starter_tags = ["baka", "angry", "sad", "cry", "happy"]

for tag in starter_tags:
    src_dir = src / tag
    dst_dir = dst / tag
    if src_dir.is_dir():
        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in src_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, dst_dir / f.name)
        print(f"复制 {tag}/ → {len(list(dst_dir.iterdir()))} 张")
```

---

## 七、文件位置

这份建议清单的对应目录：

```
data/plugin_data/astrbot_plugin_litepoke/
├── memes/              # 表情根目录
│   ├── baka/           # 必装
│   ├── angry/          # 必装
│   ├── sad/            # 必装
│   ├── cry/            # 必装
│   ├── happy/          # 必装
│   ├── surprised/      # 推荐
│   ├── confused/       # 推荐
│   ├── ...             # 按需添加
│   └── README.md       # 可选：每个目录里可以放个说明
```

---

## 八、调优建议

**L1：观察一周**

启用 litepoke 后，看 LLM 实际调用 emotion 的分布。如果发现 80% 都用 baka，说明其他标签的图不够，LLM 没信心选。

**L2：补图**

针对高频使用的标签补充图（5-10 张/标签就够随机感）。

**L3：删除低频**

如果某个标签一个月没被 LLM 选过，说明要么 LLM 觉得不合适、要么图太丑。可以考虑换图或删标签。

**L4：调引导 prompt**

如果 LLM 经常选错 emotion（比如想 sad 选了 cry），可以在 `guide_prompt` 里加示例：

```
选 emotion 的判断要点：
- 用户失落但没明说 → sad
- 用户明确表示伤心/委屈 → cry
- 用户调侃/开玩笑 → baka
- 用户表达喜欢/被打动 → heart 或 like
```
