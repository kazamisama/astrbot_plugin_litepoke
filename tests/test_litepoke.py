# Copyright (C) 2026 kazamisama
# SPDX-License-Identifier: AGPL-3.0-or-later

"""litepoke 纯逻辑单元测试（stdlib unittest，零依赖）。

运行方式（在插件根目录）：

    python -m unittest discover -s tests -v

测试通过 stub 掉 astrbot 相关模块来导入 main.py，因此不需要安装 AstrBot。
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import time
import types
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_astrbot_stubs() -> None:
    """注册最小可用的 astrbot 模块桩，让 main.py 可以在普通 Python 下导入。"""

    def mod(name: str, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        return module

    class TextPart:
        def __init__(self, text: str = "", type: str = "text"):
            self.text = text
            self.type = type

        def mark_as_temp(self):
            return self

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = list(chain or [])

    class Plain:
        def __init__(self, text: str = ""):
            self.text = text

    class AiocqhttpMessageEvent:
        pass

    class Star:
        def __init__(self, context=None):
            self.context = context

    class _Filter:
        class PlatformAdapterType:
            AIOCQHTTP = "aiocqhttp"

        class EventMessageType:
            GROUP_MESSAGE = "group_message"
            ALL = "all"

        @staticmethod
        def llm_tool(**_kwargs):
            def deco(fn):
                return fn

            return deco

        @staticmethod
        def on_llm_request(*_args, **_kwargs):
            def deco(fn):
                return fn

            return deco

        @staticmethod
        def platform_adapter_type(_adapter):
            def deco(fn):
                return fn

            return deco

        @staticmethod
        def event_message_type(_message_type):
            def deco(fn):
                return fn

            return deco

    astrbot = mod("astrbot")
    astrbot.api = mod("astrbot.api", logger=logging.getLogger("litepoke-tests"))
    astrbot.api.event = mod("astrbot.api.event", filter=_Filter)
    astrbot.api.star = mod("astrbot.api.star", Context=object, Star=Star)
    astrbot.core = mod("astrbot.core")
    astrbot.core.config = mod("astrbot.core.config")
    astrbot.core.config.astrbot_config = mod("astrbot.core.config.astrbot_config", AstrBotConfig=dict)
    astrbot.core.agent = mod("astrbot.core.agent")
    astrbot.core.agent.message = mod("astrbot.core.agent.message", TextPart=TextPart)
    astrbot.core.message = mod("astrbot.core.message")
    astrbot.core.message.components = mod(
        "astrbot.core.message.components", Plain=Plain
    )
    astrbot.core.message.message_event_result = mod(
        "astrbot.core.message.message_event_result", MessageChain=MessageChain
    )
    astrbot.core.platform = mod("astrbot.core.platform")
    astrbot.core.platform.sources = mod("astrbot.core.platform.sources")
    astrbot.core.platform.sources.aiocqhttp = mod("astrbot.core.platform.sources.aiocqhttp")
    astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event = mod(
        "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event",
        AiocqhttpMessageEvent=AiocqhttpMessageEvent,
    )


_install_astrbot_stubs()

import main  # noqa: E402  (必须先装好 stub)


def make_plugin(**overrides):
    cfg = {"poke_log_persist": False}
    cfg.update(overrides)
    return main.LitePokePlugin(None, cfg)


class _FakeEvent:
    def __init__(self, raw_message, unified_msg_origin="test:group:1"):
        self.message_obj = types.SimpleNamespace(raw_message=raw_message)
        self.unified_msg_origin = unified_msg_origin


class PokeLogTests(unittest.TestCase):
    def test_record_and_aggregate(self):
        log = main.PokeLog()
        now = time.time()
        log.record("alice", now)
        log.record("alice", now + 1)
        log.record("bob", now + 2)
        self.assertEqual(log.total_today(), 3)
        self.assertEqual(log.top_sender_today(), ("alice", 2))
        self.assertEqual(len(log.consume_recent()), 3)
        self.assertEqual(len(log.recent), 0)
        self.assertEqual(log.total_today(), 3)  # daily 不受消费影响

    def test_prune_recent(self):
        log = main.PokeLog()
        now = time.time()
        log.recent.append(("old", now - 120))
        log.recent.append(("new", now))
        log.prune_recent(now, 60)
        self.assertEqual([s for s, _ in log.recent], ["new"])

    def test_prune_daily(self):
        log = main.PokeLog()
        today = date.today().isoformat()
        log.daily = {"2000-01-01": {"a": 1}, today: {"b": 1}}
        log.prune_daily(7)
        self.assertEqual(list(log.daily), [today])

        log2 = main.PokeLog()
        log2.daily = {"2000-01-01": {"a": 1}}
        log2.prune_daily(0)
        self.assertIn("2000-01-01", log2.daily)

    def test_roundtrip(self):
        log = main.PokeLog()
        log.record("alice", time.time())
        restored = main.PokeLog.from_dict(json.loads(json.dumps(log.to_dict())))
        self.assertEqual(list(restored.recent), list(log.recent))
        self.assertEqual(restored.daily, log.daily)
        self.assertIsInstance(restored.recent, type(log.recent))

    def test_from_dict_skips_corrupt_entries(self):
        data = {
            "recent": [["a", "x"], ["b", 2.5], "junk", ["c"]],
            "daily": {"d": {"a": 2, "b": "NaN", "c": -3}, "x": []},
            "last_saved": "not-a-number",
        }
        log = main.PokeLog.from_dict(data)
        self.assertEqual([s for s, _ in log.recent], ["b"])
        self.assertEqual(log.daily, {"d": {"a": 2, "c": 0}})
        self.assertEqual(log.last_saved, 0.0)

    def test_from_dict_rejects_non_object(self):
        with self.assertRaises(ValueError):
            main.PokeLog.from_dict([])


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.plugin = make_plugin()

    def test_clamp(self):
        self.assertEqual(main.LitePokePlugin._clamp(5, 0, 3), 3)
        self.assertEqual(main.LitePokePlugin._clamp(-1, 0, 3), 0)
        self.assertEqual(main.LitePokePlugin._clamp(2), 2)

    def test_cfg_int_bad_value_falls_back(self):
        self.assertEqual(self.plugin._cfg_int("missing", 7), 7)
        plugin = make_plugin(bad="abc")
        self.assertEqual(plugin._cfg_int("bad", 7), 7)
        self.assertEqual(plugin._cfg_float("bad", 1.5), 1.5)

    def test_cfg_int_truncates_and_clamps(self):
        plugin = make_plugin(num=5.9)
        self.assertEqual(plugin._cfg_int("num", 1, min_value=1, max_value=3), 3)
        plugin = make_plugin(num=0)
        self.assertEqual(plugin._cfg_int("num", 1, min_value=1, max_value=3), 1)


class ConfigFlattenTests(unittest.TestCase):
    def test_grouped_values_flatten(self):
        flat = main._flatten_config(
            {
                "basic_settings": {"only_group": False},
                "follow_settings": {"follow_prob": 0.3},
                "poke_log_settings": {"poke_log_persist": False},
            }
        )
        self.assertFalse(flat["only_group"])
        self.assertEqual(flat["follow_prob"], 0.3)
        self.assertFalse(flat["poke_log_persist"])
        self.assertNotIn("basic_settings", flat)

    def test_top_level_legacy_value_wins(self):
        flat = main._flatten_config(
            {"only_group": True, "basic_settings": {"only_group": False}}
        )
        self.assertTrue(flat["only_group"])

    def test_plugin_reads_grouped_config(self):
        plugin = make_plugin(basic_settings={"only_group": False})
        self.assertFalse(plugin.cfg["only_group"])

    def test_flatten_handles_non_dict(self):
        self.assertEqual(main._flatten_config(None), {})
        self.assertEqual(main._flatten_config("oops"), {})

    def test_schema_groups_match_code_groups(self):
        schema = json.loads(
            (ROOT / "_conf_schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(schema), set(main._CONFIG_GROUPS))
        for group, keys in main._CONFIG_GROUPS.items():
            self.assertEqual(schema[group]["type"], "object")
            self.assertEqual(set(schema[group]["items"]), set(keys))
            for key in keys:
                self.assertIn("default", schema[group]["items"][key])

    def test_schema_has_no_direct_mode_keys(self):
        schema = json.loads(
            (ROOT / "_conf_schema.json").read_text(encoding="utf-8")
        )
        items = schema["bot_poked_settings"]["items"]
        self.assertNotIn("respond_poked_mode", items)
        self.assertNotIn("respond_poked_text", items)


class LegacyConfigMigrationTests(unittest.TestCase):
    def test_migrates_flat_config_to_groups(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "astrbot_plugin_litepoke_config.json"
            old = {
                "only_group": False,
                "trigger_keywords": ["笨蛋"],
                "respond_poked_prob": 0.5,
                "guide_prompt": "custom prompt",
                "future_key": 1,
            }
            path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

            self.assertTrue(main._migrate_legacy_config_file(path))

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("only_group", data)
            self.assertFalse(data["basic_settings"]["only_group"])
            self.assertEqual(data["guide_settings"]["trigger_keywords"], ["笨蛋"])
            self.assertEqual(data["guide_settings"]["guide_prompt"], "custom prompt")
            self.assertEqual(data["bot_poked_settings"]["respond_poked_prob"], 0.5)
            self.assertEqual(data["future_key"], 1)

            backup = path.with_name(path.name + ".legacy.bak")
            self.assertTrue(backup.exists())
            old_backup = json.loads(backup.read_text(encoding="utf-8"))
            self.assertFalse(old_backup["only_group"])
            self.assertFalse(main._migrate_legacy_config_file(path))

    def test_keeps_newer_group_value_on_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "astrbot_plugin_litepoke_config.json"
            path.write_text(
                json.dumps(
                    {
                        "only_group": False,
                        "basic_settings": {"only_group": True},
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(main._migrate_legacy_config_file(path))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data["basic_settings"]["only_group"])


class PokeEventTextTests(unittest.TestCase):
    def test_bot_poked(self):
        text = main.LitePokePlugin._build_poke_event_text(
            {"sender_id": "1", "sender_name": "alice", "target_id": "2", "is_poke_bot": True}
        )
        self.assertIn("有人戳了你", text)
        self.assertIn("alice(ID:1)", text)

    def test_other_people(self):
        text = main.LitePokePlugin._build_poke_event_text(
            {"sender_id": "1", "sender_name": "alice", "target_id": "2", "target_name": "bob"}
        )
        self.assertIn("不是戳你的消息", text)
        self.assertIn("alice(ID:1)", text)
        self.assertIn("bob(ID:2)", text)

    def test_missing_names_fall_back(self):
        text = main.LitePokePlugin._build_poke_event_text({"sender_id": "1", "target_id": "2"})
        self.assertIn("未知用户(ID:1)", text)
        self.assertIn("未知用户(ID:2)", text)

    def test_empty_input(self):
        self.assertEqual(main.LitePokePlugin._build_poke_event_text(None), "")
        self.assertEqual(main.LitePokePlugin._build_poke_event_text({}), "")
        self.assertEqual(main.LitePokePlugin._build_poke_event_text("oops"), "")


class RawPokeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.plugin = make_plugin()

    def test_empty_content_is_raw_poke(self):
        self.assertTrue(self.plugin._is_raw_poke_history_message({"role": "user", "content": []}))
        self.assertTrue(self.plugin._is_raw_poke_history_message({"role": "user", "content": ""}))
        self.assertTrue(self.plugin._is_raw_poke_history_message({"role": "user"}))
        self.assertTrue(
            self.plugin._is_raw_poke_history_message(
                {"role": "user", "content": [{"type": "text", "text": "   "}]}
            )
        )

    def test_markers(self):
        for marker in ("[Poke]", "[ComponentType.Poke]", "[poke:poke]"):
            self.assertTrue(
                self.plugin._is_raw_poke_history_message({"role": "user", "content": marker}),
                marker,
            )

    def test_content_bearing_message_is_not_raw_poke(self):
        image_msg = {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}}],
        }
        self.assertFalse(self.plugin._is_raw_poke_history_message(image_msg))
        self.assertFalse(
            self.plugin._is_raw_poke_history_message({"role": "user", "content": "你好"})
        )

    def test_ignores_litepoke_and_non_user(self):
        self.assertFalse(
            self.plugin._is_raw_poke_history_message(
                {"role": "user", "content": [], "metadata": {"source": "litepoke"}}
            )
        )
        self.assertFalse(self.plugin._is_raw_poke_history_message({"role": "assistant", "content": "hi"}))
        self.assertFalse(self.plugin._is_raw_poke_history_message("not-a-dict"))


class SanitizeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.plugin = make_plugin()

    def test_removes_empty_assistant(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": []},
            {"role": "user", "content": "ok"},
        ]
        sanitized, removed = self.plugin._sanitize_llm_history(history)
        self.assertEqual(removed, 1)
        self.assertEqual([m["role"] for m in sanitized], ["user", "user"])

    def test_removes_orphan_tool(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "call-1", "content": "x"},
        ]
        sanitized, removed = self.plugin._sanitize_llm_history(history)
        self.assertEqual(removed, 1)
        self.assertEqual(sanitized, [history[0]])

    def test_keeps_valid_tool_pair(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1"}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "result"},
            {"role": "assistant", "content": "done"},
        ]
        sanitized, removed = self.plugin._sanitize_llm_history(history)
        self.assertEqual(removed, 0)
        self.assertEqual(sanitized, history)

    def test_removes_tool_after_new_user_turn(self):
        history = [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1"}]},
            {"role": "user", "content": "打断"},
            {"role": "tool", "tool_call_id": "call-1", "content": "late"},
        ]
        sanitized, removed = self.plugin._sanitize_llm_history(history)
        self.assertEqual(removed, 1)
        self.assertEqual([m["role"] for m in sanitized], ["assistant", "user"])


class HistoryReadWriteTests(unittest.TestCase):
    def setUp(self):
        self.plugin = make_plugin()

    def test_reads_list_attribute(self):
        conv = types.SimpleNamespace(history=[{"role": "user", "content": "x"}])
        self.assertEqual(self.plugin._history_from_conversation(conv), [{"role": "user", "content": "x"}])

    def test_reads_json_string(self):
        conv = types.SimpleNamespace(history='[{"role": "user", "content": "x"}]')
        self.assertEqual(len(self.plugin._history_from_conversation(conv)), 1)

    def test_corrupt_json_returns_none(self):
        conv = types.SimpleNamespace(history="{not json")
        self.assertIsNone(self.plugin._history_from_conversation(conv))

    def test_non_list_json_returns_none(self):
        conv = types.SimpleNamespace(history='{"role": "user"}')
        self.assertIsNone(self.plugin._history_from_conversation(conv))

    def test_empty_conversation_returns_empty_list(self):
        conv = types.SimpleNamespace(history=None)
        self.assertEqual(self.plugin._history_from_conversation(conv), [])

    def test_poke_event_message_shape(self):
        msg = self.plugin._build_poke_event_message("[戳一戳事件]hi")
        self.assertEqual(msg["role"], "user")
        self.assertEqual(msg["content"], [{"type": "text", "text": "[戳一戳事件]hi"}])
        self.assertEqual(msg["metadata"]["source"], "litepoke")


class VibeAdjustTests(unittest.TestCase):
    def setUp(self):
        self.plugin = make_plugin()

    def test_no_vibe_state(self):
        prob, reason = self.plugin._vibe_adjust("g", "1", 0.2)
        self.assertEqual(prob, 0.2)
        self.assertEqual(reason, "no_vibe")

    def test_active_group_boosts(self):
        now = time.time()
        vibe = main.GroupVibe()
        vibe.recent_msgs.extend([now] * 6)
        vibe.last_msg_sender = "1"
        self.plugin._vibe["g"] = vibe
        prob, reason = self.plugin._vibe_adjust("g", "1", 0.2)
        self.assertAlmostEqual(prob, 0.3)
        self.assertIn("active(6)", reason)

    def test_quiet_group_damps(self):
        self.plugin._vibe["g"] = main.GroupVibe()
        prob, reason = self.plugin._vibe_adjust("g", "1", 0.2)
        self.assertAlmostEqual(prob, 0.06)
        self.assertIn("quiet(0)", reason)

    def test_target_silent_halves(self):
        now = time.time()
        vibe = main.GroupVibe()
        vibe.recent_msgs.extend([now] * 6)
        vibe.last_msg_sender = "2"
        self.plugin._vibe["g"] = vibe
        prob, reason = self.plugin._vibe_adjust("g", "1", 0.2)
        self.assertAlmostEqual(prob, 0.15)
        self.assertIn("target_silent", reason)

    def test_max_in_window_blocks(self):
        now = time.time()
        vibe = main.GroupVibe()
        vibe.recent_pokes.append(now)
        self.plugin._vibe["g"] = vibe
        prob, reason = self.plugin._vibe_adjust("g", "1", 0.2)
        self.assertEqual(prob, 0.0)
        self.assertIn("max_in_window", reason)

    def test_expired_poke_does_not_block(self):
        vibe = main.GroupVibe()
        vibe.recent_pokes.append(time.time() - 120)
        self.plugin._vibe["g"] = vibe
        prob, _ = self.plugin._vibe_adjust("g", "1", 0.2)
        self.assertGreater(prob, 0.0)

    def test_caps_at_one(self):
        now = time.time()
        vibe = main.GroupVibe()
        vibe.recent_msgs.extend([now] * 6)
        vibe.last_msg_sender = "1"
        self.plugin._vibe["g"] = vibe
        prob, _ = self.plugin._vibe_adjust("g", "1", 0.9)
        self.assertEqual(prob, 1.0)

    def test_max_in_window_zero_is_clamped_to_one(self):
        now = time.time()
        self.plugin.cfg["vibe_max_in_window"] = 0
        vibe = main.GroupVibe()
        vibe.recent_pokes.append(now)
        self.plugin._vibe["g"] = vibe
        prob, reason = self.plugin._vibe_adjust("g", "1", 0.2)
        self.assertEqual(prob, 0.0)
        self.assertIn("max_in_window", reason)


class ParsePokeNoticeTests(unittest.TestCase):
    def setUp(self):
        self.plugin = make_plugin()

    def test_standard_notify_poke(self):
        raw = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "self_id": 100,
            "user_id": 200,
            "target_id": 300,
            "group_id": 400,
        }
        self.assertEqual(
            self.plugin._parse_poke_notice(_FakeEvent(raw)),
            ("200", "300", "400", "100"),
        )

    def test_notice_type_poke_variant(self):
        raw = {"post_type": "notice", "notice_type": "poke", "user_id": 1, "target_id": 2, "group_id": 3, "self_id": 4}
        self.assertEqual(self.plugin._parse_poke_notice(_FakeEvent(raw)), ("1", "2", "3", "4"))

    def test_rejects_non_notice(self):
        raw = {"post_type": "message", "notice_type": "notify", "sub_type": "poke"}
        self.assertIsNone(self.plugin._parse_poke_notice(_FakeEvent(raw)))

    def test_rejects_missing_raw(self):
        self.assertIsNone(self.plugin._parse_poke_notice(types.SimpleNamespace(message_obj=None)))

    def test_private_poke(self):
        raw = {"post_type": "notice", "notice_type": "notify", "sub_type": "poke", "self_id": 100, "user_id": 200, "target_id": 100}
        self.assertEqual(self.plugin._parse_poke_notice(_FakeEvent(raw)), ("200", "100", "", "100"))


class PluginDataDirTests(unittest.TestCase):
    def test_returns_none_without_astrbot_utils(self):
        # 测试桩里没有 astrbot.core.utils.astrbot_path，导入时已降级为 None。
        self.assertIsNone(main.get_astrbot_plugin_data_path)
        self.assertIsNone(main.LitePokePlugin._plugin_data_dir())


class BotPokedResponseTests(unittest.IsolatedAsyncioTestCase):
    class _Event:
        def __init__(self):
            self.requests = []

        def get_sender_name(self):
            return "alice"

        def request_llm(self, **kwargs):
            self.requests.append(kwargs)
            return "llm-request"

    async def test_uses_llm_request(self):
        plugin = make_plugin(
            respond_poked_write_context=False,
            respond_poked_cd=0,
            respond_poked_prob=1.0,
        )

        async def no_cleanup(*_args, **_kwargs):
            return None

        plugin._delayed_cleanup_poke_history = no_cleanup
        event = self._Event()
        result = await plugin._handle_bot_poked(
            event, user_id="123", target_id="bot", scope="g1"
        )
        self.assertEqual(result, "llm-request")
        self.assertEqual(len(event.requests), 1)
        self.assertIn("[戳一戳事件]", event.requests[0]["prompt"])


class PokeUserPlatformTests(unittest.IsolatedAsyncioTestCase):
    class _Event:
        def __init__(self):
            self.sent = []

        def get_group_id(self):
            return "g1"

        def get_self_id(self):
            return "bot"

        async def send(self, chain):
            self.sent.append(chain)

    async def test_non_aiocqhttp_returns_unsupported(self):
        plugin = make_plugin()
        event = self._Event()
        result = await plugin.poke_user(event, "123")
        self.assertIn("不支持", result)
        self.assertEqual(event.sent, [])


if __name__ == "__main__":
    unittest.main()
