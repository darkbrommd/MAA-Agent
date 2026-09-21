"""Tests for the local news centre cache added to news_monitor.

The network is always mocked: fetch_announcements and article_plain_text are
patched in every test and each test runs inside a temporary directory, so the
production state file is never read or written here.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import news_monitor

BASE_TS = datetime(2026, 9, 1, 0, 0).timestamp()
ARTICLE_PREFIX = "https://ak.hypergryph.com/news/"


def make_row(number, title=None, brief="公告摘要", ts=None):
    cid = f"{number:04d}"
    return {
        "cid": cid,
        "title": title if title is not None else f"公告 {cid}",
        "brief": brief,
        "displayTime": int(ts if ts is not None else BASE_TS + number * 3600),
    }


def read_state(base_dir):
    path = Path(base_dir) / "state" / "news_monitor.json"
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(base_dir, value):
    state_dir = Path(base_dir) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "news_monitor.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class ProgressRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, current, total, message):
        self.calls.append((current, total, message))


def fake_article(cid, timeout=15.0):
    return f"正文 {cid}"


class NewsCacheRefreshTests(unittest.TestCase):
    def test_default_limits_are_fifty_and_twenty(self):
        self.assertEqual(news_monitor.DEFAULT_NEWS_CACHE_LIMIT, 50)
        self.assertEqual(news_monitor.DEFAULT_NEWS_INITIAL_FETCH_COUNT, 20)
        self.assertEqual(news_monitor._cache_limits({}), (50, 20))

    def test_first_refresh_keeps_latest_twenty_announcements(self):
        rows = [make_row(n) for n in range(1, 31)]
        recorder = ProgressRecorder()
        with tempfile.TemporaryDirectory() as td, \
                patch.object(news_monitor, "fetch_announcements", return_value=rows), \
                patch.object(news_monitor, "article_plain_text", side_effect=fake_article):
            result = news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1}, progress=recorder)
            state = read_state(td)

        self.assertEqual(result["status"], "baseline")
        self.assertEqual(result["new_count"], 20)
        self.assertEqual(result["total"], 20)
        self.assertEqual(result["errors"], [])
        self.assertTrue(recorder.calls)

        items = state["news_items"]
        self.assertEqual(len(items), 20)
        self.assertEqual([item["cid"] for item in items], [f"{n:04d}" for n in range(30, 10, -1)])
        newest = items[0]
        for key in ("cid", "title", "brief", "url", "display_time", "content"):
            self.assertIn(key, newest)
        self.assertEqual(newest["title"], "公告 0030")
        self.assertEqual(newest["url"], ARTICLE_PREFIX + "0030")
        self.assertTrue(newest["display_time"])
        self.assertTrue(newest["brief"])
        # The cache never claims poll's baseline, so old announcements cannot be re-sent.
        self.assertNotIn("initialized", state)

    def test_cache_is_capped_at_fifty_entries(self):
        first_batch = [make_row(n) for n in range(1, 21)]
        second_batch = [make_row(n) for n in range(1, 71)]
        with tempfile.TemporaryDirectory() as td, \
                patch.object(news_monitor, "article_plain_text", side_effect=fake_article), \
                patch.object(news_monitor, "fetch_announcements", return_value=first_batch):
            first = news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1})
            self.assertEqual(first["total"], 20)
            with patch.object(news_monitor, "fetch_announcements", return_value=second_batch):
                second = news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1})
            cids = [item["cid"] for item in read_state(td)["news_items"]]

        self.assertEqual(second["status"], "ok")
        self.assertEqual(second["new_count"], 50)
        self.assertEqual(second["total"], 50)
        self.assertEqual(len(cids), 50)
        self.assertEqual(len(set(cids)), 50)
        self.assertEqual(cids, [f"{n:04d}" for n in range(70, 20, -1)])

    def test_incremental_refresh_dedupes_and_reuses_cached_body(self):
        rows = [make_row(n) for n in range(1, 6)]
        fetched_bodies = []

        def counting_article(cid, timeout=15.0):
            fetched_bodies.append(cid)
            return f"正文 {cid}"

        with tempfile.TemporaryDirectory() as td, \
                patch.object(news_monitor, "article_plain_text", side_effect=counting_article), \
                patch.object(news_monitor, "fetch_announcements", return_value=rows):
            first = news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1})
            self.assertEqual(first["new_count"], 5)
            self.assertEqual(len(fetched_bodies), 5)

            updated = [make_row(n, title=f"改名 {n:04d}") for n in range(1, 6)] + [make_row(6)]
            with patch.object(news_monitor, "fetch_announcements", return_value=updated):
                second = news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1})
            items = {item["cid"]: item for item in read_state(td)["news_items"]}

        self.assertEqual(second["status"], "ok")
        self.assertEqual(second["new_count"], 1)
        self.assertEqual(second["total"], 6)
        # Only the genuinely new announcement needed a body fetch.
        self.assertEqual(len(fetched_bodies), 6)
        self.assertEqual(len(items), 6)
        self.assertEqual(items["0001"]["title"], "改名 0001")
        self.assertEqual(items["0001"]["content"], "正文 0001")

    def test_body_fetch_failure_keeps_metadata(self):
        rows = [make_row(n) for n in range(1, 4)]

        def flaky_article(cid, timeout=15.0):
            if cid == "0002":
                raise RuntimeError("模拟网络失败")
            return f"正文 {cid}"

        with tempfile.TemporaryDirectory() as td, \
                patch.object(news_monitor, "fetch_announcements", return_value=rows), \
                patch.object(news_monitor, "article_plain_text", side_effect=flaky_article):
            result = news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1})
            items = {item["cid"]: item for item in read_state(td)["news_items"]}

        self.assertEqual(result["status"], "baseline")
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("0002", result["errors"][0])
        self.assertEqual(set(items), {"0001", "0002", "0003"})
        broken = items["0002"]
        self.assertEqual(broken["title"], "公告 0002")
        self.assertTrue(broken["brief"])
        self.assertEqual(broken["url"], ARTICLE_PREFIX + "0002")
        self.assertTrue(broken["display_time"])
        self.assertNotIn("content", broken)
        self.assertEqual(items["0001"]["content"], "正文 0001")

    def test_list_fetch_failure_keeps_existing_cache(self):
        rows = [make_row(n) for n in range(1, 4)]
        with tempfile.TemporaryDirectory() as td, \
                patch.object(news_monitor, "article_plain_text", side_effect=fake_article), \
                patch.object(news_monitor, "fetch_announcements", return_value=rows):
            news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1})
            with patch.object(news_monitor, "fetch_announcements", side_effect=RuntimeError("接口不可用")):
                result = news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1})
            cached = read_state(td)["news_items"]

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["new_count"], 0)
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(len(cached), 3)

    def test_cache_limits_come_from_config(self):
        cfg = {"news_timeout_seconds": 1, "news_cache_limit": 3, "news_initial_fetch_count": 2}
        with tempfile.TemporaryDirectory() as td, \
                patch.object(news_monitor, "article_plain_text", side_effect=fake_article), \
                patch.object(news_monitor, "fetch_announcements", return_value=[make_row(n) for n in range(1, 11)]):
            first = news_monitor.refresh_news_cache(Path(td), cfg)
            self.assertEqual(first["status"], "baseline")
            self.assertEqual(first["total"], 2)
            self.assertEqual([item["cid"] for item in first["items"]], ["0010", "0009"])
            with patch.object(news_monitor, "fetch_announcements",
                              return_value=[make_row(n) for n in range(1, 16)]):
                second = news_monitor.refresh_news_cache(Path(td), cfg)

        self.assertEqual(second["total"], 3)
        self.assertEqual([item["cid"] for item in second["items"]], ["0015", "0014", "0013"])


class NewsCacheProgressTests(unittest.TestCase):
    def test_progress_reports_current_total_and_message(self):
        rows = [make_row(n) for n in range(1, 6)]
        recorder = ProgressRecorder()
        with tempfile.TemporaryDirectory() as td, \
                patch.object(news_monitor, "fetch_announcements", return_value=rows), \
                patch.object(news_monitor, "article_plain_text", side_effect=fake_article):
            news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1}, progress=recorder)

        self.assertTrue(recorder.calls)
        for current, total, message in recorder.calls:
            self.assertGreaterEqual(current, 0)
            self.assertLessEqual(current, total)
            self.assertTrue(str(message).strip())
        self.assertEqual(recorder.calls[0][0], 0)
        self.assertEqual(recorder.calls[-1][0], recorder.calls[-1][1])
        self.assertTrue(any("正文" in message for _, _, message in recorder.calls))

    def test_progress_callback_with_positional_only_signature(self):
        seen = []

        def callback(*args):
            seen.append(args)

        with tempfile.TemporaryDirectory() as td, \
                patch.object(news_monitor, "fetch_announcements", return_value=[make_row(1)]), \
                patch.object(news_monitor, "article_plain_text", side_effect=fake_article):
            result = news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1}, progress=callback)

        self.assertEqual(result["total"], 1)
        self.assertTrue(seen)
        self.assertEqual(len(seen[-1]), 3)
        self.assertEqual(seen[-1][1], seen[-1][0])

    def test_failing_progress_callback_does_not_break_refresh(self):
        def callback(**kwargs):
            raise RuntimeError("GUI 已关闭")

        with tempfile.TemporaryDirectory() as td, \
                patch.object(news_monitor, "fetch_announcements", return_value=[make_row(1)]), \
                patch.object(news_monitor, "article_plain_text", side_effect=fake_article):
            result = news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1}, progress=callback)

        self.assertEqual(result["status"], "baseline")
        self.assertEqual(result["total"], 1)


class NewsCacheCompatibilityTests(unittest.TestCase):
    def test_refresh_keeps_legacy_state_keys(self):
        legacy = {
            "initialized": True,
            "last_slot": "2026-09-20T12:05",
            "last_checked_at": "2026-09-20T12:05:49",
            "seen_ids": ["4922", "8578"],
            "last_result": "new=0, relevant=0",
            "latest_events": [],
        }
        with tempfile.TemporaryDirectory() as td:
            write_state(td, legacy)
            with patch.object(news_monitor, "fetch_announcements", return_value=[make_row(n) for n in range(1, 4)]), \
                    patch.object(news_monitor, "article_plain_text", side_effect=fake_article):
                result = news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1})
            state = read_state(td)

        self.assertEqual(result["total"], 3)
        for key, value in legacy.items():
            self.assertEqual(state[key], value, key)
        self.assertEqual(len(state["news_items"]), 3)

    def test_refresh_then_poll_still_alerts_only_new_maintenance_notice(self):
        legacy = {
            "initialized": True,
            "last_slot": "2026-09-20T12:05",
            "last_checked_at": "2026-09-20T12:05:49",
            "seen_ids": ["8001", "8002"],
            "last_result": "new=0, relevant=0",
            "latest_events": [],
        }
        window_text = "将于09月21日16:00 ~ 16:10 期间进行服务器闪断更新。"
        rows = [
            make_row(8003, title="9月21日闪断更新公告", ts=datetime(2026, 9, 21, 12, 0).timestamp()),
            make_row(8002, title="旧公告 8002"),
            make_row(8001, title="旧公告 8001"),
        ]
        cfg = {"news_timeout_seconds": 1}
        with tempfile.TemporaryDirectory() as td:
            write_state(td, legacy)
            with patch.object(news_monitor, "fetch_announcements", return_value=rows), \
                    patch.object(news_monitor, "article_plain_text", side_effect=lambda cid, timeout=15.0: window_text), \
                    patch.object(news_monitor, "apply_supervision_overrides", return_value=[]) as overrides:
                news_monitor.refresh_news_cache(Path(td), cfg)

                # Building the cache must not touch the notification bookkeeping.
                state = read_state(td)
                self.assertEqual(len(state["news_items"]), 3)
                self.assertEqual(state["seen_ids"], ["8001", "8002"])
                self.assertEqual(state["last_slot"], "2026-09-20T12:05")
                self.assertEqual(state["latest_events"], [])
                self.assertEqual(state["last_result"], "new=0, relevant=0")
                self.assertFalse(overrides.called)

                polled = news_monitor.poll(Path(td), cfg, force=True, now=datetime(2026, 9, 21, 13, 0))
                self.assertEqual(polled["status"], "ok")
                self.assertEqual(polled["new_count"], 1)
                self.assertEqual([event["cid"] for event in polled["events"]], ["8003"])

                state = read_state(td)
                self.assertEqual(len(state["news_items"]), 3)
                self.assertIn("8003", state["seen_ids"])
                self.assertEqual([event["cid"] for event in state["latest_events"]], ["8003"])

                again = news_monitor.poll(Path(td), cfg, force=True, now=datetime(2026, 9, 21, 13, 5))
                self.assertEqual(again["new_count"], 0)
                self.assertEqual(again["events"], [])

    def test_poll_keeps_supervision_override_behaviour(self):
        window_text = "将于09月21日16:00 ~ 16:10 期间进行服务器闪断更新。"
        rows = [make_row(9001, title="9月21日闪断更新公告", ts=datetime(2026, 9, 21, 12, 0).timestamp())]
        with tempfile.TemporaryDirectory() as td:
            write_state(td, {"initialized": True, "seen_ids": [], "last_slot": "2026-09-20T12:05"})
            with patch.object(news_monitor, "fetch_announcements", return_value=rows), \
                    patch.object(news_monitor, "article_plain_text", side_effect=lambda cid, timeout=15.0: window_text), \
                    patch.object(news_monitor, "apply_supervision_overrides",
                                 return_value=["MAA1 09-21 16:00：本次不做漏跑报警"]) as overrides:
                polled = news_monitor.poll(Path(td), {"news_timeout_seconds": 1}, force=True,
                                           now=datetime(2026, 9, 21, 13, 0))

        overrides.assert_called_once()
        self.assertEqual(polled["events"][0]["changes"], ["MAA1 09-21 16:00：本次不做漏跑报警"])
        self.assertEqual(polled["events"][0]["title"], "9月21日闪断更新公告")

    def test_poll_baseline_seeds_cache_and_keeps_it(self):
        rows = [make_row(n) for n in range(1, 26)]
        with tempfile.TemporaryDirectory() as td, \
                patch.object(news_monitor, "fetch_announcements", return_value=rows), \
                patch.object(news_monitor, "article_plain_text", side_effect=fake_article):
            result = news_monitor.poll(Path(td), {"news_timeout_seconds": 1}, force=True,
                                       now=datetime(2026, 9, 21, 13, 0))
            state = read_state(td)
            loaded = news_monitor.load_news_cache(Path(td))

        self.assertEqual(result["status"], "baseline")
        self.assertEqual(result["events"], [])
        self.assertEqual(len(state["news_items"]), 20)
        self.assertEqual(len(state["seen_ids"]), 25)
        self.assertEqual(loaded["total"], 20)
        self.assertEqual(loaded["items"][0]["cid"], "0025")

    def test_load_news_cache_never_touches_the_network(self):
        with tempfile.TemporaryDirectory() as td, \
                patch.object(news_monitor, "fetch_announcements", return_value=[make_row(n) for n in range(1, 6)]), \
                patch.object(news_monitor, "article_plain_text", side_effect=fake_article):
            news_monitor.refresh_news_cache(Path(td), {"news_timeout_seconds": 1})
            with patch.object(news_monitor, "fetch_announcements",
                              side_effect=AssertionError("读取缓存不应访问网络")):
                loaded = news_monitor.load_news_cache(Path(td))

        self.assertEqual(loaded["total"], 5)
        self.assertEqual(loaded["items"][0]["cid"], "0005")
        self.assertTrue(loaded["updated_at"])
        self.assertEqual(loaded["cache_file"], str(Path(td) / "state" / "news_monitor.json"))


if __name__ == "__main__":
    unittest.main()
