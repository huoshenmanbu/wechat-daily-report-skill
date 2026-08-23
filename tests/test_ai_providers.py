# -*- coding: utf-8 -*-
"""Unit tests for ai_providers helpers (run from repo root: python -m unittest tests.test_ai_providers)."""

import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ai_providers.common.json_extract import extract_first_json_object  # noqa: E402
from ai_providers.common.load_context import load_stats_and_chat  # noqa: E402
from ai_providers.common.normalize import normalize_ai_content  # noqa: E402
from ai_providers.cursor_cli import parse_cli_stdout_to_ai_dict  # noqa: E402
from ai_providers.dashscope import generate_dashscope  # noqa: E402
from ai_providers.deepseek import generate_deepseek  # noqa: E402
from ai_providers.registry import run_provider  # noqa: E402


class JsonExtractTests(unittest.TestCase):
    def test_plain_object(self):
        text = '{"topics": [], "qas": []}'
        d = extract_first_json_object(text)
        self.assertEqual(d["topics"], [])

    def test_fenced_json(self):
        text = 'noise\n```json\n{"topics": [{"title": "x"}], "qas": []}\n```\n'
        d = extract_first_json_object(text)
        self.assertEqual(d["topics"][0]["title"], "x")

    def test_embedded_object(self):
        text = 'Prefix {"topics": [], "resources": [], "qas": [], "dialogues": [], "important_messages": [], "topic_heat": [], "talker_profiles": {}} suffix'
        d = extract_first_json_object(text)
        self.assertIn("topics", d)


class NormalizeTests(unittest.TestCase):
    def test_qas_min_three(self):
        stats = {"top_talkers": [{"name": "Alice", "count": 5}]}
        out = normalize_ai_content({"qas": [], "topics": []}, stats)
        self.assertGreaterEqual(len(out["qas"]), 3)

    def test_talker_top_three_keys(self):
        stats = {"top_talkers": [{"name": "Bob", "count": 3}]}
        out = normalize_ai_content({}, stats)
        self.assertIn("Bob", out["talker_profiles"])


class CursorCliParseTests(unittest.TestCase):
    def test_prefers_full_digest_shape_over_shallow_topics(self):
        payload = {
            "topics": [],
            "wrapper": {
                "content": '{"topics":[{"title":"x"}],"resources":[],"important_messages":[],"dialogues":[],"qas":[{"q":"a"},{"q":"b"},{"q":"c"}],"topic_heat":[],"talker_profiles":{}}'
            },
        }
        parsed = parse_cli_stdout_to_ai_dict(json.dumps(payload, ensure_ascii=False))
        self.assertIn("resources", parsed)
        self.assertEqual(parsed["topics"][0]["title"], "x")


class LoadContextTests(unittest.TestCase):
    def test_resolves_relative_raw_text_paths_from_stats_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "raw").mkdir()
            stats_path = root / "stats.json"
            raw_path = root / "raw" / "chat.txt"
            raw_path.write_text("hello", encoding="utf-8")
            stats_path.write_text(
                json.dumps({"meta": {"raw_text_paths": ["raw/chat.txt"]}}, ensure_ascii=False),
                encoding="utf-8",
            )
            stats, text = load_stats_and_chat(str(stats_path), None, max_chat_chars=1000)
            self.assertIn("meta", stats)
            self.assertIn("hello", text)


class CompatibleApiProviderTests(unittest.TestCase):
    class _Response:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def test_deepseek_uses_env_key_and_returns_json_object(self):
        response = self._Response({"choices": [{"message": {"content": '{"summary": {"overview": "ok"}}'}}]})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret-key"}, clear=False):
            with patch("ai_providers.compatible_api.urllib.request.urlopen", return_value=response) as mocked:
                result = generate_deepseek("hello", {"deepseek": {"model": "deepseek-chat"}})

        self.assertEqual("ok", result["summary"]["overview"])
        request = mocked.call_args.args[0]
        self.assertEqual("https://api.deepseek.com/chat/completions", request.full_url)
        self.assertEqual("Bearer secret-key", request.get_header("Authorization"))

    def test_dashscope_rejects_missing_key_without_network_request(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("ai_providers.compatible_api.urllib.request.urlopen") as mocked:
                with self.assertRaisesRegex(RuntimeError, "DASHSCOPE_API_KEY"):
                    generate_dashscope("hello", {"dashscope": {}})

        mocked.assert_not_called()

    def test_dashscope_success_uses_compatible_endpoint(self):
        response = self._Response({"choices": [{"message": {"content": '{"summary": {"overview": "qwen"}}'}}]})
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "dash-key"}, clear=False):
            with patch("ai_providers.compatible_api.urllib.request.urlopen", return_value=response) as mocked:
                result = generate_dashscope("hello", {"dashscope": {}})

        self.assertEqual("qwen", result["summary"]["overview"])
        self.assertEqual(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            mocked.call_args.args[0].full_url,
        )

    def test_http_error_does_not_echo_response_or_api_key(self):
        error = urllib.error.HTTPError("https://api.deepseek.com", 401, "bad secret-key", {}, None)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret-key"}, clear=False):
            with patch("ai_providers.compatible_api.urllib.request.urlopen", side_effect=error):
                with self.assertRaises(RuntimeError) as raised:
                    generate_deepseek("hello", {"deepseek": {}})

        self.assertNotIn("secret-key", str(raised.exception))

    def test_retries_rate_limit_then_succeeds(self):
        error = urllib.error.HTTPError("https://api.deepseek.com", 429, "rate limited", {}, None)
        response = self._Response({"choices": [{"message": {"content": '{"summary": {"overview": "retried"}}'}}]})
        options = {"deepseek": {"retry_times": 1, "retry_backoff_seconds": 0}}
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret-key"}, clear=False):
            with patch(
                "ai_providers.compatible_api.urllib.request.urlopen",
                side_effect=[error, response],
            ) as mocked:
                result = generate_deepseek("hello", options)

        self.assertEqual("retried", result["summary"]["overview"])
        self.assertEqual(2, mocked.call_count)

    def test_rejects_response_without_chat_content(self):
        response = self._Response({"choices": []})
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "dash-key"}, clear=False):
            with patch("ai_providers.compatible_api.urllib.request.urlopen", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "no chat completion content"):
                    generate_dashscope("hello", {"dashscope": {}})

    def test_registry_dispatches_deepseek(self):
        expected = {"summary": {"overview": "registry"}}
        with patch("ai_providers.registry.generate_deepseek", return_value=expected) as mocked:
            result = run_provider(
                "deepseek",
                stats={},
                stats_path="stats.json",
                output_path="ai.json",
                chat_text="hello",
                repo_root=".",
                provider_options={"deepseek": {"model": "deepseek-chat"}},
                prompt_text="prompt",
            )

        self.assertEqual(expected, result)
        mocked.assert_called_once_with("prompt", {"deepseek": {"model": "deepseek-chat"}})


if __name__ == "__main__":
    unittest.main()
