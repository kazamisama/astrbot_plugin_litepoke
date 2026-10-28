# Copyright (C) 2026 kazamisama
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
轻量智能戳一戳插件（v2）

设计目标：
1. aiocqhttp 平台：真戳一戳
2. 其他平台（webchat、telegram、飞书等）：没有戳一戳动作，poke_user 直接返回不支持
3. 由 LLM 自主判断何时调用 poke_user tool
4. 可选的场景引导：在 on_llm_request 时根据关键词检测，注入提示

文件结构刻意保持单文件，所有逻辑清晰可见。
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
try:
    from astrbot.core.utils.astrbot_path import (
        get_astrbot_config_path,
        get_astrbot_plugin_data_path,
    )
except ImportError:  # 兼容没有该工具函数的旧版 AstrBot
    get_astrbot_plugin_data_path = None
    get_astrbot_config_path = None
try:
    from astrbot.core.agent.message import TextPart
except ImportError:  # 兼容无 TextPart 的旧版 AstrBot
    TextPart = None
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)


# AstrBot 把原始 Poke 组件写进 history 时可能留下的文本标记（统一小写比较）。
_RAW_POKE_MARKERS = {"[poke:poke]", "[componenttype.poke]", "[poke]"}

# PokeLog 持久化格式版本。旧版本 JSON 没有该字段，加载时会按 v0 兼容。
POKE_LOG_SCHEMA_VERSION = 1

# _conf_schema.json 的功能分组 → 叶键。
# WebUI 按这些 object 组渲染成分组框；插件内部仍然按扁平键读取，
# 因此这里同时负责把升级前的扁平配置迁移到分组结构。
_CONFIG_GROUPS: dict[str, tuple[str, ...]] = {
    "basic_settings": ("only_group",),
    "poke_settings": ("poke_max_times", "poke_interval"),
    "guide_settings": ("trigger_keywords", "guide_cd", "guide_prompt"),
    "bot_poked_settings": (
        "respond_poked_enabled",
        "respond_poked_prob",
        "respond_poked_cd",
        "respond_poked_write_context",
        "respond_poked_prompt",
    ),
    "follow_settings": (
        "follow_enabled",
        "follow_prob",
        "follow_cd",
        "follow_trace_inject_enabled",
        "follow_trace_ttl",
    ),
    "vibe_settings": (
        "vibe_window",
        "vibe_active_threshold",
        "vibe_quiet_threshold",
        "vibe_max_in_window",
    ),
    "poke_log_settings": (
        "poke_log_persist",
        "poke_log_path",
        "poke_log_save_interval",
        "poke_log_window",
        "poke_log_daily_keep_days",
        "poke_log_inject_enabled",
    ),
    "diagnostics_settings": ("debug_diagnostics",),
}
_LEGACY_CONFIG_KEYS = frozenset(
    key for keys in _CONFIG_GROUPS.values() for key in keys
)


def _flatten_config(raw: Any) -> dict[str, Any]:
    """把分组配置展开为扁平键；旧版顶层扁平键优先，保证升级不丢值。"""
    flat: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return flat
    for group in _CONFIG_GROUPS:
        nested = raw.get(group)
        if isinstance(nested, dict):
            for key, value in nested.items():
                flat.setdefault(key, value)
    for key, value in raw.items():
        if key not in _CONFIG_GROUPS:
            flat[key] = value
    return flat


def _migrate_legacy_config_file(path: Path) -> bool:
    """把旧版扁平插件配置改写成 _conf_schema.json 的分组结构。

    只在配置文件仍含旧版顶层键时动手；写前保留 .legacy.bak，写盘走临时文件替换。
    返回 True 表示确实做了迁移。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    if not any(key in data for key in _LEGACY_CONFIG_KEYS):
        return False

    legacy = {key: data[key] for key in _LEGACY_CONFIG_KEYS if key in data}
    migrated = {
        key: value for key, value in data.items() if key not in _LEGACY_CONFIG_KEYS
    }
    for group, keys in _CONFIG_GROUPS.items():
        nested = migrated.get(group)
        if not isinstance(nested, dict):
            nested = {}
        for key in keys:
            if key in legacy and key not in nested:
                nested[key] = legacy[key]
        if nested:
            migrated[group] = nested

    backup = path.with_name(f"{path.name}.legacy.bak")
    tmp = path.with_name(f"{path.name}.migrating")
    try:
        backup.write_bytes(path.read_bytes())
        tmp.write_text(
            json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as e:
        logger.warning(f"[litepoke] 旧配置迁移失败（将使用默认值）: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    logger.info(f"[litepoke] 旧版扁平配置已迁移到分组结构，备份: {backup}")
    return True


def _maybe_migrate_legacy_config() -> None:
    """AstrBot 加载插件模块时（早于 AstrBotConfig 读取）迁移旧扁平配置。"""
    if "astrbot.core.star.star_manager" not in sys.modules:
        return  # 单元测试 / 普通导入不碰真实配置
    if get_astrbot_config_path is None:
        return
    try:
        plugin_name = Path(__file__).resolve().parent.name
        config_path = Path(get_astrbot_config_path()) / f"{plugin_name}_config.json"
        _migrate_legacy_config_file(config_path)
    except Exception as e:
        logger.debug(f"[litepoke] 检查旧配置迁移失败: {e}")


@dataclass
class GroupVibe:
    """群内滑动窗口状态（轻量版群氛围追踪）

    仅记录时间戳和最近一次事件的发送者，不解析内容。
    跟戳决策时根据窗口内事件密度做概率调整。
    """
    recent_msgs: deque = field(default_factory=deque)    # 群内最近消息时间戳
    recent_pokes: deque = field(default_factory=deque)   # 群内最近戳一戳时间戳
    last_msg_sender: str = ""                            # 最近说话的人

    def prune(self, now: float, window: int) -> None:
        """清理窗口外的过期条目"""
        cutoff = now - window
        while self.recent_msgs and self.recent_msgs[0] < cutoff:
            self.recent_msgs.popleft()
        while self.recent_pokes and self.recent_pokes[0] < cutoff:
            self.recent_pokes.popleft()


@dataclass
class FollowPokeTrace:
    """一次自动跟戳的短期解释记录。"""
    ts: float
    group_id: str
    sender_id: str
    target_id: str
    base_prob: float
    adjusted_prob: float
    roll: float
    vibe_reason: str


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

        try:
            last_saved = float(data.get("last_saved", 0.0) or 0.0)
        except (TypeError, ValueError):
            # 单字段损坏不该让整份 PokeLog 报废（调用方会把它当坏文件归档）。
            last_saved = 0.0

        return cls(
            recent=recent_items,
            daily=daily,
            last_saved=last_saved,
        )


class LitePokePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # 配置 schema 按功能分组；内部统一用扁平键读取。
        self.cfg = _flatten_config(config)

        # 跟戳/被戳响应按群隔离，互不共享 CD；工具戳本身不做 CD。
        self._last_follow_poke: dict[str, float] = {}
        self._last_respond_poked: dict[str, float] = {}

        # 引导CD：{ scope: last_guide_time }
        self._guide_last: dict[str, float] = defaultdict(float)
        self._last_group_state_cleanup: float = 0.0

        # 群内滑动窗口（仅 aiocqhttp 群消息场景，用于跟戳决策）
        # { group_id: GroupVibe }
        self._vibe: dict[str, "GroupVibe"] = {}

        # 自动跟戳解释缓存：短期注入给 LLM，消费后清空，避免污染长期上下文。
        self._follow_traces: deque[FollowPokeTrace] = deque(maxlen=20)

        # 戳一戳累积日志（短期 + 长期，跨重启持久化）
        self._poke_log: "PokeLog" = PokeLog()
        self._poke_log_path: Path | None = None

        # 后台清理任务引用：不持有引用时 asyncio 可能在完成前回收任务。
        self._bg_tasks: set[asyncio.Task] = set()

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
        return int(self._clamp(float(value), min_value, max_value))

    def _track_task(self, coro) -> None:
        """把协程挂成后台任务并持有引用，避免任务未完成就被回收。"""
        try:
            task = asyncio.ensure_future(coro)
        except RuntimeError as e:  # 没有运行中的事件循环（理论不可达）
            logger.warning(f"[litepoke] 无法创建后台任务: {e}")
            return
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

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

    # ===================== 内部：戳一戳事件文本构造（移植自 chat_plus；chat_plus 已停用） =====================

    @staticmethod
    def _build_poke_event_text(poke_info: dict | None) -> str:
        """构造可注入到 LLM 的戳一戳事件伪消息文本

        移植自 astrbot_plugin_group_chat_plus 的 build_persistent_poke_event_text
        （chat_plus 插件本身已停用，本方法作为历史溯源保留）。
        litepoke 自 v1.3 起独立运行，不依赖任何外部插件。

        注意：sender_name / target_name 都有 "未知用户" 兜底，所以 sender_text /
        target_text 永远非空，这里只需要区分「戳的是 bot」和「戳的是别人」两种输出。
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

        sender_text = f"{sender_name}(ID:{sender_id})" if sender_id else sender_name
        target_text = f"{target_name}(ID:{target_id})" if target_id else target_name

        if poke_info.get("is_poke_bot", False):
            return f"[戳一戳事件]有人戳了你，发起者是{sender_text}"
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
        """判断 history 中由 AstrBot 原始 Poke 组件产生的空/标记 user 消息。

        只认两类：文本精确等于已知 Poke 标记，或者 content 完全为空。
        纯图片/语音等「没有文本但有内容」的消息不算，避免误删真实用户消息。
        """
        if not isinstance(msg, dict):
            return False
        if msg.get("role") != "user":
            return False

        metadata = msg.get("metadata")
        if isinstance(metadata, dict) and metadata.get("source") == "litepoke":
            return False

        content = msg.get("content")
        text = self._extract_text_from_content(content)
        if text.replace(" ", "").lower() in _RAW_POKE_MARKERS:
            return True
        if text:
            return False

        # 没有文本：只有「完全空」的内容才算原始 Poke 占位。
        if content is None:
            return True
        if isinstance(content, str):
            return not content.strip()
        if isinstance(content, list):
            return all(
                isinstance(part, dict)
                and part.get("type") == "text"
                and not str(part.get("text", "") or "").strip()
                for part in content
            )
        return False

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

    # ===================== 内部：conversation history 读写工具 =====================

    @staticmethod
    def _history_from_conversation(conv: Any) -> list[Any] | None:
        """从 conversation 取 history 列表。

        返回 None 表示 history 存在但无法解析——调用方不得写回，
        否则会把整段对话覆盖成当前改动。
        """
        raw = getattr(conv, "content", None) or getattr(conv, "history", None) or []
        if isinstance(raw, list):
            return list(raw)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except Exception:
                logger.warning("[litepoke] conversation history 不是合法 JSON，跳过写回以保护历史")
                return None
            if not isinstance(parsed, list):
                logger.warning("[litepoke] conversation history 不是消息列表，跳过写回以保护历史")
                return None
            return parsed
        return None

    async def _commit_history(self, event: Any, cid: str, history: list[Any]) -> bool:
        """把 history 写回 conversation；兼容旧版 update_conversation 位置参数签名。"""
        conv_mgr = self.context.conversation_manager
        try:
            await conv_mgr.update_conversation(event.unified_msg_origin, cid, history=history)
            return True
        except TypeError:
            try:
                await conv_mgr.update_conversation(event.unified_msg_origin, cid, history)
                return True
            except Exception as e:
                logger.warning(f"[litepoke] 写回 conversation 失败: {e}")
                return False
        except Exception as e:
            logger.warning(f"[litepoke] 写回 conversation 失败: {e}")
            return False

    @staticmethod
    def _build_poke_event_message(poke_text: str) -> dict:
        """构造写入 conversation 的戳一戳伪消息（纯文本 user 消息，不带 tool 字段）。"""
        return {
            "role": "user",
            "content": [{"type": "text", "text": poke_text}],
            "metadata": {
                "source": "litepoke",
                "event_type": "poke",
                "timestamp": time.time(),
            },
        }

    async def _sanitize_current_conversation(self, event: Any, *, reason: str = "") -> bool:
        """把当前 conversation 中已残留的非法 LLM 消息清掉。"""
        cid, conv = await self._get_conversation_with_id(event)
        if not cid or conv is None:
            return False

        history = self._history_from_conversation(conv)
        if history is None:
            return False

        sanitized, removed = self._sanitize_llm_history(history)
        if not removed:
            return False

        if not await self._commit_history(event, cid, sanitized):
            return False

        suffix = f" reason={reason}" if reason else ""
        logger.warning(f"[litepoke] 已清理非法 LLM history {removed} 条 id={cid}{suffix}")
        return True

    async def _append_poke_event_to_conversation(
        self,
        event: AiocqhttpMessageEvent,
        poke_text: str,
    ) -> bool:
        """把戳一戳 notice 写入官方 conversation，作为后续上下文的一部分。

        只写入一条纯文本 user 消息，不写 tool/tool_calls，避免污染工具调用链。
        history 无法解析时返回 False（绝不写回），避免覆盖用户历史。
        """
        if not self.cfg.get("respond_poked_write_context", True):
            return False
        if not poke_text:
            return False

        cid, conv = await self._get_conversation_with_id(event)
        if not cid or conv is None:
            return False

        history = self._history_from_conversation(conv)
        if history is None:
            return False

        history, removed_invalid = self._sanitize_llm_history(history)

        event_message = self._build_poke_event_message(poke_text)

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

        if not await self._commit_history(event, cid, history):
            return False

        action = "替换原始戳一戳消息" if replaced else "写入戳一戳事件"
        suffix = f"，并清理非法 history {removed_invalid} 条" if removed_invalid else ""
        logger.debug(f"[litepoke] 已{action}到 conversation id={cid}{suffix}")
        return True

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

        history = self._history_from_conversation(conv)
        if history is None:
            return False

        removed = False
        for idx in range(len(history) - 1, max(-1, len(history) - 4), -1):
            if not self._is_raw_poke_history_message(history[idx]):
                continue
            del history[idx]
            removed = True
            break

        if not removed:
            return False

        if not await self._commit_history(event, cid, history):
            return False

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

        history = self._history_from_conversation(conv)
        if not history:
            return False

        event_message = self._build_poke_event_message(poke_text)

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

        if not await self._commit_history(event, cid, history):
            return False

        suffix = f" reason={reason}" if reason else ""
        logger.debug(f"[litepoke] 已延迟规范化 Poke history id={cid}{suffix}")
        return True

    # ===================== 内部：状态记录 =====================

    def _maybe_prune_group_state(self) -> None:
        """按周期清理长期未活动的群级状态，避免多群运行内存持续增长。"""
        now = time.time()
        if now - self._last_group_state_cleanup < 300:
            return
        self._last_group_state_cleanup = now

        vibe_window = self._cfg_int("vibe_window", 60, min_value=1)
        for gid in list(self._vibe.keys()):
            vibe = self._vibe.get(gid)
            if vibe is None:
                continue
            vibe.prune(now, vibe_window)
            if not vibe.recent_msgs and not vibe.recent_pokes:
                self._vibe.pop(gid, None)

        ttl = max(
            300,
            self._cfg_float("follow_cd", 3, min_value=0),
            self._cfg_float("respond_poked_cd", 10, min_value=0),
            self._cfg_float("guide_cd", 30, min_value=0),
        )
        for mapping in (self._last_follow_poke, self._last_respond_poked):
            for gid in [gid for gid, ts in mapping.items() if now - ts > ttl]:
                mapping.pop(gid, None)
        for scope in [scope for scope, ts in self._guide_last.items() if now - ts > ttl]:
            self._guide_last.pop(scope, None)

    # ===================== 内部：PokeLog 持久化 =====================

    @staticmethod
    def _plugin_data_dir() -> Path | None:
        """<plugin_data>/astrbot_plugin_litepoke 目录；旧版 AstrBot 下返回 None。"""
        if get_astrbot_plugin_data_path is None:
            return None
        try:
            return Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_litepoke"
        except Exception as e:
            logger.warning(f"[litepoke] 推断插件数据目录失败: {e}")
            return None

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

        base = self._plugin_data_dir()
        if base is None:
            logger.warning("[litepoke] 推断 poke_log 路径失败: 插件数据目录不可用")
            return None

        p = base / "poke_log.json"
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"[litepoke] 创建 poke_log 目录失败: {e}")
            return None
        self._poke_log_path = p
        return p

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
        """间隔写盘：每 poke_log_save_interval 秒写一次（未开启持久化时直接跳过）"""
        if not self.cfg.get("poke_log_persist", True):
            return
        interval = self._cfg_float("poke_log_save_interval", 60, min_value=1)
        if time.time() - self._poke_log.last_saved >= interval:
            self._save_poke_log()

    # ===================== 核心：LLM Tool =====================

    @filter.llm_tool(name="poke_user")
    async def poke_user(
        self,
        event,
        user_id: str,
        times: int = 1,
    ):
        """戳一戳指定用户（仅 aiocqhttp 平台可用）。

        当你觉得用户有点欠揍、想表达不满、或者单纯想互动时，可以调用这个工具。
        aiocqhttp 平台会发送真实的戳一戳通知；其他平台没有"戳一戳"这个动作，
        调用会返回失败提示，不会发任何消息。

        Args:
            user_id(string): 目标用户的QQ号，必须是纯数字字符串，例如 "12345678"
            times(number): aiocqhttp 平台下戳的次数，默认为 1，不建议超过 3
        """
        if not user_id:
            return "戳一戳失败：user_id 不能为空"

        user_id = str(user_id).strip()

        # 平台 & 作用域
        is_aiocqhttp = isinstance(event, AiocqhttpMessageEvent)
        group_id = event.get_group_id() or ""

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

        # ID 格式校验：aiocqhttp 要求纯数字 QQ 号
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

            if success_count == times:
                return f"已成功戳用户 {user_id} {success_count} 次"
            return f"戳一戳部分失败：成功 {success_count}/{times} 次，错误：{last_err}"

        # === 分支 A2：aiocqhttp 私聊（好友戳） ===
        if is_aiocqhttp:
            try:
                # NapCat / OneBot 新实现更稳定支持 send_poke；
                # 旧 friend_poke 封装在部分适配组合里可能存在但无实际效果。
                await event.bot.api.call_action("send_poke", user_id=int(user_id))
                return f"已戳好友 {user_id} 1 次"
            except Exception as send_poke_err:
                logger.warning(
                    f"[litepoke] 私聊 send_poke 失败，尝试 friend_poke 兜底 user_id={user_id}: {send_poke_err}"
                )
                try:
                    await event.bot.friend_poke(user_id=int(user_id))
                    return f"已戳好友 {user_id} 1 次"
                except Exception as friend_poke_err:
                    return f"戳好友失败：send_poke={send_poke_err}; friend_poke={friend_poke_err}"

        # === 分支 B：非 aiocqhttp 平台没有"戳一戳"这个动作 ===
        return "戳一戳失败：当前平台不支持戳一戳（该动作仅 aiocqhttp 平台可用）"

    # ===================== 辅助：场景引导 =====================

    @filter.on_llm_request()
    async def on_llm_request(self, event, request):
        """在 LLM 请求前注入场景引导、自动跟戳解释和可选 PokeLog 统计。

        三个独立通道统一写入 ``request.extra_user_content_parts`` 并调用
        ``mark_as_temp()``，块只发给 LLM、不写入会话历史，system_prompt
        前缀保持可缓存；旧版 AstrBot 自动回退 system_prompt 拼接。
        """
        self._maybe_prune_group_state()

        if not event.get_group_id() and self.cfg.get("only_group", True):
            return
        # 清理放在 message_str 判空之前：bot 被戳的主动回应是 notice 事件，
        # message_str 为空，但它同样会进 LLM 请求链，history 自清理不该被跳过。
        try:
            await self._sanitize_current_conversation(event, reason="on_llm_request")
        except Exception as e:
            logger.debug(f"[litepoke] LLM 请求前清理 history 失败: {e}")

        msg = event.message_str or ""
        if not msg:
            # 没有用户文本时只做清理，不注入跟戳解释 / 统计 / 关键词引导——
            # 这些通道都以“用户发了一条消息”为触发前提。
            return

        def append_extra_part(block: str) -> None:
            if not block:
                return
            if TextPart is not None and hasattr(request, "extra_user_content_parts"):
                # mark_as_temp(): 只发给 LLM，不写入 conversation 历史。
                request.extra_user_content_parts.append(
                    TextPart(text=block, type="text").mark_as_temp()
                )
            elif hasattr(request, "system_prompt"):
                # 旧版 AstrBot 兜底：退回 system_prompt 拼接。
                if request.system_prompt:
                    request.system_prompt = request.system_prompt.rstrip() + "\n\n" + block
                else:
                    request.system_prompt = block

        scope = str(event.get_group_id() or "_private")

        # === 通道 1：自动跟戳解释（短期缓存，消费后清空）===
        # 跟戳是概率事件；把命中的随机数和氛围理由交给 LLM，避免事后解释靠猜。
        if self.cfg.get("follow_trace_inject_enabled", True):
            append_extra_part(self._build_follow_trace_block(scope))

        # === 通道 2：PokeLog 统计（默认关闭）===
        # v1.3.4 起戳一戳事件已写入 conversation；PokeLog 仅保留统计能力，默认不再注入 prompt。
        if self.cfg.get("poke_log_inject_enabled", False):
            append_extra_part(self._build_poke_log_block())

        # === 通道 3：关键词引导 ===
        keywords = self.cfg.get("trigger_keywords", []) or []
        if not keywords:
            return

        if not any(w in msg for w in keywords):
            return

        # scope 已在前面归一化为 str，供跟戳解释和引导 CD 共用。
        guide_cd = self._cfg_float("guide_cd", 30, min_value=0)
        if time.time() - self._guide_last[scope] < guide_cd:
            return
        self._guide_last[scope] = time.time()

        guide_prompt = self.cfg.get(
            "guide_prompt",
            "[轻量戳一戳提示] 如果当前对话氛围适合轻微互动，可以考虑调用 poke_user 工具；不要因为看到提示就强行调用。",
        )

        append_extra_part(guide_prompt)

    def _build_poke_log_block(self) -> str:
        """构造 PokeLog 统计块（作为临时 TextPart 注入到用户消息）

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

    def _build_follow_trace_block(self, scope: str) -> str:
        """构造自动跟戳解释块，供下一次 LLM 请求理解刚才为什么跟戳。"""
        now = time.time()
        window = self._cfg_int("follow_trace_ttl", 60, min_value=1)
        kept: deque[FollowPokeTrace] = deque(maxlen=20)
        matched: list[FollowPokeTrace] = []

        while self._follow_traces:
            trace = self._follow_traces.popleft()
            if now - trace.ts > window:
                continue
            if trace.group_id == scope:
                matched.append(trace)
            else:
                kept.append(trace)

        self._follow_traces = kept
        if not matched:
            return ""

        lines = []
        for trace in matched[-3:]:
            lines.append(
                "- 自动跟戳了用户 "
                f"{trace.target_id}；触发者={trace.sender_id}；"
                f"基础概率={trace.base_prob:.3f}，"
                f"群氛围调整后概率={trace.adjusted_prob:.3f}，"
                f"随机数={trace.roll:.4f}，因为随机数小于调整后概率所以命中；"
                f"氛围判断={trace.vibe_reason}。"
            )

        return (
            "[自动跟戳解释] 刚才的跟戳是 litepoke 的概率事件，不是你主动决定的。"
            "如果有人问为什么戳人，请根据下面记录简短说明，不要编造额外原因：\n"
            + "\n".join(lines)
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

        self._maybe_prune_group_state()

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
        - bot 被戳：群聊/私聊都可写入上下文并主动发起 LLM 回应（私聊受 only_group 限制）。
        - 概率跟戳：仅群聊中别人戳别人时生效。
        """
        if not isinstance(event, AiocqhttpMessageEvent):
            return

        poke = self._parse_poke_notice(event)
        if poke is None:
            return

        self._maybe_prune_group_state()

        user_id, target_id, group_id, self_id = poke
        scope = group_id or "_private"
        is_private = not group_id

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
            await self._handle_outgoing_poke(
                event,
                user_id=user_id,
                target_id=target_id,
                scope=scope,
            )
            return

        # bot 自己被戳 → 累积 PokeLog + 写入上下文 + 可选主动调 LLM 回应
        if target_id and self_id and target_id == self_id:
            request = await self._handle_bot_poked(
                event,
                user_id=user_id,
                target_id=target_id,
                scope=scope,
            )
            if request is not None:
                yield request
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

        await self._handle_follow_poke(
            event,
            user_id=user_id,
            target_id=target_id,
            scope=scope,
        )

    @staticmethod
    def _parse_poke_notice(event) -> tuple[str, str, str, str] | None:
        """解析 OneBot 戳一戳 notice，返回 (user_id, target_id, group_id, self_id)。

        兼容两种上报形状：notice_type=notify + sub_type=poke（OneBot v11 标准）
        与 notice_type=poke（部分实现）。AstrBot 自身只看 sub_type，这里放宽到
        notice_type=poke，避免漏掉只标了后者的事件。
        """
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if not isinstance(raw, dict):
            return None
        if raw.get("post_type") != "notice":
            return None
        notice_type = str(raw.get("notice_type") or "")
        sub_type = str(raw.get("sub_type") or "")
        if sub_type != "poke" and notice_type != "poke":
            return None
        return (
            str(raw.get("user_id", "") or ""),
            str(raw.get("target_id", "") or ""),
            str(raw.get("group_id", "") or ""),
            str(raw.get("self_id", "") or ""),
        )

    async def _handle_outgoing_poke(
        self, event, *, user_id: str, target_id: str, scope: str
    ) -> None:
        """处理 bot 自己发出的戳一戳（outgoing notice）：只清理上下文，不触发回应。"""
        removed = await self._drop_recent_raw_poke_from_conversation(
            event, reason="bot_outgoing_poke"
        )
        if not removed:
            self._track_task(
                self._delayed_cleanup_poke_history(event, reason="bot_outgoing_poke")
            )
        self._diagnose(
            "outgoing_poke",
            "cleanup_only",
            scope=scope,
            sender=user_id,
            target=target_id,
            removed_raw=removed,
        )

    async def _handle_bot_poked(
        self, event, *, user_id: str, target_id: str, scope: str
    ) -> Any | None:
        """处理 bot 被戳：记录统计、写入上下文，并按配置返回一次 LLM 请求。

        返回 None 表示本轮不回应或无法发起 LLM 请求（已计数并写入上下文）。
        """
        now = time.time()
        self._poke_log.record(str(user_id), now)
        self._poke_log.prune_recent(now, self._cfg_int("poke_log_window", 60, min_value=1))
        self._maybe_periodic_save()

        # 先构造并写入 poke 事件：respond_poked_cd 只控制是否主动调 LLM，
        # 不应阻止事件进入 conversation，否则 CD 期间的戳会在上下文里丢失。
        try:
            sender_name = event.get_sender_name() or "未知用户"
        except Exception:
            sender_name = "未知用户"
        poke_text = self._build_poke_event_text(
            {
                "sender_id": str(user_id),
                "sender_name": sender_name,
                "target_id": str(target_id),
                "is_poke_bot": True,
            }
        )
        if not poke_text:
            self._diagnose(
                "respond_poked",
                "empty_poke_text",
                scope=scope,
                sender=user_id,
                target=target_id,
            )
            return None

        wrote_context = await self._append_poke_event_to_conversation(event, poke_text)
        self._diagnose(
            "respond_poked",
            "recorded",
            scope=scope,
            sender=user_id,
            wrote_context=wrote_context,
            recent=len(self._poke_log.recent),
            today=self._poke_log.total_today(),
        )
        self._track_task(
            self._delayed_cleanup_poke_history(
                event,
                poke_text=poke_text,
                reason="incoming_poke",
            )
        )

        # 主动回应：主动触发 LLM 回应（v1.3.0+）
        if not self.cfg.get("respond_poked_enabled", True):
            self._diagnose("respond_poked", "disabled", scope=scope, sender=user_id)
            return None

        # CD 控制：避免群友狂戳 bot 导致 LLM 调用刷屏
        respond_cd = self._cfg_float("respond_poked_cd", 10, min_value=0)
        elapsed = now - self._last_respond_poked.get(scope, 0.0)
        if elapsed < respond_cd:
            self._diagnose(
                "respond_poked",
                "cooldown",
                scope=scope,
                sender=user_id,
                elapsed=round(elapsed, 2),
                cd=respond_cd,
            )
            return None

        # 概率控制
        respond_prob = self._cfg_float(
            "respond_poked_prob", 1.0, min_value=0.0, max_value=1.0
        )
        if respond_prob <= 0:
            self._diagnose(
                "respond_poked",
                "prob_zero",
                scope=scope,
                sender=user_id,
                prob=respond_prob,
            )
            return None
        roll = random.random()
        if roll >= respond_prob:
            self._diagnose(
                "respond_poked",
                "prob_miss",
                scope=scope,
                sender=user_id,
                roll=round(roll, 4),
                prob=respond_prob,
            )
            return None

        # CD 在发起响应前就记下：即使后续 LLM 链路失败，也按“已响应”计，
        # 避免失败重试把群刷屏。这是刻意的“防刷屏优先”取舍。
        self._last_respond_poked[scope] = time.time()

        # 被戳回应统一走 AstrBot 原生 LLM 请求链，不再直发固定文字。
        try:
            prompt_template = self.cfg.get(
                "respond_poked_prompt",
                "{poke_event}。请用符合人设的方式简短回应（1-2 句），可以反戳回去（用 poke_user 工具）或吐槽。",
            )
            conversation = await self._get_conversation(event)
            # 事件已写入 conversation 时，prompt 只引用上文，避免同一 poke_text
            # 同时出现在历史和新 user 消息里，让模型误判被戳两次。
            if wrote_context and conversation is not None:
                poke_event_ref = "[请参考上文最新的戳一戳事件]"
            else:
                poke_event_ref = poke_text
            llm_prompt = prompt_template.format(poke_event=poke_event_ref)

            logger.info(
                f"[litepoke] 主动回应被戳：scope={scope} sender={user_id} "
                "-> 直接请求 LLM 响应"
            )
            return event.request_llm(prompt=llm_prompt, conversation=conversation)
        except Exception as e:
            logger.warning(f"[litepoke] 主动回应被戳 LLM 响应失败: {e}")
            return None

    async def _handle_follow_poke(
        self, event, *, user_id: str, target_id: str, scope: str
    ) -> None:
        """群内概率跟戳：CD + vibe 调整 + 掷骰，命中后戳一下并记录解释。"""
        if not self.cfg.get("follow_enabled", True):
            self._diagnose(
                "follow_poke",
                "disabled",
                group=scope,
                sender=user_id,
                target=target_id,
            )
            return

        prob = self._cfg_float("follow_prob", 0.1, min_value=0.0, max_value=1.0)
        if prob <= 0:
            self._diagnose(
                "follow_poke",
                "prob_zero",
                group=scope,
                sender=user_id,
                target=target_id,
                prob=prob,
            )
            return

        # CD 兜底：避免群内连续戳一戳事件时连续跟戳
        follow_cd = self._cfg_float("follow_cd", 3, min_value=0)
        elapsed = time.time() - self._last_follow_poke.get(scope, 0.0)
        if elapsed < follow_cd:
            self._diagnose(
                "follow_poke",
                "cooldown",
                group=scope,
                sender=user_id,
                target=target_id,
                elapsed=round(elapsed, 2),
                cd=follow_cd,
            )
            return

        # === vibe 决策：用群内滑动窗口调整概率 ===
        adjusted_prob, reason = self._vibe_adjust(scope, target_id, prob)
        roll = random.random()
        if roll >= adjusted_prob:
            self._diagnose(
                "follow_poke",
                "prob_miss",
                group=scope,
                sender=user_id,
                target=target_id,
                roll=round(roll, 4),
                base_prob=prob,
                adjusted_prob=round(adjusted_prob, 4),
                vibe_reason=reason,
            )
            return

        try:
            await event.bot.group_poke(
                group_id=int(scope),
                user_id=int(target_id),
            )
        except Exception as e:
            logger.warning(f"[litepoke] 跟戳失败: {e}")
            self._diagnose(
                "follow_poke",
                "send_failed",
                group=scope,
                sender=user_id,
                target=target_id,
                error=e,
            )
            return

        now = time.time()
        self._last_follow_poke[scope] = now
        # 记录到 vibe
        vibe = self._vibe.get(scope)
        if vibe is None:
            vibe = GroupVibe()
            self._vibe[scope] = vibe
        window = self._cfg_int("vibe_window", 60, min_value=1)
        vibe.recent_pokes.append(now)
        vibe.prune(now, window)
        self._follow_traces.append(
            FollowPokeTrace(
                ts=now,
                group_id=scope,
                sender_id=str(user_id),
                target_id=str(target_id),
                base_prob=prob,
                adjusted_prob=adjusted_prob,
                roll=roll,
                vibe_reason=reason,
            )
        )
        self._diagnose(
            "follow_poke",
            "hit",
            group=scope,
            sender=user_id,
            target=target_id,
            roll=round(roll, 4),
            base_prob=prob,
            adjusted_prob=round(adjusted_prob, 4),
            vibe_reason=reason,
        )
        logger.debug(f"[litepoke] 跟戳 group={scope} target={target_id} reason={reason}")

    def _vibe_adjust(self, gid: str, target_id: str, base_prob: float) -> tuple[float, str]:
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
        # 0 会让规则 1 恒成立（等于静默关闭跟戳），跟戳开关请用 follow_enabled。
        max_in_window = self._cfg_int("vibe_max_in_window", 1, min_value=1)

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
        for task in list(self._bg_tasks):
            task.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()

        self._save_poke_log()
        self._guide_last.clear()
        self._vibe.clear()
        self._follow_traces.clear()
        self._last_follow_poke.clear()
        self._last_respond_poked.clear()
        logger.info("[litepoke] 插件已卸载，guide/vibe/PokeLog 状态已清空")


# 插件模块导入发生在星管理创建 AstrBotConfig 之前，借这个时机把旧版扁平配置
# 迁移成新分组结构；测试或普通导入时上面的守卫会直接跳过。
_maybe_migrate_legacy_config()
