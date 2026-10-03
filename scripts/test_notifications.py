"""Notification regressions: real scheduler/domain code, isolated SQLite.

Run: python scripts/test_notifications.py
Fixtures remain in workspace tmp for inspection; production is untouched.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hive_core.auth import SYSTEM_SUBJECT
from hive_core.home import process_alive
from hive_core.runtime_ops import op_wake_agent, _queue_notification
from hive_core.scheduler import Scheduler, notify_new_task
from hive_core.service import Service
from hive_core.store import Store


class NotificationTests(unittest.TestCase):
    def setUp(self):
        base = ROOT.parents[1] / 'tmp' / 'hive-notification-regression'
        base.mkdir(parents=True, exist_ok=True)
        self.home = pathlib.Path(tempfile.mkdtemp(dir=base))
        self.svc = Service(Store(str(self.home / 'data/hive.db')),
                           home=str(self.home))
        self.now = '2026-10-03T01:00:00Z'
        self.svc.now = lambda: self.now
        self.svc.store.kv_set('policy', {'notify_jitter': 0})
        self.sent = []
        self.svc.harnesses = SimpleNamespace(send=self.send)
        self.scheduler = Scheduler(self.svc)
        self.agent('agt_a')
        self.port('prt_a')
        self.subscribe('agt_a', 'prt_a')

    def tearDown(self):
        self.svc.store.close()

    def send(self, *args, **kwargs):
        self.sent.append((args[1], args[4], args[5]))
        return 'accepted'

    def agent(self, aid):
        cfg = self.home / (aid + '.json')
        cfg.write_text(json.dumps({'runtime': {'auto_start': True},
                                  'limits': {'max_inflight_work': 3}}),
                       encoding='utf-8')
        with self.svc.store.write() as cur:
            cur.execute("INSERT INTO agents(agent_id,name,created_at,updated_at) "
                        "VALUES (?,?,?,?)", (aid, aid, self.now, self.now))
            cur.execute("INSERT INTO agent_config_versions(agent_id,revision,"
                        "file_path,published,created_at) VALUES (?,1,?,1,?)",
                        (aid, str(cfg), self.now))
            cur.execute("INSERT INTO instances(instance_id,agent_id,harness,"
                        "handle,config_revision,generation,phase,started_at) "
                        "VALUES (?,?,'fixture','fixture',1,1,'live',?)",
                        ('ins_' + aid, aid, self.now))

    def port(self, pid, handler='agents'):
        with self.svc.store.write() as cur:
            cur.execute("INSERT INTO ports(port_id,route,name,handler,"
                        "accepted_types,defaults,created_at,updated_at) "
                        "VALUES (?,?,?,?, '[]','{}',?,?)",
                        (pid, pid, pid, handler, self.now, self.now))

    def subscribe(self, aid, pid):
        with self.svc.store.write() as cur:
            cur.execute("INSERT INTO subscriptions(agent_id,port_id,created_at,"
                        "created_by) VALUES (?,?,?,'fixture')",
                        (aid, pid, self.now))

    def task(self, tid, target='port:prt_a', kind='work', notify=True):
        with self.svc.store.write() as cur:
            cur.execute("INSERT INTO tasks(task_id,root_id,type,title,source,"
                        "target,created_at,updated_at) VALUES (?,?,?,?, '{}',?,?,?)",
                        (tid, tid, kind, tid, target,
                         '2026-10-03T00:00:00Z', self.now))
            if notify:
                notify_new_task(self.svc, cur, tid, target, kind)

    def count(self, where='1=1'):
        return self.svc.store.conn.execute(
            'SELECT COUNT(*) FROM notifications WHERE ' + where).fetchone()[0]

    def test_second_task_after_sent_wakes_immediately(self):
        for target in ('port:prt_a', 'agent:agt_a'):
            first = 'tsk_first_' + target
            second = 'tsk_second_' + target
            self.task(first, target)
            self.scheduler.run_notifications()
            before = len(self.sent)
            self.task(second, target)
            self.scheduler.run_notifications()
            self.assertEqual(len(self.sent), before + 1)
        self.assertEqual(self.count("state='sent'"), 4)

    def test_same_task_retries_are_idempotent(self):
        self.task('tsk_a')
        self.scheduler.run_notifications()
        with self.svc.store.write() as cur:
            notify_new_task(self.svc, cur, 'tsk_a', 'port:prt_a', 'work')
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.count("state='pending'"), 0)

    def test_changed_routing_epoch_can_wake_again(self):
        self.task('tsk_a')
        self.scheduler.run_notifications()
        with self.svc.store.write() as cur:
            cur.execute("UPDATE tasks SET claim_epoch=1 WHERE task_id='tsk_a'")
            notify_new_task(self.svc, cur, 'tsk_a', 'port:prt_a', 'work')
        self.assertEqual(self.count("state='pending'"), 1)

    def test_burst_coalesces_but_keeps_all_durable_signals(self):
        for i in range(12):
            self.task(f'tsk_{i:02}')
        self.scheduler.run_notifications()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.count("state='sent'"), 12)

    def test_arrival_during_send_remains_pending(self):
        self.task('tsk_first')
        def send(*args, **kwargs):
            self.task('tsk_arrived_during_send')
            return 'accepted'
        self.svc.harnesses.send = send
        self.scheduler.run_notifications()
        self.assertEqual(self.count("state='sent'"), 1)
        self.assertEqual(self.count("state='pending'"), 1)
        self.svc.harnesses.send = self.send
        self.scheduler.run_notifications()
        self.assertEqual(self.count("state='sent'"), 2)

    def test_scan_passes_exhausted_and_human_backlog_after_restart(self):
        self.port('prt_human', handler='human')
        for i in range(120):
            tid = f'tsk_old_{i:03}'
            self.task(tid, 'agent:agt_a', kind='receipt', notify=False)
            self.svc.store.kv_set('wakeround:' + tid, {
                'total': 12, 'offset': 0, 'spec': 1,
                'candidates': ['agt_a'], 'exhausted': True})
        for i in range(55):
            self.task(f'tsk_old_human_{i:03}', 'port:prt_human', notify=False)
        self.task('tsk_z_new', notify=False)
        for _ in range(4):
            # Recreate Scheduler each tick: fairness is persisted, not just RAM.
            Scheduler(self.svc).run_reconcile_wakes()
        self.assertEqual(self.count("dedupe_key LIKE 'reconcile:tsk_z_new:%'"), 1)
        self.assertEqual(self.svc.store.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE state='queued'").fetchone()[0], 176)
        self.assertEqual(self.count("dedupe_key LIKE 'reconcile:tsk_old_%'"), 0)

    def test_scan_wrap_rechecks_capacity_change(self):
        self.task('tsk_a', notify=False)
        with self.svc.store.write() as cur:
            cur.execute("UPDATE agents SET lifecycle='paused' WHERE agent_id='agt_a'")
        self.scheduler.run_reconcile_wakes()
        self.assertEqual(self.count(), 0)
        with self.svc.store.write() as cur:
            cur.execute("UPDATE agents SET lifecycle='enabled' WHERE agent_id='agt_a'")
        self.scheduler.run_reconcile_wakes()
        self.assertEqual(self.count(), 1)

    def test_reconcile_pacing_and_cap_survive_paging(self):
        self.task('tsk_a', notify=False)
        self.svc.store.kv_set('policy', {'notify_jitter': 0, 'notify_max_rounds': 2})
        self.scheduler.run_reconcile_wakes()
        self.scheduler.run_reconcile_wakes()
        self.assertEqual(self.count(), 1)
        self.now = '2026-10-03T01:05:00Z'
        self.scheduler.run_reconcile_wakes()
        self.assertEqual(self.count(), 2)
        self.scheduler.run_reconcile_wakes()
        self.scheduler.run_reconcile_wakes()
        self.assertTrue(self.svc.store.kv_get('wakeround:tsk_a')['exhausted'])
        self.assertEqual(self.svc.store.conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind='task.notify_exhausted'"
        ).fetchone()[0], 1)

    def test_explicit_wake_new_request_after_sent_not_swallowed(self):
        op_wake_agent(self.svc, SYSTEM_SUBJECT, 'agt_a', 'fixture', 'req_1')
        self.scheduler.run_notifications()
        op_wake_agent(self.svc, SYSTEM_SUBJECT, 'agt_a', 'fixture', 'req_1')
        self.assertEqual(self.count(), 1)
        op_wake_agent(self.svc, SYSTEM_SUBJECT, 'agt_a', 'fixture', 'req_2')
        self.assertEqual(self.count("state='pending'"), 1)

    def test_explicit_sends_not_coalesced(self):
        _queue_notification(self.svc, 'agt_a', 'same command', 'send:req_1')
        _queue_notification(self.svc, 'agt_a', 'same command', 'send:req_2')
        self.scheduler.run_notifications()
        self.assertEqual(len(self.sent), 2)

    def test_resume_wakes_all_subscribers_each_time(self):
        self.agent('agt_b')
        self.subscribe('agt_b', 'prt_a')
        self.scheduler.notify_port_resumed('prt_a')
        self.scheduler.run_notifications()
        self.assertEqual({s[0] for s in self.sent}, {'agt_a', 'agt_b'})
        self.scheduler.notify_port_resumed('prt_a')
        self.assertEqual(self.count("state='pending'"), 2)

    def test_failed_batch_preserves_individual_retry_attempts(self):
        for i in range(2):
            self.task(f'tsk_{i}')
        with self.svc.store.write() as cur:
            cur.execute("UPDATE notifications SET attempts=1 WHERE "
                        "dedupe_key LIKE '%tsk_1:%'")
        self.svc.harnesses.send = lambda *args, **kwargs: 'failed'
        self.scheduler.run_notifications()
        self.assertEqual(sorted(r['attempts'] for r in self.svc.store.conn.execute(
            'SELECT attempts FROM notifications')), [1, 2])
        self.assertEqual(self.count("state='pending'"), 2)


@unittest.skipUnless(sys.platform == 'win32', 'Windows process handles')
class WindowsLivenessTests(unittest.TestCase):
    def check_exit_code(self, code, expected):
        kernel = MagicMock()
        kernel.OpenProcess.return_value = 42
        def get_exit_code(handle, pointer):
            pointer._obj.value = code
            return True
        kernel.GetExitCodeProcess.side_effect = get_exit_code
        with patch('hive_core.home.ctypes.windll.kernel32', kernel):
            self.assertEqual(process_alive(123), expected)
        kernel.CloseHandle.assert_called_once_with(42)

    def test_open_handle_to_exited_process_is_not_alive(self):
        self.check_exit_code(0, False)

    def test_open_handle_to_running_process_is_alive(self):
        self.check_exit_code(259, True)

    def test_current_process_is_alive(self):
        self.assertTrue(process_alive(os.getpid()))


if __name__ == '__main__':
    unittest.main(verbosity=2)
