# -*- coding: utf-8 -*-
"""Build Feishu outbound payload text from report markdown."""

from __future__ import annotations

import os
from typing import Dict


def build_text_payload(
    report_path: str,
    *,
    chatroom: str,
    window_id: str,
    max_chars: int,
) -> Dict[str, str]:
    if not report_path or not os.path.isfile(report_path):
        raise FileNotFoundError(f"report not found: {report_path}")

    with open(report_path, "r", encoding="utf-8", errors="replace") as f:
        body = f.read().strip()

    title = f"群聊总结 {chatroom} [{window_id}]"
    max_chars = max(200, int(max_chars))
    if len(body) > max_chars:
        body = body[:max_chars] + "\n\n…[truncated]"

    text = f"{title}\n\n{body}\n\n本地文件: {report_path}"
    return {"title": title, "text": text}
