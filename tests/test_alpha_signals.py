import unittest

from scripts.alpha_signals import extract_alpha_candidates
from scripts.generate_report import build_text_report


class AlphaSignalTests(unittest.TestCase):
    def test_prioritizes_focus_member_with_investment_evidence(self):
        messages = [
            {
                "sender": "alice_id",
                "accountName": "Alice",
                "groupNickname": "Alice",
                "timestamp": 1_700_000_000,
                "type": 0,
                "content": "项目明天上所，合约 0x1234567890abcdef1234567890abcdef12345678，先观察流动性",
            },
            {
                "sender": "bob_id",
                "accountName": "Bob",
                "groupNickname": "Bob",
                "timestamp": 1_700_000_010,
                "type": 0,
                "content": "哈哈哈",
            },
        ]

        candidates = extract_alpha_candidates(messages, focus_members=["Alice"])

        self.assertEqual(1, len(candidates))
        candidate = candidates[0]
        self.assertEqual("Alice", candidate["sender"])
        self.assertTrue(candidate["is_focus_member"])
        self.assertEqual("高", candidate["priority"])
        self.assertIn("重点成员", candidate["signals"])
        self.assertIn("催化剂", candidate["signals"])
        self.assertIn("链上地址", candidate["signals"])

    def test_excludes_social_chatter_without_investment_signal(self):
        messages = [
            {
                "sender": "alice_id",
                "accountName": "Alice",
                "groupNickname": "Alice",
                "timestamp": 1_700_000_000,
                "type": 0,
                "content": "早上好，今天吃什么？",
            }
        ]

        self.assertEqual([], extract_alpha_candidates(messages, focus_members=["Alice"]))

    def test_keeps_terse_non_social_message_from_focus_member(self):
        messages = [
            {
                "sender": "alice_id",
                "accountName": "Alice",
                "groupNickname": "Alice",
                "timestamp": 1_700_000_000,
                "type": 0,
                "content": "SOL 可以看了",
            }
        ]

        candidates = extract_alpha_candidates(messages, focus_members=["Alice"])

        self.assertEqual(1, len(candidates))
        self.assertIn("重点成员", candidates[0]["signals"])

    def test_detects_evm_address_next_to_chinese_and_solana_address(self):
        evm = "合约0x1234567890abcdef1234567890abcdef12345678这个"
        solana = "9xQeWvG816bUx9EPfA7F2qMNjW6ZpC1kY3tR5sH8aB2C"
        messages = [
            {"sender": "a", "accountName": "A", "timestamp": 1, "type": 0, "content": evm},
            {"sender": "b", "accountName": "B", "timestamp": 2, "type": 0, "content": solana},
        ]

        candidates = extract_alpha_candidates(messages)

        self.assertEqual(2, len(candidates))
        self.assertTrue(all("链上地址" in row["signals"] for row in candidates))

    def test_keeps_non_focus_message_when_it_has_multiple_hard_signals(self):
        messages = [
            {
                "sender": "bob_id",
                "accountName": "Bob",
                "groupNickname": "Bob",
                "timestamp": 1_700_000_000,
                "type": 0,
                "content": "融资公告：https://example.com/round，估值 5000 万，今晚公布代币经济模型",
            }
        ]

        candidates = extract_alpha_candidates(messages, focus_members=[])

        self.assertEqual(1, len(candidates))
        self.assertFalse(candidates[0]["is_focus_member"])
        self.assertGreaterEqual(candidates[0]["score"], 5)

    def test_report_renders_deterministic_alpha_candidates_before_ai_summary(self):
        report = build_text_report(
            {
                "meta": {"name": "测试群", "date": "2026-08-22", "time_range": "10:00 至 11:00"},
                "alpha_candidates": [
                    {
                        "priority": "高",
                        "sender": "Alice",
                        "time": "10:12",
                        "signals": ["重点成员", "催化剂"],
                        "content": "项目明天上所，先观察流动性",
                    }
                ],
            },
            {},
        )

        self.assertIn("## 优先关注线索", report)
        self.assertIn("Alice @ 10:12", report)
        self.assertIn("重点成员、催化剂", report)

    def test_report_is_silent_when_provider_fails_without_alpha(self):
        report = build_text_report(
            {"meta": {"name": "测试群"}},
            {"_provider_status": {"ok": False, "provider": "deepseek"}},
        )

        self.assertEqual("", report)


if __name__ == "__main__":
    unittest.main()
