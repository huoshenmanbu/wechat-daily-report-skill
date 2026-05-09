# -*- coding: utf-8 -*-
"""Load stats.json and simplified chat text for prompts."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple


def _read_json(path: str) -> Dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_text_files(paths: List[str], max_total_chars: Optional[int]) -> str:
    parts: List[str] = []
    total = 0
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            chunk = f.read()
        if max_total_chars is not None:
            remain = max_total_chars - total
            if remain <= 0:
                break
            if len(chunk) > remain:
                chunk = chunk[:remain] + "\n…[truncated]"
        parts.append(chunk)
        total += len(chunk)
        if max_total_chars is not None and total >= max_total_chars:
            break
    return "\n\n".join(parts)


def load_stats_and_chat(
    stats_path: str,
    text_path: Optional[str],
    *,
    max_chat_chars: Optional[int],
) -> Tuple[Dict[str, Any], str]:
    stats = _read_json(stats_path)
    stats_dir = os.path.dirname(os.path.abspath(stats_path)) if stats_path else ""
    paths_ordered: List[str] = []
    if text_path:
        paths_ordered.append(text_path)
    meta = stats.get("meta") or {}
    for p in meta.get("raw_text_paths") or []:
        if not p:
            continue
        resolved = p
        if not os.path.isabs(resolved) and stats_dir:
            resolved = os.path.normpath(os.path.join(stats_dir, resolved))
        if resolved not in paths_ordered:
            paths_ordered.append(resolved)

    chat_text = _read_text_files(paths_ordered, max_chat_chars)
    return stats, chat_text
