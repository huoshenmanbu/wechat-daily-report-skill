# -*- coding: utf-8 -*-
"""Send report text directly to a Feishu custom-bot webhook."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict
from urllib.request import Request, urlopen

from report_senders.common.sanitize import redact_sensitive


def send_feishu_webhook(
    payload: Dict[str, str],
    sender_options: Dict[str, Any],
    *,
    timeout_seconds: int,
) -> Dict[str, Any]:
    """Post a text message to a Feishu custom-bot webhook using only the stdlib."""
    cfg = (sender_options or {}).get("feishu_webhook") or {}
    webhook_env = str(cfg.get("webhook_url_env") or "FEISHU_WEBHOOK_URL").strip()
    secret_env = str(cfg.get("webhook_secret_env") or "FEISHU_WEBHOOK_SECRET").strip()
    webhook = os.getenv(webhook_env, "").strip()
    if not webhook:
        raise RuntimeError(f"Missing env `{webhook_env}` for Feishu webhook")
    if not webhook.startswith("https://"):
        raise ValueError("Feishu webhook URL must use https")

    message_format = str(cfg.get("message_format") or "text").strip().lower()
    if message_format != "text":
        raise ValueError("feishu_webhook.message_format currently supports only text")

    body: Dict[str, Any] = {
        "msg_type": "text",
        "content": {"text": payload["text"]},
    }
    secret = os.getenv(secret_env, "").strip()
    if secret:
        timestamp = str(int(time.time()))
        body["timestamp"] = timestamp
        body["sign"] = _build_signature(timestamp, secret)

    request = Request(
        webhook,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(request, timeout=max(1, int(timeout_seconds))) as response:
        raw = response.read().decode("utf-8", errors="replace")

    if not raw.strip():
        return _failure("invalid_response", "Feishu webhook returned an empty response")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return _failure("invalid_response", "Feishu webhook returned invalid JSON")
    if not isinstance(result, dict):
        return _failure("invalid_response", "Feishu webhook returned an invalid response")

    if "code" in result:
        code = result["code"]
    elif "StatusCode" in result:
        code = result["StatusCode"]
    else:
        return _failure("invalid_response", "Feishu webhook response has no status code")
    if code is None:
        return _failure("invalid_response", "Feishu webhook response has an invalid status code")
    if str(code) != "0":
        message = str(result.get("msg") or result.get("StatusMessage") or "Feishu webhook rejected the message")
        return _failure(str(code), message)

    message_id = result.get("message_id") or result.get("msg_id")
    return {
        "ok": True,
        "provider": "feishu_webhook",
        "message_id": message_id if isinstance(message_id, str) and message_id.strip() else None,
        "error_code": None,
        "error": None,
    }


def _build_signature(timestamp: str, secret: str) -> str:
    value = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(value, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _failure(error_code: str, error: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "provider": "feishu_webhook",
        "message_id": None,
        "error_code": error_code,
        "error": redact_sensitive(error),
    }
