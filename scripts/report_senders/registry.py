# -*- coding: utf-8 -*-
"""Sender registry and retry wrapper."""

from __future__ import annotations

import time
from typing import Any, Dict

from report_senders.feishu_cli_card import send_feishu_cli_card
from report_senders.feishu_cli_webhook import send_feishu_cli_webhook
from report_senders.feishu_im_api import send_feishu_im_api
from report_senders.feishu_webhook import send_feishu_webhook

REGISTERED = {"none", "feishu_webhook", "feishu_cli_webhook", "feishu_cli_card", "feishu_im_api"}


def send_report_with_retry(
    sender: str,
    payload: Dict[str, str],
    sender_options: Dict[str, Any],
    *,
    timeout_seconds: int,
    retry_times: int,
    retry_backoff_seconds: int,
) -> Dict[str, Any]:
    key = (sender or "none").strip().lower()
    if key not in REGISTERED:
        raise RuntimeError(f"Unsupported sender: {key}. Supported: {sorted(REGISTERED)}")

    if key == "none":
        return {"ok": True, "provider": "none", "message_id": None, "error_code": None, "error": None, "attempts": 0}

    last: Dict[str, Any] = {"ok": False, "provider": key, "message_id": None, "error_code": "unknown", "error": "unknown", "attempts": 0}
    max_tries = max(0, int(retry_times)) + 1
    backoff = max(0, int(retry_backoff_seconds))

    for i in range(max_tries):
        try:
            if key == "feishu_webhook":
                last = send_feishu_webhook(payload, sender_options, timeout_seconds=timeout_seconds)
            elif key == "feishu_cli_webhook":
                last = send_feishu_cli_webhook(payload, sender_options, timeout_seconds=timeout_seconds)
            elif key == "feishu_cli_card":
                last = send_feishu_cli_card(payload, sender_options, timeout_seconds=timeout_seconds)
            elif key == "feishu_im_api":
                last = send_feishu_im_api(payload, sender_options, timeout_seconds=timeout_seconds)
            else:
                raise RuntimeError(f"Sender not wired: {key}")
        except Exception as e:  # noqa: BLE001
            last = {
                "ok": False,
                "provider": key,
                "message_id": None,
                "error_code": type(e).__name__,
                "error": str(e),
            }
        last["attempts"] = i + 1
        if last.get("ok"):
            return last
        if i + 1 < max_tries and backoff > 0:
            time.sleep(backoff)

    return last
