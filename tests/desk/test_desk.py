"""Read-only projection: real leases, state transitions and damaged history."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from autoswarm import platform
from autoswarm.desk.watch import Scanner, diff
from autoswarm.records import write_json


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="desk-中文-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.directory = self.home / "calls" / "call_01"
        write_json(self.directory / "task.json", {"call_id": "call_01", "agent_id": "call_01", "provider": "codex", "prompt": "测试", "workspace": str(self.home)})

    def fingerprint(self):
        return {str(p.relative_to(self.home)): (None if p.suffix == '.lock' else p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob('*') if p.is_file()}

    def test_scan_and_restart_never_write_or_create_lock(self):
        before = self.fingerprint()
        first = Scanner(self.home).scan()
        self.assertEqual(first, Scanner(self.home).scan())
        self.assertEqual(first['calls']['call_01']['state'], 'interrupted')
        self.assertEqual(self.fingerprint(), before)
        self.assertFalse((self.directory / 'runner.lock').exists())

    def test_real_lease_release_is_observed_without_file_change(self):
        lock = self.directory / 'runner.lock'
        # A subprocess is essential on POSIX (locks in the same process differ).
        script = 'from pathlib import Path; from autoswarm.platform import session_lease; import sys; lease=session_lease(Path(sys.argv[1])); print("ready",flush=True); sys.stdin.readline()'
        child = subprocess.Popen([sys.executable, '-c', script, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            write_json(self.directory / 'handle.json', {'runner_lock': str(lock), 'phase': 'execution'})
            scanner = Scanner(self.home)
            before = self.fingerprint()
            running = scanner.scan()
            self.assertEqual(running['calls']['call_01']['state'], 'running')
            self.assertEqual(before, self.fingerprint())
            child.communicate('\n', timeout=10)
            stopped = scanner.scan()
            self.assertEqual(stopped['calls']['call_01']['state'], 'interrupted')
            self.assertTrue(any(e['event'] == 'call.changed' for e in diff(running, stopped)))
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()

    def test_approvals_and_results_are_authoritative(self):
        lease = platform.session_lease(self.directory / 'runner.lock')
        self.addCleanup(lease.close)
        approval = self.directory / 'approvals' / 'approval_01'
        write_json(approval / 'request.json', {'kind': 'approval_required'})
        scanner = Scanner(self.home)
        self.assertTrue(scanner.scan()['requests']['call_01/approval_01']['pending'])
        write_json(approval / 'decision.json', {'actor': 'user', 'decision': 'deny'})
        self.assertFalse(scanner.scan()['requests']['call_01/approval_01']['pending'])
        write_json(self.directory / 'result.json', {'outcome': 'failed', 'content': '原生最终结果', 'error': 'denied'})
        snapshot = scanner.scan()
        self.assertEqual(snapshot['calls']['call_01']['state'], 'failed')
        self.assertEqual(snapshot['calls']['call_01']['result']['content'], '原生最终结果')

    def test_malformed_record_is_skipped_and_reported(self):
        path = self.home / 'calls' / 'call_02' / 'task.json'
        path.parent.mkdir()
        path.write_text('{', encoding='utf8')
        snapshot = Scanner(self.home).scan()
        self.assertIn('call_01', snapshot['calls'])
        self.assertNotIn('call_02', snapshot['calls'])
        self.assertEqual(len(snapshot['warnings']), 1)

    def test_deleted_call_and_message_delivery_emit_changes(self):
        message = self.home / 'agents/call_01/messages/msg_01'
        write_json(message / 'message.json', {'message_id': 'msg_01', 'content': 'hello'})
        scanner = Scanner(self.home)
        before = scanner.scan()
        (self.directory / 'task.json').unlink()
        write_json(message / 'delivery.json', {'state': 'delivered'})
        events = diff(before, scanner.scan())
        self.assertIn({'event': 'calls.removed', 'id': 'call_01'}, events)
        self.assertTrue(any(e['event'] == 'message.changed' for e in events))

    def test_nonexistent_home_stays_nonexistent(self):
        path = self.home / 'missing'
        self.assertFalse(Scanner(path).scan()['exists'])
        self.assertFalse(path.exists())

    def test_module_emits_utf8_snapshot(self):
        run = subprocess.run([sys.executable, '-m', 'autoswarm.desk.watch', '--home', str(self.home), '--once'], capture_output=True, encoding='utf8', check=True)
        value = json.loads(run.stdout)
        self.assertEqual(value['event'], 'snapshot')
        self.assertEqual(value['data']['calls']['call_01']['task']['prompt'], '测试')


if __name__ == '__main__':
    unittest.main()
