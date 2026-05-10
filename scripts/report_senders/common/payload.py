# -*- coding: utf-8 -*-
"""Build Feishu outbound payload text from report markdown."""

from __future__ import annotations

import math
import os
from typing import Dict, List


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


def build_text_payload_chunks(
    report_path: str,
    *,
    chatroom: str,
    window_id: str,
    max_chars: int,
) -> List[Dict[str, str]]:
    """
    Build one or more message payloads. When report body is larger than max_chars,
    split it into multiple parts instead of truncating.
    """
    if not report_path or not os.path.isfile(report_path):
        raise FileNotFoundError(f"report not found: {report_path}")

    with open(report_path, "r", encoding="utf-8", errors="replace") as f:
        body = f.read().strip()

    title = f"群聊总结 {chatroom} [{window_id}]"
    max_chars = max(200, int(max_chars))

    # Base prefix/suffix kept in each message for readability.
    # Reserve extra room for "(i/n)" marker to keep each chunk <= max_chars.
    prefix_base = f"{title} (00/00)\n\n"
    suffix = f"\n\n本地文件: {report_path}"
    room = max_chars - len(prefix_base) - len(suffix)
    if room <= 20:
        room = max_chars

    if len(body) <= room:
        return [{"title": title, "text": f"{prefix_base}{body}{suffix}"}]

    parts = [body[i : i + room] for i in range(0, len(body), room)]
    total = len(parts)
    payloads: List[Dict[str, str]] = []
    digits = int(math.log10(total)) + 1 if total > 0 else 1
    for idx, part in enumerate(parts, start=1):
        header = f"{title} ({idx:0{digits}d}/{total:0{digits}d})\n\n"
        text = f"{header}{part}{suffix}"
        payloads.append({"title": title, "text": text})
    return payloads
