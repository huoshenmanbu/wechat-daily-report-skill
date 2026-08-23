#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
schedule_report.py - 周期总结调度器（run-once）

每次进程启动可处理至多 max_catchup_windows 个积压窗口，适配系统任务计划定时触发。
窗口语义与消息筛选一致：[start, end)（见 wechat_decrypted_reader.message_in_range）。
"""

import argparse
import copy
import datetime as dt
import json
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from scripts.ai_provider import generate_ai_content
except ModuleNotFoundError:
    from ai_provider import generate_ai_content
try:
    from scripts.report_senders.common.payload import build_text_payload_chunks
    from scripts.report_senders.registry import send_report_with_retry
except ModuleNotFoundError:
    from report_senders.common.payload import build_text_payload_chunks
    from report_senders.registry import send_report_with_retry

# 仓库根目录（用于子进程 cwd，避免任务计划工作目录非仓库根时失败）
REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args():
    parser = argparse.ArgumentParser(description="Run scheduled summary windows by config.")
    parser.add_argument("--config", default="config/report_schedule.yaml", help="Schedule config path")
    parser.add_argument("--dry-run", action="store_true", help="Only print the next computed window")
    parser.add_argument("--force", action="store_true", help="Force regenerate even if report already exists")
    parser.add_argument("--daemon", action="store_true", help="Keep process alive and run on aligned schedule")
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=30,
        help="Daemon sleep polling interval in seconds (min: 5, default: 30)",
    )
    parser.add_argument(
        "--daemon-backoff-seconds",
        type=int,
        default=60,
        help="Daemon retry backoff after failure in seconds (min: 5, default: 60)",
    )
    parser.add_argument("--start", help="Manual window start, format: YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--end", help="Manual window end, format: YYYY-MM-DD HH:MM:SS")
    return parser.parse_args()


def _parse_bool(val: Any, default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def _parse_int_field(
    data: Dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    """
    从 YAML/JSON 配置解析整数；缺失键用 default，键存在但为空/非数字时抛出清晰错误。
    """
    if key not in data:
        val = default
    else:
        raw = data[key]
        if isinstance(raw, bool):
            raise ValueError(f"Config `{key}` must be an integer, not a boolean (got {raw!r}).")
        if isinstance(raw, int):
            val = raw
        elif isinstance(raw, float):
            if raw.is_integer():
                val = int(raw)
            else:
                raise ValueError(f"Config `{key}` must be a whole number, got {raw!r}.")
        else:
            s = str(raw).strip()
            if not s:
                raise ValueError(
                    f"Config `{key}` is empty. Either set an integer or remove the line "
                    f"(default for `{key}` is {default})."
                )
            try:
                val = int(s, 10)
            except ValueError as e:
                raise ValueError(f"Config `{key}` must be an integer, got {raw!r}.") from e
    if minimum is not None and val < minimum:
        raise ValueError(f"Config `{key}` must be >= {minimum}, got {val}.")
    if maximum is not None and val > maximum:
        raise ValueError(f"Config `{key}` must be <= {maximum}, got {val}.")
    return val


def _load_config(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = _load_flat_yaml(path)

    cfg = {
        "interval_minutes": _parse_int_field(data, "interval_minutes", 60, minimum=1),
        "chatroom": str(data.get("chatroom", "")).strip(),
        "decrypted_dir": str(data.get("decrypted_dir", "")).strip(),
        "output_dir": str(data.get("output_dir", "outputs/reports")).strip(),
        "work_dir": str(data.get("work_dir", "runtime/hourly")).strip(),
        "output_format": str(data.get("output_format", "md")).strip().lower(),
        "align_mode": str(data.get("align_mode", "floor")).strip().lower(),
        "min_messages": _parse_int_field(data, "min_messages", 5, minimum=0),
        "low_activity_skip_threshold": _parse_int_field(
            data, "low_activity_skip_threshold", 0, minimum=0, maximum=1_000_000
        ),
        "low_activity_skip_message": str(data.get("low_activity_skip_message", "") or "").strip(),
        "focus_members": [
            name.strip() for name in str(data.get("focus_members", "") or "").split(",") if name.strip()
        ],
        "retry_times": _parse_int_field(data, "retry_times", 1, minimum=0),
        "skip_refresh": _parse_bool(data.get("skip_refresh", False), False),
        "provider": str(data.get("provider", "stub")).strip().lower(),
        "provider_timeout_seconds": _parse_int_field(data, "provider_timeout_seconds", 90, minimum=1),
        "analyze_timeout_seconds": _parse_int_field(data, "analyze_timeout_seconds", 600, minimum=1),
        "report_timeout_seconds": _parse_int_field(data, "report_timeout_seconds", 300, minimum=1),
        "max_catchup_windows": _parse_int_field(data, "max_catchup_windows", 24, minimum=1),
        "lock_file": str(data.get("lock_file", "runtime/schedule_report.lock")).strip(),
        "lock_ttl_seconds": _parse_int_field(data, "lock_ttl_seconds", 600, minimum=1),
        "state_file": str(data.get("state_file", "runtime/state.json")).strip(),
        "python_executable": str(data.get("python_executable", sys.executable)).strip() or sys.executable,
        "provider_config_file": str(data.get("provider_config_file", "")).strip(),
        "max_chat_chars": _parse_int_field(data, "max_chat_chars", 0, minimum=0),
        "sender": str(data.get("sender", "none")).strip().lower(),
        "sender_timeout_seconds": _parse_int_field(data, "sender_timeout_seconds", 30, minimum=1),
        "sender_retry_times": _parse_int_field(data, "sender_retry_times", 1, minimum=0),
        "sender_retry_backoff_seconds": _parse_int_field(data, "sender_retry_backoff_seconds", 2, minimum=0),
        "sender_strict": _parse_bool(data.get("sender_strict", False), False),
        "sender_config_file": str(data.get("sender_config_file", "")).strip(),
        "feishu_message_max_chars": _parse_int_field(data, "feishu_message_max_chars", 3000, minimum=200),
    }
    if not cfg["chatroom"]:
        raise ValueError("Config `chatroom` is required.")
    if cfg["output_format"] not in ("md", "txt", "html"):
        raise ValueError("Config `output_format` must be one of: md, txt, html.")
    if cfg["align_mode"] not in ("floor", "rolling"):
        raise ValueError("Config `align_mode` must be one of: floor, rolling.")
    _providers_allowed = {"stub", "cursor_cli", "dashscope", "deepseek", "volc_ark"}
    if cfg["provider"] not in _providers_allowed:
        raise ValueError(
            f"Config `provider` must be one of {sorted(_providers_allowed)}, got {cfg['provider']!r}."
        )
    _senders_allowed = {"none", "feishu_cli_webhook", "feishu_cli_card", "feishu_im_api"}
    if cfg["sender"] not in _senders_allowed:
        raise ValueError(
            f"Config `sender` must be one of {sorted(_senders_allowed)}, got {cfg['sender']!r}."
        )

    pcf = cfg["provider_config_file"]
    if pcf:
        pth = Path(pcf)
        if not pth.is_absolute():
            pth = REPO_ROOT / pth
        if not pth.is_file():
            raise FileNotFoundError(f"provider_config_file not found: {pth}")
        with open(pth, "r", encoding="utf-8") as f:
            cfg["provider_options"] = json.load(f)
    else:
        cfg["provider_options"] = {}

    scf = cfg["sender_config_file"]
    if scf:
        pth = Path(scf)
        if not pth.is_absolute():
            pth = REPO_ROOT / pth
        if not pth.is_file():
            raise FileNotFoundError(f"sender_config_file not found: {pth}")
        with open(pth, "r", encoding="utf-8") as f:
            cfg["sender_options"] = json.load(f)
    else:
        cfg["sender_options"] = {}

    lt = cfg["low_activity_skip_threshold"]
    if lt > 0 and lt >= cfg["min_messages"]:
        print(
            "[scheduler] warning: `low_activity_skip_threshold` ("
            f"{lt}) >= `min_messages` ({cfg['min_messages']}); "
            "the minimal-AI (`min_messages`) branch will never run for windows with "
            "messages: every positive count is either fully skipped (< threshold) or "
            "not below `min_messages`.",
            file=sys.stderr,
        )

    return cfg


def _load_flat_yaml(path: str) -> Dict[str, Any]:
    """仅支持扁平 key: value（一行一项）；嵌套 YAML 请改用 .json 配置。"""
    result: Dict[str, Any] = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            k = key.strip()
            v = _strip_inline_comment(value).strip().strip("'").strip('"')
            if k:
                result[k] = v
    return result


def _strip_inline_comment(value: str) -> str:
    """Strip trailing inline comments (outside quotes), preserving Windows paths."""
    s = value.rstrip()
    in_single = False
    in_double = False
    for i, ch in enumerate(s):
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "#" and not in_single and not in_double:
            return s[:i]
    return s


def _load_state(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _parse_iso_time(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def _fmt_time(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def _parse_cli_window_time(value: str, name: str) -> dt.datetime:
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as e:
        raise ValueError(f"`{name}` must be in format YYYY-MM-DD HH:MM:SS, got {value!r}.") from e


def _floor_to_interval(now: dt.datetime, minutes: int) -> dt.datetime:
    total_minutes = now.hour * 60 + now.minute
    floored = (total_minutes // minutes) * minutes
    hour = floored // 60
    minute = floored % 60
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _compute_next_window(cfg: Dict[str, Any], state: Dict[str, Any]) -> Optional[Tuple[dt.datetime, dt.datetime]]:
    """
    计算下一个待处理窗口 [start, end)，语义与 DB 筛选一致（end 不包含）。
    - 有 last_end：下一窗为 [last_end, last_end + interval)，仅当 end <= now 时可处理（窗口已结束）。
    - 无 last_end：bootstrap，floor 模式使用 [boundary - interval, boundary)；rolling 首次亦对齐到当前整段网格。
    """
    now = dt.datetime.now()
    interval = dt.timedelta(minutes=cfg["interval_minutes"])
    boundary_now = _floor_to_interval(now, cfg["interval_minutes"])
    last_end = _parse_iso_time(state.get("last_end"))
    mode = cfg["align_mode"]

    if mode == "rolling" and last_end is None:
        print("[scheduler] rolling bootstrap: align first window to floor boundary")

    if last_end is not None:
        start = last_end
        end = last_end + interval
        if end > now:
            return None
        return start, end

    # 无状态：从当前网格锚点启动单窗 bootstrap
    end = boundary_now
    start = end - interval
    if end <= start:
        return None
    return start, end


def _read_stats_total_count(path: str) -> int:
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("meta", {}).get("total_count", 0))
    except Exception:
        return 0


def _read_stats_alpha_candidate_count(path: str) -> int:
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        candidates = data.get("alpha_candidates") or []
        return len(candidates) if isinstance(candidates, list) else 0
    except Exception:
        return 0


def _run_cmd(cmd, cwd: Path, timeout: Optional[float] = None):
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(cwd),
    )


def _acquire_lock(lock_file: str, ttl_seconds: int) -> bool:
    """尝试获取锁；失败返回 False（另一实例占用），勿抛异常。"""
    os.makedirs(os.path.dirname(os.path.abspath(lock_file)), exist_ok=True)
    now = int(time.time())
    if os.path.exists(lock_file):
        try:
            mtime = int(os.path.getmtime(lock_file))
            if now - mtime > ttl_seconds:
                os.remove(lock_file)
                print(f"[lock] removed stale lock: {lock_file}")
        except OSError:
            pass
    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "ts": now}, ensure_ascii=False))
        return True
    except FileExistsError:
        print(f"[scheduler] lock busy ({lock_file}), another instance is running; exit 0")
        return False


def _release_lock(lock_file: str) -> None:
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)
        except OSError:
            pass


def _build_paths(cfg: Dict[str, Any], start: dt.datetime, end: dt.datetime) -> Dict[str, str]:
    window_id = f"{start.strftime('%Y%m%d_%H%M')}-{end.strftime('%H%M')}"
    report_ext = cfg["output_format"]
    output_dir = cfg["output_dir"]
    work_dir = cfg["work_dir"]
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    return {
        "window_id": window_id,
        "report": os.path.join(output_dir, f"report_{window_id}.{report_ext}"),
        "stats": os.path.join(work_dir, f"stats_{window_id}.json"),
        "text": os.path.join(work_dir, f"simplified_chat_{window_id}.txt"),
        "ai": os.path.join(work_dir, f"ai_content_{window_id}.json"),
    }


def _killable_process_entry(send_conn, target, args, kwargs) -> None:
    try:
        target(*args, **kwargs)
        send_conn.send(("ok", "", ""))
    except BaseException as exc:
        send_conn.send(("error", type(exc).__name__, str(exc)[:1000]))
    finally:
        send_conn.close()


def _run_in_killable_process(target, args, kwargs, *, timeout: float) -> None:
    """Run a provider in a child process so a wall-clock timeout can terminate it."""
    ctx = multiprocessing.get_context("spawn")
    recv_conn, send_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_killable_process_entry, args=(send_conn, target, args, kwargs))
    process.daemon = True
    process.start()
    send_conn.close()
    process.join(max(0.01, float(timeout)))
    if process.is_alive():
        process.terminate()
        process.join(5)
        recv_conn.close()
        raise TimeoutError(f"provider exceeded {timeout}s")

    status = recv_conn.recv() if recv_conn.poll() else None
    recv_conn.close()
    if status and status[0] == "error":
        raise RuntimeError(f"provider child failed ({status[1]}): {status[2]}")
    if process.exitcode not in (0, None):
        raise RuntimeError(f"provider child exited with code {process.exitcode}")


def _run_provider_with_timeout(
    cfg: Dict[str, Any],
    stats_path: str,
    ai_path: str,
    text_path: str,
) -> None:
    timeout = max(cfg["provider_timeout_seconds"], 1)

    provider_options = copy.deepcopy(cfg.get("provider_options") or {})
    if cfg["provider"] == "cursor_cli":
        cursor_opts = provider_options.setdefault("cursor_cli", {})
        if isinstance(cursor_opts, dict):
            cursor_opts.setdefault("timeout_seconds", timeout)
    elif cfg["provider"] in {"dashscope", "deepseek"}:
        api_opts = provider_options.setdefault(cfg["provider"], {})
        if isinstance(api_opts, dict):
            api_opts.setdefault("timeout_seconds", timeout)

    args = (cfg["provider"], stats_path, ai_path)
    kwargs = {
        "text_path": text_path,
        "provider_options": provider_options,
        "max_chat_chars": cfg.get("max_chat_chars"),
        "repo_root": str(REPO_ROOT),
    }

    if cfg["provider"] == "cursor_cli":
        generate_ai_content(*args, **kwargs)
        return
    _run_in_killable_process(generate_ai_content, args, kwargs, timeout=timeout)


def _format_low_activity_skip_message(
    cfg: Dict[str, Any],
    start: dt.datetime,
    end: dt.datetime,
    total_count: int,
    threshold: int,
) -> str:
    """Build Feishu/plain-text body when a window is skipped for low activity."""
    tpl = (cfg.get("low_activity_skip_message") or "").strip()
    if tpl:
        try:
            return tpl.format(
                chatroom=cfg.get("chatroom", ""),
                start=_fmt_time(start),
                end=_fmt_time(end),
                total_count=total_count,
                threshold=threshold,
            )
        except (KeyError, ValueError) as e:
            print(
                f"[scheduler] warning: low_activity_skip_message format failed ({e!r}); "
                "using built-in template.",
                file=sys.stderr,
            )
    return (
        f"【群聊总结跳过】{cfg.get('chatroom', '')} 窗口 {_fmt_time(start)} ~ {_fmt_time(end)} "
        f"仅 {total_count} 条消息（<{threshold}），本窗不调用 AI、不生成报告；下一时段将继续。"
    )


def _run_sender_text(cfg: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Send arbitrary text via configured sender (same retry/timeout as report sender)."""
    sender = cfg.get("sender", "none")
    if sender == "none":
        return {
            "ok": True,
            "provider": "none",
            "message_id": None,
            "error_code": None,
            "error": None,
            "attempts": 0,
        }
    return send_report_with_retry(
        sender,
        {"text": text},
        cfg.get("sender_options") or {},
        timeout_seconds=cfg.get("sender_timeout_seconds", 30),
        retry_times=cfg.get("sender_retry_times", 1),
        retry_backoff_seconds=cfg.get("sender_retry_backoff_seconds", 2),
    )


def _run_sender_with_timeout(
    cfg: Dict[str, Any],
    *,
    report_path: str,
    window_id: str,
) -> Dict[str, Any]:
    sender = cfg.get("sender", "none")
    if sender == "none":
        return {
            "ok": True,
            "provider": "none",
            "message_id": None,
            "error_code": None,
            "error": None,
            "attempts": 0,
        }

    payloads = build_text_payload_chunks(
        report_path,
        chatroom=cfg.get("chatroom", ""),
        window_id=window_id,
        max_chars=cfg.get("feishu_message_max_chars", 3000),
    )
    last_ok: Dict[str, Any] = {
        "ok": True,
        "provider": sender,
        "message_id": None,
        "error_code": None,
        "error": None,
        "attempts": 0,
    }
    total_attempts = 0
    for idx, payload in enumerate(payloads, start=1):
        out = send_report_with_retry(
            sender,
            payload,
            cfg.get("sender_options") or {},
            timeout_seconds=cfg.get("sender_timeout_seconds", 30),
            retry_times=cfg.get("sender_retry_times", 1),
            retry_backoff_seconds=cfg.get("sender_retry_backoff_seconds", 2),
        )
        total_attempts += int(out.get("attempts", 0))
        if not out.get("ok"):
            out["attempts"] = total_attempts
            out["error"] = f"chunk {idx}/{len(payloads)} failed: {out.get('error')}"
            return out
        last_ok = out
    last_ok["attempts"] = total_attempts
    if len(payloads) > 1:
        print(f"[sender] split report into {len(payloads)} messages")
    return last_ok


def process_one_window(
    cfg: Dict[str, Any],
    start: dt.datetime,
    end: dt.datetime,
    dry_run: bool,
    update_state: bool = True,
    force: bool = False,
) -> int:
    paths = _build_paths(cfg, start, end)

    print(f"[scheduler] window: {_fmt_time(start)} -> {_fmt_time(end)}")
    print(f"[scheduler] report: {paths['report']}")

    if dry_run:
        return 0

    if os.path.exists(paths["report"]) and not force:
        print("[scheduler] report already exists, skip")
        if update_state:
            new_state = {
                "last_end": end.isoformat(),
                "last_success_job_id": paths["window_id"],
                "last_run_at": dt.datetime.now().isoformat(),
                "last_result": "skipped_existing",
            }
            _atomic_write_json(cfg["state_file"], new_state)
        return 0
    if os.path.exists(paths["report"]) and force:
        print("[scheduler] report already exists, but --force enabled; regenerating")

    analyze_cmd = [
        cfg["python_executable"],
        "scripts/analyze_chat.py",
        "--chatroom",
        cfg["chatroom"],
        "--start",
        _fmt_time(start),
        "--end",
        _fmt_time(end),
        "--output-stats",
        paths["stats"],
        "--output-text",
        paths["text"],
    ]
    if cfg["decrypted_dir"]:
        analyze_cmd.extend(["--decrypted-dir", cfg["decrypted_dir"]])
    if cfg["skip_refresh"]:
        analyze_cmd.append("--skip-refresh")
    if cfg.get("focus_members"):
        analyze_cmd.extend(["--focus-members", ",".join(cfg["focus_members"])])

    retries = max(cfg["retry_times"], 0)
    last_err = None
    analyze_timeout = max(cfg["analyze_timeout_seconds"], 1)
    for attempt in range(retries + 1):
        ret = _run_cmd(analyze_cmd, cwd=REPO_ROOT, timeout=analyze_timeout)
        if ret.returncode == 0:
            last_err = None
            break
        last_err = ret
        print(f"[analyze] failed attempt={attempt + 1}/{retries + 1}")
        print(ret.stdout)
        print(ret.stderr)
    if last_err is not None:
        return 2

    total_count = _read_stats_total_count(paths["stats"])
    alpha_candidate_count = _read_stats_alpha_candidate_count(paths["stats"])
    print(f"[scheduler] total_count={total_count} alpha_candidate_count={alpha_candidate_count}")

    if total_count == 0:
        print("[scheduler] empty window, skip report generation")
        if update_state:
            new_state = {
                "last_end": end.isoformat(),
                "last_success_job_id": paths["window_id"],
                "last_run_at": dt.datetime.now().isoformat(),
                "last_result": "skip_with_log",
            }
            _atomic_write_json(cfg["state_file"], new_state)
        return 0

    skip_th = int(cfg.get("low_activity_skip_threshold") or 0)
    if skip_th > 0 and 0 < total_count < skip_th and alpha_candidate_count == 0:
        print(
            f"[scheduler] low_activity_skip: total_count={total_count} < "
            f"low_activity_skip_threshold={skip_th}; skipping AI and report "
            "(runtime stats/text from analyze are still on disk)"
        )
        notice = _format_low_activity_skip_message(cfg, start, end, total_count, skip_th)
        sender_result: Dict[str, Any] = {
            "ok": True,
            "provider": "none",
            "message_id": None,
            "error_code": None,
            "error": None,
            "attempts": 0,
        }
        try:
            sender_result = _run_sender_text(cfg, notice)
        except Exception as e:
            sender_result = {
                "ok": False,
                "provider": cfg.get("sender", "none"),
                "message_id": None,
                "error_code": type(e).__name__,
                "error": str(e),
                "attempts": 1,
            }
        if not sender_result.get("ok"):
            print(
                "[sender] failed: "
                f"provider={sender_result.get('provider')} "
                f"code={sender_result.get('error_code')} "
                f"error={sender_result.get('error')}"
            )
            strict_sender_fail = bool(cfg.get("sender_strict", False))
        else:
            print(
                "[sender] ok: "
                f"provider={sender_result.get('provider')} "
                f"message_id={sender_result.get('message_id')}"
            )
            strict_sender_fail = False
        if update_state:
            new_state = {
                "last_end": end.isoformat(),
                "last_success_job_id": paths["window_id"],
                "last_run_at": dt.datetime.now().isoformat(),
                "last_result": "skipped_low_activity",
                "last_sender_result": "ok" if sender_result.get("ok") else "error",
                "last_sender_provider": sender_result.get("provider"),
                "last_sender_error": sender_result.get("error"),
                "last_sender_at": dt.datetime.now().isoformat(),
            }
            _atomic_write_json(cfg["state_file"], new_state)
        if strict_sender_fail:
            return 4
        print("[scheduler] window done (skipped_low_activity)")
        return 0

    provider_result = "ok"
    provider_error = None
    if total_count < cfg["min_messages"]:
        with open(paths["ai"], "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)
        print("[scheduler] low activity, using minimal ai_content")
        provider_result = "skipped_low_activity"
    else:
        try:
            _run_provider_with_timeout(cfg, paths["stats"], paths["ai"], paths["text"])
        except Exception as e:
            print(f"[provider] fallback to minimal ai_content because: {e}")
            provider_result = "error"
            provider_error = str(e)[:1000]
            with open(paths["ai"], "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "_provider_status": {
                            "ok": False,
                            "provider": cfg.get("provider", "unknown"),
                        }
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

    report_cmd = [
        cfg["python_executable"],
        "scripts/generate_report.py",
        "--stats",
        paths["stats"],
        "--ai-content",
        paths["ai"],
        "--output",
        paths["report"],
    ]
    report_timeout = max(cfg["report_timeout_seconds"], 1)
    ret = _run_cmd(report_cmd, cwd=REPO_ROOT, timeout=report_timeout)
    if ret.returncode != 0:
        print(ret.stdout)
        print(ret.stderr)
        return 3

    # An empty Alpha report means there was no actionable investment signal.
    # Do not leave an empty artifact or send a notification for that window.
    if os.path.isfile(paths["report"]) and os.path.getsize(paths["report"]) == 0:
        os.remove(paths["report"])
        print("[scheduler] no alpha, skip report delivery")
        if update_state:
            _atomic_write_json(
                cfg["state_file"],
                {
                    "last_end": end.isoformat(),
                    "last_success_job_id": paths["window_id"],
                    "last_run_at": dt.datetime.now().isoformat(),
                    "last_result": "skipped_no_alpha",
                    "last_provider_result": provider_result,
                    "last_provider_error": provider_error,
                },
            )
        return 0

    sender_result = {
        "ok": True,
        "provider": "none",
        "message_id": None,
        "error_code": None,
        "error": None,
        "attempts": 0,
    }
    try:
        sender_result = _run_sender_with_timeout(
            cfg,
            report_path=paths["report"],
            window_id=paths["window_id"],
        )
    except Exception as e:
        sender_result = {
            "ok": False,
            "provider": cfg.get("sender", "none"),
            "message_id": None,
            "error_code": type(e).__name__,
            "error": str(e),
            "attempts": 1,
        }
    if not sender_result.get("ok"):
        print(
            "[sender] failed: "
            f"provider={sender_result.get('provider')} "
            f"code={sender_result.get('error_code')} "
            f"error={sender_result.get('error')}"
        )
        strict_sender_fail = bool(cfg.get("sender_strict", False))
    else:
        print(
            "[sender] ok: "
            f"provider={sender_result.get('provider')} "
            f"message_id={sender_result.get('message_id')}"
        )
        strict_sender_fail = False

    if update_state:
        new_state = {
            "last_end": end.isoformat(),
            "last_success_job_id": paths["window_id"],
            "last_run_at": dt.datetime.now().isoformat(),
            "last_result": "ok_degraded_provider" if provider_result == "error" else "ok",
            "last_provider_result": provider_result,
            "last_provider_error": provider_error,
            "last_sender_result": "ok" if sender_result.get("ok") else "error",
            "last_sender_provider": sender_result.get("provider"),
            "last_sender_error": sender_result.get("error"),
            "last_sender_at": dt.datetime.now().isoformat(),
        }
        _atomic_write_json(cfg["state_file"], new_state)
    if strict_sender_fail:
        return 4
    print("[scheduler] window done")
    return 0


def run_scheduled(
    cfg: Dict[str, Any],
    dry_run: bool = False,
    manual_window: Optional[Tuple[dt.datetime, dt.datetime]] = None,
    force: bool = False,
) -> int:
    """依次处理积压窗口，最多 max_catchup_windows 次。"""
    if manual_window is not None:
        start, end = manual_window
        print("[scheduler] manual window mode enabled (state disabled)")
        return process_one_window(cfg, start, end, dry_run, update_state=False, force=force)

    max_n = cfg["max_catchup_windows"]
    processed = 0

    while processed < max_n:
        state = _load_state(cfg["state_file"])
        window = _compute_next_window(cfg, state)
        if not window:
            if processed == 0:
                print("[scheduler] no ready window yet")
            break
        start, end = window
        code = process_one_window(cfg, start, end, dry_run, force=force)
        if dry_run:
            return code
        if code != 0:
            return code
        processed += 1

    if processed > 1:
        print(f"[scheduler] catch-up processed {processed} window(s)")
    elif processed == 1:
        print("[scheduler] all done")
    return 0


def _next_boundary(now: dt.datetime, interval_minutes: int) -> dt.datetime:
    return _floor_to_interval(now, interval_minutes) + dt.timedelta(minutes=interval_minutes)


def _sleep_until(
    target: dt.datetime,
    *,
    poll_seconds: int,
    heartbeat_seconds: int = 300,
) -> None:
    poll = max(5, int(poll_seconds))
    next_heartbeat = dt.datetime.now() + dt.timedelta(seconds=heartbeat_seconds)
    while True:
        now = dt.datetime.now()
        if now >= target:
            return
        if now >= next_heartbeat:
            left = max(0, int((target - now).total_seconds()))
            print(f"[daemon] waiting for next boundary, remaining={left}s")
            next_heartbeat = now + dt.timedelta(seconds=heartbeat_seconds)
        sleep_for = min(poll, max(1, int((target - now).total_seconds())))
        time.sleep(sleep_for)


def run_daemon(
    cfg: Dict[str, Any],
    *,
    poll_seconds: int,
    backoff_seconds: int,
    force: bool,
) -> int:
    poll = max(5, int(poll_seconds))
    backoff = max(5, int(backoff_seconds))
    print(
        "[daemon] started: "
        f"interval_minutes={cfg['interval_minutes']} poll_seconds={poll} backoff_seconds={backoff}"
    )
    while True:
        code = run_scheduled(cfg, dry_run=False, manual_window=None, force=force)
        if code != 0:
            print(f"[daemon] run failed with code={code}, backoff {backoff}s then continue")
            time.sleep(backoff)
            continue
        next_at = _next_boundary(dt.datetime.now(), cfg["interval_minutes"])
        print(f"[daemon] next wake at {_fmt_time(next_at)}")
        _sleep_until(next_at, poll_seconds=poll)


def main() -> None:
    args = parse_args()
    if args.poll_seconds < 5:
        raise SystemExit("`--poll-seconds` must be >= 5.")
    if args.daemon_backoff_seconds < 5:
        raise SystemExit("`--daemon-backoff-seconds` must be >= 5.")
    cfg = _load_config(args.config)
    manual_window: Optional[Tuple[dt.datetime, dt.datetime]] = None
    if bool(args.start) != bool(args.end):
        raise SystemExit("`--start` and `--end` must be provided together.")
    if args.start and args.end:
        start = _parse_cli_window_time(args.start, "start")
        end = _parse_cli_window_time(args.end, "end")
        if end <= start:
            raise SystemExit("`--end` must be later than `--start`.")
        manual_window = (start, end)
    if args.daemon and args.dry_run:
        raise SystemExit("`--daemon` cannot be used with `--dry-run`.")
    if args.daemon and manual_window is not None:
        raise SystemExit("`--daemon` cannot be used with `--start/--end` manual window.")

    if not _acquire_lock(cfg["lock_file"], cfg["lock_ttl_seconds"]):
        raise SystemExit(0)
    try:
        if args.daemon:
            code = run_daemon(
                cfg,
                poll_seconds=args.poll_seconds,
                backoff_seconds=args.daemon_backoff_seconds,
                force=args.force,
            )
        else:
            code = run_scheduled(cfg, args.dry_run, manual_window=manual_window, force=args.force)
    except KeyboardInterrupt:
        print("[daemon] interrupted by user, exiting")
        code = 0
    finally:
        _release_lock(cfg["lock_file"])
    raise SystemExit(code)


if __name__ == "__main__":
    main()
