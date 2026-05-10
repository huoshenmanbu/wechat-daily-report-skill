# -*- coding: utf-8 -*-
"""Unit tests for report_senders helpers."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from report_senders.common.payload import build_text_payload  # noqa: E402
from report_senders.common.payload import build_text_payload_chunks  # noqa: E402
from report_senders.common.sanitize import redact_sensitive  # noqa: E402
from report_senders.feishu_cli_webhook import send_feishu_cli_webhook  # noqa: E402
from report_senders.feishu_cli_webhook import _resolve_args_template  # noqa: E402
import report_senders.registry as sender_registry  # noqa: E402
from report_senders.registry import send_report_with_retry  # noqa: E402


class PayloadTests(unittest.TestCase):
    def test_payload_truncates(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.md"
            p.write_text("A" * 1000, encoding="utf-8")
            data = build_text_payload(str(p), chatroom="g", window_id="w", max_chars=200)
            self.assertIn("…[truncated]", data["text"])
            self.assertIn("群聊总结", data["title"])

    def test_payload_chunks_split_when_long(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.md"
            p.write_text("B" * 1200, encoding="utf-8")
            max_chars = 300
            chunks = build_text_payload_chunks(str(p), chatroom="g", window_id="w", max_chars=max_chars)
            self.assertGreaterEqual(len(chunks), 2)
            self.assertIn("(1/", chunks[0]["text"])
            self.assertIn("本地文件:", chunks[-1]["text"])
            self.assertTrue(all(len(c["text"]) <= max_chars for c in chunks))


class SanitizeTests(unittest.TestCase):
    def test_redacts_webhook_and_token(self):
        src = "url=https://open.feishu.cn/open-apis/bot/v2/hook/abc token=SECRET123"
        out = redact_sensitive(src)
        self.assertNotIn("SECRET123", out)
        self.assertIn("<REDACTED", out)

    def test_redacts_query_token(self):
        src = "https://x.y/z?token=abc123&k=1"
        out = redact_sensitive(src)
        self.assertNotIn("abc123", out)
        self.assertIn("<REDACTED>", out)

    def test_redacts_query_signature_and_access_token(self):
        src = "https://x.y/z?signature=sig123&access_token=tok123&k=1"
        out = redact_sensitive(src)
        self.assertNotIn("sig123", out)
        self.assertNotIn("tok123", out)
        self.assertIn("signature=<REDACTED>", out)
        self.assertIn("access_token=<REDACTED>", out)


class ArgsTemplateTests(unittest.TestCase):
    def test_secret_pair_removed_when_empty(self):
        tpl = ["webhook", "send", "--secret", "__SECRET__", "--content", "__CONTENT__"]
        args = _resolve_args_template(
            tpl,
            webhook="w",
            secret="",
            content="c",
            msg_type="text",
        )
        self.assertNotIn("--secret", args)
        self.assertIn("--content", args)

    def test_secret_inline_replaced(self):
        tpl = ["x", "--secret=__SECRET__"]
        args = _resolve_args_template(
            tpl,
            webhook="w",
            secret="S",
            content="c",
            msg_type="text",
        )
        self.assertIn("--secret=S", args)


class FeishuWebhookIntegrationTests(unittest.TestCase):
    @patch("report_senders.feishu_cli_webhook.subprocess.run")
    def test_send_webhook_success_and_message_id(self, run_mock):
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = '{"message_id":"msg-123"}'
        run_mock.return_value.stderr = ""

        sender_options = {
            "feishu_cli_webhook": {
                "command": ["feishu-cli"],
                "args_template": [
                    "webhook",
                    "send",
                    "--url",
                    "__WEBHOOK__",
                    "--msg-type",
                    "__MSG_TYPE__",
                    "--content",
                    "__CONTENT__",
                ],
            }
        }
        payload = {"text": "hello from test"}

        with patch.dict("os.environ", {"FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc"}):
            out = send_feishu_cli_webhook(payload, sender_options, timeout_seconds=5)

        self.assertTrue(out["ok"])
        self.assertEqual(out["message_id"], "msg-123")
        self.assertEqual(out["provider"], "feishu_cli_webhook")
        self.assertTrue(run_mock.called)
        cmd = run_mock.call_args.kwargs.get("args") or run_mock.call_args.args[0]
        self.assertIn("--url", cmd)
        self.assertIn("--content", cmd)


class RegistryTests(unittest.TestCase):
    def test_none_sender_ok(self):
        out = send_report_with_retry(
            "none",
            {"text": "x"},
            {},
            timeout_seconds=1,
            retry_times=2,
            retry_backoff_seconds=0,
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["provider"], "none")

    def test_unknown_sender_raises(self):
        with self.assertRaises(RuntimeError):
            send_report_with_retry(
                "unknown",
                {"text": "x"},
                {},
                timeout_seconds=1,
                retry_times=0,
                retry_backoff_seconds=0,
            )

    def test_retry_until_success(self):
        calls = {"n": 0}
        original = sender_registry.send_feishu_cli_webhook

        def fake_sender(_payload, _sender_options, *, timeout_seconds):  # noqa: ARG001
            calls["n"] += 1
            if calls["n"] < 2:
                return {
                    "ok": False,
                    "provider": "feishu_cli_webhook",
                    "message_id": None,
                    "error_code": "x",
                    "error": "fail",
                }
            return {
                "ok": True,
                "provider": "feishu_cli_webhook",
                "message_id": "m1",
                "error_code": None,
                "error": None,
            }

        sender_registry.send_feishu_cli_webhook = fake_sender
        try:
            out = send_report_with_retry(
                "feishu_cli_webhook",
                {"text": "x"},
                {},
                timeout_seconds=1,
                retry_times=2,
                retry_backoff_seconds=0,
            )
        finally:
            sender_registry.send_feishu_cli_webhook = original

        self.assertTrue(out["ok"])
        self.assertEqual(out["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
