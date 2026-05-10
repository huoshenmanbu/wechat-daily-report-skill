# -*- coding: utf-8 -*-
"""Cursor CLI provider: non-interactive `agent -p` subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, Optional, Tuple

from ai_providers.common.json_extract import extract_first_json_object


# Conservative limit for Windows CreateProcess command-line (~8191); leave margin for flags.
_MAX_CMDLINE_CHARS = 7000
# PowerShell -> CreateProcess supports the normal Windows command-line ceiling.
_WINDOWS_AGENT_CMDLINE_CHARS = 28000


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


def _unwrap_cursor_envelope(text: str) -> str:
    """Extract inner model output from the Cursor CLI result envelope.

    Cursor CLI wraps model output as:
        {"type":"result","subtype":"success","result":"<model_output_string>", ...}

    The inner ``result`` value is a plain string (the model's reply). It may itself
    contain a JSON object that we want to parse as ai_content.
    """
    stripped = text.strip()
    try:
        outer = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    if not isinstance(outer, dict):
        return text
    if outer.get("type") != "result":
        return text
    inner = outer.get("result", "")
    if isinstance(inner, str) and inner.strip():
        return inner
    return text


def parse_cli_stdout_to_ai_dict(stdout: str) -> Dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise ValueError("cursor_cli returned empty stdout")

    # Unwrap Cursor CLI result envelope: {"type":"result","result":"<model reply string>"}
    unwrapped = _unwrap_cursor_envelope(text)
    if unwrapped.strip():
        text = unwrapped.strip()

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


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_agent_via_powershell(
    base: list,
    prompt: str,
    cwd: str,
    timeout: Optional[float],
) -> Tuple[int, str, str]:
    """Run Cursor Agent through PowerShell on Windows.

    Direct Python -> agent subprocess calls currently return code 0 with empty
    stdout on Windows. Wrapping the same command in PowerShell preserves the CLI's
    --print output while still letting Python capture it.
    """
    prompt_fd, prompt_path = tempfile.mkstemp(suffix=".txt", prefix="cursor_prompt_")
    os.close(prompt_fd)
    try:
        with open(prompt_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(prompt)

        cmd = str(base[0])
        arg_list = ", ".join(_ps_quote(str(a)) for a in base[1:])
        script = (
            "$ErrorActionPreference = 'Stop'; "
            f"$prompt = Get-Content -Raw -Encoding UTF8 -LiteralPath {_ps_quote(prompt_path)}; "
            f"$cmd = {_ps_quote(cmd)}; "
            f"$argsList = @({arg_list}); "
            "& $cmd @argsList $prompt; "
            "exit $LASTEXITCODE"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    finally:
        try:
            os.unlink(prompt_path)
        except OSError:
            pass


def _run_agent_direct(
    base: list,
    prompt: str,
    cwd: str,
    timeout: Optional[float],
    creation_flags: int = 0,
) -> Tuple[int, str, str]:
    run_kwargs: Dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "cwd": cwd,
        "timeout": timeout,
    }
    if creation_flags:
        run_kwargs["creationflags"] = creation_flags
    proc = subprocess.run(base + [prompt], **run_kwargs)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _run_agent_once(
    base: list,
    prompt: str,
    cwd: str,
    timeout: Optional[float],
    creation_flags: int = 0,
) -> Tuple[int, str, str]:
    if sys.platform == "win32":
        return _run_agent_via_powershell(base, prompt, cwd, timeout)
    return _run_agent_direct(base, prompt, cwd, timeout, creation_flags)


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
    # 非交互 / 任务计划无真人点「信任」；与手动加 --trust 等价。设 cursor_cli.trust_workspace=false 可关闭。
    if opts.get("trust_workspace", True):
        base.append("--trust")
    # 无界面运行时若 agent 等待工具授权，可设 cursor_cli.force: true（等价 --force，请自行评估风险）
    if opts.get("force", False):
        base.append("--force")

    original_len = len(prompt)
    p = prompt
    cmdline_limit = int(opts.get("cmdline_char_limit") or 0)
    if cmdline_limit <= 0:
        cmdline_limit = _WINDOWS_AGENT_CMDLINE_CHARS if sys.platform == "win32" else _MAX_CMDLINE_CHARS
    while _estimate_cmdline_chars(base + [p]) > cmdline_limit:
        if len(p) <= 256:
            raise RuntimeError(
                "cursor_cli: prompt cannot fit Windows argv limit even after truncation; "
                "lower max_chat_chars or shorten ai_prompt.md usage."
            )
        p = p[: max(256, len(p) - max(400, len(p) // 8))]
    if len(p) < original_len:
        print(
            f"[cursor_cli] prompt truncated for argv safety: {original_len} -> {len(p)} chars "
            f"(cmdline cap ~{cmdline_limit}); reduce max_chat_chars if output degrades.",
            file=sys.stderr,
        )

    # Windows: 默认不使用 CREATE_NO_WINDOW（某些 Cursor CLI 版本在隐藏窗口时不写临时文件）
    # 需要隐藏控制台窗口时再设 cursor_cli.hide_window: true。
    creation_flags = 0
    if sys.platform == "win32" and opts.get("hide_window", False):
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creation_flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

    timeout_seconds = opts.get("timeout_seconds")
    timeout: Optional[float] = None
    if timeout_seconds is not None:
        timeout = max(float(timeout_seconds), 1.0)

    try:
        returncode, out, err = _run_agent_once(base, p, repo_root, timeout, creation_flags)
    except subprocess.TimeoutExpired as e:
        partial_out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        partial_err = (e.stderr or "") if isinstance(e.stderr, str) else ""
        detail = (partial_err[-800:] or partial_out[:300] or "no partial output").strip()
        raise TimeoutError(f"cursor_cli exceeded {timeout}s: {detail!r}") from None
    if err.strip():
        print(err, file=sys.stderr)
    if returncode != 0:
        err_tail = err[-1200:].strip()
        out_preview = out[:500].strip()
        detail = err_tail or out_preview or "(no stdout/stderr captured)"
        raise RuntimeError(f"cursor_cli exited with code {returncode}: {detail!r}")

    if not out.strip():
        # 第一次为空时，用更短的 prompt 重试一次
        if opts.get("retry_on_empty_stdout", False):
            retry_len = int(opts.get("empty_stdout_retry_prompt_chars", 1800) or 1800)
            retry_len = max(600, min(retry_len, 4000))
            retry_prompt = p[:retry_len]
            if len(retry_prompt) < len(p):
                print(
                    f"[cursor_cli] empty stdout, retry with shorter prompt: "
                    f"{len(p)} -> {len(retry_prompt)} chars",
                    file=sys.stderr,
                )
            try:
                rc2, out2, err2 = _run_agent_once(base, retry_prompt, repo_root, timeout, creation_flags)
            except subprocess.TimeoutExpired as e:
                retry_err = (e.stderr or "") if isinstance(e.stderr, str) else ""
                print(
                    f"[cursor_cli] empty-stdout retry timed out after {timeout}s, stderr={retry_err[-500:]!r}",
                    file=sys.stderr,
                )
                rc2, out2, err2 = 124, "", retry_err
            if err2.strip():
                print(err2, file=sys.stderr)
            if rc2 == 0 and out2.strip():
                return parse_cli_stdout_to_ai_dict(out2)
            retry_err = err2[-800:].strip()
            retry_out = out2[:200].strip()
            if retry_err or retry_out:
                print(
                    f"[cursor_cli] empty-stdout retry failed, "
                    f"stderr={retry_err!r}, stdout={retry_out!r}",
                    file=sys.stderr,
                )

        err_tail = err[-2000:].strip()
        hint = (
            "stdout was empty (agent returned 0 but wrote nothing to the output file). "
            "Check that `agent` is in PATH and supports --output-format json "
            "(run: agent about). If the command works in an interactive terminal but "
            "not here, try setting cursor_cli.force=true in ai_providers.json."
        )
        raise ValueError(f"cursor_cli returned empty stdout. stderr (tail): {err_tail!r}. {hint}")

    return parse_cli_stdout_to_ai_dict(out)


def generate_via_cursor_cli(
    prompt: str,
    repo_root: str,
    provider_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return run_cursor_cli(prompt, repo_root, provider_options)
