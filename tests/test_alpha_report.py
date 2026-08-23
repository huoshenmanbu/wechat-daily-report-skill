import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.generate_report import build_text_report, main


class AlphaReportTests(unittest.TestCase):
    def test_returns_empty_text_when_no_alpha_exists(self):
        report = build_text_report(
            {"meta": {"name": "测试群"}, "alpha_candidates": []},
            {"other_activity": "其余为闲聊，无新增投资信息。"},
        )

        self.assertEqual("", report)

    def test_legacy_key_information_without_action_is_not_actionable(self):
        report = build_text_report(
            {"meta": {"name": "测试群"}, "alpha_candidates": []},
            {
                "key_information": [
                    {
                        "title": "项目完成新一轮融资",
                        "detail": "群内转发了一条融资消息。",
                        "action_or_followup": "",
                    }
                ]
            },
        )

        self.assertEqual("", report)

    def test_html_output_is_empty_when_no_alpha_exists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stats_path = root / "stats.json"
            ai_path = root / "ai.json"
            output_path = root / "report.html"
            stats_path.write_text(
                json.dumps({"meta": {"name": "测试群"}, "alpha_candidates": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            ai_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")

            argv = [
                "generate_report.py",
                "--stats",
                str(stats_path),
                "--ai-content",
                str(ai_path),
                "--output",
                str(output_path),
            ]
            with patch.object(sys, "argv", argv):
                main()

            self.assertEqual("", output_path.read_text(encoding="utf-8"))

    def test_default_report_is_a_concise_alpha_brief(self):
        report = build_text_report(
            {
                "meta": {"name": "测试群", "date": "2026-08-22", "time_range": "10:00 至 11:00"},
                "night_owl": {"name": "闲聊用户", "last_time": "03:00", "msg_count": 1},
                "word_cloud": [{"text": "吃饭", "count": 20}],
            },
            {
                "summary": {"overview": "SOL 与新币上线是本时段投资主线。"},
                "investment_meme_focus": [
                    {
                        "symbol_or_theme": "SOL",
                        "market_bias": "关注做多",
                        "why_mentioned": "Alice 认为回踩支撑后可关注。",
                        "key_signals": ["回踩支撑"],
                        "risks": ["跌破支撑"],
                    }
                ],
                "alpha_actions": [
                    {"action": "观察 SOL 支撑", "detail": "跌破则放弃", "source_people": ["Alice"]}
                ],
                "other_activity": "其余为闲聊，无新增投资信息。",
                "topics": [{"title": "不应展示的普通话题"}],
                "member_sentiment": [{"name": "Bob"}],
            },
        )

        self.assertIn("# 测试群 Alpha 快报", report)
        self.assertIn("## 交易/标的", report)
        self.assertIn("SOL：关注做多；Alice 认为回踩支撑后可关注。", report)
        self.assertIn("## 可行动信号", report)
        self.assertIn("观察 SOL 支撑；跌破则放弃（来源：Alice）", report)
        self.assertIn("## 其他动态", report)
        self.assertIn("其余为闲聊，无新增投资信息。", report)
        self.assertNotIn("讨论热点", report)
        self.assertNotIn("成员情绪", report)
        self.assertNotIn("深夜活跃", report)
        self.assertNotIn("词云高频词", report)


if __name__ == "__main__":
    unittest.main()
