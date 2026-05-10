#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ai_provider.py - 可插拔 AI 内容提供层

输入: stats.json（可选精简聊天文本路径）
输出: ai_content.json

provider: stub | cursor_cli | dashscope（预留）| volc_ark（预留）
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from ai_providers.common.load_context import load_stats_and_chat
from ai_providers.common.normalize import normalize_ai_content
from ai_providers.common.prompt import build_prompt_bundle
from ai_providers.registry import run_provider


def _dump_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def generate_ai_content(
    provider: str,
    stats_path: str,
    output_path: str,
    *,
    text_path: Optional[str] = None,
    provider_options: Optional[Dict[str, Any]] = None,
    max_chat_chars: Optional[int] = None,
    repo_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate ai_content.json using the selected backend and write to output_path.

    text_path: simplified_chat file for this window (schedule_report passes it).
    provider_options: merged contents of config/ai_providers.json (nested by backend).
    max_chat_chars: cap total chars read from chat files (both OS consistent).
    repo_root: repository root for Cursor --workspace and references/ai_prompt.md.
    """
    root = repo_root or str(Path(__file__).resolve().parent.parent)
    chat_char_cap = max_chat_chars
    if isinstance(chat_char_cap, int) and chat_char_cap <= 0:
        chat_char_cap = None
    stats, chat_text = load_stats_and_chat(
        stats_path,
        text_path,
        max_chat_chars=chat_char_cap,
    )
    provider_key = (provider or "stub").strip().lower()
    prompt_text = ""
    if provider_key != "stub":
        prompt_text = build_prompt_bundle(root, stats, chat_text)

    raw = run_provider(
        provider_key,
        stats=stats,
        stats_path=stats_path,
        output_path=output_path,
        chat_text=chat_text,
        repo_root=root,
        provider_options=provider_options or {},
        prompt_text=prompt_text,
    )
    normalized = normalize_ai_content(raw, stats)
    _dump_json(output_path, normalized)
    return normalized
