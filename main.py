"""
轻量智能戳一戳插件（v2：跨平台）

设计目标：
1. aiocqhttp 平台：真戳一戳
2. 其他平台（webchat、telegram、飞书等）：用表情包 / QQ face / 文字模拟"戳"这个动作
3. 由 LLM 自主判断何时调用 poke_user tool
4. 可选的场景引导：在 on_llm_request 时根据关键词检测，注入提示

文件结构刻意保持单文件，所有逻辑清晰可见。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import Face, Image, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)


# 表情文件后缀
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


@dataclass
class GroupVibe:
    """群内滑动窗口状态（轻量版群氛围追踪）

    仅记录时间戳和最近一次事件的发送者，不解析内容。
    跟戳决策时根据窗口内事件密度做概率调整。
    """
    recent_msgs: deque = field(default_factory=deque)    # 群内最近消息时间戳
    recent_pokes: deque = field(default_factory=deque)   # 群内最近戳一戳时间戳
    last_msg_sender: str = ""                            # 最近说话的人
    last_poke_target: str = ""                           # 最近被戳的人

    def prune(self, now: float, window: int) -> None:
        """清理窗口外的过期条目"""
        cutoff = now - window
        while self.recent_msgs and self.recent_msgs[0] < cutoff:
            self.recent_msgs.popleft()
        while self.recent_pokes and self.recent_pokes[0] < cutoff:
            self.recent_pokes.popleft()


@dataclass
class PokeLog:
    """戳一戳累积日志（短期 + 长期两个维度）

    - recent: 60s 短期窗口的戳一戳事件。LLM 消费一次后清空。
    - daily:  按日累计，按 sender_id 分人。跨重启保留。
    """
    recent: deque = field(default_factory=deque)        # [(sender, time)]
    daily: dict[str, dict[str, int]] = field(default_factory=dict)  # date -> {sender: count}
    last_saved: float = 0.0

    def record(self, sender: str, now: float) -> None:
        """记录一次戳一戳事件"""
        self.recent.append((sender, now))
        date_key = date.today().isoformat()
        day = self.daily.setdefault(date_key, {})
        day[sender] = day.get(sender, 0) + 1

    def prune_recent(self, now: float, window: int) -> None:
        """裁剪 recent 窗口外的条目

        recent deque 里每个元素是 (sender, time) tuple
        所以拿最老条目要 self.recent[0][1]（先取第一个 tuple，再取 time）
        """
        cutoff = now - window
        while self.recent and self.recent[0][1] < cutoff:
            self.recent.popleft()

    def prune_daily(self, keep_days: int) -> None:
        """删除 keep_days 之外的日期"""
        if keep_days <= 0:
            return
        from datetime import timedelta
        cutoff_date = (date.today() - timedelta(days=keep_days)).isoformat()
        stale = [d for d in self.daily if d < cutoff_date]
        for d in stale:
            del self.daily[d]

    def consume_recent(self) -> list[tuple[str, float]]:
        """取出并清空 recent 队列（给 LLM 消费）"""
        items = list(self.recent)
        self.recent.clear()
        return items

    def top_sender_today(self) -> tuple[str, int] | None:
        """今天戳 bot 最多的人"""
        today = date.today().isoformat()
        day = self.daily.get(today)
        if not day:
            return None
        top = max(day, key=day.get)
        return top, day[top]

    def total_today(self) -> int:
        today = date.today().isoformat()
        return sum(self.daily.get(today, {}).values())

    def to_dict(self) -> dict:
        return {
            "recent": [list(item) for item in self.recent],
            "daily": self.daily,
            "last_saved": self.last_saved,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PokeLog":
        return cls(
            recent=deque(tuple(item) for item in data.get("recent", [])),
            daily={k: dict(v) for k, v in data.get("daily", {}).items()},
            last_saved=float(data.get("last_saved", 0.0)),
        )


class LitePokePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = config

        # 全局CD：任意两次戳人之间的最小间隔
        self._last_any_poke: float = 0.0

        # per-user CD：{ (scope, user_id): last_poke_time }
        # scope 在群聊是 group_id，私聊是 "_private"
        self._user_last_poke: dict[tuple[str, str], float] = {}

        # 引导CD：{ scope: last_guide_time }
        self._guide_last: dict[str, float] = defaultdict(float)

        # meme 路径缓存
        self._meme_dir: Path | None = None
        self._meme_index: dict[str, list[Path]] = {}  # emotion -> [paths]
        self._meme_index_mtime: float = 0.0

        # 群内滑动窗口（仅 aiocqhttp 群消息场景，用于跟戳决策）
        # { group_id: GroupVibe }
        self._vibe: dict[str, "GroupVibe"] = {}

        # 戳一戳累积日志（短期 + 长期，跨重启持久化）
        self._poke_log: "PokeLog" = PokeLog()
        self._poke_log_path: Path | None = None

        # 启动时从 JSON 恢复 PokeLog
        self._load_poke_log()

    # ===================== 内部：CD 管理 =====================

    def _cd_passed(self, scope: str, user_id: str) -> bool:
        now = time.time()
        global_cd = float(self.cfg.get("global_cd", 5))
        if now - self._last_any_poke < global_cd:
            return False
        user_cd = float(self.cfg.get("user_cd", 60))
        last = self._user_last_poke.get((scope, user_id), 0.0)
        if now - last < user_cd:
            return False
        return True

    def _record_poke(self, scope: str, user_id: str) -> None:
        now = time.time()
        self._last_any_poke = now
        self._user_last_poke[(scope, user_id)] = now

    # ===================== 内部：PokeLog 持久化 =====================

    def _resolve_poke_log_path(self) -> Path | None:
        """PokeLog JSON 文件路径

        1. 配置 poke_log_path 填了 → 用之
        2. 没填 → 用 <plugin_data>/astrbot_plugin_litepoke/poke_log.json
        """
        if self._poke_log_path is not None:
            return self._poke_log_path

        configured = (self.cfg.get("poke_log_path", "") or "").strip()
        if configured:
            p = Path(configured).expanduser()
            self._poke_log_path = p
            return p

        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
            data_root = Path(get_astrbot_plugin_data_path())
            p = data_root / "astrbot_plugin_litepoke" / "poke_log.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            self._poke_log_path = p
            return p
        except Exception as e:
            logger.warning(f"[litepoke] 推断 poke_log 路径失败: {e}")
            return None

    def _load_poke_log(self) -> None:
        if not self.cfg.get("poke_log_persist", True):
            return
        p = self._resolve_poke_log_path()
        if p is None or not p.is_file():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            self._poke_log = PokeLog.from_dict(data)
            logger.info(
                f"[litepoke] PokeLog 已加载：recent={len(self._poke_log.recent)}, "
                f"daily_keys={len(self._poke_log.daily)}"
            )
        except Exception as e:
            logger.warning(f"[litepoke] 加载 PokeLog 失败: {e}")
            self._poke_log = PokeLog()

    def _save_poke_log(self) -> None:
        if not self.cfg.get("poke_log_persist", True):
            return
        p = self._resolve_poke_log_path()
        if p is None:
            return
        try:
            self._poke_log.last_saved = time.time()
            # 写盘前清理
            keep_days = int(self.cfg.get("poke_log_daily_keep_days", 7))
            self._poke_log.prune_daily(keep_days)
            p.write_text(
                json.dumps(self._poke_log.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[litepoke] 保存 PokeLog 失败: {e}")

    def _maybe_periodic_save(self) -> None:
        """间隔写盘：每 poke_log_save_interval 秒写一次"""
        interval = float(self.cfg.get("poke_log_save_interval", 60))
        if time.time() - self._poke_log.last_saved >= interval:
            self._save_poke_log()

    # ===================== 内部：meme 索引 =====================

    def _resolve_meme_dir(self) -> Path | None:
        """获取 litepoke 自己的表情根目录（带缓存）

        顺序：
        1. 用户在配置里填了 meme_dir → 用之
        2. 没填 → 用插件自己的数据目录 <plugin_data>/astrbot_plugin_litepoke/memes
        """
        if self._meme_dir is not None:
            return self._meme_dir

        # 1. 用户自定义
        configured = (self.cfg.get("meme_dir", "") or "").strip()
        if configured:
            p = Path(configured).expanduser()
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            if p.is_dir():
                self._meme_dir = p
                return p
            logger.warning(f"[litepoke] 配置的 meme_dir 不存在且无法创建: {p}")

        # 2. 插件自己的数据目录
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

            data_root = Path(get_astrbot_plugin_data_path())
            candidate = data_root / "astrbot_plugin_litepoke" / "memes"
            candidate.mkdir(parents=True, exist_ok=True)
            self._meme_dir = candidate
            return candidate
        except Exception as e:
            logger.warning(f"[litepoke] 创建自带 meme 目录失败: {e}")

        return None

    def _refresh_meme_index(self) -> None:
        """扫描 meme_dir，建立 emotion -> [paths] 索引"""
        meme_dir = self._resolve_meme_dir()
        if meme_dir is None:
            self._meme_index = {}
            return

        try:
            current_mtime = meme_dir.stat().st_mtime
        except OSError:
            self._meme_index = {}
            return

        # 如果目录没变化，跳过扫描
        if current_mtime == self._meme_index_mtime and self._meme_index:
            return

        idx: dict[str, list[Path]] = {}
        try:
            for sub in meme_dir.iterdir():
                if not sub.is_dir():
                    continue
                # 子目录名就是 emotion 标签
                files = [
                    p
                    for p in sub.iterdir()
                    if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
                ]
                if files:
                    idx[sub.name] = files
        except Exception as e:
            logger.warning(f"[litepoke] 扫描 meme 目录失败: {e}")

        self._meme_index = idx
        self._meme_index_mtime = current_mtime

    def _pick_meme(self, emotion: str | None) -> Path | None:
        """根据 emotion 选一张 meme 图，找不到则回退到默认标签"""
        if not self.cfg.get("enable_meme_fallback", True):
            return None

        self._refresh_meme_index()
        if not self._meme_index:
            return None

        candidates: list[Path] = []
        if emotion and emotion in self._meme_index:
            candidates = self._meme_index[emotion]
        else:
            default = self.cfg.get("default_emotion", "baka")
            if default in self._meme_index:
                candidates = self._meme_index[default]
            else:
                # 都没匹配，随便抽一个标签
                for paths in self._meme_index.values():
                    candidates = paths
                    break

        if not candidates:
            return None
        return random.choice(candidates)

    # ===================== 内部：发送回退 =====================

    async def _send_fallback(self, event: Any) -> str:
        """在非 aiocqhttp 平台发送回退表达（meme / face / text）

        返回给人看的成功描述（喂给 LLM 当 tool result）
        """
        # 尝试 1：meme
        meme_path = self._pick_meme(None)
        if meme_path is not None:
            try:
                chain = MessageChain([Image.fromFileSystem(str(meme_path))])
                await event.send(chain)
                return f"已用表情包回应（{meme_path.parent.name}/{meme_path.name}）"
            except Exception as e:
                logger.warning(f"[litepoke] 发meme失败: {e}")

        # 尝试 2：QQ face（不是所有平台都支持）
        face_id = int(self.cfg.get("fallback_face_id", 0))
        if face_id > 0:
            try:
                chain = MessageChain([Face(id=face_id)])
                await event.send(chain)
                return f"已用QQ表情回应（face_id={face_id}）"
            except Exception as e:
                logger.warning(f"[litepoke] 发face失败: {e}")

        # 尝试 3：纯文字
        text = (self.cfg.get("fallback_text", "") or "").strip()
        if text:
            try:
                chain = MessageChain([Plain(text)])
                await event.send(chain)
                return f"已用文字回应（{text}）"
            except Exception as e:
                logger.warning(f"[litepoke] 发文字失败: {e}")

        return "回退表达全部失败：meme/face/text 都没发出去"

    # ===================== 核心：LLM Tool =====================

    @filter.llm_tool(name="poke_user")
    async def poke_user(
        self,
        event,
        user_id: str,
        times: int = 1,
        emotion: str | None = None,
    ):
        """戳一戳指定用户，或在不支持戳一戳的平台用表情包/文字回应。

        当你觉得用户有点欠揍、想表达不满、或者单纯想互动时，可以调用这个工具。
        aiocqhttp 平台会发送真实的戳一戳通知；其他平台（webchat/telegram/飞书等）
        会发送一张情绪表情包来表达同样的"戳"。

        Args:
            user_id(string): 目标用户的QQ号，必须是纯数字字符串，例如 "12345678"
            times(number): aiocqhttp 平台下戳的次数，默认为 1，不建议超过 3
            emotion(string): 表情包情绪标签，可选。例如 baka/angry/happy/sad。
                            留空则使用配置的默认标签。仅在非 aiocqhttp 平台生效
        """
        if not user_id:
            return "戳一戳失败：user_id 不能为空"

        user_id = str(user_id).strip()

        # 平台 & 作用域
        is_aiocqhttp = isinstance(event, AiocqhttpMessageEvent)
        group_id = event.get_group_id() if is_aiocqhttp else ""
        scope = group_id or "_private"

        # 私聊开关
        if not group_id and self.cfg.get("only_group", True):
            return "戳一戳失败：私聊场景未启用戳一戳"

        # 自我保护
        try:
            self_id = str(event.get_self_id())
            if user_id == self_id:
                return "戳一戳失败：不能戳机器人自己"
        except Exception:
            pass

        # ID 格式校验：aiocqhttp 要求纯数字；其他平台宽松（webchat 是字符串 user_id）
        if is_aiocqhttp and not user_id.isdigit():
            return "戳一戳失败：aiocqhttp 平台下 user_id 必须是纯数字 QQ 号"

        # CD 检查：CD 期间不真正戳，但静默累积到 PokeLog
        if not self._cd_passed(scope, user_id):
            self._poke_log.record(user_id, time.time())
            self._maybe_periodic_save()
            return ""  # 空返回，不报错，让 LLM 自然处理

        # === 分支 A：aiocqhttp 真戳 ===
        if is_aiocqhttp and group_id:
            max_times = int(self.cfg.get("poke_max_times", 3))
            try:
                times = int(times)
            except (TypeError, ValueError):
                times = 1
            times = max(1, min(times, max_times))

            interval = float(self.cfg.get("poke_interval", 0.5))
            success_count = 0
            last_err: str | None = None
            for i in range(times):
                try:
                    await event.bot.group_poke(
                        group_id=int(group_id),
                        user_id=int(user_id),
                    )
                    success_count += 1
                except Exception as e:
                    last_err = str(e)
                    logger.warning(f"[litepoke] 戳一戳失败 user_id={user_id}: {e}")
                    break
                if i < times - 1:
                    await asyncio.sleep(interval)

            self._record_poke(scope, user_id)
            if success_count == times:
                return f"已成功戳用户 {user_id} {success_count} 次"
            return f"戳一戳部分失败：成功 {success_count}/{times} 次，错误：{last_err}"

        # === 分支 A2：aiocqhttp 私聊（好友戳） ===
        if is_aiocqhttp:
            try:
                await event.bot.friend_poke(user_id=int(user_id))
                self._record_poke(scope, user_id)
                return f"已戳好友 {user_id} 1 次"
            except Exception as e:
                return f"戳好友失败：{e}"

        # === 分支 B：非 aiocqhttp 平台用表情包回退 ===
        self._record_poke(scope, user_id)
        return await self._send_fallback(event)

    # ===================== 辅助：场景引导 =====================

    @filter.on_llm_request()
    async def on_llm_request(self, event, request):
        """在 LLM 请求前注入场景引导 + PokeLog 统计

        两个独立通道：
        1. PokeLog 统计：总是尝试（如果有累积）。让 LLM 感知"刚被戳"或"今天被戳 N 次"。
        2. 关键词引导：仅当用户消息命中 trigger_keywords 时执行。
        """
        msg = event.message_str or ""
        if not msg:
            return

        if not event.get_group_id() and self.cfg.get("only_group", True):
            return

        # === 通道 1：PokeLog 统计（总是）===
        poke_log_block = self._build_poke_log_block()
        if poke_log_block:
            if hasattr(request, "system_prompt") and request.system_prompt:
                request.system_prompt = request.system_prompt.rstrip() + "\n\n" + poke_log_block
            elif hasattr(request, "system_prompt"):
                request.system_prompt = poke_log_block

        # === 通道 2：关键词引导 ===
        keywords = self.cfg.get("trigger_keywords", []) or []
        if not keywords:
            return

        if not any(w in msg for w in keywords):
            return

        scope = event.get_group_id() or "_private"
        guide_cd = float(self.cfg.get("guide_cd", 30))
        if time.time() - self._guide_last[scope] < guide_cd:
            return
        self._guide_last[scope] = time.time()

        guide_prompt = self.cfg.get(
            "guide_prompt",
            "[轻量戳一戳提示] 用户语气可能带有调侃/挑衅，你可以考虑调用 poke_user 工具戳回去。",
        )

        if hasattr(request, "system_prompt") and request.system_prompt:
            request.system_prompt = request.system_prompt.rstrip() + "\n\n" + guide_prompt
        elif hasattr(request, "system_prompt"):
            request.system_prompt = guide_prompt

    def _build_poke_log_block(self) -> str:
        """构造 PokeLog 统计块（注入到 system_prompt）

        只在 PokeLog 有累积时返回非空字符串。
        消费后清空 recent（避免重复注入）。
        """
        window = int(self.cfg.get("poke_log_window", 60))
        self._poke_log.prune_recent(time.time(), window)
        keep_days = int(self.cfg.get("poke_log_daily_keep_days", 7))
        self._poke_log.prune_daily(keep_days)

        recent = self._poke_log.consume_recent()
        total_today = self._poke_log.total_today()
        top = self._poke_log.top_sender_today()

        if not recent and total_today == 0:
            return ""

        from collections import Counter
        recent_counter = Counter(s for s, _ in recent)

        recent_text = ""
        if recent:
            top_in_window = recent_counter.most_common(1)[0]
            recent_text = (
                f"刚被戳了 {len(recent)} 次（{window}秒内），"
                f"其中 {top_in_window[0]} 戳了 {top_in_window[1]} 次"
            )
        else:
            recent_text = "近期没被戳"

        today_text = ""
        if total_today > 0:
            if top:
                today_text = (
                    f"；今天总共被戳 {total_today} 次，"
                    f"{top[0]} 是'主力'（戳了 {top[1]} 次）"
                )
            else:
                today_text = f"；今天总共被戳 {total_today} 次"

        return (
            f"[戳一戳统计] {recent_text}{today_text}。"
            f"如果觉得对方过界了，可以考虑用 poke_user 工具戳回去或发个文字吐槽。"
        )

    # ===================== 辅助：群内消息监听（维护滑动窗口） =====================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event):
        """监听群内消息，维护 vibe 滑动窗口

        每个群一条消息进来 → 一次 deque append + 一次 deque popleft（O(1)）。
        不解析消息内容，只记时间戳和发送者。
        """
        gid = event.get_group_id()
        if not gid:
            return

        vibe = self._vibe.get(gid)
        if vibe is None:
            vibe = GroupVibe()
            self._vibe[gid] = vibe

        now = time.time()
        window = int(self.cfg.get("vibe_window", 60))
        vibe.recent_msgs.append(now)
        vibe.last_msg_sender = str(event.get_sender_id())
        vibe.prune(now, window)

    # ===================== 辅助：群内戳一戳监听 + 概率跟戳 =====================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_group_poke(self, event):
        """监听群内戳一戳事件，按概率跟着戳被戳的人

        仅触发条件：
        - aiocqhttp 平台
        - 戳一戳事件（post_type=notice, notice_type=notify, sub_type=poke）
        - 群内（group_id 非空）
        - 是别人戳别人（不是自己发的，也不是戳 bot 自己）
        - 按 follow_prob 概率触发
        - 走 follow_cd 兜底，避免连续事件刷屏
        """
        if not isinstance(event, AiocqhttpMessageEvent):
            return

        if not self.cfg.get("follow_enabled", True):
            return

        # 解析戳一戳事件
        raw = getattr(event.message_obj, "raw_message", None)
        if not isinstance(raw, dict):
            return
        if raw.get("post_type") != "notice":
            return
        if raw.get("notice_type") != "notify":
            return
        if raw.get("sub_type") != "poke":
            return

        self_id = raw.get("self_id", 0)
        user_id = raw.get("user_id", 0)        # 戳人的
        target_id = raw.get("target_id", 0)    # 被戳的
        group_id = raw.get("group_id", 0)

        # 只处理群内
        if not group_id:
            return

        # 忽略机器人自己发的戳
        if user_id == self_id:
            return

        # bot 自己被戳 → 不在这里处理，让 LLM 自主决定（poke_user / 表情回退）
        if target_id == self_id:
            # bot 自己被戳 → 累积到 PokeLog，等用户发消息时让 LLM 感知
            self._poke_log.record(str(user_id), time.time())
            self._maybe_periodic_save()
            return

        # 概率判定
        prob = float(self.cfg.get("follow_prob", 0.1))
        prob = max(0.0, min(prob, 1.0))
        if prob <= 0:
            return

        # CD 兜底：避免群内连续戳一戳事件时连续跟戳
        follow_cd = float(self.cfg.get("follow_cd", 3))
        if time.time() - self._last_any_poke < follow_cd:
            return

        # === vibe 决策：用群内滑动窗口调整概率 ===
        adjusted_prob, reason = self._vibe_adjust(gid, target_id, prob)
        if random.random() >= adjusted_prob:
            return

        # 跟戳
        try:
            await event.bot.group_poke(
                group_id=int(group_id),
                user_id=int(target_id),
            )
            self._last_any_poke = time.time()
            # 记录到 vibe
            vibe = self._vibe.get(gid)
            if vibe is None:
                vibe = GroupVibe()
                self._vibe[gid] = vibe
            window = int(self.cfg.get("vibe_window", 60))
            vibe.recent_pokes.append(time.time())
            vibe.last_poke_target = str(target_id)
            vibe.prune(time.time(), window)
            logger.debug(f"[litepoke] 跟戳 group={gid} target={target_id} reason={reason}")
        except Exception as e:
            logger.warning(f"[litepoke] 跟戳失败: {e}")

    def _vibe_adjust(self, gid: str, target_id: int, base_prob: float) -> tuple[float, str]:
        """根据群内滑动窗口调整跟戳概率

        规则（都靠滑动窗口 + deque，O(1)）：
        - 窗口内消息数 ≥ active_threshold → 群活跃，概率 ×1.5（封顶 1.0）
        - 窗口内消息数 < quiet_threshold  → 群冷清，概率 ×0.3
        - 窗口内已跟戳次数 ≥ max_in_window → 概率 = 0
        - 被戳的人不是最近说话的人        → 概率 ×0.5

        返回 (调整后概率, 决策原因) — reason 仅用于 debug 日志
        """
        vibe = self._vibe.get(gid)
        if vibe is None:
            return base_prob, "no_vibe"

        window = int(self.cfg.get("vibe_window", 60))
        now = time.time()
        vibe.prune(now, window)

        prob = base_prob
        msg_count = len(vibe.recent_msgs)
        poke_count = len(vibe.recent_pokes)
        reason_parts: list[str] = []

        active_threshold = int(self.cfg.get("vibe_active_threshold", 5))
        quiet_threshold = int(self.cfg.get("vibe_quiet_threshold", 1))
        max_in_window = int(self.cfg.get("vibe_max_in_window", 1))

        # 规则 1：窗口内跟戳次数已达上限 → 跳过
        if poke_count >= max_in_window:
            return 0.0, f"max_in_window={poke_count}"

        # 规则 2：群活跃 → 概率上调
        if msg_count >= active_threshold:
            prob = min(1.0, prob * 1.5)
            reason_parts.append(f"active({msg_count})")

        # 规则 3：群冷清 → 概率下调
        if msg_count < quiet_threshold:
            prob *= 0.3
            reason_parts.append(f"quiet({msg_count})")

        # 规则 4：被戳的人最近没说话 → 概率下调
        target_str = str(target_id)
        if vibe.last_msg_sender and target_str != vibe.last_msg_sender:
            prob *= 0.5
            reason_parts.append("target_silent")

        return prob, ",".join(reason_parts) or "base"

    # ===================== 生命周期 =====================

    async def terminate(self):
        self._save_poke_log()
        self._user_last_poke.clear()
        self._guide_last.clear()
        self._meme_index.clear()
        self._vibe.clear()
        logger.info("[litepoke] 插件已卸载，CD/meme/vibe/PokeLog 状态已清空")
