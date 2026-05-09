# -*- coding: utf-8 -*-
"""Cursor CLI provider: non-interactive `agent -p` subprocess."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, Dict, Optional

from ai_providers.common.json_extract import extract_first_json_object


# Conservative limit for Windows CreateProcess command-line (~8191); leave margin for flags.
_MAX_CMDLINE_CHARS = 7000


def _digest_score(d: Dict[str, Any]) -> int:
    """Heuristic score for ai_content-like dicts; higher means closer match."""
    score = 0
    required_list_keys = ("topics", "resources", "important_messages", "dialogues", "qas", "topic_heat")
    for k in required_list_keys:
        if isinstance(d.get(k), list):
            score += 2
    if isinstance(d.get("talker_profiles"), dict):
        score += 2
    if "topics" in d:
        # Prefer non-empty topics payload over wrapper dicts with empty placeholders.
        topics = d.get("topics")
        if isinstance(topics, list) and topics:
            score += 2
    return score


def _deep_find_digest_dict(obj: Any) -> Optional[Dict[str, Any]]:
    """Find the best-matching ai_content dict recursively."""
    best: Optional[Dict[str, Any]] = None
    best_score = -1

    def visit(node: Any) -> None:
        nonlocal best, best_score
        if isinstance(node, dict):
            s = _digest_score(node)
            if s > best_score:
                best = node
                best_score = s
            for v in node.values():
                visit(v)
            return
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if isinstance(node, str):
            try:
                d = extract_first_json_object(node)
            except ValueError:
                return
            visit(d)

    visit(obj)
    # 6 keys * 2 + talker_profiles(2) = 14; require at least half shape confidence.
    return best if best is not None and best_score >= 7 else None


def parse_cli_stdout_to_ai_dict(stdout: str) -> Dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise ValueError("cursor_cli returned empty stdout")

    try:
        d = extract_first_json_object(text)
        if _digest_score(d) >= 7:
            return d
    except ValueError:
        pass

    try:
        payload = json.loads(text)
        found = _deep_find_digest_dict(payload)
        if found is not None:
            return found
    except json.JSONDecodeError:
        pass

    raise ValueError(
        "could not parse ai_content JSON from Cursor CLI output; "
        "try --output-format json and inspect stderr in logs."
    )


def _estimate_cmdline_chars(argv: list) -> int:
    return sum(len(str(a)) for a in argv) + len(argv)


def run_cursor_cli(
    prompt: str,
    repo_root: str,
    provider_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    opts = (provider_options or {}).get("cursor_cli") or {}
    cmd_prefix = opts.get("command")
    if not cmd_prefix:
        cmd_prefix = ["agent"]

    if not isinstance(cmd_prefix, list) or not cmd_prefix:
        raise ValueError("cursor_cli.command must be a non-empty JSON array of strings")

    base = (
        list(cmd_prefix)
        + [
            "-p",
            "--print",
            "--output-format",
            "json",
            "--mode=ask",
            "--workspace",
            repo_root,
        ]
    )

    original_len = len(prompt)
    p = prompt
    while _estimate_cmdline_chars(base + [p]) > _MAX_CMDLINE_CHARS:
        if len(p) <= 256:
            raise RuntimeError(
                "cursor_cli: prompt cannot fit Windows argv limit even after truncation; "
                "lower max_chat_chars or shorten ai_prompt.md usage."
            )
        p = p[: max(256, len(p) - max(400, len(p) // 8))]
    if len(p) < original_len:
        print(
            f"[cursor_cli] prompt truncated for argv safety: {original_len} -> {len(p)} chars "
            f"(cmdline cap ~{_MAX_CMDLINE_CHARS}); reduce max_chat_chars if output degrades.",
            file=sys.stderr,
        )

    kwargs: Dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "cwd": repo_root,
    }
    if sys.platform == "win32":
        # subprocess.CREATE_NO_WINDOW on Python 3.7+
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.run(base + [p], **kwargs)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError(
            f"cursor_cli exited with code {proc.returncode}: {proc.stdout[:500]!r}"
        )

    return parse_cli_stdout_to_ai_dict(proc.stdout or "")


def generate_via_cursor_cli(
    prompt: str,
    repo_root: str,
    provider_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return run_cursor_cli(prompt, repo_root, provider_options)
