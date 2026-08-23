# -*- coding: utf-8 -*-
"""Unit tests for report_senders helpers."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from report_senders.common.payload import build_text_payload  # noqa: E402
from report_senders.common.payload import build_text_payload_chunks  # noqa: E402
from report_senders.common.sanitize import redact_sensitive  # noqa: E402
from report_senders.feishu_cli_webhook import send_feishu_cli_webhook  # noqa: E402
from report_senders.feishu_cli_webhook import _resolve_args_template  # noqa: E402
from report_senders.feishu_webhook import send_feishu_webhook  # noqa: E402
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


class DirectFeishuWebhookTests(unittest.TestCase):
    @patch("report_senders.feishu_webhook.urlopen")
    @patch("report_senders.feishu_webhook.time.time", return_value=1_700_000_000)
    def test_send_webhook_posts_text_and_optional_signature(self, _time_mock, urlopen_mock):
        response = MagicMock()
        response.read.return_value = b'{"code": 0, "msg": "success"}'
        urlopen_mock.return_value.__enter__.return_value = response

        with patch.dict(
            "os.environ",
            {
                "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
                "FEISHU_WEBHOOK_SECRET": "signing-secret",
            },
        ):
            out = send_feishu_webhook(
                {"text": "hello from test"},
                {"feishu_webhook": {"message_format": "text"}},
                timeout_seconds=5,
            )

        self.assertTrue(out["ok"])
        self.assertEqual(out["provider"], "feishu_webhook")
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, "https://open.feishu.cn/open-apis/bot/v2/hook/abc")
        self.assertEqual(urlopen_mock.call_args.kwargs["timeout"], 5)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["msg_type"], "text")
        self.assertEqual(body["content"], {"text": "hello from test"})
        self.assertEqual(body["timestamp"], "1700000000")
        self.assertEqual(body["sign"], "HsyfQO1P0UCajraMQX1oZdxcKNMCR4IrjjzIVqAXhEw=")

    @patch("report_senders.feishu_webhook.urlopen")
    def test_empty_or_unrecognized_response_is_not_success(self, urlopen_mock):
        with patch.dict("os.environ", {"FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc"}):
            for raw in (b"", b"{}", b'{"code": null}'):
                with self.subTest(raw=raw):
                    response = MagicMock()
                    response.read.return_value = raw
                    urlopen_mock.return_value.__enter__.return_value = response
                    out = send_feishu_webhook({"text": "hello"}, {}, timeout_seconds=5)
                    self.assertFalse(out["ok"])
                    self.assertEqual(out["error_code"], "invalid_response")

    @patch("report_senders.feishu_webhook.urlopen")
    def test_unsigned_request_omits_signature_fields_and_accepts_legacy_success(self, urlopen_mock):
        response = MagicMock()
        response.read.return_value = b'{"StatusCode": 0, "StatusMessage": "success"}'
        urlopen_mock.return_value.__enter__.return_value = response

        with patch.dict(
            "os.environ",
            {"FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc"},
            clear=True,
        ):
            out = send_feishu_webhook({"text": "hello"}, {}, timeout_seconds=5)

        self.assertTrue(out["ok"])
        body = json.loads(urlopen_mock.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("timestamp", body)
        self.assertNotIn("sign", body)

    @patch("report_senders.feishu_webhook.urlopen")
    def test_feishu_error_response_is_reported_without_echoing_webhook(self, urlopen_mock):
        response = MagicMock()
        response.read.return_value = b'{"code": 19024, "msg": "Key Words Not Found"}'
        urlopen_mock.return_value.__enter__.return_value = response

        with patch.dict("os.environ", {"FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/abc"}):
            out = send_feishu_webhook({"text": "hello"}, {}, timeout_seconds=5)

        self.assertFalse(out["ok"])
        self.assertEqual(out["error_code"], "19024")
        self.assertEqual(out["error"], "Key Words Not Found")


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

    @patch.object(sender_registry, "send_feishu_webhook")
    def test_direct_webhook_sender_is_dispatched(self, sender_mock):
        sender_mock.return_value = {
            "ok": True,
            "provider": "feishu_webhook",
            "message_id": None,
            "error_code": None,
            "error": None,
        }

        out = send_report_with_retry(
            "feishu_webhook",
            {"text": "x"},
            {},
            timeout_seconds=1,
            retry_times=0,
            retry_backoff_seconds=0,
        )

        self.assertTrue(out["ok"])
        self.assertEqual(out["provider"], "feishu_webhook")
        sender_mock.assert_called_once()

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
