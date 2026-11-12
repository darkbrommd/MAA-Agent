import copy
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import schedule_utils
import agent
import agent_runtime


def timer_root(timers):
    return {"Timers": {"List": timers}}


@contextmanager
def isolated_agent(extra_cfg=None):
    attrs = [
        "BASE_DIR", "LOG_DIR", "ARCHIVE_DIR", "STATE_DIR", "DISABLED_DIR",
        "PID_FILE", "STOP_FILE", "BARK_QUEUE_FILE", "DAILY_REPORT_SKIP_FILE", "DAILY_REPORT_SENT_FILE",
        "RESOURCE_PRESSURE_FILE", "PROCESS_HEALTH_FILE", "HEARTBEAT_STATE_FILE",
        "NETWORK_HEALTH_FILE", "HUNG_STATE_FILE", "SCHEDULE_CACHE_FILE",
        "SCHEDULE_RUN_STATE_FILE", "GENERIC_ISSUE_DIR", "cfg",
    ]
    old = {k: getattr(agent, k) for k in attrs}
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        state = base / "state"
        logs = base / "logs"
        archive = logs / "archive"
        disabled = base / "disabled_alerts"
        issues = state / "issue_tracker"
        for p in (state, logs, archive, disabled, issues):
            p.mkdir(parents=True, exist_ok=True)
        agent.BASE_DIR = base
        agent.LOG_DIR = logs
        agent.ARCHIVE_DIR = archive
        agent.STATE_DIR = state
        agent.DISABLED_DIR = disabled
        agent.PID_FILE = state / "agent.pid"
        agent.STOP_FILE = state / "stop.request"
        agent.BARK_QUEUE_FILE = state / "bark_queue.jsonl"
        agent.DAILY_REPORT_SKIP_FILE = state / "daily_report_skip_date.txt"
        agent.DAILY_REPORT_SENT_FILE = state / "daily_report_sent_date.txt"
        agent.RESOURCE_PRESSURE_FILE = state / "resource_pressure.json"
        agent.PROCESS_HEALTH_FILE = state / "process_health.json"
        agent.HEARTBEAT_STATE_FILE = state / "heartbeat_state.json"
        agent.NETWORK_HEALTH_FILE = state / "network_health.json"
        agent.HUNG_STATE_FILE = state / "hung_windows.json"
        agent.SCHEDULE_CACHE_FILE = state / "schedule_cache.json"
        agent.SCHEDULE_RUN_STATE_FILE = state / "schedule_run_status.json"
        agent.GENERIC_ISSUE_DIR = issues
        c = copy.deepcopy(old["cfg"])
        c.update({
            "enable_bark": False,
            "maa_schedule": {},
            "maa_instance_dirs": {},
            "maa_gui_config_paths": {},
            "schedule_override_file": "state/schedule_overrides.json",
            "generic_error_stall_minutes": 6,
            "connection_error_stall_minutes": 5,
            "scheduled_match_before_minutes": 2,
            "scheduled_match_after_minutes": 10,
            "process_missing_sustain_minutes": 0,
            "startup_process_grace_minutes": 0,
        })
        if extra_cfg:
            c.update(extra_cfg)
        agent.cfg = c
        try:
            yield base
        finally:
            for k, v in old.items():
                setattr(agent, k, v)


class FrozenDateTime(datetime):
    current = datetime(2026, 9, 20, 10, 30, 0)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.current
        return tz.fromutc(cls.current.replace(tzinfo=tz))


class ScheduleUtilsTests(unittest.TestCase):
    def test_reads_main_config_and_deduplicates_same_clock_time(self):
        with tempfile.TemporaryDirectory() as td:
            main = Path(td) / "gui.new.json"
            main.write_text(json.dumps(timer_root([
                {"Id": 0, "IsEnabled": True, "Config": "A", "Hour": 4, "Minute": 0},
                {"Id": 1, "IsEnabled": True, "Config": "B", "Hour": 4, "Minute": 0},
                {"Id": 2, "IsEnabled": False, "Config": "C", "Hour": 5, "Minute": 0},
                {"Id": 3, "IsEnabled": None, "Config": "D", "Hour": 6, "Minute": 30},
            ])), encoding="utf-8")
            cfg = {"maa_gui_config_paths": {"MAA1": str(main)}, "maa_schedule": {}}
            schedule, meta = schedule_utils.read_maa_timer_schedule(cfg)
            self.assertEqual([x["time"] for x in schedule["MAA1"]], ["04:00", "06:30"])
            self.assertFalse(schedule["MAA1"][0]["one_time"])
            self.assertTrue(schedule["MAA1"][1]["one_time"])
            self.assertEqual(meta["MAA1"]["source"], "maa_gui")

    def test_reads_backup_when_main_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            main = Path(td) / "gui.new.json"
            backup = Path(str(main) + ".bak")
            backup.write_text(json.dumps(timer_root([
                {"Id": 7, "IsEnabled": True, "Config": "A", "Hour": 12, "Minute": 5}
            ])), encoding="utf-8")
            cfg = {"maa_gui_config_paths": {"MAA1": str(main)}, "maa_schedule": {"MAA1": ["01:00"]}}
            schedule, meta = schedule_utils.read_maa_timer_schedule(cfg)
            self.assertEqual(schedule["MAA1"][0]["time"], "12:05")
            self.assertEqual(meta["MAA1"]["source"], "maa_gui_bak")

    def test_reads_backup_when_main_is_corrupt(self):
        with tempfile.TemporaryDirectory() as td:
            main = Path(td) / "gui.new.json"
            main.write_text("{broken", encoding="utf-8")
            backup = Path(str(main) + ".bak")
            backup.write_text(json.dumps(timer_root([
                {"Id": 1, "IsEnabled": True, "Config": "A", "Hour": 8, "Minute": 15}
            ])), encoding="utf-8")
            cfg = {"maa_gui_config_paths": {"MAA1": str(main)}, "maa_schedule": {}}
            schedule, meta = schedule_utils.read_maa_timer_schedule(cfg)
            self.assertEqual(schedule["MAA1"][0]["time"], "08:15")
            self.assertEqual(meta["MAA1"]["source"], "maa_gui_bak")

    def test_falls_back_when_both_maa_configs_unreadable(self):
        with tempfile.TemporaryDirectory() as td:
            main = Path(td) / "gui.new.json"
            main.write_text("not-json", encoding="utf-8")
            cfg = {"maa_gui_config_paths": {"MAA1": str(main)}, "maa_schedule": {"MAA1": ["04:00", "10:00"]}}
            schedule, meta = schedule_utils.read_maa_timer_schedule(cfg)
            self.assertEqual([x["time"] for x in schedule["MAA1"]], ["04:00", "10:00"])
            self.assertEqual(meta["MAA1"]["source"], "fallback")

    def test_one_time_timer_just_missed_stays_visible_to_monitoring_window(self):
        with tempfile.TemporaryDirectory() as td, patch.object(schedule_utils, "datetime", FrozenDateTime):
            main = Path(td) / "gui.new.json"
            main.write_text(json.dumps(timer_root([
                {"Id": 3, "IsEnabled": None, "Config": "A", "Hour": 10, "Minute": 0}
            ])), encoding="utf-8")
            cfg = {"maa_gui_config_paths": {"MAA1": str(main)}, "maa_schedule": {}}
            rows, _ = schedule_utils.base_occurrences(Path(td), cfg, datetime(2026, 9, 20, 8, 0), datetime(2026, 9, 20, 10, 31))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["datetime"], datetime(2026, 9, 20, 10, 0))

    def test_one_time_timer_future_only_window_resolves_to_next_day(self):
        with tempfile.TemporaryDirectory() as td, patch.object(schedule_utils, "datetime", FrozenDateTime):
            main = Path(td) / "gui.new.json"
            main.write_text(json.dumps(timer_root([
                {"Id": 3, "IsEnabled": None, "Config": "A", "Hour": 10, "Minute": 0}
            ])), encoding="utf-8")
            cfg = {"maa_gui_config_paths": {"MAA1": str(main)}, "maa_schedule": {}}
            rows, _ = schedule_utils.base_occurrences(Path(td), cfg, datetime(2026, 9, 20, 11, 0), datetime(2026, 9, 21, 12, 0))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["datetime"], datetime(2026, 9, 21, 10, 0))


    def test_move_override_more_than_one_day_does_not_disappear(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            cfg={"maa_schedule":{"MAA1":["22:00"]},"schedule_override_file":"state/overrides.json","schedule_override_keep_days":3650}
            original=datetime(2026,9,20,22,0)
            target=datetime(2026,9,25,23,30)
            schedule_utils.add_override(base,cfg,"MAA1",original,"move",target)
            rows,_=schedule_utils.effective_occurrences(base,cfg,datetime(2026,9,25,20),datetime(2026,9,26,1))
            moved=[r for r in rows if r["original_datetime"]==original]
            self.assertEqual(len(moved),1)
            self.assertEqual(moved[0]["datetime"],target)
            # The regular Sep-25 recurring 22:00 occurrence still legitimately exists.
            self.assertTrue(any(r["original_datetime"]==datetime(2026,9,25,22,0) for r in rows))

    def test_skip_and_cross_midnight_move_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cfg = {"maa_schedule": {"MAA1": ["22:00"]}, "schedule_override_file": "state/overrides.json", "schedule_override_keep_days": 3650}
            original = datetime(2026, 9, 20, 22, 0)
            schedule_utils.add_override(base, cfg, "MAA1", original, "move", datetime(2026, 9, 21, 1, 30))
            rows, _ = schedule_utils.effective_occurrences(base, cfg, datetime(2026, 9, 20, 20), datetime(2026, 9, 21, 2))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["datetime"], datetime(2026, 9, 21, 1, 30))
            schedule_utils.add_override(base, cfg, "MAA1", original, "skip")
            rows, _ = schedule_utils.effective_occurrences(base, cfg, datetime(2026, 9, 20, 20), datetime(2026, 9, 21, 2), include_skipped=True)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["skipped"])


class AgentLogicTests(unittest.TestCase):
    def test_parse_realistic_callback_line(self):
        line = ('[2026-06-27 04:32:36.909][INF][Px1][Tx2] Assistant::append_callback | '
                'SubTaskStart {"class":"asst::StopGameTaskPlugin","details":{},"subtask":"StopGameTask",'
                '"taskchain":"CloseDown","taskid":46}')
        rows = agent.parse_maa_records(line)
        self.assertEqual(rows[0]["callback"], "SubTaskStart")
        self.assertEqual(rows[0]["chain"], "CloseDown")
        self.assertEqual(rows[0]["time"], datetime(2026, 6, 27, 4, 32, 36, 909000))

    def test_repeated_errors_do_not_reset_stall_clock(self):
        with isolated_agent() as _:
            d = {"category":"task_failed","title":"任务链持续失败","task":"自动清理智","happened":"自动清理智失败。","likely":"测试","action":"检查"}
            first = datetime.fromtimestamp(time.time() - 7 * 60)
            recent = datetime.fromtimestamp(time.time() - 30)
            agent.queue_pending_issue("MAA1", d, "Fight", first)
            agent.queue_pending_issue("MAA1", d, "Fight", recent)
            calls = []
            with patch.object(agent, "bark", side_effect=lambda *a, **k: calls.append((a,k))):
                agent.evaluate_pending_issues("MAA1")
            self.assertEqual(len(calls), 1, "continuous repeated errors must alert after first unresolved error exceeds threshold")
            pending = agent.load_pending_issues("MAA1")["issues"]
            self.assertTrue(next(iter(pending.values()))["alerted"])

    def test_new_task_id_rearms_an_unresolved_failure(self):
        with isolated_agent():
            d = {"category":"task_failed","title":"任务执行失败","task":"自动清理智","happened":"自动清理智失败。","likely":"测试","action":"检查"}
            first = datetime.now() - timedelta(hours=13)
            agent.queue_pending_issue("MAA2", d, "Fight", first, {"taskid":160,"uuid":"device"})
            state = agent.load_pending_issues("MAA2")
            state["issues"][agent.pending_signature(d,"Fight")]["alerted"] = True
            agent.save_pending_issues("MAA2", state)
            second = datetime.now()
            rec = agent.queue_pending_issue("MAA2", d, "Fight", second, {"taskid":168,"uuid":"device"})
            self.assertFalse(rec["alerted"])
            self.assertAlmostEqual(rec["first_error"], second.timestamp(), delta=1)
            calls=[]
            with patch.object(agent, "bark", side_effect=lambda *a,**k: calls.append((a,k))):
                agent.evaluate_pending_issues("MAA2")
            self.assertEqual(len(calls),1)
            self.assertTrue(next(iter(agent.load_pending_issues("MAA2")["issues"].values()))["alerted"])

    def test_only_same_chain_completion_resolves_task_failure(self):
        with isolated_agent():
            d = {"category":"task_failed","title":"任务链持续失败","task":"自动清理智","happened":"自动清理智失败。","likely":"测试","action":"检查"}
            agent.queue_pending_issue("MAA1", d, "Fight", datetime.now() - timedelta(minutes=7))
            sig = agent.pending_signature(d, "Fight")
            agent.add_incident("task_failed", "自动清理智失败", label="MAA1", key=f"maa_issue|MAA1|{sig}")
            pending = agent.load_pending_issues("MAA1")
            pending["issues"][sig]["alerted"] = True
            agent.save_pending_issues("MAA1", pending)
            agent.resolve_pending_on_progress("MAA1", datetime.now(), "TaskChainStart", "Recruit")
            self.assertTrue(agent.load_pending_issues("MAA1")["issues"], "unrelated progress must not erase a failed chain")
            agent.resolve_pending_on_progress("MAA1", datetime.now(), "AllTasksCompleted", "CloseDown")
            self.assertTrue(agent.load_pending_issues("MAA1")["issues"], "queue completion is not task success")
            agent.resolve_pending_on_progress("MAA1", datetime.now(), "TaskChainCompleted", "Fight")
            self.assertFalse(agent.load_pending_issues("MAA1")["issues"])
            self.assertFalse(agent.incident_is_open(f"maa_issue|MAA1|{sig}"))

    def test_task_chain_error_alerts_even_when_later_tasks_continue(self):
        with isolated_agent({"enable_bark": True}):
            now = datetime.now()
            text = "\n".join([
                f'[{now:%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainError {{"taskchain":"StartUp","taskid":1,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=1)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainStart {{"taskchain":"Recruit","taskid":2,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=2)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | AllTasksCompleted {{"taskchain":"Recruit","finished_tasks":[1,2],"uuid":"device"}}',
            ])
            sent=[]
            with patch.object(agent, "send_bark_payload", side_effect=lambda payload: sent.append(payload) or True):
                agent.process_log_text("MAA2", Path("asst.log"), text)
            self.assertEqual(len(sent), 1)
            self.assertIn("启动", sent[0]["body"])
            self.assertTrue(agent.load_pending_issues("MAA2")["issues"])

    def test_terminal_connection_failure_is_not_downgraded_to_retry(self):
        with isolated_agent({"enable_bark": True}):
            now = datetime.now()
            text = "\n".join([
                f'[{now:%Y-%m-%d %H:%M:%S}.000][ERR] controller is unavailable: device offline',
                f'[{(now+timedelta(seconds=1)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainError {{"taskchain":"Fight","taskid":8,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=2)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainStart {{"taskchain":"Award","taskid":9,"uuid":"device"}}',
            ])
            sent=[]
            with patch.object(agent,"send_bark_payload",side_effect=lambda payload: sent.append(payload) or True):
                agent.process_log_text("MAA1",Path("asst.log"),text)
            self.assertEqual(len(sent),1)
            self.assertIn("ADB",sent[0]["title"]+sent[0]["body"])
            self.assertTrue(agent.load_pending_issues("MAA1")["issues"])

    def test_subtask_error_is_silent_when_chain_later_completes(self):
        with isolated_agent({"enable_bark": True}):
            now = datetime.now()
            text = "\n".join([
                f'[{now:%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | SubTaskError {{"taskchain":"Fight","taskid":3,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=1)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainCompleted {{"taskchain":"Fight","taskid":3,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=2)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | AllTasksCompleted {{"taskchain":"Fight","finished_tasks":[3],"uuid":"device"}}',
            ])
            sent=[]
            with patch.object(agent, "send_bark_payload", side_effect=lambda payload: sent.append(payload) or True):
                agent.process_log_text("MAA1", Path("asst.log"), text)
            self.assertEqual(len(sent), 0)
            self.assertFalse(agent.load_pending_issues("MAA1")["issues"])

    def test_schedule_run_state_completes_after_queue_finishes(self):
        with isolated_agent():
            now=datetime.now().replace(second=0,microsecond=0)
            row={"label":"MAA1","datetime":now,"source":"test"}
            text="\n".join([
                f'[{now:%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainStart {{"taskchain":"StartUp","taskid":10,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=1)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | SubTaskError {{"taskchain":"StartUp","taskid":10,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=2)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainCompleted {{"taskchain":"StartUp","taskid":10,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=3)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | AllTasksCompleted {{"finished_tasks":[10],"taskchain":"StartUp","taskid":10,"uuid":"device"}}',
            ])
            with patch.object(agent,"match_schedule_start",return_value=row):
                agent.process_log_text("MAA1",Path("asst.log"),text)
            rec=agent.load_schedule_run_state()["runs"][agent.schedule_run_key("MAA1",now)]
            self.assertEqual(rec["status"],"completed")
            self.assertNotIn("error",rec)

    def test_schedule_run_failure_survives_later_queue_completion(self):
        with isolated_agent():
            now=datetime.now().replace(second=0,microsecond=0)
            row={"label":"MAA2","datetime":now,"source":"test"}
            text="\n".join([
                f'[{now:%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainStart {{"taskchain":"Fight","taskid":20,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=1)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainError {{"taskchain":"Fight","taskid":20,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=2)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | AllTasksCompleted {{"finished_tasks":[20],"taskchain":"Fight","taskid":20,"uuid":"device"}}',
            ])
            with patch.object(agent,"match_schedule_start",return_value=row):
                agent.process_log_text("MAA2",Path("asst.log"),text)
            rec=agent.load_schedule_run_state()["runs"][agent.schedule_run_key("MAA2",now)]
            self.assertEqual(rec["status"],"failed")
            self.assertEqual(rec["error"]["task"],"自动清理智")
            self.assertIn("正常完成",rec["error"]["summary"])

    def test_offlineconfirm_in_candidate_list_is_not_a_failure(self):
        with isolated_agent({"enable_bark": True}):
            now = datetime.now()
            text = "\n".join([
                f'[{now:%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainStart {{"taskchain":"StartUp","taskid":214,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=1)):%Y-%m-%d %H:%M:%S}.000][INF] {{"details":{{"to_be_recognized":["StartUp@OfflineConfirm","StartButton"]}},"taskchain":"StartUp","taskid":214}}',
                f'[{(now+timedelta(seconds=2)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainCompleted {{"taskchain":"StartUp","taskid":214,"uuid":"device"}}',
            ])
            sent=[]
            with patch.object(agent,"send_bark_payload",side_effect=lambda payload: sent.append(payload) or True):
                agent.process_log_text("MAA2",Path("asst.log"),text)
            self.assertEqual(sent,[])
            self.assertFalse(agent.load_pending_issues("MAA2")["issues"])

    def test_login_failure_task_chain_stopped_alerts(self):
        with isolated_agent({"enable_bark": True}):
            now=datetime.now()
            text="\n".join([
                f'[{now:%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainStart {{"taskchain":"StartUp","taskid":206,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=1)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | SubTaskStart {{"class":"asst::ProcessTask","details":{{"task":"OfflineConfirm"}},"taskchain":"StartUp","taskid":0,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=2)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | SubTaskError {{"class":"asst::AccountSwitchTask","details":{{}},"taskchain":"StartUp","taskid":0,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=3)):%Y-%m-%d %H:%M:%S}.000][WRN] asst::StartUpTask::run | Login failed, entering game-restart loop',
                f'[{(now+timedelta(seconds=4)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainStopped {{"taskchain":"StartUp","taskid":206,"uuid":"device"}}',
            ])
            sent=[]
            with patch.object(agent,"send_bark_payload",side_effect=lambda payload: sent.append(payload) or True):
                agent.process_log_text("MAA2",Path("asst.log"),text)
            self.assertEqual(len(sent),1)
            self.assertIn("离线",sent[0]["title"]+sent[0]["body"])
            pending=agent.load_pending_issues("MAA2")["issues"]
            self.assertTrue(pending)
            self.assertTrue(all(x.get("alerted") for x in pending.values()))

    def test_copilot_failure_is_not_suppressed_when_optional_notice_is_off(self):
        with isolated_agent({"enable_bark":True,"copilot_notify_default":False}):
            now=datetime.now(); text=f'[{now:%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainStopped {{"taskchain":"Copilot","taskid":44,"uuid":"device"}}'
            sent=[]
            with patch.object(agent,"send_bark_payload",side_effect=lambda payload: sent.append(payload) or True):
                agent.process_log_text("MAA1",Path("asst.log"),text)
            self.assertEqual(len(sent),1)
            self.assertIn("自动战斗",sent[0]["body"])

    def test_explicit_failure_alerts_before_same_chain_later_recovers(self):
        with isolated_agent({"enable_bark": True}):
            now = datetime.now()
            text = "\n".join([
                f'[{now:%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainError {{"taskchain":"Fight","taskid":10,"uuid":"device"}}',
                f'[{(now+timedelta(seconds=1)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainCompleted {{"taskchain":"Fight","taskid":11,"uuid":"device"}}',
            ])
            sent=[]
            with patch.object(agent, "send_bark_payload", side_effect=lambda payload: sent.append(payload) or True):
                agent.process_log_text("MAA1", Path("asst.log"), text)
            self.assertEqual(len(sent), 1, "an explicit task failure must never be hidden by a later success in the same read batch")
            self.assertFalse(agent.load_pending_issues("MAA1")["issues"])

    def test_captured_eight_chain_failure_shape_and_later_manual_recovery(self):
        with isolated_agent({"enable_bark": True}):
            now=datetime.now(); chains=["StartUp","Recruit","Infrast","Mall","Fight","Award","OperBox","Depot"]
            failed=[f'[{(now+timedelta(seconds=i)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainError {{"taskchain":"{chain}","taskid":{156+i},"uuid":"device"}}' for i,chain in enumerate(chains)]
            failed.extend([
                f'[{(now+timedelta(seconds=9)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | AllTasksCompleted {{"taskchain":"Depot","finished_tasks":[156,157,158,159,160,161,162,163],"uuid":"device"}}',
                f'[{(now+timedelta(seconds=10)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainCompleted {{"taskchain":"CloseDown","taskid":164,"uuid":"device"}}',
            ])
            sent=[]
            with patch.object(agent,"send_bark_payload",side_effect=lambda payload: sent.append(payload) or True):
                agent.process_log_text("MAA2",Path("asst.log"),"\n".join(failed))
            self.assertEqual(len(sent),1,"one broken batch should notify without sending eight Bark messages")
            pending=agent.load_pending_issues("MAA2")["issues"]
            self.assertEqual({x.get("chain") for x in pending.values()},set(chains))

            recovered=[f'[{(now+timedelta(hours=2,seconds=i)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainCompleted {{"taskchain":"{chain}","taskid":{195+i},"uuid":"device"}}' for i,chain in enumerate(chains)]
            with patch.object(agent,"send_bark_payload",return_value=True):
                agent.process_log_text("MAA2",Path("asst.log"),"\n".join(recovered))
            self.assertFalse(agent.load_pending_issues("MAA2")["issues"],"a later successful manual run should resolve each matching chain")

    def test_observed_run_removes_unverified_bookkeeping(self):
        with isolated_agent():
            dt = datetime.now().replace(second=0, microsecond=0)
            data = agent.load_daily_stats()
            data["schedule"]["unverified"] = [f"MAA1 {dt:%Y-%m-%d %H:%M}"]
            agent.save_daily_stats(data)
            self.assertTrue(agent.mark_observed_run("MAA1", dt, actual_dt=dt + timedelta(minutes=1)))
            data = agent.load_daily_stats()
            self.assertEqual(data["schedule"]["unverified"], [])
            self.assertTrue(agent.is_observed_occurrence("MAA1", dt, data))

    def test_expected_count_includes_matched_one_time_run_even_if_live_schedule_is_gone(self):
        with isolated_agent({"maa_schedule": {}}):
            now = datetime.now().replace(second=0, microsecond=0)
            dt = now.replace(hour=max(0, now.hour - 1), minute=0)
            if dt > now:
                dt -= timedelta(days=1)
            # Keep it on today's date for this assertion.
            if dt.date() != now.date():
                dt = now.replace(hour=0, minute=0)
            data = agent.load_daily_stats()
            data.setdefault("observed_runs", {}).setdefault("MAA1", []).append({"occurrence": dt.strftime("%Y-%m-%d %H:%M")})
            agent.save_daily_stats(data)
            self.assertGreaterEqual(agent.expected_schedule_count_today(now), 1)

    def test_skipped_override_is_not_marked_observed(self):
        with isolated_agent({"maa_schedule": {"MAA1": ["10:00"]}, "schedule_override_keep_days":3650}) as base:
            dt = datetime(2026, 9, 20, 10, 0)
            schedule_utils.add_override(base, agent.cfg, "MAA1", dt, "skip")
            row = agent.match_schedule_start("MAA1", dt + timedelta(minutes=1), chain="Fight")
            self.assertIsNone(row)
            self.assertFalse(agent.is_observed_occurrence("MAA1", dt))


    def test_schedule_start_within_miss_grace_still_matches(self):
        with isolated_agent({
            "maa_schedule":{"MAA1":["10:00"]},
            "scheduled_match_after_minutes":10,
            "scheduled_run_grace_minutes":20,
        }):
            scheduled=datetime(2026,9,20,10,0)
            row=agent.match_schedule_start("MAA1",scheduled+timedelta(minutes=15),chain="Fight")
            self.assertIsNotNone(row)
            self.assertTrue(agent.is_observed_occurrence("MAA1",scheduled))

    def test_schedule_match_marks_recurring_occurrence(self):
        with isolated_agent({"maa_schedule": {"MAA1": ["10:00"]}}):
            dt = datetime(2026, 9, 20, 10, 0)
            row = agent.match_schedule_start("MAA1", dt + timedelta(minutes=3), chain="Fight")
            self.assertIsNotNone(row)
            self.assertTrue(agent.is_observed_occurrence("MAA1", dt))

    def test_schedule_cache_recovers_disappeared_one_time_timer(self):
        with isolated_agent({"maa_schedule": {}}):
            actual = datetime.now().replace(second=0, microsecond=0)
            scheduled = actual - timedelta(minutes=2)
            cache = {
                "saved_at": agent.now_str(),
                "schedule": {"MAA1": [{"time": scheduled.strftime("%H:%M"), "one_time": True, "source":"maa_gui"}]},
                "meta": {},
            }
            agent.SCHEDULE_CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
            row = agent.match_schedule_start("MAA1", actual, chain="Fight")
            self.assertIsNotNone(row)
            self.assertTrue(agent.is_observed_occurrence("MAA1", row["datetime"]))

    def test_unknown_process_prevents_false_named_instance_alert(self):
        with isolated_agent({"maa_instance_dirs":{"MAA1":"C:/MAA1","MAA2":"C:/MAA2"}}):
            p1, p2 = object(), object()
            calls=[]
            with patch.object(agent, "startup_process_grace_active", return_value=False), \
                 patch.object(agent, "find_processes", return_value=[p1,p2]), \
                 patch.object(agent, "detect_maa_instance_processes", return_value=({"MAA1":[p1],"MAA2":[]},[p2])), \
                 patch.object(agent, "bark", side_effect=lambda *a,**k: calls.append((a,k))):
                agent.check_processes()
            self.assertEqual(calls, [], "an unknown second MAA process means we cannot safely name MAA2 as missing")

    def test_exact_mapping_can_name_missing_instance(self):
        with isolated_agent({"maa_instance_dirs":{"MAA1":"C:/MAA1","MAA2":"C:/MAA2"}}):
            p1 = object(); calls=[]
            with patch.object(agent, "startup_process_grace_active", return_value=False), \
                 patch.object(agent, "find_processes", return_value=[p1]), \
                 patch.object(agent, "detect_maa_instance_processes", return_value=({"MAA1":[p1],"MAA2":[]},[])), \
                 patch.object(agent, "bark", side_effect=lambda *a,**k: calls.append((a,k))):
                agent.check_processes()
            self.assertEqual(len(calls), 1)
            self.assertIn("MAA2", calls[0][0][0])

    def test_low_level_error_followed_by_progress_does_not_leave_pending(self):
        with isolated_agent():
            now = datetime.now()
            text = "\n".join([
                f"[{now:%Y-%m-%d %H:%M:%S}.000][ERR] connect failed: device offline",
                f"[{(now+timedelta(seconds=5)):%Y-%m-%d %H:%M:%S}.000][INF] Assistant::append_callback | TaskChainStart {{\"taskchain\":\"Fight\"}}",
            ])
            with patch.object(agent, "bark", return_value=None):
                agent.process_log_text("MAA1", Path("dummy.log"), text)
            self.assertFalse(agent.load_pending_issues("MAA1")["issues"])

    def test_login_failure_text_waits_for_terminal_chain_result(self):
        with isolated_agent({"enable_bark":True}):
            now=datetime.now(); text=f'[{now:%Y-%m-%d %H:%M:%S}.000][WRN] asst::StartUpTask::run | Login failed, entering game-restart loop'
            sent=[]
            with patch.object(agent,"send_bark_payload",side_effect=lambda payload: sent.append(payload) or True):
                agent.process_log_text("MAA2",Path("asst.log"),text)
            self.assertEqual(sent,[])
            self.assertFalse(agent.load_pending_issues("MAA2")["issues"])

    def test_restart_preserves_log_offset_and_reads_downtime_append(self):
        with isolated_agent() as base:
            source=base/"MAA1"/"debug"/"asst.log"; source.parent.mkdir(parents=True)
            source.write_text("old line\n",encoding="utf-8")
            agent.cfg["maa_log_paths"]=[str(source)]; agent.cfg["skip_old_logs_on_start"]=True
            agent.initialize_log_offsets_on_start()
            source.write_text("old line\nnew while agent stopped\n",encoding="utf-8")
            agent.initialize_log_offsets_on_start()
            self.assertEqual(agent.read_new_log_text(source),"new while agent stopped\n")

    def test_deepseek_only_when_useful(self):
        with isolated_agent():
            data = agent.load_daily_stats()
            self.assertFalse(agent.deepseek_daily_worth_calling(data))
            data["memory"]["pressure_minutes"] = 31
            self.assertTrue(agent.deepseek_daily_worth_calling(data))



    def test_manual_skip_resolves_existing_schedule_incident(self):
        with isolated_agent({"maa_schedule":{"MAA1":["10:00"]},"enable_scheduled_run_check":True,"scheduled_run_grace_minutes":20,"schedule_override_keep_days":3650}) as base:
            FrozenDateTime.current=datetime(2026,9,20,10,30)
            key="schedule|MAA1|2026-09-20 10:00"
            with patch.object(agent,"datetime",FrozenDateTime):
                agent.add_incident("scheduled_run_missing","MAA1 10:00 未观测到实际启动",label="MAA1",key=key)
                schedule_utils.add_override(base,agent.cfg,"MAA1",datetime(2026,9,20,10,0),"skip")
                with patch.object(agent,"schedule_monitoring_coverage_start",return_value=datetime(2026,9,20,9,0)), \
                     patch.object(agent,"bark",return_value=None):
                    agent.check_scheduled_runs()
            self.assertFalse(agent.incident_is_open(key))

    def test_manual_move_resolves_old_incident_but_keeps_new_target_expected(self):
        with isolated_agent({"maa_schedule":{"MAA1":["10:00"]},"enable_scheduled_run_check":True,"scheduled_run_grace_minutes":20,"schedule_override_keep_days":3650}) as base:
            FrozenDateTime.current=datetime(2026,9,20,10,30)
            original=datetime(2026,9,20,10,0); target=datetime(2026,9,20,11,0)
            key="schedule|MAA1|2026-09-20 10:00"
            with patch.object(agent,"datetime",FrozenDateTime):
                agent.add_incident("scheduled_run_missing","old",label="MAA1",key=key)
                schedule_utils.add_override(base,agent.cfg,"MAA1",original,"move",target)
                with patch.object(agent,"schedule_monitoring_coverage_start",return_value=datetime(2026,9,20,9,0)), \
                     patch.object(agent,"bark",return_value=None):
                    agent.check_scheduled_runs()
            self.assertFalse(agent.incident_is_open(key))
            rows,_=schedule_utils.effective_occurrences(base,agent.cfg,datetime(2026,9,20,10,30),datetime(2026,9,20,11,30))
            self.assertTrue(any(r["datetime"]==target for r in rows))

    def test_missed_schedule_after_grace_alerts_when_agent_covered_time(self):
        with isolated_agent({"maa_schedule":{"MAA1":["10:00"]},"enable_scheduled_run_check":True,"scheduled_run_grace_minutes":20}):
            FrozenDateTime.current = datetime(2026, 9, 20, 10, 30)
            calls=[]
            with patch.object(agent, "datetime", FrozenDateTime), \
                 patch.object(agent, "schedule_monitoring_coverage_start", return_value=datetime(2026,9,20,9,0)), \
                 patch.object(agent, "bark", side_effect=lambda *a,**k: calls.append((a,k))):
                agent.check_scheduled_runs()
            self.assertEqual(len(calls),1)
            self.assertIn("计划任务可能没跑", calls[0][0][0])

    def test_schedule_before_agent_coverage_is_unverified_not_alerted(self):
        with isolated_agent({"maa_schedule":{"MAA1":["10:00"]},"enable_scheduled_run_check":True,"scheduled_run_grace_minutes":20}):
            FrozenDateTime.current = datetime(2026, 9, 20, 10, 30)
            calls=[]
            with patch.object(agent, "datetime", FrozenDateTime), \
                 patch.object(agent, "schedule_monitoring_coverage_start", return_value=datetime(2026,9,20,10,15)), \
                 patch.object(agent, "bark", side_effect=lambda *a,**k: calls.append((a,k))):
                agent.check_scheduled_runs()
                data=agent.load_daily_stats()
            self.assertEqual(calls,[])
            self.assertTrue(any("MAA1 2026-09-20 10:00" == x for x in data["schedule"]["unverified"]))

    def test_observed_schedule_is_not_reported_missing(self):
        with isolated_agent({"maa_schedule":{"MAA1":["10:00"]},"enable_scheduled_run_check":True,"scheduled_run_grace_minutes":20}):
            FrozenDateTime.current = datetime(2026, 9, 20, 10, 30)
            with patch.object(agent, "datetime", FrozenDateTime):
                agent.mark_observed_run("MAA1",datetime(2026,9,20,10,0),actual_dt=datetime(2026,9,20,10,2))
                calls=[]
                with patch.object(agent, "schedule_monitoring_coverage_start", return_value=datetime(2026,9,20,9,0)), \
                     patch.object(agent, "bark", side_effect=lambda *a,**k: calls.append((a,k))):
                    agent.check_scheduled_runs()
            self.assertEqual(calls,[])

    def test_observed_schedule_before_midnight_is_found_after_daily_rollover(self):
        with isolated_agent({"maa_schedule":{"MAA1":["22:00"]},"enable_scheduled_run_check":True,"scheduled_run_grace_minutes":20}) as base:
            previous = {
                "date": "2026-09-21",
                "observed_runs": {"MAA1": [{"occurrence": "2026-09-21 22:00"}]},
            }
            (base / "state" / "daily_2026-09-21.json").write_text(
                json.dumps(previous), encoding="utf-8"
            )
            FrozenDateTime.current = datetime(2026, 9, 22, 0, 30)
            calls=[]
            with patch.object(agent, "datetime", FrozenDateTime), \
                 patch.object(agent, "schedule_monitoring_coverage_start", return_value=datetime(2026,9,21,21,0)), \
                 patch.object(agent, "bark", side_effect=lambda *a,**k: calls.append((a,k))):
                agent.check_scheduled_runs()
            self.assertEqual(calls,[])

    def test_live_timer_change_does_not_create_previous_day_miss(self):
        with isolated_agent({"maa_schedule":{"MAA1":["22:15"]},"maa_instance_dirs":{},"maa_gui_config_paths":{},"enable_scheduled_run_check":True,"scheduled_run_grace_minutes":20}):
            FrozenDateTime.current = datetime(2026, 9, 22, 0, 30)
            calls=[]
            with patch.object(agent, "datetime", FrozenDateTime), \
                 patch.object(agent, "schedule_monitoring_coverage_start", return_value=datetime(2026,9,21,21,0)), \
                 patch.object(agent, "bark", side_effect=lambda *a,**k: calls.append((a,k))):
                agent.check_scheduled_runs()
            self.assertEqual(calls,[])

    def test_future_timer_change_replaces_stale_unobserved_ledger_time(self):
        with isolated_agent({"maa_schedule":{"MAA1":["04:15"]},"maa_instance_dirs":{},"maa_gui_config_paths":{},"enable_scheduled_run_check":True,"scheduled_run_grace_minutes":20}):
            FrozenDateTime.current = datetime(2026, 9, 22, 4, 10)
            with patch.object(agent, "datetime", FrozenDateTime), \
                 patch.object(agent, "schedule_monitoring_coverage_start", return_value=datetime(2026,9,22,3,0)), \
                 patch.object(agent, "bark", return_value=None):
                data=agent.load_daily_stats()
                data["schedule"]["expected_occurrences"]=["MAA1|2026-09-22 04:00"]
                agent.save_daily_stats(data)
                agent.check_scheduled_runs()
                ledger=agent.load_daily_stats()["schedule"]["expected_occurrences"]
                self.assertNotIn("MAA1|2026-09-22 04:00",ledger)
                self.assertNotIn("MAA1|2026-09-22 04:15",ledger)

    def test_skipped_schedule_is_not_reported_missing(self):
        with isolated_agent({"maa_schedule":{"MAA1":["10:00"]},"enable_scheduled_run_check":True,"scheduled_run_grace_minutes":20,"schedule_override_keep_days":3650}) as base:
            schedule_utils.add_override(base,agent.cfg,"MAA1",datetime(2026,9,20,10,0),"skip")
            FrozenDateTime.current = datetime(2026, 9, 20, 10, 30)
            calls=[]
            with patch.object(agent, "datetime", FrozenDateTime), \
                 patch.object(agent, "schedule_monitoring_coverage_start", return_value=datetime(2026,9,20,9,0)), \
                 patch.object(agent, "bark", side_effect=lambda *a,**k: calls.append((a,k))):
                agent.check_scheduled_runs()
                data=agent.load_daily_stats()
            self.assertEqual(calls,[])
            self.assertTrue(data["schedule"]["skipped_overrides"])


    def test_daily_report_render_and_send_smoke(self):
        with isolated_agent({"enable_daily_report":True,"deepseek_daily_report":False,"enable_scheduled_run_check":False}):
            calls=[]
            with patch.object(agent,"bark_force",side_effect=lambda *a,**k:calls.append((a,k))), \
                 patch.object(agent,"deepseek_daily_report",return_value=None):
                text=agent.local_daily_report_text()
                compact=agent.compact_daily_report_for_bark(text,None)
                sent=agent.daily_report()
            self.assertIn("系统内存",text)
            self.assertIn("关键数据",compact)
            self.assertTrue(sent)
            self.assertEqual(len(calls),1)
            self.assertTrue(agent.daily_report_already_sent_today())

    def test_daily_report_due_after_target_minute_and_persists_sent_marker(self):
        with isolated_agent({"daily_report_time":"23:50","enable_daily_report":True}):
            FrozenDateTime.current = datetime(2026,9,20,23,51)
            with patch.object(agent,"datetime",FrozenDateTime):
                self.assertTrue(agent.daily_report_due())
                agent.mark_daily_report_sent_today()
                self.assertFalse(agent.daily_report_due())

    def test_daily_report_skip_blocks_due_even_after_target(self):
        with isolated_agent({"daily_report_time":"23:50","enable_daily_report":True}):
            FrozenDateTime.current = datetime(2026,9,20,23,59)
            with patch.object(agent,"datetime",FrozenDateTime):
                agent.DAILY_REPORT_SKIP_FILE.write_text("2026-09-20",encoding="utf-8")
                self.assertFalse(agent.daily_report_due())

    def test_proxy_http_407_counts_as_failure_and_can_recover(self):
        with isolated_agent({
            "enable_proxy_healthcheck":True,
            "proxy_healthcheck_proxy_url":"http://127.0.0.1:7890",
            "proxy_failure_sustain_minutes":10,
            "proxy_healthcheck_interval_minutes":1,
        }):
            old=time.time()-11*60
            agent.NETWORK_HEALTH_FILE.write_text(json.dumps({"failure_since":old,"last_check":0,"alerted":False}),encoding="utf-8")
            class Resp:
                def __init__(self,status): self.status_code=status
            calls=[]
            with patch.object(agent.requests,"get",return_value=Resp(407)), \
                 patch.object(agent,"bark",side_effect=lambda *a,**k:calls.append((a,k))):
                agent.check_proxy_health()
            self.assertEqual(len(calls),1)
            self.assertTrue(agent.incident_is_open("network|proxy"))
            state=json.loads(agent.NETWORK_HEALTH_FILE.read_text(encoding="utf-8")); state["last_check"]=0
            agent.NETWORK_HEALTH_FILE.write_text(json.dumps(state),encoding="utf-8")
            with patch.object(agent.requests,"get",return_value=Resp(204)):
                agent.check_proxy_health()
            self.assertFalse(agent.incident_is_open("network|proxy"))

    def test_disabled_proxy_healthcheck_does_not_read_or_probe_proxy(self):
        with isolated_agent({"enable_proxy_healthcheck":False}), \
             patch.object(agent,"windows_system_http_proxy",side_effect=AssertionError("proxy settings must not be read")), \
             patch.object(agent.requests,"get",side_effect=AssertionError("network probe must not run")):
            agent.check_proxy_health()
            self.assertFalse(agent.NETWORK_HEALTH_FILE.exists())

    def test_startup_snapshot_respects_disabled_proxy_healthcheck(self):
        class Mem:
            percent=42
            available=32*1024*1024*1024
            total=64*1024*1024*1024
        with isolated_agent({"enable_proxy_healthcheck":False,"maa_instance_dirs":{}}), \
             patch.object(agent,"find_processes",side_effect=[[object(),object()],[object()]]), \
             patch.object(agent,"detect_maa_instance_processes",return_value=({},[])), \
             patch.object(agent.psutil,"virtual_memory",return_value=Mem()), \
             patch.object(agent,"windows_system_http_proxy",side_effect=AssertionError("proxy settings must not be read")):
            text=agent.startup_status_text()
        self.assertIn("健康检查已关闭",text)

    def test_heartbeat_http_error_is_not_recorded_as_ok(self):
        with isolated_agent({"heartbeat_url":"https://example.invalid/ping","heartbeat_interval_minutes":1}):
            class Resp: status_code=500
            with patch.object(agent.requests,"get",return_value=Resp()):
                agent.send_external_heartbeat_if_due()
            state=json.loads(agent.HEARTBEAT_STATE_FILE.read_text(encoding="utf-8"))
            self.assertNotIn("last_ok",state)
            self.assertEqual(state.get("last_error"),"HTTP 500")



    def test_recoverable_issues_are_aggregated_not_spammed(self):
        with isolated_agent():
            agent.add_recoverable_issue("subtask_retry","自动清理智一次重试",label="MAA1",task="自动清理智")
            agent.add_recoverable_issue("subtask_retry","自动清理智又一次重试",label="MAA1",task="自动清理智")
            data=agent.load_daily_stats()
            self.assertEqual(len(data["recoverable_issues"]),1)
            self.assertEqual(data["recoverable_issues"][0]["occurrences"],2)
            self.assertEqual(agent.recoverable_issue_total(data),2)

    def test_special_notice_is_humanized_and_deduplicated(self):
        with isolated_agent():
            now=datetime.now()
            text="\n".join([
                f"[{now:%Y-%m-%d %H:%M:%S}.000][INF] GameStartUpdateOCR",
                f"[{now:%Y-%m-%d %H:%M:%S}.100][INF] 正在获取更新",
            ])
            agent.process_log_text("MAA1",Path("dummy.log"),text)
            notices=agent.load_daily_stats()["special_notices"]
            self.assertEqual(len(notices),1)
            self.assertEqual(notices[0]["message"],"检测到游戏更新流程")

    def test_bark_queue_retries_only_limited_items_per_loop(self):
        with isolated_agent({"bark_queue_retry_per_loop":3,"bark_queue_max_items":100}):
            now=agent.now_str()
            lines=[]
            for i in range(10):
                lines.append(json.dumps({"queued_at":now,"payload":{"title":f"t{i}","body":"x","device_key":"k","group":"g","level":"active"}}))
            agent.BARK_QUEUE_FILE.write_text("\n".join(lines)+"\n",encoding="utf-8")
            calls=[]
            with patch.object(agent,"send_bark_payload",side_effect=lambda payload,attempts=1: calls.append(payload) or False):
                agent.process_bark_queue()
            self.assertEqual(len(calls),3)
            remaining=agent.BARK_QUEUE_FILE.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(remaining),10)

    def test_daily_expected_count_includes_all_due_recurring_runs_at_2350(self):
        with isolated_agent({"maa_schedule":{"MAA1":["04:00","10:00","16:00","22:00"]}}):
            now=datetime.now().replace(hour=23,minute=50,second=0,microsecond=0)
            self.assertEqual(agent.expected_schedule_count_today(now),4)

    def test_forced_proxy_url_is_used_without_windows_registry(self):
        with isolated_agent({"proxy_healthcheck_proxy_url":"http://127.0.0.1:7890"}):
            self.assertEqual(agent.windows_system_http_proxy(), "http://127.0.0.1:7890")

    def test_agent_owned_relative_path_follows_current_copy(self):
        with isolated_agent({"copilot_notify_once_file":"state/copilot_notify.once"}) as base:
            self.assertEqual(agent.copilot_notify_once_path(), base / "state" / "copilot_notify.once")

    def test_startup_status_is_user_facing_not_policy_explanation(self):
        class Mem:
            percent=42
            available=32*1024*1024*1024
        with isolated_agent({"maa_instance_dirs":{}}), \
             patch.object(agent,"find_processes",side_effect=[[object(),object()],[object()]]), \
             patch.object(agent,"detect_maa_instance_processes",return_value=({},[])), \
             patch.object(agent.psutil,"virtual_memory",return_value=Mem()), \
             patch.object(agent,"windows_system_http_proxy",return_value="http://127.0.0.1:7890"):
            text=agent.startup_status_text()
        self.assertIn("初始状态",text)
        for forbidden in ("宽限","10 分钟","DeepSeek：","报警策略","TaskChain"):
            self.assertNotIn(forbidden,text)

    def test_cross_directory_agent_process_can_be_detected(self):
        class FakeProc:
            pid=99991
            info={"pid":99991,"name":"python.exe"}
            def name(self): return "python.exe"
            def cmdline(self): return ["python",str(Path(tempfile.gettempdir())/"MAA-Agent_backup"/"agent.py")]
            def cwd(self): return tempfile.gettempdir()
            def create_time(self): return 100.0
        with patch.object(agent_runtime.psutil,"process_iter",return_value=[FakeProc()]):
            row=agent.find_other_maa_agent_process()
        self.assertIsNotNone(row)
        self.assertTrue(row["script"].lower().endswith("maa-agent_backup/agent.py") or row["script"].lower().endswith("maa-agent_backup\\agent.py"))

    def test_editor_with_agent_path_is_not_a_running_agent(self):
        class FakeEditor:
            pid=99992
            info={"pid":99992,"name":"Code.exe"}
            def name(self): return "Code.exe"
            def cmdline(self): return ["Code.exe",str(Path(tempfile.gettempdir())/"MAA-Agent_0920"/"agent.py")]
            def cwd(self): return tempfile.gettempdir()
            def create_time(self): return 100.0
        with patch.object(agent_runtime.psutil,"process_iter",return_value=[FakeEditor()]):
            row=agent.find_other_maa_agent_process()
        self.assertIsNone(row)

    def test_powershell_text_argument_is_not_a_running_agent(self):
        path=str(Path(tempfile.gettempdir())/"MAA-Agent_0920"/"agent.py")
        self.assertIsNone(agent_runtime.script_from_cmdline(["pwsh.exe","-Command",path],tempfile.gettempdir(),"pwsh.exe"))

    def test_virtualenv_launcher_can_be_explicitly_excluded(self):
        class FakeLauncher:
            pid=99993
            info={"pid":99993,"name":"pythonw.exe"}
            def name(self): return "pythonw.exe"
            def cmdline(self): return ["pythonw.exe",str(Path(tempfile.gettempdir())/"MAA-Agent_0920"/"agent.py")]
            def cwd(self): return tempfile.gettempdir()
            def create_time(self): return 100.0
            def ppid(self): return 1
        with patch.object(agent_runtime.psutil,"process_iter",return_value=[FakeLauncher()]):
            rows=agent_runtime.find_running_agents(exclude_pids={99993})
        self.assertEqual(rows,[])

    def test_virtualenv_launcher_parent_is_deduplicated(self):
        script = str(Path(tempfile.gettempdir()) / "MAA-Agent_0920" / "app" / "agent.py")
        class FakeProc:
            def __init__(self, pid, ppid):
                self.pid = pid
                self._ppid = ppid
                self.info = {"pid": pid, "name": "pythonw.exe"}
            def name(self): return "pythonw.exe"
            def cmdline(self): return ["pythonw.exe", script]
            def cwd(self): return tempfile.gettempdir()
            def create_time(self): return 100.0
            def ppid(self): return self._ppid
        parent = FakeProc(99996, 1)
        child = FakeProc(99997, 99996)
        with patch.object(agent_runtime.psutil, "process_iter", return_value=[parent, child]):
            rows = agent_runtime.find_running_agents()
        self.assertEqual([row["pid"] for row in rows], [99997])

    def test_migrated_installation_accepts_legacy_root_agent(self):
        base = Path(tempfile.gettempdir()) / "MAA-Agent_migrated"
        legacy = base / "agent.py"
        row = {"pid": 99994, "name": "pythonw.exe", "script": str(legacy), "create_time": 100.0}
        with patch.object(agent_runtime, "read_runtime_record", return_value=None), \
             patch.object(agent_runtime, "find_running_agents", return_value=[row]):
            found = agent_runtime.validated_agent_for_dir(base)
        self.assertEqual(found, row)
        self.assertTrue(agent_runtime.is_script_for_installation(legacy, base))
        self.assertTrue(agent_runtime.is_script_for_installation(base / "app" / "agent.py", base))
        self.assertFalse(agent_runtime.is_script_for_installation(base.parent / "MAA-Agent_other" / "agent.py", base))

    def test_stop_revalidates_the_detected_legacy_script(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "MAA-Agent_migrated"
            base.mkdir()
            legacy = base / "agent.py"
            row = {"pid": 99995, "name": "pythonw.exe", "script": str(legacy), "create_time": 100.0}
            with patch.object(agent_runtime, "validated_agent_for_dir", return_value=row), \
                 patch.object(agent_runtime, "agent_process_info", return_value=None) as process_info:
                result = agent_runtime.request_stop(base, timeout_seconds=0.1)
            self.assertEqual(result.status, "stopped")
            process_info.assert_called_with(99995, legacy)

    def test_notification_drill_catalog_avoids_internal_jargon(self):
        import notification_drill
        cases=notification_drill.build_cases()
        self.assertEqual(len(cases),24)
        self.assertEqual(len({c.key for c in cases}),len(cases))
        combined="\n".join(c.title+"\n"+c.body for c in cases)
        for forbidden in ("TaskChainStart","TaskChainError","UUID：","PID：","超过宽限时间","判断依据："):
            self.assertNotIn(forbidden,combined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
