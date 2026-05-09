# -*- coding: utf-8 -*-
"""Extract a JSON object from model output (markdown fences, surrounding text)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict


_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_first_json_object(text: str) -> Dict[str, Any]:
    """
    Parse the first top-level JSON object found in text.
    Strips optional ```json fences; scans with JSONDecoder.raw_decode on `{` positions.
    """
    if not text or not text.strip():
        raise ValueError("empty model output")

    stripped = text.strip()
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    for m in _FENCE_RE.finditer(text):
        inner = m.group(1).strip()
        try:
            obj = json.loads(inner)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue

    dec = json.JSONDecoder()
    for i, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(stripped, i)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue

    raise ValueError("no JSON object found in model output")


# Backwards-compatible alias
extract_ai_json = extract_first_json_object
