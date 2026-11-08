import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
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

    def test_parses_official_midnight_written_as_24(self):
        text = "将于09月22日16:00 ~ 24:00期间进行维护。"
        start, end = news_monitor.parse_maintenance_window(text, datetime(2026, 9, 22, 12).timestamp())
        self.assertEqual(start, datetime(2026, 9, 22, 16, 0))
        self.assertEqual(end, datetime(2026, 9, 23, 0, 0))

    def test_due_slot_is_stable_between_runs(self):
        now = datetime(2026, 9, 20, 21, 43)
        self.assertEqual(news_monitor.current_due_slot(now, ["00:05", "12:05"]), "2026-09-20T12:05")

    def test_due_slot_accepts_24_hour_notation(self):
        now = datetime(2026, 9, 21, 0, 6)
        self.assertEqual(news_monitor.current_due_slot(now, ["24:05"]), "2026-09-21T00:05")

    def test_article_cleanup_removes_site_navigation_and_builds_complete_summary(self):
        title = "09月22日维护公告"
        raw = (
            f"{title} - 明日方舟 公告 {title} 2026 // 09 / 22 "
            + "第一句用于介绍公告。" * 8
            + "第二部分说明具体内容。" * 20
            + " INFO 返回列表 GO BACK 返回首页 HOMEPAGE ARKNIGHTS INFORMATION"
        )
        cleaned = news_monitor.clean_article_text(raw)
        summary = news_monitor.article_summary(cleaned, "被截断的摘要")
        self.assertNotIn("返回列表", cleaned)
        self.assertNotIn("HOMEPAGE", cleaned)
        self.assertFalse(cleaned.startswith(title))
        self.assertTrue(summary.endswith(("。", "！", "？", "；")))


class GuiFormattingTests(unittest.TestCase):
    def test_public_release_metadata_and_portable_copilot_scripts(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(maa_agent_gui.APP_VERSION, version)
        self.assertEqual(maa_agent_gui.AUTHOR_NAME, "Taylor Yu")
        self.assertEqual(maa_agent_gui.AUTHOR_URL, "https://github.com/darkbrommd")
        requirements = (ROOT / "requirements-gui.txt").read_text(encoding="utf-8").lower()
        for package in ("psutil", "requests", "pywin32", "openai", "pystray", "pillow"):
            self.assertIn(package, requirements)
        for name in (
            "disable_copilot_notify.bat",
            "enable_copilot_notify_once.bat",
            "enable_copilot_notify_always.bat",
        ):
            script = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("%~dp0state", script)
            self.assertNotIn("D:\\MAA-Agent", script)

    def test_dashboard_log_cutoff_does_not_change_source_text(self):
        text = "[2026-09-22 00:10:00] old\n[2026-09-22 00:11:00] new"
        filtered = maa_agent_gui.log_text_after(text, datetime(2026, 9, 22, 0, 10, 30))
        self.assertEqual(filtered, "[2026-09-22 00:11:00] new")
        self.assertIn("old", text)

    def test_only_earliest_future_occurrence_is_marked_next(self):
        now=datetime(2026,9,26,20,0)
        rows=[
            {"label":"MAA1","datetime":now+timedelta(hours=2)},
            {"label":"MAA2","datetime":now+timedelta(hours=4)},
            {"label":"MAA1","datetime":now-timedelta(hours=2)},
        ]
        self.assertEqual(
            maa_agent_gui.next_schedule_keys(rows,now),
            {maa_agent_gui.schedule_occurrence_key("MAA1",now+timedelta(hours=2))},
        )

    def test_schedule_card_uses_terminal_run_state(self):
        app=object.__new__(maa_agent_gui.ManagerApp)
        app.cfg={"gui_dark_mode":True}; app.palette=maa_agent_gui.PALETTES["dark"]
        app._daily_state=lambda _day: {}
        now=datetime(2026,9,26,20,0); row={"label":"MAA1","datetime":now}
        key=maa_agent_gui.schedule_occurrence_key("MAA1",now)
        failed={key:{"status":"failed","error":{"title":"自动清理智失败"}}}
        self.assertEqual(app._schedule_card_state(row,now,{},failed)[2],"failed")
        running={key:{"status":"running","updated_at":now.strftime("%Y-%m-%d %H:%M:%S"),"current_task":"基建"}}
        self.assertEqual(app._schedule_card_state(row,now,{},running)[0],"正在进行")
        self.assertNotEqual(app._card_background("failed"),app._card_background("running"))


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
