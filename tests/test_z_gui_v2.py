"""Qt GUI tests run after the legacy Tk suite to keep toolkit teardown isolated."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from PySide6.QtCore import QCoreApplication, QEventLoop, QRect, QThreadPool
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from gui.components.resource_chart import ResourceChart
from gui.components.timeline_delegate import compute_card_layout
from gui.main_window import MainWindow
from gui.models.schedule_model import ScheduleModel
from gui.pages.resources import _WhitelistSaveWorker
from gui.services.process_service import _build_snapshot
from gui.services.schedule_service import decorate_schedule_rows, occurrence_key
from gui.state.refresh_manager import RefreshManager
from gui.themes.tokens import DARK, LIGHT, load_stylesheet


def process_events(milliseconds: int = 80) -> None:
    deadline = time.monotonic() + milliseconds / 1000
    while time.monotonic() < deadline:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)


class GuiV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_theme_tokens_and_qss_are_complete(self):
        for theme in (DARK, LIGHT):
            self.assertTrue(theme.background.startswith("#"))
            self.assertTrue(theme.disabled_background.startswith("#"))
            stylesheet = load_stylesheet(theme.name)
            self.assertIn("QFrame#NavigationRail", stylesheet)
            self.assertNotIn("{background}", stylesheet)

    def test_background_launcher_path_imports_no_pyside(self):
        code = (
            "import sys,types; "
            f"sys.path.insert(0, {str(APP_DIR)!r}); "
            "sys.argv=['launcher','--maa-agent-background']; "
            "module=types.ModuleType('agent'); module.main=lambda:None; sys.modules['agent']=module; "
            "import launcher; launcher.main(); "
            "print(any(name.startswith('PySide6') for name in sys.modules))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "False")

    def test_failed_schedule_state_has_priority_and_next_is_unique(self):
        now = datetime(2026, 11, 24, 12, 0)
        failed_time = now - timedelta(hours=1)
        next_time = now + timedelta(hours=1)
        later_time = now + timedelta(hours=3)
        rows = [
            {"label": "MAA1", "datetime": failed_time, "config": "A", "skipped": False},
            {"label": "MAA1", "datetime": next_time, "config": "B", "skipped": False},
            {"label": "MAA1", "datetime": later_time, "config": "C", "skipped": False},
        ]
        records = {
            occurrence_key("MAA1", failed_time): {
                "status": "failed",
                "error": {"title": "任务失败"},
            }
        }
        decorated = decorate_schedule_rows(rows, records, {}, set(), now)
        self.assertEqual(decorated[0]["state"], "failed")
        self.assertEqual(decorated[0]["status_detail"], "任务失败")
        self.assertEqual([row["state"] for row in decorated].count("next"), 1)
        self.assertEqual(decorated[1]["state"], "next")

    def test_delegate_uses_one_shared_layout_for_error_hit_area(self):
        rect = QRect(0, 0, 920, 112)
        layout = compute_card_layout(rect, {"state": "failed"})
        self.assertFalse(layout.error_button.isEmpty())
        self.assertTrue(layout.card.contains(layout.error_button.center()))
        self.assertGreaterEqual(layout.error_button.left(), layout.status.right())
        for scaled_width in (920, 1150, 1380):
            scaled = compute_card_layout(QRect(0, 0, scaled_width, 140), {"state": "failed"})
            self.assertTrue(scaled.card.contains(scaled.error_button.center()))
            self.assertGreater(scaled.title.width(), 0)

    def test_schedule_model_replaces_rows_atomically(self):
        model = ScheduleModel()
        model.set_rows(({"label": "MAA1", "datetime": datetime.now()},))
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.item(0)["label"], "MAA1")
        model.set_rows(())
        self.assertEqual(model.rowCount(), 0)

    def test_refresh_manager_runs_only_visible_active_page(self):
        manager = RefreshManager()
        calls = []
        timer = manager.register("schedule", 100, lambda: calls.append("schedule"))
        manager.set_active_page("schedule")
        self.assertTrue(timer.isActive())
        manager.set_window_visible(False)
        self.assertFalse(timer.isActive())
        manager.trigger_active()
        self.assertEqual(calls, [])
        manager.set_window_visible(True)
        manager.trigger_active()
        self.assertEqual(calls, ["schedule"])

    def test_process_snapshot_maps_configured_instances_from_one_snapshot(self):
        cfg = {
            "maa_instance_dirs": {"MAA1": "D:/MAA/one"},
            "mumu_process_names": ["MuMuPlayer.exe"],
        }
        rows = [
            {"pid": 11, "name": "MAA.exe", "exe": "D:/MAA/one/MAA.exe", "memory": 100, "cmdline": ()},
            {"pid": 12, "name": "MuMuPlayer.exe", "exe": "D:/MuMu/MuMuPlayer.exe", "memory": 2048, "cmdline": ()},
            {"pid": 13, "name": "MAA-Agent.exe", "exe": "D:/Agent/MAA-Agent.exe", "memory": 1024, "cmdline": ("MAA-Agent.exe", "--maa-agent-background")},
        ]
        snapshot = _build_snapshot(cfg, rows)
        self.assertTrue(snapshot.agent_running)
        self.assertEqual(snapshot.agent_pid, 13)
        self.assertEqual(snapshot.maa_instances[0].pids, (11,))
        self.assertEqual(snapshot.mumu_count, 1)

    def test_whitelist_save_is_atomic_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            config_dir = base_dir / "config"
            config_dir.mkdir(parents=True)
            path = config_dir / "config.json"
            original = {"bark_key": "keep-me", "resource_whitelist": []}
            path.write_text(json.dumps(original), encoding="utf-8")
            rows = ({"name": "Game", "process_name": "game.exe", "path": "D:/Game/game.exe", "enabled": True},)
            results = []
            worker = _WhitelistSaveWorker(base_dir, rows)
            worker.signals.done.connect(lambda ok, message: results.append((ok, message)))
            worker.run()
            saved = json.loads(path.read_text(encoding="utf-8"))
            backup = json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8"))
            self.assertEqual(results[0][0], True)
            self.assertEqual(saved["bark_key"], "keep-me")
            self.assertEqual(saved["resource_whitelist"][0]["process_name"], "game.exe")
            self.assertEqual(backup, original)

    def test_resource_chart_renders_offscreen(self):
        chart = ResourceChart("dark")
        chart.resize(900, 360)
        base = datetime(2026, 11, 24)
        rows = [
            {"epoch": (base + timedelta(minutes=index * 30)).timestamp(), "ram": 50 + index % 30, "cpu": 20 + index % 50, "used_mb": 8000, "available_mb": 8000}
            for index in range(48)
        ]
        chart.set_data(rows, base.date())
        image = QImage(chart.size(), QImage.Format.Format_ARGB32)
        chart.render(image)
        self.assertFalse(image.isNull())
        self.assertEqual(len(chart._rows), 48)

    def test_main_window_lazily_creates_and_preserves_pages(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            (base_dir / "config").mkdir(parents=True)
            (base_dir / "state").mkdir(parents=True)
            (base_dir / "metadata").mkdir(parents=True)
            (base_dir / "metadata" / "VERSION").write_text("2.0.0", encoding="utf-8")
            (base_dir / "config" / "config.json").write_text(
                json.dumps({"maa_instance_dirs": {}, "maa_schedule": {}, "operational_day_start_hour": 4}),
                encoding="utf-8",
            )
            window = MainWindow(base_dir)
            self.assertEqual(set(window._pages), {"schedule"})
            window.navigation._toggle_collapsed()
            self.assertEqual(window.navigation.width(), 68)
            window.navigation._toggle_collapsed()
            self.assertEqual(window.navigation.width(), 192)
            schedule = window._pages["schedule"]
            window.show_page("resources")
            resources = window._pages["resources"]
            chosen = date(2026, 11, 20)
            resources.request_day(chosen)
            window.show_page("schedule")
            window.show_page("resources")
            self.assertIs(window._pages["schedule"], schedule)
            self.assertIs(window._pages["resources"], resources)
            self.assertEqual(resources.selected_day, chosen)
            window.close()
            process_events(100)
        QThreadPool.globalInstance().waitForDone(3000)


if __name__ == "__main__":
    unittest.main()
