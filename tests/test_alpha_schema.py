import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ai_providers.cursor_cli import parse_cli_stdout_to_ai_dict  # noqa: E402


class AlphaSchemaTests(unittest.TestCase):
    def test_cursor_parser_accepts_compact_alpha_schema(self):
        output = (
            '{"summary":{"overview":"SOL 是主线"},'
            '"investment_meme_focus":[{"symbol_or_theme":"SOL"}],'
            '"alpha_actions":[{"action":"观察支撑"}],'
            '"other_activity":"其余为闲聊"}'
        )

        result = parse_cli_stdout_to_ai_dict(output)

        self.assertEqual("SOL 是主线", result["summary"]["overview"])
        self.assertEqual("观察支撑", result["alpha_actions"][0]["action"])

    def test_prompt_defines_only_the_compact_alpha_schema(self):
        prompt = (ROOT / "references" / "ai_prompt.md").read_text(encoding="utf-8-sig")

        self.assertIn("实际输出必须只包含这四个顶层键", prompt)
        self.assertNotIn("须输出下方 JSON 全部顶层键", prompt)
        self.assertNotIn("旧版日报 JSON", prompt)
        self.assertNotIn("`topics` 3–6", prompt)


if __name__ == "__main__":
    unittest.main()
