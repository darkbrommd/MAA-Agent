import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent
import diagnostic_commands
import news_monitor
import resource_history
import maa_agent_gui


class NewsMonitorTests(unittest.TestCase):
    def test_parses_official_flash_update_window(self):
        text = "将于09月20日16:00 ~ 16:10 期间进行服务器闪断更新。"
        start, end = news_monitor.parse_maintenance_window(text, datetime(2026, 9, 20, 15).timestamp())
        self.assertEqual(start, datetime(2026, 9, 20, 16, 0))
        self.assertEqual(end, datetime(2026, 9, 20, 16, 10))

    def test_due_slot_is_stable_between_runs(self):
        now = datetime(2026, 9, 20, 21, 43)
        self.assertEqual(news_monitor.current_due_slot(now, ["00:05", "12:05"]), "2026-09-20T12:05")


class PersistentDetectorTests(unittest.TestCase):
    def test_agent_restart_same_windows_boot_preserves_missing_timer(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            old = (agent.PROCESS_HEALTH_FILE, agent.RESOURCE_PRESSURE_FILE, agent.HUNG_STATE_FILE,
                   agent.NETWORK_HEALTH_FILE, agent.GENERIC_ISSUE_DIR)
            try:
                agent.PROCESS_HEALTH_FILE = state / "process_health.json"
                agent.RESOURCE_PRESSURE_FILE = state / "resource_pressure.json"
                agent.HUNG_STATE_FILE = state / "hung.json"
                agent.NETWORK_HEALTH_FILE = state / "network.json"
                agent.GENERIC_ISSUE_DIR = state / "issues"; agent.GENERIC_ISSUE_DIR.mkdir()
                agent.save_process_health_state({"windows_boot_time": 1000, "missing": {"instance:MAA1": {"since": 123}}})
                with patch.object(agent.psutil, "boot_time", return_value=1000):
                    agent.reset_transient_session_state()
                saved = json.loads(agent.PROCESS_HEALTH_FILE.read_text(encoding="utf-8"))
                self.assertEqual(saved["missing"]["instance:MAA1"]["since"], 123)
            finally:
                (agent.PROCESS_HEALTH_FILE, agent.RESOURCE_PRESSURE_FILE, agent.HUNG_STATE_FILE,
                 agent.NETWORK_HEALTH_FILE, agent.GENERIC_ISSUE_DIR) = old

    def test_windows_reboot_clears_missing_timer(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            old = (agent.PROCESS_HEALTH_FILE, agent.RESOURCE_PRESSURE_FILE, agent.HUNG_STATE_FILE,
                   agent.NETWORK_HEALTH_FILE, agent.GENERIC_ISSUE_DIR)
            try:
                agent.PROCESS_HEALTH_FILE = state / "process_health.json"
                agent.RESOURCE_PRESSURE_FILE = state / "resource_pressure.json"
                agent.HUNG_STATE_FILE = state / "hung.json"
                agent.NETWORK_HEALTH_FILE = state / "network.json"
                agent.GENERIC_ISSUE_DIR = state / "issues"; agent.GENERIC_ISSUE_DIR.mkdir()
                agent.save_process_health_state({"windows_boot_time": 900, "missing": {"instance:MAA1": {"since": 123}}})
                with patch.object(agent.psutil, "boot_time", return_value=1000):
                    agent.reset_transient_session_state()
                saved = json.loads(agent.PROCESS_HEALTH_FILE.read_text(encoding="utf-8"))
                self.assertEqual(saved["missing"], {})
            finally:
                (agent.PROCESS_HEALTH_FILE, agent.RESOURCE_PRESSURE_FILE, agent.HUNG_STATE_FILE,
                 agent.NETWORK_HEALTH_FILE, agent.GENERIC_ISSUE_DIR) = old


class UtilityTests(unittest.TestCase):
    def test_diagnostic_queue_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            command_id = diagnostic_commands.enqueue(base, "maa_missing", "MAA1")
            rows = diagnostic_commands.consume(base)
            self.assertEqual(rows[0]["id"], command_id)
            self.assertEqual(rows[0]["target"], "MAA1")
            self.assertEqual(diagnostic_commands.consume(base), [])

    def test_operational_day_starts_at_four(self):
        self.assertEqual(resource_history.operational_date(datetime(2026, 9, 20, 3, 59), 4), date(2026, 9, 19))
        self.assertEqual(resource_history.operational_date(datetime(2026, 9, 20, 4, 0), 4), date(2026, 9, 20))

    def test_calendar_day_merges_legacy_operational_files(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            directory = base / "state" / "resource_history"
            directory.mkdir(parents=True)
            def row(moment):
                return json.dumps({"time": moment.isoformat(), "epoch": moment.timestamp(), "ram": 50})
            (directory / "2026-09-20.jsonl").write_text("\n".join([
                row(datetime(2026, 9, 20, 23, 59)), row(datetime(2026, 9, 21, 2, 0))
            ]) + "\n", encoding="utf-8")
            (directory / "2026-09-21.jsonl").write_text("\n".join([
                row(datetime(2026, 9, 21, 4, 0)), row(datetime(2026, 9, 22, 0, 1))
            ]) + "\n", encoding="utf-8")
            rows = resource_history.load_calendar_day(base, date(2026, 9, 21), 4)
            self.assertEqual([datetime.fromtimestamp(x["epoch"]).hour for x in rows], [2, 4])

    def test_legacy_memory_row_can_show_used_and_available(self):
        used, available, total = resource_history.memory_values({"ram": 75, "available_mb": 8192})
        self.assertAlmostEqual(available, 8192)
        self.assertAlmostEqual(total, 32768, places=0)
        self.assertAlmostEqual(used, 24576, places=0)
        text = maa_agent_gui.memory_text(total, available, 75)
        self.assertIn("已用", text)
        self.assertIn("可用", text)

    def test_mumu_nx_is_grouped_as_mumu(self):
        category, name = resource_history._category("MuMuNxMain.exe", [], {"maa.exe"}, {"mumuplayer.exe"}, set())
        self.assertEqual((category, name), ("mumu", "MuMu"))

    def test_sleep_poll_executes_diagnostic_without_waiting_full_loop(self):
        with patch.object(agent, "stop_requested", return_value=False), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(agent, "execute_diagnostic_commands") as execute, \
             patch.object(agent.time, "time", side_effect=[0, 0, 0, 2]), \
             patch.object(agent.time, "sleep"):
            agent.sleep_with_stop(1)
        execute.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
