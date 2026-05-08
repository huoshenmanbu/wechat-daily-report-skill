#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
schedule_report.py - 周期总结调度器（run-once）

每次进程启动可处理至多 max_catchup_windows 个积压窗口，适配系统任务计划定时触发。
窗口语义与消息筛选一致：[start, end)（见 wechat_decrypted_reader.message_in_range）。
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from scripts.ai_provider import generate_ai_content
except ModuleNotFoundError:
    from ai_provider import generate_ai_content

# 仓库根目录（用于子进程 cwd，避免任务计划工作目录非仓库根时失败）
REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args():
    parser = argparse.ArgumentParser(description="Run scheduled summary windows by config.")
    parser.add_argument("--config", default="config/report_schedule.yaml", help="Schedule config path")
    parser.add_argument("--dry-run", action="store_true", help="Only print the next computed window")
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
    }
    if not cfg["chatroom"]:
        raise ValueError("Config `chatroom` is required.")
    if cfg["output_format"] not in ("md", "txt", "html"):
        raise ValueError("Config `output_format` must be one of: md, txt, html.")
    if cfg["align_mode"] not in ("floor", "rolling"):
        raise ValueError("Config `align_mode` must be one of: floor, rolling.")
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
            v = value.strip().strip("'").strip('"')
            if k:
                result[k] = v
    return result


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


def _run_provider_with_timeout(cfg: Dict[str, Any], stats_path: str, ai_path: str) -> None:
    timeout = max(cfg["provider_timeout_seconds"], 1)

    def _call():
        generate_ai_content(cfg["provider"], stats_path, ai_path)

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_call)
        try:
            fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"provider exceeded {timeout}s") from None


def process_one_window(cfg: Dict[str, Any], start: dt.datetime, end: dt.datetime, dry_run: bool) -> int:
    paths = _build_paths(cfg, start, end)

    print(f"[scheduler] window: {_fmt_time(start)} -> {_fmt_time(end)}")
    print(f"[scheduler] report: {paths['report']}")

    if dry_run:
        return 0

    if os.path.exists(paths["report"]):
        print("[scheduler] report already exists, skip")
        new_state = {
            "last_end": end.isoformat(),
            "last_success_job_id": paths["window_id"],
            "last_run_at": dt.datetime.now().isoformat(),
            "last_result": "skipped_existing",
        }
        _atomic_write_json(cfg["state_file"], new_state)
        return 0

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
    print(f"[scheduler] total_count={total_count}")

    if total_count == 0:
        print("[scheduler] empty window, skip report generation")
        new_state = {
            "last_end": end.isoformat(),
            "last_success_job_id": paths["window_id"],
            "last_run_at": dt.datetime.now().isoformat(),
            "last_result": "skip_with_log",
        }
        _atomic_write_json(cfg["state_file"], new_state)
        return 0

    if total_count < cfg["min_messages"]:
        with open(paths["ai"], "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)
        print("[scheduler] low activity, using minimal ai_content")
    else:
        try:
            _run_provider_with_timeout(cfg, paths["stats"], paths["ai"])
        except Exception as e:
            print(f"[provider] fallback to minimal ai_content because: {e}")
            with open(paths["ai"], "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)

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

    new_state = {
        "last_end": end.isoformat(),
        "last_success_job_id": paths["window_id"],
        "last_run_at": dt.datetime.now().isoformat(),
        "last_result": "ok",
    }
    _atomic_write_json(cfg["state_file"], new_state)
    print("[scheduler] window done")
    return 0


def run_scheduled(cfg: Dict[str, Any], dry_run: bool = False) -> int:
    """依次处理积压窗口，最多 max_catchup_windows 次。"""
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
        code = process_one_window(cfg, start, end, dry_run)
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


def main() -> None:
    args = parse_args()
    cfg = _load_config(args.config)
    if not _acquire_lock(cfg["lock_file"], cfg["lock_ttl_seconds"]):
        raise SystemExit(0)
    try:
        code = run_scheduled(cfg, args.dry_run)
    finally:
        _release_lock(cfg["lock_file"])
    raise SystemExit(code)


if __name__ == "__main__":
    main()
