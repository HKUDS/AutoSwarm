import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from autoswarm import engine, platform
from autoswarm.providers import invocation, opencode_environment, opencode_transport
from autoswarm.records import Records, SwarmError, read_json, write_json
try:
    from autoswarm_test_support import cleanup_temporary
except ImportError:  # Run as tests.<module> from the checkout.
    from tests.autoswarm_test_support import cleanup_temporary

FIXTURE = Path(__file__).parent/'fixtures/provider.py'


class OpenCodeTransportTests(unittest.TestCase):
    def test_real_opencode_acp_recording_is_retained(self):
        from autoswarm.desk.lens import normalize
        rows = [json.loads(line) for line in (Path(__file__).parent/'fixtures/desk-native/opencode.jsonl').read_text(encoding='utf8').splitlines()]
        events = [e for row in rows for e in normalize('opencode',row)]
        self.assertTrue(any(e['kind']=='tool' for e in events))
        self.assertTrue(any(e['kind']=='turn' for e in events))

    def test_version_detection_keeps_v1_and_selects_v2_acp(self):
        for version, expected in [('1.15.13', 'http'), ('opencode v2.0.32', 'acp'), ('fixture 2.0', 'http')]:
            with patch('autoswarm.providers.subprocess.run', return_value=subprocess.CompletedProcess([], 0, version, '')):
                self.assertEqual(opencode_transport(['opencode'], {}), expected)
        self.assertEqual(opencode_transport(['unused'], {'providers': {'opencode': {'transport': 'http'}}}), 'http')
        with self.assertRaises(SwarmError):
            opencode_transport([], {'providers': {'opencode': {'transport': 'guess'}}})

    def test_acp_model_and_variant_are_native_session_controls(self):
        task = {'provider':'opencode', 'provider_args':['--model','provider/model','--variant','high']}
        command = invocation(task, {'providers':{'opencode':{'command':[sys.executable], 'transport':'acp'}}})
        self.assertEqual(command,[sys.executable,'acp'])
        self.assertEqual(task['native_transport'],'acp')
        self.assertEqual(task['native_options']['model'],'provider/model')
        self.assertEqual(task['native_options']['effort'],'high')
        self.assertNotIn('native_endpoint',task)

    def test_opencode_reaches_its_local_server_directly_when_a_proxy_is_set(self):
        task = {'provider': 'opencode', 'native_transport': 'acp'}
        proxied = opencode_environment(task, {'HTTPS_PROXY': 'http://proxy.invalid:8080', 'NO_PROXY': 'corp.local'})
        self.assertEqual(proxied['NO_PROXY'], 'corp.local,localhost,127.0.0.1,::1')
        self.assertEqual(proxied['no_proxy'], 'localhost,127.0.0.1,::1')
        self.assertEqual(opencode_environment(task, {'PATH': 'x'}), {'PATH': 'x'}, 'no proxy, no change')
        other = {'HTTPS_PROXY': 'http://proxy.invalid:8080'}
        self.assertIs(opencode_environment({'provider': 'codex'}, other), other)

    def test_new_acp_and_legacy_http_keep_capture_result_and_resume(self):
        for transport, peer in [('acp','cursor'),('http','opencode')]:
            with self.subTest(transport=transport):
                temporary=tempfile.TemporaryDirectory()
                records=Records(Path(temporary.name)/'state')
                write_json(records.home/'config.json', {'providers':{'opencode':{'command':[sys.executable,str(FIXTURE),peer],'transport':transport}},'events':{'capture':True}})
                ids=[]
                try:
                    first=engine.start_call(records,'opencode','first',temporary.name,return_mode='wait')
                    ids.append(first['call_id'])
                    result=engine.wait(records,ids)[0]
                    self.assertEqual(result['outcome'],'completed')
                    self.assertTrue((records.call(ids[0])/'events.jsonl').stat().st_size)
                    resumed=engine.resume(records,ids[0],'second')
                    ids.append(resumed['call_id'])
                    followup=engine.wait(records,[ids[1]])[0]
                    self.assertEqual(followup['outcome'],'completed')
                    self.assertEqual(followup['native_session_id'],result['native_session_id'])
                    self.assertIn('second',followup['content'])
                finally:
                    for cid in ids: engine.cancel(records,cid)
                    deadline=time.monotonic()+10
                    while any(platform.running(read_json(records.call(cid)/'handle.json')) for cid in ids) and time.monotonic()<deadline: time.sleep(.02)
                    cleanup_temporary(temporary,deadline)
