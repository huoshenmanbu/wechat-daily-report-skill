# -*- coding: utf-8 -*-
"""Tests for Cursor CLI session persistence and resume argv wiring."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ai_providers import cursor_cli as cc  # noqa: E402
from ai_providers.cursor_cli_session import (  # noqa: E402
    clear_session,
    load_session_chat_id,
    normalize_on_resume_failure,
    resolve_state_path,
    sanitize_chat_id,
    save_session_chat_id,
)


def _digest_envelope_result_inner() -> str:
    inner = (
        '{"topics":[{"title":"x"}],'
        '"resources":[],"important_messages":[],"dialogues":[],'
        '"qas":[{"q":"a"},{"q":"b"},{"q":"c"}],'
        '"topic_heat":[],"talker_profiles":{}}'
    )
    return json.dumps(
        {"type": "result", "subtype": "success", "result": inner, "sessionId": "chat-from-envelope"},
        ensure_ascii=False,
    )


class SessionStoreTests(unittest.TestCase):
    def test_resolve_relative_under_repo(self):
        with tempfile.TemporaryDirectory() as td:
            p = resolve_state_path(td, "runtime/foo.json")
            self.assertEqual(p, Path(td) / "runtime" / "foo.json")

    def test_resolve_absolute(self):
        with tempfile.TemporaryDirectory() as td:
            abs_path = str(Path(td) / "abs.json")
            p = resolve_state_path("/other", abs_path)
            self.assertEqual(p, Path(abs_path))

    def test_load_corrupt_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            opts = {"session_state_path": "state.json"}
            path = Path(td) / "state.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(load_session_chat_id(td, opts))

    def test_save_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            opts = {"session_state_path": "state.json"}
            save_session_chat_id(td, opts, "  my-id  ")
            self.assertEqual(load_session_chat_id(td, opts), "my-id")
            clear_session(td, opts)
            self.assertIsNone(load_session_chat_id(td, opts))

    def test_normalize_invalid_on_resume_failure(self):
        self.assertEqual(
            normalize_on_resume_failure({"on_resume_failure": "nope"}),
            "clear_and_retry_fresh",
        )

    def test_resolve_rejects_parent_escape(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                resolve_state_path(td, "../outside.json")

    def test_load_ignores_unsanitary_chat_id_in_file(self):
        with tempfile.TemporaryDirectory() as td:
            opts = {"session_state_path": "state.json"}
            path = Path(td) / "state.json"
            path.write_text(
                json.dumps({"version": 1, "chat_id": "has space"}),
                encoding="utf-8",
            )
            self.assertIsNone(load_session_chat_id(td, opts))


class SanitizeChatIdTests(unittest.TestCase):
    def test_accepts_common_opaque_ids(self):
        self.assertEqual(sanitize_chat_id("  abc-123_X.oc_9  "), "abc-123_X.oc_9")

    def test_rejects_shellish_chars(self):
        self.assertIsNone(sanitize_chat_id('x";rm -rf'))
        self.assertIsNone(sanitize_chat_id("a" * 300))


class ExtractChatIdTests(unittest.TestCase):
    def test_reads_session_id_from_envelope(self):
        out = _digest_envelope_result_inner()
        self.assertEqual(cc.extract_chat_id_from_cli_stdout(out), "chat-from-envelope")

    def test_non_result_returns_none(self):
        self.assertIsNone(cc.extract_chat_id_from_cli_stdout('{"type":"other"}'))


class RunCursorCliResumeTests(unittest.TestCase):
    def setUp(self):
        self._calls: list[list[str]] = []

    def _capture_invoke(self, base, prompt, cwd, timeout, creation_flags=0):
        self._calls.append(list(base))
        inner = (
            '{"topics":[{"title":"x"}],'
            '"resources":[],"important_messages":[],"dialogues":[],'
            '"qas":[{"q":"a"},{"q":"b"},{"q":"c"}],'
            '"topic_heat":[],"talker_profiles":{}}'
        )
        out = json.dumps(
            {"type": "result", "subtype": "success", "result": inner},
            ensure_ascii=False,
        )
        return 0, out, ""

    def test_reuse_false_does_not_add_resume(self):
        with tempfile.TemporaryDirectory() as td:
            opts = {"cursor_cli": {"reuse_session": False, "command": ["agent"]}}
            with patch.object(cc, "_run_agent_once", side_effect=self._capture_invoke):
                cc.run_cursor_cli("hello", td, opts)
            self.assertEqual(len(self._calls), 1)
            self.assertNotIn("--resume", self._calls[0])

    def test_reuse_true_with_saved_id_adds_resume(self):
        with tempfile.TemporaryDirectory() as td:
            opts = {
                "cursor_cli": {
                    "reuse_session": True,
                    "command": ["agent"],
                    "session_state_path": "runtime/sess.json",
                }
            }
            save_session_chat_id(td, opts["cursor_cli"], "abc123")
            with patch.object(cc, "_run_agent_once", side_effect=self._capture_invoke):
                cc.run_cursor_cli("hello", td, opts)
            self.assertEqual(len(self._calls), 1)
            self.assertIn("--resume", self._calls[0])
            idx = self._calls[0].index("--resume")
            self.assertEqual(self._calls[0][idx + 1], "abc123")

    def test_resume_style_continue(self):
        with tempfile.TemporaryDirectory() as td:
            opts = {
                "cursor_cli": {
                    "reuse_session": True,
                    "resume_style": "continue",
                    "command": ["agent"],
                }
            }
            with patch.object(cc, "_run_agent_once", side_effect=self._capture_invoke):
                cc.run_cursor_cli("hello", td, opts)
            self.assertIn("--continue", self._calls[0])

    def test_nonzero_with_resume_retries_without_resume(self):
        calls: list[list[str]] = []

        def side_effect(base, prompt, cwd, timeout, creation_flags=0):
            calls.append(list(base))
            if any(a == "--resume" for a in base):
                return 1, "", "resume failed"
            inner = (
                '{"topics":[{"title":"x"}],'
                '"resources":[],"important_messages":[],"dialogues":[],'
                '"qas":[{"q":"a"},{"q":"b"},{"q":"c"}],'
                '"topic_heat":[],"talker_profiles":{}}'
            )
            out = json.dumps(
                {"type": "result", "subtype": "success", "result": inner},
                ensure_ascii=False,
            )
            return 0, out, ""

        with tempfile.TemporaryDirectory() as td:
            opts = {
                "cursor_cli": {
                    "reuse_session": True,
                    "command": ["agent"],
                    "session_state_path": "runtime/sess.json",
                    "on_resume_failure": "clear_and_retry_fresh",
                }
            }
            save_session_chat_id(td, opts["cursor_cli"], "bad-id")
            with patch.object(cc, "_run_agent_once", side_effect=side_effect):
                cc.run_cursor_cli("hello", td, opts)
            self.assertEqual(len(calls), 2)
            self.assertIn("--resume", calls[0])
            self.assertNotIn("--resume", calls[1])

    def test_nonzero_with_resume_raise_skips_retry(self):
        def side_effect(base, prompt, cwd, timeout, creation_flags=0):
            if any(a == "--resume" for a in base):
                return 1, "", "boom"
            return 0, "{}", ""

        with tempfile.TemporaryDirectory() as td:
            opts = {
                "cursor_cli": {
                    "reuse_session": True,
                    "command": ["agent"],
                    "session_state_path": "runtime/sess.json",
                    "on_resume_failure": "raise",
                }
            }
            save_session_chat_id(td, opts["cursor_cli"], "x")
            with patch.object(cc, "_run_agent_once", side_effect=side_effect):
                with self.assertRaises(RuntimeError):
                    cc.run_cursor_cli("hello", td, opts)

    def test_non_dict_cursor_cli_opts_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as td:
            opts = {"cursor_cli": ["not", "a", "dict"]}
            with patch.object(cc, "_run_agent_once", side_effect=self._capture_invoke):
                cc.run_cursor_cli("hello", td, opts)
            self.assertEqual(len(self._calls), 1)
            self.assertNotIn("--resume", self._calls[0])


if __name__ == "__main__":
    unittest.main()
