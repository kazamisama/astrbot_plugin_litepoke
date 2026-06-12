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
import copy
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

# PokeLog 持久化格式版本。旧版本 JSON 没有该字段，加载时会按 v0 兼容。
POKE_LOG_SCHEMA_VERSION = 1


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
            "schema_version": POKE_LOG_SCHEMA_VERSION,
            "recent": [list(item) for item in self.recent],
            "daily": self.daily,
            "last_saved": self.last_saved,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PokeLog":
        if not isinstance(data, dict):
            raise ValueError("PokeLog data must be a JSON object")

        recent_items: deque = deque()
        for item in data.get("recent", []):
            try:
                sender, ts = item
                recent_items.append((str(sender), float(ts)))
            except (TypeError, ValueError):
                # 跳过单条损坏 recent，不让整份日志报废。
                continue

        daily: dict[str, dict[str, int]] = {}
        raw_daily = data.get("daily", {})
        if isinstance(raw_daily, dict):
            for day, counts in raw_daily.items():
                if not isinstance(counts, dict):
                    continue
                day_counts: dict[str, int] = {}
                for sender, count in counts.items():
                    try:
                        day_counts[str(sender)] = max(0, int(count))
                    except (TypeError, ValueError):
                        continue
                if day_counts:
                    daily[str(day)] = day_counts

        return cls(
            recent=recent_items,
            daily=daily,
            last_saved=float(data.get("last_saved", 0.0) or 0.0),
        )


class LitePokePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = config

        # 分开记录不同主动动作的时间，避免工具戳、跟戳、被戳回应互相误伤 CD
        self._last_tool_poke: float = 0.0
        self._last_follow_poke: float = 0.0
        self._last_respond_poked: float = 0.0

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

    @staticmethod
    def _clamp(value: float, min_value: float | None = None, max_value: float | None = None) -> float:
        if min_value is not None:
            value = max(min_value, value)
        if max_value is not None:
            value = min(max_value, value)
        return value

    def _cfg_float(
        self,
        key: str,
        default: float,
        *,
        min_value: float | None = None,
        max_value: float | None = None,
    ) -> float:
        """读取 float 配置，配置损坏时降级默认值，避免事件处理链炸掉。"""
        try:
            value = float(self.cfg.get(key, default))
        except (TypeError, ValueError):
            logger.warning(f"[litepoke] 配置 {key} 不是有效数字，使用默认值 {default}")
            value = float(default)
        return self._clamp(value, min_value, max_value)

    def _cfg_int(
        self,
        key: str,
        default: int,
        *,
        min_value: int | None = None,
        max_value: int | None = None,
    ) -> int:
        """读取 int 配置，配置损坏时降级默认值。"""
        try:
            value = int(self.cfg.get(key, default))
        except (TypeError, ValueError):
            logger.warning(f"[litepoke] 配置 {key} 不是有效整数，使用默认值 {default}")
            value = int(default)
        if min_value is not None:
            value = max(min_value, value)
        if max_value is not None:
            value = min(max_value, value)
        return value

    def _diagnose(self, action: str, reason: str, **fields: Any) -> None:
        """按需输出诊断日志，方便排查一次戳一戳为什么被跳过/降级。"""
        if not self.cfg.get("debug_diagnostics", False):
            return
        details = " ".join(
            f"{key}={value}"
            for key, value in fields.items()
            if value is not None and value != ""
        )
        suffix = f" {details}" if details else ""
        logger.info(f"[litepoke][diag] action={action} reason={reason}{suffix}")

    # ===================== 内部：戳一戳事件文本构造（移植 chat_plus） =====================

    @staticmethod
    def _build_poke_event_text(poke_info: dict | None) -> str:
        """构造可注入到 LLM 的戳一戳事件伪消息文本

        移植自 astrbot_plugin_group_chat_plus 的 build_persistent_poke_event_text。
        不依赖 chat_plus，独立运行。
        """
        if not poke_info or not isinstance(poke_info, dict):
            return ""

        sender_id = str(poke_info.get("sender_id", "") or "")
        sender_name = (
            str(poke_info.get("sender_name", "") or "").strip() or "未知用户"
        )
        target_id = str(poke_info.get("target_id", "") or "")
        target_name = (
            str(poke_info.get("target_name", "") or "").strip() or "未知用户"
        )
        is_poke_bot = bool(poke_info.get("is_poke_bot", False))

        sender_text = f"{sender_name}(ID:{sender_id})" if sender_id else sender_name
        target_text = f"{target_name}(ID:{target_id})" if target_id else target_name

        if is_poke_bot:
            if not sender_text:
                return "[戳一戳事件]有人戳了你"
            return f"[戳一戳事件]有人戳了你，发起者是{sender_text}"

        if not sender_text and not target_text:
            return "[戳一戳事件]发生了一次戳一戳互动"
        if not sender_text:
            return f"[戳一戳事件]这不是戳你的消息，有人戳了{target_text}"
        if not target_text:
            return f"[戳一戳事件]这不是戳你的消息，{sender_text}戳了别人"
        return f"[戳一戳事件]这不是戳你的消息，{sender_text}戳了{target_text}"

    async def _get_conversation_with_id(self, event: AiocqhttpMessageEvent):
        """获取当前会话 ID 和 Conversation 对象。"""
        try:
            umo = event.unified_msg_origin
            conv_mgr = self.context.conversation_manager
            cid = await conv_mgr.get_curr_conversation_id(umo)
            if not cid:
                cid = await conv_mgr.new_conversation(umo, event.get_platform_id())
            conv = await conv_mgr.get_conversation(umo, cid)
            return cid, conv
        except Exception as e:
            logger.warning(f"[litepoke] 获取 conversation 失败: {e}")
            return None, None

    async def _get_conversation(self, event: AiocqhttpMessageEvent):
        """获取当前会话的 Conversation 对象（移植自 pokepro）

        拿不到就返回 None，调用方需处理。
        """
        _, conv = await self._get_conversation_with_id(event)
        return conv

    @staticmethod
    def _extract_text_from_content(content: Any) -> str:
        """从 AstrBot/OpenAI 风格 content 中提取可读文本。"""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "text":
                    continue
                text = str(item.get("text", "") or "").strip()
                if text:
                    parts.append(text)
            return "\n".join(parts).strip()
        return ""

    def _is_raw_poke_history_message(self, msg: Any) -> bool:
        """判断 history 中由 AstrBot 原始 Poke 组件产生的空/标记 user 消息。"""
        if not isinstance(msg, dict):
            return False
        if msg.get("role") != "user":
            return False

        metadata = msg.get("metadata")
        if isinstance(metadata, dict) and metadata.get("source") == "litepoke":
            return False

        text = self._extract_text_from_content(msg.get("content"))
        normalized = text.replace(" ", "").lower()
        return not text or normalized in {"[poke:poke]", "[componenttype.poke]"}

    def _sanitize_llm_history(self, history: list[Any]) -> tuple[list[Any], int]:
        """清理会导致 OpenAI 兼容接口 400 的明显非法 history 消息。

        只移除两类确定不合法/不完整的记录：
        1. role=assistant 且 content 为空、tool_calls 也为空。
        2. 没有匹配上一个 assistant.tool_calls 的孤立 role=tool。
        """
        sanitized: list[Any] = []
        removed = 0
        pending_tool_call_ids: set[str] = set()

        for msg in history:
            if not isinstance(msg, dict):
                sanitized.append(msg)
                continue

            role = msg.get("role")
            if role == "assistant":
                content = msg.get("content")
                tool_calls = msg.get("tool_calls")
                has_text = bool(self._extract_text_from_content(content))
                has_tool_calls = isinstance(tool_calls, list) and bool(tool_calls)
                if not has_text and not has_tool_calls:
                    removed += 1
                    pending_tool_call_ids.clear()
                    continue

                pending_tool_call_ids.clear()
                if has_tool_calls:
                    for call in tool_calls:
                        if not isinstance(call, dict):
                            continue
                        call_id = str(call.get("id", "") or "")
                        if call_id:
                            pending_tool_call_ids.add(call_id)
                sanitized.append(msg)
                continue

            if role == "tool":
                tool_call_id = str(msg.get("tool_call_id", "") or "")
                if not tool_call_id or tool_call_id not in pending_tool_call_ids:
                    removed += 1
                    continue
                pending_tool_call_ids.discard(tool_call_id)
                sanitized.append(msg)
                continue

            if role in {"user", "system"}:
                pending_tool_call_ids.clear()
            sanitized.append(msg)

        return sanitized, removed

    async def _sanitize_current_conversation(self, event: Any, *, reason: str = "") -> bool:
        """把当前 conversation 中已残留的非法 LLM 消息清掉。"""
        cid, conv = await self._get_conversation_with_id(event)
        if not cid or conv is None:
            return False

        raw_history = getattr(conv, "content", None) or getattr(conv, "history", None) or []
        if isinstance(raw_history, str):
            try:
                history = json.loads(raw_history)
            except Exception:
                return False
        elif isinstance(raw_history, list):
            history = list(raw_history)
        else:
            return False

        sanitized, removed = self._sanitize_llm_history(history)
        if not removed:
            return False

        conv_mgr = self.context.conversation_manager
        try:
            await conv_mgr.update_conversation(event.unified_msg_origin, cid, history=sanitized)
        except TypeError:
            await conv_mgr.update_conversation(event.unified_msg_origin, cid, sanitized)

        suffix = f" reason={reason}" if reason else ""
        logger.warning(f"[litepoke] 已清理非法 LLM history {removed} 条 id={cid}{suffix}")
        return True

    async def _replay_poke_as_message(
        self,
        event: AiocqhttpMessageEvent,
        poke_text: str,
    ) -> bool:
        """把 poke notice 伪造成一条普通文字消息，重新投递到 AstrBot 事件队列。

        借鉴 pokepro 的 COMMAND 模块：copy event → 改 message/message_str
        → should_call_llm(True) → set_extra 防递归 → put_nowait。
        不插入真实 At 组件，只强制唤醒，让它像“被戳了一下”这类普通文字输入。
        """
        if not poke_text:
            return False

        try:
            evt = copy.copy(event)
            evt.message_obj = copy.copy(event.message_obj)
            try:
                evt._extras = dict(event.get_extra())
            except Exception:
                pass

            evt.clear_result()
            event.stop_event()

            chain = [Plain(poke_text)]
            evt.message_obj.message = chain
            evt.message_obj.message_str = poke_text
            evt.message_str = poke_text
            evt.is_at_or_wake_command = True
            evt.should_call_llm(True)
            evt.set_extra("litepoke_replayed_poke", True)

            self.context.get_event_queue().put_nowait(evt)
            return True
        except Exception as e:
            logger.warning(f"[litepoke] poke 伪消息重投递失败: {e}")
            return False

    async def _append_poke_event_to_conversation(
        self,
        event: AiocqhttpMessageEvent,
        poke_text: str,
    ) -> bool:
        """把戳一戳 notice 写入官方 conversation，作为后续上下文的一部分。

        只写入一条纯文本 user 消息，不写 tool/tool_calls，避免污染工具调用链。
        """
        if not self.cfg.get("respond_poked_write_context", True):
            return False
        if not poke_text:
            return False

        cid, conv = await self._get_conversation_with_id(event)
        if not cid or conv is None:
            return False

        raw_history = getattr(conv, "content", None) or getattr(conv, "history", None) or []
        if isinstance(raw_history, str):
            try:
                history = json.loads(raw_history)
            except Exception:
                history = []
        elif isinstance(raw_history, list):
            history = list(raw_history)
        else:
            history = []

        history, removed_invalid = self._sanitize_llm_history(history)

        event_message = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": poke_text,
                }
            ],
            "metadata": {
                "source": "litepoke",
                "event_type": "poke",
                "timestamp": time.time(),
            },
        }

        # 避免同一 notice 分支异常重入时重复写入完全相同的最后一条事件。
        if history:
            last = history[-1]
            if (
                isinstance(last, dict)
                and self._extract_text_from_content(last.get("content")) == poke_text
            ):
                if not removed_invalid:
                    return True

        # AstrBot 可能已先把原始 ComponentType.Poke 作为一条空 user 消息写入 history。
        # 这里优先把最近的原始空 Poke 消息规范化为可读文本，避免一次戳被模型看成两条。
        replaced = False
        for idx in range(len(history) - 1, max(-1, len(history) - 4), -1):
            if not self._is_raw_poke_history_message(history[idx]):
                continue

            history[idx] = event_message
            replaced = True
            break

        if not replaced:
            history.append(event_message)

        conv_mgr = self.context.conversation_manager
        try:
            await conv_mgr.update_conversation(event.unified_msg_origin, cid, history=history)
            action = "替换原始戳一戳消息" if replaced else "写入戳一戳事件"
            suffix = f"，并清理非法 history {removed_invalid} 条" if removed_invalid else ""
            logger.debug(f"[litepoke] 已{action}到 conversation id={cid}{suffix}")
            return True
        except TypeError:
            try:
                await conv_mgr.update_conversation(event.unified_msg_origin, cid, history)
                action = "替换原始戳一戳消息" if replaced else "写入戳一戳事件"
                suffix = f"，并清理非法 history {removed_invalid} 条" if removed_invalid else ""
                logger.debug(f"[litepoke] 已{action}到 conversation id={cid}{suffix}")
                return True
            except Exception as e:
                logger.warning(f"[litepoke] 写入戳一戳事件到 conversation 失败: {e}")
                return False
        except Exception as e:
            logger.warning(f"[litepoke] 写入戳一戳事件到 conversation 失败: {e}")
            return False

    async def _drop_recent_raw_poke_from_conversation(
        self,
        event: AiocqhttpMessageEvent,
        *,
        reason: str = "",
    ) -> bool:
        """删除最近由 AstrBot 原始 Poke 组件写入的空/标记 user 消息。

        bot 自己调用 poke_user 时，aiocqhttp 可能回送一条 self poke notice，
        AstrBot 又会把它写进当前 conversation。这里把这条 outgoing 动作清掉，
        避免下一轮模型把 bot 的动作误读成新的用户输入。
        """
        cid, conv = await self._get_conversation_with_id(event)
        if not cid or conv is None:
            return False

        raw_history = getattr(conv, "content", None) or getattr(conv, "history", None) or []
        if isinstance(raw_history, str):
            try:
                history = json.loads(raw_history)
            except Exception:
                history = []
        elif isinstance(raw_history, list):
            history = list(raw_history)
        else:
            history = []

        removed = False
        for idx in range(len(history) - 1, max(-1, len(history) - 4), -1):
            if not self._is_raw_poke_history_message(history[idx]):
                continue
            del history[idx]
            removed = True
            break

        if not removed:
            return False

        conv_mgr = self.context.conversation_manager
        try:
            await conv_mgr.update_conversation(event.unified_msg_origin, cid, history=history)
        except TypeError:
            await conv_mgr.update_conversation(event.unified_msg_origin, cid, history)

        suffix = f" reason={reason}" if reason else ""
        logger.debug(f"[litepoke] 已删除原始 outgoing 戳一戳 history id={cid}{suffix}")
        return True

    async def _delayed_cleanup_poke_history(
        self,
        event: AiocqhttpMessageEvent,
        *,
        poke_text: str = "",
        reason: str = "",
        delay: float = 0.3,
    ) -> None:
        """延迟清理原始 Poke history，补偿 AstrBot 写 conversation 的时序差异。"""
        try:
            await asyncio.sleep(delay)
            if poke_text:
                await self._normalize_recent_poke_history(event, poke_text, reason=reason)
            else:
                await self._drop_recent_raw_poke_from_conversation(event, reason=reason)
        except Exception as e:
            logger.debug(f"[litepoke] 延迟清理 Poke history 失败: {e}")

    async def _normalize_recent_poke_history(
        self,
        event: AiocqhttpMessageEvent,
        poke_text: str,
        *,
        reason: str = "",
    ) -> bool:
        """把最近原始 Poke 消息规范化，并清掉同一事件附近的重复空 Poke。"""
        if not poke_text:
            return False

        cid, conv = await self._get_conversation_with_id(event)
        if not cid or conv is None:
            return False

        raw_history = getattr(conv, "content", None) or getattr(conv, "history", None) or []
        if isinstance(raw_history, str):
            try:
                history = json.loads(raw_history)
            except Exception:
                history = []
        elif isinstance(raw_history, list):
            history = list(raw_history)
        else:
            history = []

        if not history:
            return False

        event_message = {
            "role": "user",
            "content": [{"type": "text", "text": poke_text}],
            "metadata": {
                "source": "litepoke",
                "event_type": "poke",
                "timestamp": time.time(),
            },
        }

        changed = False
        seen_litepoke = False
        # 只动最近几条，避免误删很久以前的真实上下文。
        start = max(0, len(history) - 8)
        for idx in range(len(history) - 1, start - 1, -1):
            msg = history[idx]
            text = self._extract_text_from_content(msg.get("content") if isinstance(msg, dict) else None)
            is_same_litepoke = (
                isinstance(msg, dict)
                and msg.get("role") == "user"
                and text == poke_text
            )
            if is_same_litepoke:
                if seen_litepoke:
                    del history[idx]
                    changed = True
                else:
                    seen_litepoke = True
                continue

            if not self._is_raw_poke_history_message(msg):
                continue

            if seen_litepoke:
                del history[idx]
            else:
                history[idx] = event_message
                seen_litepoke = True
            changed = True

        if not changed:
            return False

        conv_mgr = self.context.conversation_manager
        try:
            await conv_mgr.update_conversation(event.unified_msg_origin, cid, history=history)
        except TypeError:
            await conv_mgr.update_conversation(event.unified_msg_origin, cid, history)

        suffix = f" reason={reason}" if reason else ""
        logger.debug(f"[litepoke] 已延迟规范化 Poke history id={cid}{suffix}")
        return True

    # ===================== 内部：状态记录 =====================

    def _record_poke(self, scope: str, user_id: str) -> None:
        self._last_tool_poke = time.time()

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
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"[litepoke] 创建 poke_log_path 父目录失败: {e}")
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
            self._diagnose("poke_log_load", "persist_disabled")
            return
        p = self._resolve_poke_log_path()
        if p is None:
            self._diagnose("poke_log_load", "path_unavailable")
            return
        if not p.is_file():
            self._diagnose("poke_log_load", "file_missing", path=p)
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            schema_version = int(data.get("schema_version", 0) or 0) if isinstance(data, dict) else 0
            if schema_version > POKE_LOG_SCHEMA_VERSION:
                logger.warning(
                    f"[litepoke] PokeLog schema_version={schema_version} 高于当前支持版本 "
                    f"{POKE_LOG_SCHEMA_VERSION}，将尝试兼容加载"
                )
            self._poke_log = PokeLog.from_dict(data)
            logger.info(
                f"[litepoke] PokeLog 已加载：recent={len(self._poke_log.recent)}, "
                f"daily_keys={len(self._poke_log.daily)}, schema_version={schema_version}"
            )
        except Exception as e:
            logger.warning(f"[litepoke] 加载 PokeLog 失败: {e}")
            try:
                corrupt_path = p.with_name(f"{p.name}.corrupt.{int(time.time())}")
                p.replace(corrupt_path)
                logger.warning(f"[litepoke] 已保留损坏 PokeLog: {corrupt_path}")
            except Exception as move_e:
                logger.warning(f"[litepoke] 保留损坏 PokeLog 失败: {move_e}")
            self._poke_log = PokeLog()

    def _save_poke_log(self) -> None:
        if not self.cfg.get("poke_log_persist", True):
            self._diagnose("poke_log_save", "persist_disabled")
            return
        p = self._resolve_poke_log_path()
        if p is None:
            self._diagnose("poke_log_save", "path_unavailable")
            return
        try:
            self._poke_log.last_saved = time.time()
            # 写盘前清理
            now = time.time()
            window = self._cfg_int("poke_log_window", 60, min_value=1)
            keep_days = self._cfg_int("poke_log_daily_keep_days", 7, min_value=0)
            self._poke_log.prune_recent(now, window)
            self._poke_log.prune_daily(keep_days)

            tmp_path = p.with_name(f"{p.name}.tmp")
            payload = json.dumps(self._poke_log.to_dict(), ensure_ascii=False, indent=2)
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(p)
            self._diagnose(
                "poke_log_save",
                "saved",
                path=p,
                recent=len(self._poke_log.recent),
                daily_keys=len(self._poke_log.daily),
            )
        except Exception as e:
            logger.warning(f"[litepoke] 保存 PokeLog 失败: {e}")

    def _maybe_periodic_save(self) -> None:
        """间隔写盘：每 poke_log_save_interval 秒写一次"""
        interval = self._cfg_float("poke_log_save_interval", 60, min_value=1)
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
            mtimes = [meme_dir.stat().st_mtime]
            mtimes.extend(sub.stat().st_mtime for sub in meme_dir.iterdir() if sub.is_dir())
            current_mtime = max(mtimes)
        except OSError:
            self._meme_index = {}
            return

        # 如果根目录和子目录都没变化，跳过扫描
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
        face_id = self._cfg_int("fallback_face_id", 0, min_value=0)
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

        # 不再做工具级 CD：LLM 调用 poke_user 时应当要么真实执行，要么返回明确失败。
        # 防刷屏交给 platform / 群被戳主动响应 / 跟戳分支各自的限制处理。

        # === 分支 A：aiocqhttp 真戳 ===
        if is_aiocqhttp and group_id:
            max_times = self._cfg_int("poke_max_times", 3, min_value=1)
            try:
                times = int(times)
            except (TypeError, ValueError):
                times = 1
            times = max(1, min(times, max_times))

            interval = self._cfg_float("poke_interval", 0.5, min_value=0)
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
                # NapCat / OneBot 新实现更稳定支持 send_poke；
                # 旧 friend_poke 封装在部分适配组合里可能存在但无实际效果。
                await event.bot.api.call_action("send_poke", user_id=int(user_id))
                self._record_poke(scope, user_id)
                return f"已戳好友 {user_id} 1 次"
            except Exception as send_poke_err:
                logger.warning(
                    f"[litepoke] 私聊 send_poke 失败，尝试 friend_poke 兜底 user_id={user_id}: {send_poke_err}"
                )
                try:
                    await event.bot.friend_poke(user_id=int(user_id))
                    self._record_poke(scope, user_id)
                    return f"已戳好友 {user_id} 1 次"
                except Exception as friend_poke_err:
                    return f"戳好友失败：send_poke={send_poke_err}; friend_poke={friend_poke_err}"

        # === 分支 B：非 aiocqhttp 平台用表情包回退 ===
        self._record_poke(scope, user_id)
        return await self._send_fallback(event)

    # ===================== 辅助：场景引导 =====================

    @filter.on_llm_request()
    async def on_llm_request(self, event, request):
        """在 LLM 请求前注入场景引导 + 可选 PokeLog 统计

        两个独立通道：
        1. PokeLog 统计：默认关闭；开启 poke_log_inject_enabled 后才注入。
        2. 关键词引导：仅当用户消息命中 trigger_keywords 时执行。
        """
        msg = event.message_str or ""
        if not msg:
            return

        if not event.get_group_id() and self.cfg.get("only_group", True):
            return

        try:
            await self._sanitize_current_conversation(event, reason="on_llm_request")
        except Exception as e:
            logger.debug(f"[litepoke] LLM 请求前清理 history 失败: {e}")

        # === 通道 1：PokeLog 统计（默认关闭）===
        # v1.3.4 起戳一戳事件已写入 conversation；PokeLog 仅保留统计能力，默认不再注入 prompt。
        if self.cfg.get("poke_log_inject_enabled", False):
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
        guide_cd = self._cfg_float("guide_cd", 30, min_value=0)
        if time.time() - self._guide_last[scope] < guide_cd:
            return
        self._guide_last[scope] = time.time()

        guide_prompt = self.cfg.get(
            "guide_prompt",
            "[轻量戳一戳提示] 如果当前对话氛围适合轻微互动，可以考虑调用 poke_user 工具；不要因为看到提示就强行调用。",
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
        window = self._cfg_int("poke_log_window", 60, min_value=1)
        self._poke_log.prune_recent(time.time(), window)
        keep_days = self._cfg_int("poke_log_daily_keep_days", 7, min_value=0)
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
        window = self._cfg_int("vibe_window", 60, min_value=1)
        vibe.recent_msgs.append(now)
        vibe.last_msg_sender = str(event.get_sender_id())
        vibe.prune(now, window)

    # ===================== 辅助：戳一戳监听 + 概率跟戳 =====================

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_group_poke(self, event):
        """监听 aiocqhttp 戳一戳事件。

        支持两类场景：
        - bot 被戳：群聊/私聊都可写入上下文并主动调 LLM 回应（私聊受 only_group 配置限制）。
        - 概率跟戳：仅群聊中别人戳别人时生效。
        """
        if not isinstance(event, AiocqhttpMessageEvent):
            return
        if event.get_extra("litepoke_replayed_poke"):
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

        self_id = str(raw.get("self_id", "") or "")
        user_id = str(raw.get("user_id", "") or "")        # 戳人的
        target_id = str(raw.get("target_id", "") or "")    # 被戳的
        group_id = str(raw.get("group_id", "") or "")
        is_private = not group_id
        gid = group_id

        # 私聊开关：与 poke_user 工具保持一致。
        if is_private and self.cfg.get("only_group", True):
            self._diagnose(
                "incoming_poke",
                "private_disabled",
                sender=user_id,
                target=target_id,
            )
            return

        # bot 自己发出的戳一戳是 outgoing 动作，不应作为新的 user 输入进入上下文。
        # 注意：这里必须早于 follow_enabled；follow_enabled 只控制概率跟戳，不控制清理 outgoing notice。
        if user_id and self_id and user_id == self_id:
            removed = await self._drop_recent_raw_poke_from_conversation(event, reason="bot_outgoing_poke")
            if not removed:
                asyncio.create_task(
                    self._delayed_cleanup_poke_history(event, reason="bot_outgoing_poke")
                )
            self._diagnose(
                "outgoing_poke",
                "cleanup_only",
                scope=group_id or "_private",
                sender=user_id,
                target=target_id,
                removed_raw=removed,
            )
            return

        # bot 自己被戳 → 累积 PokeLog + 写入上下文 + 可选主动调 LLM 回应
        if target_id and self_id and target_id == self_id:
            self._poke_log.record(str(user_id), time.time())
            self._poke_log.prune_recent(
                time.time(),
                self._cfg_int("poke_log_window", 60, min_value=1),
            )
            self._maybe_periodic_save()

            # 先构造并写入 poke 事件：respond_poked_cd 只控制是否主动调 LLM，
            # 不应阻止事件进入 conversation，否则 CD 期间的戳会在上下文里丢失。
            try:
                sender_name = event.get_sender_name() or "未知用户"
            except Exception:
                sender_name = "未知用户"
            poke_info = {
                "sender_id": str(user_id),
                "sender_name": sender_name,
                "target_id": str(self_id),
                "is_poke_bot": True,
            }
            poke_text = self._build_poke_event_text(poke_info)
            if not poke_text:
                self._diagnose(
                    "respond_poked",
                    "empty_poke_text",
                    scope=group_id or "_private",
                    sender=user_id,
                    target=target_id,
                )
                return
            wrote_context = await self._append_poke_event_to_conversation(event, poke_text)
            self._diagnose(
                "respond_poked",
                "recorded",
                scope=group_id or "_private",
                sender=user_id,
                wrote_context=wrote_context,
                recent=len(self._poke_log.recent),
                today=self._poke_log.total_today(),
            )
            asyncio.create_task(
                self._delayed_cleanup_poke_history(
                    event,
                    poke_text=poke_text,
                    reason="incoming_poke",
                )
            )

            # 接管：主动触发 LLM 回应（v1.3.0+）
            if not self.cfg.get("respond_poked_enabled", True):
                self._diagnose(
                    "respond_poked",
                    "disabled",
                    scope=group_id or "_private",
                    sender=user_id,
                )
                return

            # CD 控制：避免群友狂戳 bot 导致 LLM 调用刷屏
            respond_cd = self._cfg_float("respond_poked_cd", 10, min_value=0)
            elapsed = time.time() - self._last_respond_poked
            if elapsed < respond_cd:
                self._diagnose(
                    "respond_poked",
                    "cooldown",
                    scope=group_id or "_private",
                    sender=user_id,
                    elapsed=round(elapsed, 2),
                    cd=respond_cd,
                )
                return

            # 概率控制
            respond_prob = self._cfg_float("respond_poked_prob", 1.0, min_value=0.0, max_value=1.0)
            if respond_prob <= 0:
                self._diagnose(
                    "respond_poked",
                    "prob_zero",
                    scope=group_id or "_private",
                    sender=user_id,
                    prob=respond_prob,
                )
                return
            roll = random.random()
            if roll >= respond_prob:
                self._diagnose(
                    "respond_poked",
                    "prob_miss",
                    scope=group_id or "_private",
                    sender=user_id,
                    roll=round(roll, 4),
                    prob=respond_prob,
                )
                return

            # 非 CD：默认直接发送回退表达，避免 notice 事件重投递后只走插件监听、没有触发 LLM。
            # 如需继续尝试“伪消息 → 标准消息 pipeline → LLM”，可把 respond_poked_mode 设为 replay。
            respond_mode = str(
                self.cfg.get("respond_poked_mode", "direct") or "direct"
            ).strip().lower()
            self._last_respond_poked = time.time()

            if respond_mode not in {"replay", "llm"}:
                result = await self._send_fallback(event)
                event.stop_event()
                logger.info(
                    f"[litepoke] 接管戳一戳：scope={group_id or '_private'} sender={user_id} "
                    f"-> 直发回退响应: {result}"
                )
                return

            # llm/replay 模式：直接走 AstrBot 原生 LLM 请求链。
            # 旧版 put_nowait 伪消息在部分 pipeline 中只会触发插件监听，不会进入核心 LLM 回复阶段。
            try:
                prompt_template = self.cfg.get(
                    "respond_poked_prompt",
                    "{poke_event}。请用符合人设的方式简短回应（1-2 句），可以反戳回去（用 poke_user 工具）或吐槽。",
                )
                llm_prompt = prompt_template.format(poke_event=poke_text)
                conversation = await self._get_conversation(event)

                logger.info(
                    f"[litepoke] 接管戳一戳：scope={group_id or '_private'} sender={user_id} "
                    "-> 直接请求 LLM 响应"
                )
                yield event.request_llm(prompt=llm_prompt, conversation=conversation)
            except Exception as e:
                logger.warning(f"[litepoke] 接管戳一戳 LLM 响应失败: {e}")
                try:
                    result = await self._send_fallback(event)
                    event.stop_event()
                    logger.info(
                        f"[litepoke] LLM 响应失败，已直发回退响应: {result}"
                    )
                except Exception as fallback_e:
                    logger.warning(f"[litepoke] 戳一戳回退响应失败: {fallback_e}")
            return

        # 概率跟戳只支持群聊；私聊中不是 bot 被戳/不是 bot 发出的 poke，到这里直接结束。
        if is_private:
            self._diagnose(
                "follow_poke",
                "private_unsupported",
                sender=user_id,
                target=target_id,
            )
            return

        if not self.cfg.get("follow_enabled", True):
            self._diagnose(
                "follow_poke",
                "disabled",
                group=gid,
                sender=user_id,
                target=target_id,
            )
            return

        # 概率判定
        prob = self._cfg_float("follow_prob", 0.1, min_value=0.0, max_value=1.0)
        if prob <= 0:
            self._diagnose(
                "follow_poke",
                "prob_zero",
                group=gid,
                sender=user_id,
                target=target_id,
                prob=prob,
            )
            return

        # CD 兜底：避免群内连续戳一戳事件时连续跟戳
        follow_cd = self._cfg_float("follow_cd", 3, min_value=0)
        elapsed = time.time() - self._last_follow_poke
        if elapsed < follow_cd:
            self._diagnose(
                "follow_poke",
                "cooldown",
                group=gid,
                sender=user_id,
                target=target_id,
                elapsed=round(elapsed, 2),
                cd=follow_cd,
            )
            return

        # === vibe 决策：用群内滑动窗口调整概率 ===
        adjusted_prob, reason = self._vibe_adjust(gid, target_id, prob)
        roll = random.random()
        if roll >= adjusted_prob:
            self._diagnose(
                "follow_poke",
                "prob_miss",
                group=gid,
                sender=user_id,
                target=target_id,
                roll=round(roll, 4),
                base_prob=prob,
                adjusted_prob=round(adjusted_prob, 4),
                vibe_reason=reason,
            )
            return

        # 跟戳
        try:
            await event.bot.group_poke(
                group_id=int(group_id),
                user_id=int(target_id),
            )
            self._last_follow_poke = time.time()
            # 记录到 vibe
            vibe = self._vibe.get(gid)
            if vibe is None:
                vibe = GroupVibe()
                self._vibe[gid] = vibe
            window = self._cfg_int("vibe_window", 60, min_value=1)
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

        window = self._cfg_int("vibe_window", 60, min_value=1)
        now = time.time()
        vibe.prune(now, window)

        prob = base_prob
        msg_count = len(vibe.recent_msgs)
        poke_count = len(vibe.recent_pokes)
        reason_parts: list[str] = []

        active_threshold = self._cfg_int("vibe_active_threshold", 5, min_value=0)
        quiet_threshold = self._cfg_int("vibe_quiet_threshold", 1, min_value=0)
        max_in_window = self._cfg_int("vibe_max_in_window", 1, min_value=0)

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
        self._guide_last.clear()
        self._meme_index.clear()
        self._vibe.clear()
        logger.info("[litepoke] 插件已卸载，guide/meme/vibe/PokeLog 状态已清空")
