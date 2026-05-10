# -*- coding: utf-8 -*-
"""Build the user prompt for LLM / Cursor CLI from ai_prompt.md + stats + chat."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


# Conservative cap so stats JSON does not dominate the context window.
_MAX_STATS_JSON_CHARS = 120_000


def build_prompt_bundle(repo_root: str, stats: Dict[str, Any], chat_text: str) -> str:
    root = Path(repo_root)
    ref_path = root / "references" / "ai_prompt.md"
    if not ref_path.is_file():
        raise FileNotFoundError(f"references/ai_prompt.md not found under {repo_root}")

    template = ref_path.read_text(encoding="utf-8")

    stats_blob = json.dumps(stats, ensure_ascii=False)
    if len(stats_blob) > _MAX_STATS_JSON_CHARS:
        stats_blob = stats_blob[:_MAX_STATS_JSON_CHARS] + "\n…[stats truncated for prompt size]"

    parts = [
        "You are generating structured JSON for a WeChat group digest.",
        "Follow the schema and style rules in the template below.",
        "Answer with a single JSON object only — no markdown fences, no commentary outside JSON.",
        "",
        "--- references/ai_prompt.md ---",
        template,
        "--- end ai_prompt.md ---",
        "",
        "--- stats.json ---",
        stats_blob,
        "--- end stats.json ---",
        "",
        "--- simplified chat text ---",
        chat_text if chat_text.strip() else "(empty)",
        "--- end simplified chat text ---",
        "",
        "Output requirements:",
        "- Valid UTF-8 JSON object matching the schema described in ai_prompt.md.",
        "- Use ai_prompt.md as the single source of truth for required keys and field names.",
        "- Prefer complete output across all major sections defined by ai_prompt.md.",
        "- Do not write files; respond with JSON text only.",
    ]
    return "\n".join(parts)
