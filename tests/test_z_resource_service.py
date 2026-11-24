"""Resource-service tests run after the legacy Tk suite."""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from PySide6.QtCore import QCoreApplication, QEventLoop, QThreadPool

MODULE_PATH = APP_DIR / "gui" / "services" / "resource_service.py"
SPEC = importlib.util.spec_from_file_location(
    "_maa_gui_v2_resource_service", MODULE_PATH
)
RESOURCE_SERVICE_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RESOURCE_SERVICE_MODULE
SPEC.loader.exec_module(RESOURCE_SERVICE_MODULE)

ResourceService = RESOURCE_SERVICE_MODULE.ResourceService
ResourceSnapshot = RESOURCE_SERVICE_MODULE.ResourceSnapshot
resource_file_signature = RESOURCE_SERVICE_MODULE.resource_file_signature


def process_until(predicate, timeout_ms=2500):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        QCoreApplication.processEvents(
            QEventLoop.ProcessEventsFlag.AllEvents, 20
        )
        if predicate():
            return True
        time.sleep(0.002)
    QCoreApplication.processEvents()
    return bool(predicate())


class ResourceServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(4)
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.clear_cache()
        self.pool.waitForDone(3000)
        QCoreApplication.processEvents()

    def make_service(self, base_dir, **kwargs):
        service = ResourceService(
            base_dir, thread_pool=self.pool, **kwargs
        )
        self.services.append(service)
        return service

    def test_signature_tracks_mtime_size_and_missing_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            base_dir = Path(temporary)
            history = base_dir / "state" / "resource_history"
            history.mkdir(parents=True)
            source = history / "2026-11-23.jsonl"
            source.write_text(
                json.dumps({"epoch": 1, "cpu": 1}) + "\n",
                encoding="utf-8",
            )

            day = date(2026, 11, 24)
            initial = resource_file_signature(base_dir, day, 4)
            self.assertEqual(len(initial), 2)
            self.assertTrue(
                any(path.endswith("2026-11-23.jsonl") for path, _, _ in initial)
            )
            self.assertTrue(
                any(path.endswith("2026-11-24.jsonl") for path, _, _ in initial)
            )

            first_mtime = 1_700_000_000_000_000_000
            second_mtime = first_mtime + 1_000_000
            os.utime(source, ns=(first_mtime, first_mtime))
            after_mtime = resource_file_signature(base_dir, day, 4)
            self.assertNotEqual(initial, after_mtime)

            source.write_bytes(source.read_bytes() + b" ")
            os.utime(source, ns=(second_mtime, second_mtime))
            after_size = resource_file_signature(base_dir, day, 4)
            self.assertNotEqual(after_mtime, after_size)

            source_entry = next(
                entry for entry in after_size if entry[0].endswith(source.name)
            )
            self.assertEqual(source_entry[1], second_mtime)
            self.assertEqual(source_entry[2], source.stat().st_size)

    def test_cache_is_lru_and_limited_to_requested_size(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = self.make_service(
                Path(temporary), cache_days=2, loader=lambda *_: []
            )
            first = date(2026, 11, 20)
            second = date(2026, 11, 21)
            third = date(2026, 11, 22)
            for day in (first, second):
                service._store_cached(
                    (day, 4), ResourceSnapshot(day, (), (str(day),))
                )

            self.assertIsNotNone(service.cached_snapshot(first))
            service._store_cached(
                (third, 4), ResourceSnapshot(third, (), (str(third),))
            )

            self.assertIsNotNone(service.cached_snapshot(first))
            self.assertIsNone(service.cached_snapshot(second))
            self.assertIsNotNone(service.cached_snapshot(third))
            self.assertEqual(len(service._cache), 2)

    def test_cache_hit_reuses_rows_without_loader_reread(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = threading.Lock()
            calls = {}

            def loader(base_dir, day, start_hour):
                with lock:
                    calls[day] = calls.get(day, 0) + 1
                return [{"day": day.isoformat(), "epoch": day.toordinal()}]

            service = self.make_service(
                Path(temporary),
                debounce_ms=0,
                loader=loader,
            )
            loaded = []
            service.loaded.connect(
                lambda request_id, snapshot: loaded.append(
                    (request_id, snapshot)
                )
            )
            day = date(2026, 11, 20)

            first_id = service.request_day(day)
            self.assertTrue(
                process_until(
                    lambda: any(item[0] == first_id for item in loaded)
                )
            )
            first_snapshot = next(
                item[1] for item in loaded if item[0] == first_id
            )
            self.assertFalse(first_snapshot.from_cache)
            with lock:
                self.assertEqual(calls[day], 1)

            second_id = service.request_day(day)
            self.assertTrue(
                process_until(
                    lambda: any(item[0] == second_id for item in loaded)
                )
            )
            second_snapshot = next(
                item[1] for item in loaded if item[0] == second_id
            )
            self.assertTrue(second_snapshot.from_cache)
            self.assertEqual(first_snapshot.rows, second_snapshot.rows)
            with lock:
                self.assertEqual(calls[day], 1)

    def test_stale_request_is_not_emitted_and_can_fill_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            first_day = date(2026, 11, 1)
            second_day = date(2026, 11, 10)
            first_started = threading.Event()
            release_first = threading.Event()

            def loader(base_dir, day, start_hour):
                if day == first_day:
                    first_started.set()
                    release_first.wait(3)
                return [{"day": day.isoformat()}]

            service = self.make_service(
                Path(temporary),
                debounce_ms=0,
                loader=loader,
            )
            loaded = []
            service.loaded.connect(
                lambda request_id, snapshot: loaded.append(
                    (request_id, snapshot)
                )
            )

            first_id = service.request_day(first_day)
            self.assertTrue(
                process_until(first_started.is_set)
            )
            second_id = service.request_day(second_day)
            try:
                self.assertTrue(
                    process_until(
                        lambda: any(item[0] == second_id for item in loaded)
                    )
                )
            finally:
                release_first.set()

            self.assertTrue(
                process_until(
                    lambda: service.cached_snapshot(first_day) is not None
                )
            )
            self.assertTrue(
                process_until(lambda: not service._active_tasks)
            )
            self.assertEqual([item[0] for item in loaded], [second_id])
            self.assertEqual(service.current_request_id, second_id)
            self.assertIsNotNone(service.cached_snapshot(first_day))
            self.assertNotEqual(first_id, second_id)

    def test_debounce_runs_only_latest_pending_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = threading.Lock()
            requested_days = []

            def loader(base_dir, day, start_hour):
                with lock:
                    requested_days.append(day)
                return [{"day": day.isoformat()}]

            service = self.make_service(
                Path(temporary),
                debounce_ms=40,
                loader=loader,
            )
            loaded = []
            service.loaded.connect(
                lambda request_id, snapshot: loaded.append(request_id)
            )
            days = [
                date(2026, 11, 1),
                date(2026, 11, 10),
                date(2026, 11, 20),
            ]

            request_ids = [service.request_day(day) for day in days]
            self.assertEqual(request_ids, [1, 2, 3])
            self.assertTrue(
                process_until(lambda: request_ids[-1] in loaded)
            )
            self.assertTrue(
                process_until(lambda: not service._active_tasks)
            )
            with lock:
                self.assertNotIn(days[0], requested_days)
                self.assertNotIn(days[1], requested_days)
                self.assertIn(days[2], requested_days)

    def test_success_prefetches_adjacent_days_without_emitting(self):
        with tempfile.TemporaryDirectory() as temporary:
            def loader(base_dir, day, start_hour):
                return [{"day": day.isoformat()}]

            service = self.make_service(
                Path(temporary),
                debounce_ms=0,
                cache_days=7,
                loader=loader,
            )
            loaded = []
            service.loaded.connect(
                lambda request_id, snapshot: loaded.append(
                    (request_id, snapshot)
                )
            )
            day = date(2026, 11, 20)

            request_id = service.request_day(day, 6)
            self.assertTrue(
                process_until(
                    lambda: any(item[0] == request_id for item in loaded)
                )
            )
            previous_day = date(2026, 11, 19)
            next_day = date(2026, 11, 21)
            self.assertTrue(
                process_until(
                    lambda: service.cached_snapshot(previous_day, 6) is not None
                    and service.cached_snapshot(next_day, 6) is not None
                )
            )
            self.assertEqual(
                [item[0] for item in loaded],
                [request_id],
            )
            self.assertEqual(service.current_request_id, request_id)

            next_id = service.request_day(next_day, 6)
            self.assertTrue(
                process_until(
                    lambda: any(item[0] == next_id for item in loaded)
                )
            )
            snapshot = next(
                item[1] for item in loaded if item[0] == next_id
            )
            self.assertTrue(snapshot.from_cache)

    def test_one_hundred_rapid_requests_emit_only_latest(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = self.make_service(
                Path(temporary),
                debounce_ms=20,
                loader=lambda base_dir, day, start_hour: [{"day": day.isoformat()}],
            )
            loaded = []
            service.loaded.connect(lambda request_id, snapshot: loaded.append((request_id, snapshot.day)))
            first = date(2026, 8, 1)
            request_id = 0
            for offset in range(100):
                request_id = service.request_day(first.fromordinal(first.toordinal() + offset))
            self.assertEqual(request_id, 100)
            self.assertTrue(process_until(lambda: any(item[0] == 100 for item in loaded)))
            self.assertTrue(process_until(lambda: not service._active_tasks))
            self.assertEqual([item[0] for item in loaded], [100])
            self.assertEqual(loaded[0][1], first.fromordinal(first.toordinal() + 99))


if __name__ == "__main__":
    unittest.main()
