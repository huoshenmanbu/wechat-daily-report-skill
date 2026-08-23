# -*- coding: utf-8 -*-
"""Tests for low_activity_skip_threshold in schedule_report."""

import datetime as dt
import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import schedule_report as sr  # noqa: E402


def _minimal_schedule_yaml(
    td: Path,
    *,
    low_activity_skip_threshold: int = 50,
    low_activity_skip_message: str = "",
) -> str:
    lines = [
        "interval_minutes: 60",
        "chatroom: TestRoom",
        "decrypted_dir:",
        f"output_dir: {td / 'out'}",
        f"work_dir: {td / 'work'}",
        "output_format: md",
        "align_mode: floor",
        "min_messages: 5",
        f"low_activity_skip_threshold: {low_activity_skip_threshold}",
        "retry_times: 0",
        "skip_refresh: false",
        "max_catchup_windows: 24",
        "provider: stub",
        "provider_timeout_seconds: 60",
        "analyze_timeout_seconds: 60",
        "report_timeout_seconds: 60",
        "max_chat_chars: 0",
        "sender: none",
        "sender_timeout_seconds: 30",
        "sender_retry_times: 0",
        "sender_retry_backoff_seconds: 0",
        "sender_strict: false",
        "feishu_message_max_chars: 3000",
        f"lock_file: {td / 'lock'}",
        "lock_ttl_seconds: 600",
        f"state_file: {td / 'state.json'}",
    ]
    if low_activity_skip_message:
        # Flat YAML: one line; escape quotes in message not needed for simple template
        lines.append(f'low_activity_skip_message: "{low_activity_skip_message}"')
    p = td / "sched.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


class LowActivitySkipTests(unittest.TestCase):
    def test_empty_alpha_report_is_not_sent_or_kept(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            (td_path / "out").mkdir()
            (td_path / "work").mkdir()
            cfg = sr._load_config(_minimal_schedule_yaml(td_path, low_activity_skip_threshold=0))
            start = dt.datetime(2026, 5, 10, 10, 0, 0)
            end = dt.datetime(2026, 5, 10, 11, 0, 0)
            written_state = {}

            def fake_run_cmd(cmd, **_kwargs):
                if any(str(arg).endswith("generate_report.py") for arg in cmd):
                    Path(cmd[cmd.index("--output") + 1]).write_text("", encoding="utf-8")
                return MagicMock(returncode=0, stdout="", stderr="")

            with patch.object(sr, "_run_cmd", side_effect=fake_run_cmd):
                with patch.object(sr, "_read_stats_total_count", return_value=30):
                    with patch.object(sr, "_read_stats_alpha_candidate_count", return_value=0):
                        with patch.object(sr.os.path, "exists", return_value=False):
                            with patch.object(sr, "_run_provider_with_timeout"):
                                with patch.object(sr, "_run_sender_with_timeout") as m_send:
                                    with patch.object(sr, "_atomic_write_json", side_effect=lambda _p, d: written_state.update(d)):
                                        rc = sr.process_one_window(cfg, start, end, dry_run=False)

            self.assertEqual(0, rc)
            m_send.assert_not_called()
            self.assertEqual("skipped_no_alpha", written_state["last_result"])
            self.assertFalse((td_path / "out" / "report_20260510_1000-1100.md").exists())

    def test_skip_branch_skips_report_and_calls_notice(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            (td_path / "out").mkdir()
            (td_path / "work").mkdir()
            cfg_path = _minimal_schedule_yaml(td_path, low_activity_skip_threshold=50)
            cfg = sr._load_config(cfg_path)

            start = dt.datetime(2026, 5, 10, 10, 0, 0)
            end = dt.datetime(2026, 5, 10, 11, 0, 0)

            cmd_calls = []

            def fake_run_cmd(cmd, **kwargs):
                cmd_calls.append(list(cmd))
                m = MagicMock()
                m.returncode = 0
                m.stdout = ""
                m.stderr = ""
                return m

            written_state = {}

            def capture_state(path, data):
                written_state.clear()
                written_state["path"] = path
                written_state["data"] = dict(data)

            with patch.object(sr, "_run_cmd", side_effect=fake_run_cmd):
                with patch.object(sr, "_read_stats_total_count", return_value=30):
                    with patch.object(sr.os.path, "exists", return_value=False):
                        with patch.object(sr, "_atomic_write_json", side_effect=capture_state):
                            with patch.object(sr, "_run_sender_text") as m_send:
                                m_send.return_value = {
                                    "ok": True,
                                    "provider": "none",
                                    "message_id": None,
                                    "error_code": None,
                                    "error": None,
                                    "attempts": 0,
                                }
                                rc = sr.process_one_window(
                                    cfg, start, end, dry_run=False, update_state=True, force=False
                                )

            self.assertEqual(rc, 0)
            flat = " ".join(str(x) for c in cmd_calls for x in c)
            self.assertNotIn("generate_report.py", flat)
            self.assertIn("analyze_chat.py", flat)
            m_send.assert_called_once()
            body = m_send.call_args[0][1]
            self.assertIn("【群聊总结跳过】", body)
            self.assertIn("30", body)
            self.assertIn("50", body)
            self.assertEqual(written_state["data"].get("last_result"), "skipped_low_activity")

    def test_custom_skip_message_template(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            (td_path / "out").mkdir()
            (td_path / "work").mkdir()
            tpl = "c={chatroom} s={start} e={end} n={total_count} t={threshold}"
            cfg_path = _minimal_schedule_yaml(
                td_path,
                low_activity_skip_threshold=10,
                low_activity_skip_message=tpl,
            )
            cfg = sr._load_config(cfg_path)
            start = dt.datetime(2026, 1, 2, 8, 0, 0)
            end = dt.datetime(2026, 1, 2, 9, 0, 0)
            out = sr._format_low_activity_skip_message(cfg, start, end, 3, 10)
            self.assertIn("c=TestRoom", out)
            self.assertIn("n=3", out)
            self.assertIn("t=10", out)

    def test_threshold_zero_runs_report_path(self):
        """With threshold 0, low-activity skip is disabled; stub path still runs generate_report."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            (td_path / "out").mkdir()
            (td_path / "work").mkdir()
            cfg_path = _minimal_schedule_yaml(td_path, low_activity_skip_threshold=0)
            cfg = sr._load_config(cfg_path)
            start = dt.datetime(2026, 5, 10, 10, 0, 0)
            end = dt.datetime(2026, 5, 10, 11, 0, 0)

            cmd_calls = []

            def fake_run_cmd(cmd, **kwargs):
                cmd_calls.append(list(cmd))
                m = MagicMock()
                m.returncode = 0
                m.stdout = ""
                m.stderr = ""
                return m

            with patch.object(sr, "_run_cmd", side_effect=fake_run_cmd):
                with patch.object(sr, "_read_stats_total_count", return_value=30):
                    with patch.object(sr.os.path, "exists", return_value=False):
                        with patch.object(sr, "_run_sender_with_timeout") as m_rpt_send:
                            m_rpt_send.return_value = {
                                "ok": True,
                                "provider": "none",
                                "message_id": None,
                                "error_code": None,
                                "error": None,
                                "attempts": 0,
                            }
                            with patch.object(sr, "_run_provider_with_timeout"):
                                with patch.object(sr, "_atomic_write_json"):
                                    rc = sr.process_one_window(
                                        cfg, start, end, dry_run=False, update_state=True, force=False
                                    )

            self.assertEqual(rc, 0)
            flat = " ".join(str(x) for c in cmd_calls for x in c)
            self.assertIn("generate_report.py", flat)

    def test_alpha_candidate_prevents_low_activity_skip(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            (td_path / "out").mkdir()
            (td_path / "work").mkdir()
            cfg = sr._load_config(_minimal_schedule_yaml(td_path, low_activity_skip_threshold=50))
            start = dt.datetime(2026, 5, 10, 10, 0, 0)
            end = dt.datetime(2026, 5, 10, 11, 0, 0)
            cmd_calls = []

            def fake_run_cmd(cmd, **kwargs):
                cmd_calls.append(list(cmd))
                result = MagicMock(returncode=0, stdout="", stderr="")
                return result

            with patch.object(sr, "_run_cmd", side_effect=fake_run_cmd):
                with patch.object(sr, "_read_stats_total_count", return_value=1):
                    with patch.object(sr, "_read_stats_alpha_candidate_count", return_value=1):
                        with patch.object(sr.os.path, "exists", return_value=False):
                            with patch.object(sr, "_run_provider_with_timeout"):
                                with patch.object(sr, "_run_sender_with_timeout") as m_send:
                                    m_send.return_value = {"ok": True, "provider": "none"}
                                    with patch.object(sr, "_atomic_write_json"):
                                        rc = sr.process_one_window(cfg, start, end, dry_run=False)

            self.assertEqual(0, rc)
            self.assertIn("generate_report.py", " ".join(str(x) for c in cmd_calls for x in c))


class LoadConfigWarningTests(unittest.TestCase):
    def test_warns_when_threshold_ge_min_messages(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            (td_path / "out").mkdir()
            (td_path / "work").mkdir()
            p = td_path / "sched.yaml"
            p.write_text(
                "\n".join(
                    [
                        "interval_minutes: 60",
                        "chatroom: X",
                        "decrypted_dir:",
                        f"output_dir: {td_path / 'out'}",
                        f"work_dir: {td_path / 'work'}",
                        "output_format: md",
                        "align_mode: floor",
                        "min_messages: 10",
                        "low_activity_skip_threshold: 10",
                        "retry_times: 0",
                        "skip_refresh: false",
                        "max_catchup_windows: 24",
                        "provider: stub",
                        "provider_timeout_seconds: 60",
                        "analyze_timeout_seconds: 60",
                        "report_timeout_seconds: 60",
                        "max_chat_chars: 0",
                        "sender: none",
                        "sender_timeout_seconds: 30",
                        "sender_retry_times: 0",
                        "sender_retry_backoff_seconds: 0",
                        "sender_strict: false",
                        "feishu_message_max_chars: 3000",
                        f"lock_file: {td_path / 'lock'}",
                        "lock_ttl_seconds: 600",
                        f"state_file: {td_path / 'state.json'}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            buf = io.StringIO()
            with patch.object(sr.sys, "stderr", buf):
                sr._load_config(str(p))
            joined = buf.getvalue()
            self.assertIn("low_activity_skip_threshold", joined)
            self.assertIn("min_messages", joined)
            self.assertIn(">=", joined)


class ProviderReliabilityTests(unittest.TestCase):
    def test_killable_process_enforces_wall_clock_timeout(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            sr._run_in_killable_process(time.sleep, (2,), {}, timeout=0.1)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_provider_failure_is_recorded_as_degraded(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            (td_path / "out").mkdir()
            (td_path / "work").mkdir()
            cfg = sr._load_config(_minimal_schedule_yaml(td_path, low_activity_skip_threshold=0))
            cfg["provider"] = "deepseek"
            start = dt.datetime(2026, 5, 10, 10, 0, 0)
            end = dt.datetime(2026, 5, 10, 11, 0, 0)
            written_state = {}

            def fake_run_cmd(_cmd, **_kwargs):
                return MagicMock(returncode=0, stdout="", stderr="")

            def capture_state(_path, data):
                written_state.update(data)

            with patch.object(sr, "_run_cmd", side_effect=fake_run_cmd):
                with patch.object(sr, "_read_stats_total_count", return_value=30):
                    with patch.object(sr, "_read_stats_alpha_candidate_count", return_value=0):
                        with patch.object(sr.os.path, "exists", return_value=False):
                            with patch.object(sr, "_run_provider_with_timeout", side_effect=RuntimeError("HTTP 401")):
                                with patch.object(sr, "_run_sender_with_timeout", return_value={"ok": True, "provider": "none"}):
                                    with patch.object(sr, "_atomic_write_json", side_effect=capture_state):
                                        rc = sr.process_one_window(cfg, start, end, dry_run=False)

            self.assertEqual(0, rc)
            self.assertEqual("ok_degraded_provider", written_state["last_result"])
            self.assertEqual("error", written_state["last_provider_result"])


if __name__ == "__main__":
    unittest.main()
