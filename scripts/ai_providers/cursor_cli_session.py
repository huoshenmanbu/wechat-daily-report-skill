# -*- coding: utf-8 -*-
"""Persist Cursor Agent CLI chat/session id for optional reuse across runs."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional

DEFAULT_RELATIVE_STATE = "runtime/cursor_cli_session.json"
# Opaque ids from Cursor / manual seed files: ASCII, no whitespace or shell metacharacters.
_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,256}$")


def resolve_state_path(repo_root: str, session_state_path: Optional[str]) -> Path:
    rel = (session_state_path or DEFAULT_RELATIVE_STATE).strip() or DEFAULT_RELATIVE_STATE
    p = Path(rel)
    if p.is_absolute():
        return p
    root = Path(repo_root).resolve()
    cand = (root / p).resolve()
    try:
        cand.relative_to(root)
    except ValueError as e:
        raise ValueError(
            f"session_state_path {rel!r} resolves outside repo_root; refusing to use this path."
        ) from e
    return cand


def sanitize_chat_id(raw: Optional[str]) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or not _CHAT_ID_RE.fullmatch(s):
        return None
    return s


def load_session_chat_id(repo_root: str, opts: Dict[str, Any]) -> Optional[str]:
    try:
        path = resolve_state_path(repo_root, opts.get("session_state_path"))
    except ValueError:
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    cid = sanitize_chat_id(data.get("chat_id"))
    if cid:
        return cid
    return None


def save_session_chat_id(repo_root: str, opts: Dict[str, Any], chat_id: str) -> None:
    cid = sanitize_chat_id(chat_id)
    if not cid:
        return
    path = resolve_state_path(repo_root, opts.get("session_state_path"))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": 1, "chat_id": cid}, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_path = tempfile.mkstemp(
        prefix=".cursor_cli_session_",
        suffix=".json",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as f:
            f.write(payload)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def clear_session(repo_root: str, opts: Dict[str, Any]) -> None:
    try:
        path = resolve_state_path(repo_root, opts.get("session_state_path"))
    except ValueError:
        return
    try:
        path.unlink()
    except OSError:
        pass


def normalize_on_resume_failure(opts: Dict[str, Any]) -> str:
    raw = str(opts.get("on_resume_failure") or "clear_and_retry_fresh").strip().lower()
    normalized = raw.replace("-", "_")
    allowed = ("clear_and_retry_fresh", "raise", "clear_only")
    if normalized in allowed:
        return normalized
    print(
        f"[cursor_cli] invalid on_resume_failure={raw!r}, using clear_and_retry_fresh",
        file=sys.stderr,
    )
    return "clear_and_retry_fresh"
