"""Qt GUI tests run after the legacy Tk suite to keep toolkit teardown isolated."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from PySide6.QtCore import QCoreApplication, QEventLoop, QPoint, QPointF, QRect, QThreadPool, Qt
from PySide6.QtGui import QImage, QPainter, QWheelEvent
from PySide6.QtWidgets import QApplication

from gui.components.resource_chart import ResourceChart
from gui.components.responsive_layout import responsive_card_rect
from gui.components.timeline_delegate import TimelineDelegate, compute_card_layout
from gui.main_window import MainWindow
from gui.models.news_model import NewsModel
from gui.pages.dashboard import DashboardPage
from gui.pages.news import NewsPage
from gui.pages.logs import LogsPage
from gui.models.schedule_model import ScheduleModel, schedule_row_key
from gui.pages.resources import _WhitelistSaveWorker
from gui.pages.schedule import SchedulePage, _OverrideWorker
from gui.resource_paths import resource_path
from gui.services.dashboard_service import DashboardSnapshot, dashboard_source_signature, load_dashboard_snapshot
from gui.services import agent_control_service
from gui.services.log_service import line_matches, read_log_chunk
from gui.services.news_service import NewsSnapshot
from gui.services.process_service import _build_snapshot
from gui.services.schedule_service import decorate_schedule_rows, occurrence_key, schedule_run_records
from gui.state.refresh_manager import RefreshManager
from gui.state.app_state import AppState
from gui.themes.tokens import DARK, LIGHT, load_stylesheet


def process_events(milliseconds: int = 80) -> None:
    deadline = time.monotonic() + milliseconds / 1000
    while time.monotonic() < deadline:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)


def wait_until(predicate, milliseconds: int = 1000) -> bool:
    deadline = time.monotonic() + milliseconds / 1000
    while time.monotonic() < deadline:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class GuiV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_theme_tokens_and_qss_are_complete(self):
        for theme in (DARK, LIGHT):
            self.assertTrue(theme.background.startswith("#"))
            self.assertTrue(theme.disabled_background.startswith("#"))
            self.assertTrue(theme.running_card.startswith("#"))
            self.assertTrue(theme.next_card.startswith("#"))
            self.assertTrue(theme.ended_card.startswith("#"))
            stylesheet = load_stylesheet(theme.name)
            self.assertIn("QFrame#NavigationRail", stylesheet)
            self.assertIn('QLabel#StatusPill[status="running"]', stylesheet)
            self.assertIn("QListView::item:selected", stylesheet)
            self.assertNotIn("QWidget {\n    background:", stylesheet)
            self.assertNotIn("{background}", stylesheet)
        self.assertTrue(resource_path("assets", "icons", "calendar.svg").is_file())

    def test_gui_resource_path_prefers_onefile_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            icon = bundle / "assets" / "icons" / "calendar.svg"
            icon.parent.mkdir(parents=True)
            icon.write_text("<svg/>", encoding="utf-8")
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "_MEIPASS", str(bundle), create=True),
                patch.object(sys, "executable", str(bundle / "MAA-Agent.exe")),
            ):
                self.assertEqual(resource_path("assets", "icons", "calendar.svg"), icon)

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

    def test_due_schedule_waits_for_start_evidence_during_grace(self):
        now = datetime(2026, 11, 26, 4, 5)
        due = {"label": "MAA1", "datetime": now - timedelta(minutes=5), "config": "A", "skipped": False}
        decorated = decorate_schedule_rows([due], {}, {}, set(), now, grace_minutes=15)
        self.assertEqual(decorated[0]["state"], "running")
        self.assertEqual(decorated[0]["status_title"], "等待启动确认")
        expired = decorate_schedule_rows([due], {}, {}, set(), now + timedelta(minutes=16), grace_minutes=15)
        self.assertEqual(expired[0]["state"], "off")

    def test_schedule_run_records_accepts_current_and_legacy_shapes(self):
        current = {"runs": {"MAA1|2026-11-25 08:00": {"status": "failed"}}, "version": 2}
        self.assertEqual(schedule_run_records(current), current["runs"])
        legacy = {"MAA1|2026-11-25 08:00": {"status": "completed"}}
        self.assertIs(schedule_run_records(legacy), legacy)

    def test_delegate_uses_one_shared_layout_for_error_hit_area(self):
        rect = QRect(0, 0, 920, 112)
        layout = compute_card_layout(rect, {"state": "failed", "override": {"id": "one"}})
        self.assertFalse(layout.error_button.isEmpty())
        self.assertFalse(layout.move_button.isEmpty())
        self.assertFalse(layout.clear_button.isEmpty())
        self.assertTrue(layout.card.contains(layout.error_button.center()))
        self.assertTrue(layout.card.contains(layout.toggle.center()))
        self.assertTrue(layout.card.contains(layout.state_badge.center()))
        self.assertGreaterEqual(layout.error_button.left(), layout.status.right())
        self.assertFalse(layout.error_button.intersects(layout.move_button))
        self.assertFalse(layout.move_button.intersects(layout.clear_button))
        for scaled_width in (920, 1150, 1380, 1610):
            scaled = compute_card_layout(QRect(0, 0, scaled_width, 140), {"state": "failed", "override": {"id": "one"}})
            self.assertTrue(scaled.card.contains(scaled.error_button.center()))
            self.assertTrue(scaled.card.contains(scaled.move_button.center()))
            self.assertTrue(scaled.card.contains(scaled.clear_button.center()))
            self.assertGreater(scaled.title.width(), 0)

    def test_timeline_cards_align_and_stop_growing_on_ultrawide_views(self):
        normal = compute_card_layout(QRect(0, 0, 1200, 122), {"state": "future"})
        wide = compute_card_layout(QRect(0, 0, 2500, 122), {"state": "future"})
        self.assertAlmostEqual(normal.toggle.center().y(), normal.time.center().y(), delta=4)
        self.assertAlmostEqual(normal.time.center().y(), normal.card.center().y(), delta=1)
        self.assertLess(wide.card.width(), 1800)
        self.assertGreater(wide.card.width(), normal.card.width())
        resource_card = responsive_card_rect(QRect(0, 0, 2500, 82))
        self.assertLess(resource_card.width(), 1800)

    def test_schedule_day_headers_add_space_without_shifting_card_hit_areas(self):
        plain = compute_card_layout(QRect(0, 0, 1200, 122), {"state": "future"})
        headed = compute_card_layout(QRect(0, 0, 1200, 153), {"state": "future", "show_date_header": True})
        self.assertGreater(headed.card.top(), plain.card.top() + 25)
        self.assertTrue(headed.card.contains(headed.toggle.center()))
        self.assertTrue(headed.card.contains(headed.move_button.center()))

    def test_timeline_toggle_animates_immediately_and_can_reverse(self):
        delegate = TimelineDelegate("dark")
        when = datetime(2026, 11, 25, 8, 0)
        item = {"label": "MAA1", "datetime": when, "original_datetime": when, "skipped": False}
        self.assertEqual(delegate.toggle_progress(item), 1.0)
        delegate.animate_toggle(item, False)
        self.assertTrue(wait_until(lambda: delegate.toggle_progress(item) < 0.99))
        self.assertTrue(wait_until(lambda: abs(delegate.toggle_progress(item)) < 0.02))
        delegate.animate_toggle(item, True)
        self.assertTrue(wait_until(lambda: delegate.toggle_progress(item) > 0.98))

    def test_schedule_model_replaces_rows_atomically(self):
        model = ScheduleModel()
        model.set_rows(({"label": "MAA1", "datetime": datetime.now()},))
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.item(0)["label"], "MAA1")
        model.set_rows(())
        self.assertEqual(model.rowCount(), 0)

    def test_schedule_model_extends_edges_without_reset(self):
        model = ScheduleModel()
        base = datetime(2026, 11, 24, 8, 0)

        def row(offset: int) -> dict:
            when = base + timedelta(hours=offset)
            return {"label": "MAA1", "datetime": when, "original_datetime": when, "state": "future"}

        model.set_rows((row(0), row(1)))
        resets = []
        inserts = []
        model.modelReset.connect(lambda: resets.append(True))
        model.rowsInserted.connect(lambda _parent, first, last: inserts.append((first, last)))
        self.assertEqual(model.sync_rows((row(0), row(1), row(2))), "append")
        self.assertEqual(model.sync_rows((row(-1), row(0), row(1), row(2))), "prepend")
        self.assertEqual(resets, [])
        self.assertEqual(inserts, [(2, 2), (0, 0)])
        self.assertEqual(model.rowCount(), 4)
        self.assertEqual(model.item(0)["datetime"], base - timedelta(hours=1))
        duplicate_a = {**row(3), "timer_id": 1, "config": "A"}
        duplicate_b = {**row(3), "timer_id": 2, "config": "B"}
        self.assertNotEqual(schedule_row_key(duplicate_a), schedule_row_key(duplicate_b))

    def test_schedule_page_keeps_scroll_anchor_when_window_grows(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            (base_dir / "config").mkdir(parents=True)
            (base_dir / "state").mkdir()
            (base_dir / "config" / "config.json").write_text(
                json.dumps({"maa_schedule": {}, "maa_instance_dirs": {}}), encoding="utf-8"
            )
            page = SchedulePage(base_dir, {})
            QThreadPool.globalInstance().waitForDone(3000)
            process_events(80)
            base = datetime(2026, 11, 24)

            def row(offset: int) -> dict:
                when = base + timedelta(hours=offset)
                return {
                    "label": "MAA1",
                    "datetime": when,
                    "original_datetime": when,
                    "config": "任务",
                    "state": "future",
                    "status_title": "等待运行",
                    "status_detail": "已加入后续监测",
                    "skipped": False,
                    "override": None,
                }

            original = tuple(row(offset) for offset in range(16))
            page.model.set_rows(original)
            page.resize(900, 420)
            page.show()
            process_events(120)
            bar = page.list_view.verticalScrollBar()
            bar.setValue(bar.maximum())
            process_events(40)
            page._capture_view_anchor()
            old_value = page._preserve_scroll_value
            update_kind = page.model.sync_rows(original + tuple(row(offset) for offset in range(16, 20)))
            process_events(40)
            page._restore_after_update(update_kind, "extend_down")
            self.assertEqual(update_kind, "append")
            self.assertEqual(bar.value(), old_value)

            bar.setValue(max(bar.minimum(), bar.value() // 2))
            process_events(40)
            page._capture_view_anchor()
            anchor = page._preserve_key
            prepended = tuple(row(offset) for offset in range(-4, 0)) + page.model.rows()
            update_kind = page.model.sync_rows(prepended)
            process_events(40)
            page._restore_after_update(update_kind, "extend_up")
            process_events(40)
            top_index = page.list_view.indexAt(QPoint(12, 12))
            self.assertEqual(update_kind, "prepend")
            self.assertTrue(top_index.isValid())
            self.assertEqual(schedule_row_key(page.model.item(top_index.row())), anchor)
            self.assertEqual(page._override_pool.maxThreadCount(), 1)
            page.close()

    def test_wheel_at_existing_bottom_starts_one_extension_and_reset_unlocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            (base_dir / "config").mkdir(parents=True)
            (base_dir / "state").mkdir()
            (base_dir / "config" / "config.json").write_text(
                json.dumps({"maa_schedule": {}, "maa_instance_dirs": {}}), encoding="utf-8"
            )
            page = SchedulePage(base_dir, {})
            QThreadPool.globalInstance().waitForDone(3000)
            process_events(80)
            page._loading = False
            old_end = page.window_end
            event = QWheelEvent(
                QPointF(20, 20),
                QPointF(20, 20),
                QPoint(),
                QPoint(0, -120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase,
                False,
            )
            QApplication.sendEvent(page.list_view.viewport(), event)
            self.assertEqual(page.window_end, old_end + timedelta(days=2))
            self.assertTrue(page._wheel_blocked)
            selected = page.selected_day + timedelta(days=1)
            page.set_day(selected)
            self.assertFalse(page._wheel_blocked)
            context = page._request_contexts[page._active_request_id]
            self.assertEqual(context["mode"], "reset")
            QThreadPool.globalInstance().waitForDone(3000)
            process_events(80)
            page.close()

    def test_blocked_wheel_tail_is_consumed_instead_of_replayed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            (base_dir / "config").mkdir(parents=True)
            (base_dir / "state").mkdir()
            (base_dir / "config" / "config.json").write_text(
                json.dumps({"maa_schedule": {}, "maa_instance_dirs": {}}), encoding="utf-8"
            )
            page = SchedulePage(base_dir, {})
            QThreadPool.globalInstance().waitForDone(3000)
            process_events(100)
            page._loading = False
            page._edge_lock = False
            page._wheel_blocked = True
            page._extension_direction = 1
            before = page.list_view.verticalScrollBar().value()
            for _ in range(3):
                event = QWheelEvent(
                    QPointF(20, 20),
                    QPointF(20, 20),
                    QPoint(),
                    QPoint(0, -120),
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                    Qt.ScrollPhase.NoScrollPhase,
                    False,
                )
                QApplication.sendEvent(page.list_view.viewport(), event)
            self.assertEqual(page.list_view.verticalScrollBar().value(), before)
            self.assertTrue(page._wheel_blocked)
            self.assertTrue(page._wheel_quiet_timer.isActive())
            page.close()

    def test_schedule_card_toggle_worker_only_changes_supervision_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            (base_dir / "config").mkdir(parents=True)
            (base_dir / "state").mkdir()
            (base_dir / "config" / "config.json").write_text(
                json.dumps({"schedule_override_file": "state/schedule_overrides.json"}), encoding="utf-8"
            )
            when = datetime(2026, 11, 25, 8, 0)
            item = {"label": "MAA1", "datetime": when, "original_datetime": when}
            _OverrideWorker(base_dir, item, "skip", None).run()
            override_path = base_dir / "state" / "schedule_overrides.json"
            saved = json.loads(override_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["overrides"][0]["action"], "skip")
            stale_override = saved["overrides"][0]
            _OverrideWorker(base_dir, item, "move", when + timedelta(hours=1)).run()
            replaced = json.loads(override_path.read_text(encoding="utf-8"))["overrides"][0]
            self.assertNotEqual(replaced["id"], stale_override["id"])
            _OverrideWorker(base_dir, {**item, "override": stale_override}, "clear", None).run()
            cleared = json.loads(override_path.read_text(encoding="utf-8"))
            self.assertEqual(cleared["overrides"], [])
            messages = []
            missing = _OverrideWorker(base_dir, {**item, "override": stale_override}, "clear", None)
            missing.signals.done.connect(messages.append)
            missing.run()
            self.assertIn("已不存在", messages[0])

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
            "maa_instance_aliases": {"MAA1": "方舟主号"},
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
        self.assertEqual(snapshot.maa_instances[0].display_name, "方舟主号（MAA1）")
        self.assertEqual(snapshot.mumu_count, 1)
        self.assertEqual([item.pid for item in snapshot.top_processes], [12, 13, 11])
        self.assertGreaterEqual(snapshot.disk_total_gb, 0)

    def test_process_snapshot_groups_apps_but_keeps_maa_instances_separate(self):
        cfg = {
            "maa_instance_dirs": {"MAA1": "D:/MAA/one", "MAA2": "D:/MAA/two"},
            "maa_instance_aliases": {"MAA1": "主号", "MAA2": "副号"},
        }
        rows = [
            {"pid": index, "name": "ChatGPT.exe", "exe": "C:/ChatGPT/ChatGPT.exe", "memory": 500 * 1048576, "cmdline": ()}
            for index in (1, 2, 3)
        ] + [
            {"pid": 11, "name": "MAA.exe", "exe": "D:/MAA/one/MAA.exe", "memory": 900 * 1048576, "cmdline": ()},
            {"pid": 12, "name": "MAA.exe", "exe": "D:/MAA/two/MAA.exe", "memory": 800 * 1048576, "cmdline": ()},
        ]
        snapshot = _build_snapshot(cfg, rows)
        self.assertEqual(snapshot.top_processes[0].name, "ChatGPT.exe")
        self.assertEqual(snapshot.top_processes[0].process_count, 3)
        self.assertEqual(snapshot.top_processes[0].memory_mb, 1500)
        self.assertEqual({item.name for item in snapshot.top_processes[1:]}, {"主号（MAA1）", "副号（MAA2）"})

    def test_agent_control_refuses_duplicate_for_same_installation(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(agent_control_service, "validated_agent_for_dir", return_value={"pid": 321}), \
                patch.object(agent_control_service.subprocess, "Popen") as launch:
            ok, message = agent_control_service.start_agent_process(Path(temporary))
        self.assertFalse(ok)
        self.assertIn("321", message)
        launch.assert_not_called()

    def test_agent_restart_starts_only_after_normal_stop(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(agent_control_service, "request_stop", return_value=SimpleNamespace(status="stopped", message="已停止")), \
                patch.object(agent_control_service, "start_agent_process", return_value=(True, "启动请求已发送。")) as start:
            worker = agent_control_service._AgentActionWorker(Path(temporary), "restart")
            results = []
            worker.signals.done.connect(lambda ok, message: results.append((ok, message)))
            worker.run()
        self.assertEqual(results, [(True, "启动请求已发送。")])
        start.assert_called_once()

    def test_log_tail_reads_only_appended_bytes_and_resets_after_truncate(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "agent.log"
            path.write_text("first\nsecond\n", encoding="utf-8")
            initial = read_log_chunk(path)
            self.assertTrue(initial.reset)
            self.assertEqual(initial.text.splitlines(), ["first", "second"])
            with path.open("a", encoding="utf-8") as handle:
                handle.write("third\n")
            appended = read_log_chunk(
                path,
                offset=initial.offset,
                identity=initial.identity,
                anchor=initial.anchor,
                initialized=True,
            )
            self.assertFalse(appended.reset)
            self.assertEqual(appended.text.splitlines(), ["third"])
            replacement_text = "replacement after a fast truncate is longer than the previous file\n"
            path.write_text(replacement_text, encoding="utf-8")
            replaced = read_log_chunk(
                path,
                offset=appended.offset,
                identity=appended.identity,
                anchor=appended.anchor,
                initialized=True,
            )
            self.assertTrue(replaced.reset)
            self.assertEqual(replaced.text.splitlines(), [replacement_text.strip()])
            path.write_text("new\n", encoding="utf-8")
            truncated = read_log_chunk(
                path,
                offset=replaced.offset,
                identity=replaced.identity,
                anchor=replaced.anchor,
                initialized=True,
            )
            self.assertTrue(truncated.reset)
            self.assertEqual(truncated.text.splitlines(), ["new"])
            path.unlink()
            missing = read_log_chunk(
                path,
                offset=truncated.offset,
                identity=truncated.identity,
                anchor=truncated.anchor,
                initialized=True,
            )
            self.assertTrue(missing.reset)
            self.assertFalse(missing.exists)
        self.assertTrue(line_matches("ERROR task failed", "错误与警告"))
        self.assertFalse(line_matches("INFO normal", "错误与警告"))
        self.assertTrue(line_matches("INFO 已恢复", "事故与恢复", "恢复"))

    def test_news_model_and_page_keep_selected_cid_when_rows_refresh(self):
        model = NewsModel()
        model.set_rows(({"cid": "one"}, {"cid": "two"}))
        self.assertEqual(model.row_for_cid("two"), 1)
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            (base_dir / "config").mkdir()
            (base_dir / "state").mkdir()
            (base_dir / "config" / "config.json").write_text("{}", encoding="utf-8")
            page = NewsPage(base_dir, {"selected_cid": "two"})
            first = NewsSnapshot(
                ({"cid": "one", "title": "A"}, {"cid": "two", "title": "B"}),
                "now",
                "local",
                total=2,
            )
            page._apply_snapshot(first)
            page.list.setCurrentIndex(page.model.index(1, 0))
            second = NewsSnapshot(
                ({"cid": "new", "title": "N"}, {"cid": "two", "title": "B2"}),
                "later",
                "local",
                total=2,
            )
            page._apply_snapshot(second)
            self.assertEqual(page.model.item(page.list.currentIndex().row())["cid"], "two")
            QThreadPool.globalInstance().waitForDone(3000)
            process_events(80)
            page.close()

    def test_dashboard_clear_only_changes_display_cutoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            (base_dir / "logs").mkdir()
            path = base_dir / "logs" / "agent.log"
            original = "2026-11-25 12:00:00 INFO keep me\n"
            path.write_text(original, encoding="utf-8")
            state = {}
            page = DashboardPage(base_dir, state)
            page._log_lines = original.splitlines()
            page._render_logs()
            page._clear_home_log()
            self.assertIn("log_cutoff", state)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(page.log_view.toPlainText(), "")
            page.close()

    def test_dashboard_schedule_uses_day_header_and_fixed_height_cards(self):
        with tempfile.TemporaryDirectory() as temporary:
            page = DashboardPage(Path(temporary), {})
            when = datetime(2026, 11, 26, 4, 0)
            rows = (
                {"label": "MAA1", "display_label": "主号（MAA1）", "datetime": when, "state": "running", "status_title": "正在进行", "status_detail": "当前：任务队列", "show_date_header": True},
                {"label": "MAA2", "display_label": "副号（MAA2）", "datetime": when + timedelta(hours=2), "state": "future", "status_title": "等待运行", "status_detail": "已加入后续监测", "show_date_header": False},
            )
            page._apply_dashboard(DashboardSnapshot(rows, (), when.date()))
            self.assertEqual(page.schedule_list.count(), 3)
            self.assertEqual(page.schedule_list.item(0).sizeHint().height(), 34)
            self.assertEqual(page.schedule_list.item(1).sizeHint().height(), 88)
            page.close()

    def test_dashboard_snapshot_keeps_recently_due_occurrence(self):
        now = datetime(2026, 11, 26, 4, 5)
        due_time = datetime(2026, 11, 26, 4, 0)
        due = {"label": "MAA1", "datetime": due_time, "original_datetime": due_time, "config": "A", "skipped": False}
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "config").mkdir()
            (base / "config" / "config.json").write_text("{}", encoding="utf-8")
            with patch("gui.services.dashboard_service.effective_occurrences", return_value=([due], {})) as occurrences, \
                    patch("gui.services.dashboard_service.resource_history.load_calendar_day", return_value=[]):
                snapshot = load_dashboard_snapshot(base, now)
        self.assertEqual(snapshot.schedule_rows[0]["status_title"], "等待启动确认")
        self.assertEqual(occurrences.call_args.args[2], now - timedelta(hours=2))

    def test_logs_page_uses_user_facing_names_without_changing_filenames(self):
        with tempfile.TemporaryDirectory() as temporary:
            page = LogsPage(Path(temporary), {"file": "decision.log"})
            self.assertEqual(page.file.currentText(), "决策日志（decision.log）")
            self.assertEqual(page._current_filename(), "decision.log")
            page.file.setCurrentIndex(page.file.findData("agent.log"))
            self.assertEqual(page.page_state["file"], "agent.log")
            QThreadPool.globalInstance().waitForDone(3000)
            page.close()

    def test_dashboard_signature_changes_only_when_sources_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            (base_dir / "config").mkdir()
            (base_dir / "state").mkdir()
            path = base_dir / "config" / "config.json"
            path.write_text("{}", encoding="utf-8")
            now = datetime(2026, 11, 25, 12, 34, 10)
            first = dashboard_source_signature(base_dir, now)
            self.assertEqual(first, dashboard_source_signature(base_dir, now + timedelta(seconds=20)))
            path.write_text('{"operational_day_start_hour": 5}', encoding="utf-8")
            self.assertNotEqual(first, dashboard_source_signature(base_dir, now))

    def test_whitelist_save_is_atomic_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            config_dir = base_dir / "config"
            config_dir.mkdir(parents=True)
            path = config_dir / "config.json"
            original = {"bark_key": "keep-me", "resource_whitelist": []}
            path.write_text(json.dumps(original), encoding="utf-8")
            rows = ({"name": "GTA 系列", "process_name": "", "process_regex": ".*GTAV.*", "path": "", "enabled": True},)
            results = []
            worker = _WhitelistSaveWorker(base_dir, rows)
            worker.signals.done.connect(lambda ok, message: results.append((ok, message)))
            worker.run()
            saved = json.loads(path.read_text(encoding="utf-8"))
            backup = json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8"))
            self.assertEqual(results[0][0], True)
            self.assertEqual(saved["bark_key"], "keep-me")
            self.assertEqual(saved["resource_whitelist"][0]["process_regex"], ".*GTAV.*")
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
            self.assertEqual(schedule.list_view.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.assertEqual(resources.anomalies.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.assertEqual(resources.history_splitter.count(), 2)
            chosen = date(2026, 11, 20)
            resources.request_day(chosen)
            window.show_page("schedule")
            window.show_page("resources")
            self.assertIs(window._pages["schedule"], schedule)
            self.assertIs(window._pages["resources"], resources)
            self.assertEqual(resources.selected_day, chosen)
            window.show_page("dashboard")
            window.show_page("news")
            window.show_page("logs")
            self.assertIsInstance(window._pages["dashboard"], DashboardPage)
            self.assertIsInstance(window._pages["news"], NewsPage)
            self.assertEqual(window._pages["logs"].view.maximumBlockCount(), 5000)
            window.close()
            process_events(100)
        QThreadPool.globalInstance().waitForDone(3000)

    def test_navigation_collapse_uses_icons_and_page_state_is_persisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            state = AppState(base_dir)
            page_state = state.page_state("resources")
            page_state["splitter_sizes"] = [420, 260]
            state.flush()
            restored = AppState(base_dir)
            self.assertEqual(restored.page_state("resources")["splitter_sizes"], [420, 260])
            restored.flush()

        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            (base_dir / "config").mkdir(parents=True)
            (base_dir / "state").mkdir(parents=True)
            (base_dir / "metadata").mkdir(parents=True)
            (base_dir / "metadata" / "VERSION").write_text("2.0.0", encoding="utf-8")
            (base_dir / "config" / "config.json").write_text(
                json.dumps({"maa_instance_dirs": {}, "maa_schedule": {}}), encoding="utf-8"
            )
            window = MainWindow(base_dir)
            window.navigation._toggle_collapsed()
            for button in window.navigation._buttons.values():
                self.assertEqual(button.text(), "")
                self.assertFalse(button.icon().isNull())
                self.assertTrue(button.toolTip())
            window.close()


if __name__ == "__main__":
    unittest.main()
