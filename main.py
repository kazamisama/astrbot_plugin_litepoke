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
import random
import time
from collections import defaultdict
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

        # CD 检查
        if not self._cd_passed(scope, user_id):
            return "戳一戳失败：CD 还没到，请稍候再试"

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
        """在 LLM 请求前注入场景引导提示"""
        msg = event.message_str or ""
        if not msg:
            return

        if not event.get_group_id() and self.cfg.get("only_group", True):
            return

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

    # ===================== 生命周期 =====================

    async def terminate(self):
        self._user_last_poke.clear()
        self._guide_last.clear()
        self._meme_index.clear()
        logger.info("[litepoke] 插件已卸载，CD/meme 索引已清空")
