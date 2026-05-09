# -*- coding: utf-8 -*-
"""Unit tests for ai_providers helpers (run from repo root: python -m unittest tests.test_ai_providers)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ai_providers.common.json_extract import extract_first_json_object  # noqa: E402
from ai_providers.common.load_context import load_stats_and_chat  # noqa: E402
from ai_providers.common.normalize import normalize_ai_content  # noqa: E402
from ai_providers.cursor_cli import parse_cli_stdout_to_ai_dict  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
