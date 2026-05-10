# -*- coding: utf-8 -*-
"""Send report by Feishu CLI webhook mode."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict

from report_senders.common.sanitize import redact_sensitive


def send_feishu_cli_webhook(
    payload: Dict[str, str],
    sender_options: Dict[str, Any],
    *,
    timeout_seconds: int,
) -> Dict[str, Any]:
    cfg = (sender_options or {}).get("feishu_cli_webhook") or {}
    command = cfg.get("command") or ["feishu-cli"]
    if not isinstance(command, list) or not command:
        raise ValueError("feishu_cli_webhook.command must be a non-empty string array")

    webhook_env = cfg.get("webhook_url_env") or "FEISHU_WEBHOOK_URL"
    secret_env = cfg.get("webhook_secret_env") or "FEISHU_WEBHOOK_SECRET"
    webhook = os.getenv(webhook_env, "").strip()
    if not webhook:
        raise RuntimeError(f"Missing env `{webhook_env}` for Feishu webhook")

    message_format = str(cfg.get("message_format") or "text").strip().lower()
    if message_format not in ("text", "markdown"):
        raise ValueError("feishu_cli_webhook.message_format must be text|markdown")

    args_template = cfg.get("args_template")
    if args_template is None:
        # Default generic command shape (customizable in sender config file):
        # feishu-cli webhook send --url <url> --msg-type text --content <content> [--secret <secret>]
        args_template = ["webhook", "send", "--url", "__WEBHOOK__", "--msg-type", "__MSG_TYPE__", "--content", "__CONTENT__"]
        if os.getenv(secret_env, "").strip():
            args_template += ["--secret", "__SECRET__"]
    if not isinstance(args_template, list) or not all(isinstance(x, str) for x in args_template):
        raise ValueError("feishu_cli_webhook.args_template must be a string array")

    msg_type = "text" if message_format == "text" else "markdown"
    content = payload["text"]
    secret = os.getenv(secret_env, "").strip()

    resolved = _resolve_args_template(
        args_template,
        webhook=webhook,
        secret=secret,
        content=content,
        msg_type=msg_type,
    )

    cmd = list(command) + resolved
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1, int(timeout_seconds)),
    )
    out = redact_sensitive(proc.stdout or "")
    err = redact_sensitive(proc.stderr or "")
    if out:
        print(f"[sender] feishu stdout: {out}")
    if err:
        print(f"[sender] feishu stderr: {err}")

    if proc.returncode != 0:
        return {
            "ok": False,
            "provider": "feishu_cli_webhook",
            "message_id": None,
            "error_code": f"exit_{proc.returncode}",
            "error": "feishu cli command failed",
        }

    message_id = _extract_message_id(out)
    return {
        "ok": True,
        "provider": "feishu_cli_webhook",
        "message_id": message_id,
        "error_code": None,
        "error": None,
    }


def _extract_message_id(stdout: str) -> str | None:
    if not stdout:
        return None
    try:
        data = json.loads(stdout)
        if isinstance(data, dict):
            for key in ("message_id", "msg_id"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    except json.JSONDecodeError:
        pass
    return None


def _resolve_args_template(
    args_template: list[str],
    *,
    webhook: str,
    secret: str,
    content: str,
    msg_type: str,
) -> list[str]:
    """
    Resolve special placeholders and avoid dangling secret flags when secret is missing.
    Supports both:
    - ["--secret", "__SECRET__"]
    - ["--secret=__SECRET__"]
    """
    resolved: list[str] = []
    i = 0
    while i < len(args_template):
        token = args_template[i]

        if token == "__WEBHOOK__":
            resolved.append(webhook)
            i += 1
            continue
        if token == "__CONTENT__":
            resolved.append(content)
            i += 1
            continue
        if token == "__MSG_TYPE__":
            resolved.append(msg_type)
            i += 1
            continue
        if token == "__SECRET__":
            if secret:
                resolved.append(secret)
            i += 1
            continue

        # Pattern: ["--secret", "__SECRET__"] -> remove both when secret missing.
        if i + 1 < len(args_template) and args_template[i + 1] == "__SECRET__":
            if secret:
                resolved.extend([token, secret])
            i += 2
            continue

        # Pattern: "--secret=__SECRET__"
        if "__SECRET__" in token:
            if secret:
                resolved.append(token.replace("__SECRET__", secret))
            i += 1
            continue

        resolved.append(token)
        i += 1

    return resolved
