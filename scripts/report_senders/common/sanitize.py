# -*- coding: utf-8 -*-
"""Redact secrets from sender logs."""

from __future__ import annotations

import re


_PATTERNS = [
    re.compile(r"(https?://[^\s]*?/hook/[^\s]+)", re.IGNORECASE),
    re.compile(r"((?:token|secret|webhook)[=\s:]+)([A-Za-z0-9._\-+/=]+)", re.IGNORECASE),
    re.compile(r"([?&](?:token|secret|signature|access_token)=)([^&\s]+)", re.IGNORECASE),
]


def redact_sensitive(text: str) -> str:
    if not text:
        return ""
    out = text
    out = _PATTERNS[0].sub("<REDACTED_WEBHOOK_URL>", out)
    out = _PATTERNS[1].sub(lambda m: f"{m.group(1)}<REDACTED>", out)
    out = _PATTERNS[2].sub(lambda m: f"{m.group(1)}<REDACTED>", out)
    return out
