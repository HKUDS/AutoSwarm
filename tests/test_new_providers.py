from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

from autoswarm import engine, platform
from autoswarm.nanobot import bridge_command, skill_directory
from autoswarm.providers import canonical, invocation
from autoswarm.records import Records, SwarmError, read_json, write_json
from autoswarm.selection import dsh_model_value
from autoswarm.setup import install

if __package__:
    from .autoswarm_test_support import cleanup_temporary
else:
    from autoswarm_test_support import cleanup_temporary


class ProviderLaunchTests(unittest.TestCase):
    def test_kimi_skill_respects_native_home_and_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()  # TEMP may be an 8.3 short path; setup resolves it.
            records = Records(root / 'state')
            for env, expected in (({'KIMI_CODE_HOME': str(root / 'custom')}, root / 'custom/skills/autoswarm'),
                                  ({}, root / '.kimi-code/skills/autoswarm')):
                with patch.dict(os.environ, env, clear=True), patch('autoswarm.setup.Path.home', return_value=root), patch('autoswarm.setup.discover', return_value=[]):
                    result = install(records, 'kimi')
                self.assertEqual(result['skill'], str(expected))
                self.assertEqual(result['native_return'], 'wait')
                self.assertTrue((expected / 'SKILL.md').is_file())
                self.assertFalse(any((expected / 'runtime').rglob('SKILL.md')))

    def test_agents_installed_outside_path_are_found_in_their_installer_folders(self):
        from autoswarm.providers import command_for
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            local = home / 'AppData/Local'
            suffix = '.exe' if os.name == 'nt' else ''
            grok = home / '.grok/bin' / ('grok' + suffix)
            hermes = (local / 'hermes/bin' if os.name == 'nt' else home / '.hermes/bin') / ('hermes' + suffix)
            for path in (grok, hermes):
                path.parent.mkdir(parents=True)
                path.write_text('')
            with patch('autoswarm.providers.shutil.which', return_value=None), patch('autoswarm.providers.Path.home', return_value=home), \
                    patch.dict(os.environ, {'LOCALAPPDATA': str(local), 'APPDATA': str(home / 'AppData/Roaming')}):
                self.assertEqual(command_for('grok', {}), [str(grok)])
                self.assertEqual(command_for('hermes', {}), [str(hermes)])
                with self.assertRaises(SwarmError) as raised:
                    command_for('kimi', {})
                self.assertEqual(raised.exception.code, 'provider_unavailable')
                # A command the user configured is used as given, never replaced by a guess.
                with self.assertRaises(SwarmError):
                    command_for('grok', {'providers': {'grok': {'command': ['grok']}}})

    def test_new_native_launchers_preserve_default_permissions(self):
        for provider, expected in [('hermes',['acp']),('dsh',['--profile','acp']),('grok',['agent','--no-leader','stdio']),('kimi',['acp'])]:
            task={'provider':provider}
            result=invocation(task,{'providers':{provider:{'command':[sys.executable]}}})
            self.assertEqual(result[1:],expected)
            self.assertFalse(task['native_options'])
            self.assertFalse({'--always-approve','--yolo','--ignore-rules'}.intersection(result))
        for name,provider in [('grok-build','grok'),('deepseek-harness','dsh'),('hermes-agent','hermes'),('nanobot-ai','nanobot'),('kimi-code','kimi'),('kimi-cli','kimi')]:
            self.assertEqual(canonical(name),provider)

    def test_dsh_selection_uses_advertised_full_route(self):
        value='["configured-provider","native-model"]'
        options=[{'group':'configured-provider','options':[{'value':value}]}]
        self.assertEqual(dsh_model_value(options,'configured-provider/native-model'),value)
        self.assertEqual(dsh_model_value(options,value),value)
        self.assertEqual(dsh_model_value(options,'native-model'),value)
        with self.assertRaises(SwarmError):
            dsh_model_value(options,'other/model')
        options[0]['options'].append({'value':'["other-provider","native-model"]'})
        with self.assertRaises(SwarmError):
            dsh_model_value(options,'native-model')

    def test_nanobot_uses_its_native_python_and_never_resolves_venv_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            native=root/'native-python';native.touch()
            cli=root/'nanobot';cli.write_text('#!'+str(native)+'\n')
            result=bridge_command([str(cli)],{})
            self.assertEqual(result[0],str(native))
            self.assertTrue(result[2].endswith('nanobot_bridge.py'))
            self.assertEqual(result[1],'-u')

    def test_nanobot_commands_keep_the_call_identity(self):
        # Nanobot's shell passes a minimal environment; agents its commands start must still belong to the call.
        from types import SimpleNamespace
        from autoswarm import nanobot_bridge
        config = SimpleNamespace(tools=SimpleNamespace(exec=SimpleNamespace(allowed_env_keys=['NATIVE_KEY', 'AUTOSWARM_HOME'])))
        with patch.dict(os.environ, {'AUTOSWARM_CALL_ID': 'call_' + 'a' * 32, 'AUTOSWARM_HOME': 'records'}):
            nanobot_bridge.autoswarm_commands(config)
        keys = config.tools.exec.allowed_env_keys
        self.assertEqual(keys[:3], ['NATIVE_KEY', 'AUTOSWARM_HOME', 'AUTOSWARM_CALL_ID'], 'native keys stay, identity is added once')
        nanobot_bridge.autoswarm_commands(SimpleNamespace())  # A loader without these settings is left alone.

    def test_nanobot_exe_launchers_carry_their_python(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            native=root/'venv dir'/'Scripts'/'python.exe';native.parent.mkdir(parents=True);native.touch()
            # pip/distlib: the stub, then a shebang and the zipped __main__ (the stub's own "#!" text is not one).
            cli=root/'bin'/'nanobot.exe';cli.parent.mkdir()
            cli.write_bytes(b'MZ\x90\x00'+b'#!\x00\x00P\x00A\x00T\x00H'+b'\x00'*64+b'#!"'+str(native).encode()+b'"\n'+b'PK\x03\x04zipped')
            self.assertEqual(bridge_command([str(cli)],{})[:2],[str(native),'-u'])
            if os.name=='nt':
                # uv's trampoline keeps the bare path at its end.
                cli.write_bytes(b'MZ\x90\x00'+b'\x00'*64+str(native).encode()+b'\x10\x00\x00\x00UVSC')
                self.assertEqual(bridge_command([str(cli)],{})[0],str(native))
            cli.write_bytes(b'MZ\x90\x00'+b'\x00'*64)
            with self.assertRaises(SwarmError):
                bridge_command([str(cli)],{})

    def test_nanobot_python_override_is_validated(self):
        self.assertEqual(bridge_command([sys.executable],{'providers':{'nanobot':{'python':[sys.executable]}}})[0],sys.executable)
        for bad in ('python',[],[None]):
            with self.assertRaises(SwarmError):
                bridge_command([sys.executable],{'providers':{'nanobot':{'python':bad}}})

    def test_nanobot_quoted_native_python_path_preserves_spaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            native=Path(temporary)/'native python'
            native.touch()
            cli=Path(temporary)/'nanobot'
            cli.write_text('#!"'+str(native)+'" -u\n')
            self.assertEqual(bridge_command([str(cli)],{})[:2],[str(native),'-u'])

    def test_nanobot_env_shebang_resolves_native_python(self):
        with tempfile.TemporaryDirectory() as temp:
            cli=Path(temp)/'nanobot'
            cli.write_text('#!/usr/bin/env -S python3 -u\n')
            with patch('autoswarm.nanobot.shutil.which', return_value=sys.executable):
                self.assertEqual(bridge_command([str(cli)],{})[:2],[sys.executable,'-u'])


class NanobotBridgeTests(unittest.TestCase):
    def test_bridge_tools_inherit_neither_end_of_the_wire(self):
        # A reader thread blocks on the wire, as the bridge's does; a native tool started meanwhile
        # must start (on Windows an inherited pipe with a pending read would stall it) and its
        # stdout must not reach the wire.
        script = textwrap.dedent('''
            import subprocess, sys, threading, time
            sys.path.insert(0, sys.argv[1])
            import nanobot_bridge
            reader, writer = nanobot_bridge.private_wire()
            got = []
            threading.Thread(target=lambda: got.append(reader.readline()), daemon=True).start()
            time.sleep(0.3)
            started = time.monotonic()
            tool = subprocess.run([sys.executable, "-c", "print('tool output'); import sys; print(repr(sys.stdin.read()))"], timeout=20)
            writer.write("tool exited %d after %.1fs\\n" % (tool.returncode, time.monotonic() - started))
            while not got:
                time.sleep(0.05)
            writer.write("wire read " + got[0])
        ''')
        bridge_dir = str(Path(__file__).resolve().parent.parent / "src/autoswarm")
        child = subprocess.Popen([sys.executable, "-c", script, bridge_dir], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        first = child.stdout.readline().decode()
        self.assertRegex(first, r"tool exited 0 after \d+\.\d+s", "the tool ran while the wire was being read")
        self.assertTrue(first.endswith("s\n"), "the wire's lines end with a bare newline")
        self.assertLess(float(first.rsplit(" ", 1)[1].strip().rstrip("s")), 15)
        child.stdin.write(b'{"jsonrpc": "2.0"}\n')
        child.stdin.close()
        rest, errors = child.communicate(timeout=20)
        self.assertEqual(rest.decode().strip(), 'wire read {"jsonrpc": "2.0"}', "only the bridge's own lines are on the wire")
        self.assertIn("tool output", errors.decode(), "a tool's stdout goes to stderr")
        self.assertIn("''", errors.decode(), "a tool's stdin is empty")

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name).resolve()
        self.native=self.root/'native-workspace';self.native.mkdir()
        self.config=self.root/'native-config.json'
        self.config.write_text(json.dumps({'workspace':str(self.native),'model':'native-default','effort':'native-default-effort','restricted':True}))
        self.original=self.config.read_bytes()
        sdk=Path(__file__).parent/'fixtures/nanobot_sdk'
        self.env=patch.dict(os.environ,{'PYTHONPATH':str(sdk)+(os.pathsep+os.environ['PYTHONPATH'] if os.environ.get('PYTHONPATH') else '')})
        self.env.start()
        self.records=Records(self.root/'autoswarm')
        write_json(self.records.home/'config.json',{'providers':{'nanobot':{'command':[sys.executable], 'python':[sys.executable], 'args':['--config',str(self.config)]}}})
        self.calls=[]

    def tearDown(self):
        for call in self.calls:
            engine.cancel(self.records,call['call_id'])
        end=time.monotonic()+10 # Fixture cleanup only.
        while any(platform.running(read_json(self.records.call(c['call_id'])/'handle.json')) for c in self.calls):
            if time.monotonic()>end:self.fail('SDK bridge did not exit')
            time.sleep(.01)
        self.assertEqual(self.config.read_bytes(),self.original)
        self.env.stop()
        cleanup_temporary(self.tmp,end)

    def start(self,text,**options):
        call=engine.start_call(self.records,'nanobot',text,self.root,return_mode='wait',**options)
        self.calls.append(call)
        return call

    def final(self,call):
        return next(v for v in engine.wait(self.records,[call['call_id']],controller=call['controller_id']) if v.get('outcome'))

    def test_real_bridge_wire_persists_separate_native_sessions_and_resume(self):
        a=self.start('remember:中文 🐝');b=self.start('remember:another')
        ar,br=self.final(a),self.final(b)
        self.assertEqual(ar['outcome'],'completed',ar)
        self.assertNotEqual(ar['native_session_id'],br['native_session_id'])
        resumed=engine.resume(self.records,a['call_id'],'recall',return_mode='wait');self.calls.append(resumed)
        value=self.final(resumed)
        self.assertEqual(value['content'],'中文 🐝')
        self.assertEqual(value['native_session_id'],ar['native_session_id'])
        self.assertEqual(len(list((self.native/'native-sessions').glob('*.json'))),2)

    def test_desk_sees_what_nanobot_tools_run(self):
        write_json(self.records.home/'config.json',{**read_json(self.records.home/'config.json'),'events':{'capture':'auto'}})
        call=self.start('remember:x')
        self.assertEqual(self.final(call)['outcome'],'completed')
        from autoswarm.desk.streams import page
        events=page(self.records.call(call['call_id']),'nanobot')['events']
        tools=[e for e in events if e['kind']=='tool']
        self.assertEqual([(e['id'],e.get('title'),e.get('status')) for e in tools],[('native-tool','echo fixture','in_progress'),('native-tool',None,'completed')],
                         'the command, then how it ended (the same tool row)')
        self.assertIn('thought',[e['kind'] for e in events])
        self.assertEqual([e['text'] for e in events if e['kind']=='message'],['remember:x'],'the reply is unchanged')

    def test_native_profile_is_separate_and_full_access_overrides_restricted_default(self):
        call=self.start('inspect-native')
        value=self.final(call)
        self.assertEqual(value['outcome'],'completed',value)
        details=json.loads(value['content'])
        self.assertEqual(details['workspace'],str(self.native))
        self.assertEqual(details['project'],{'project_path':str(self.root),'access_mode':'full'})
        self.assertEqual(details['model'],'native-default')
        self.assertEqual(details['effort'],'native-default-effort')
        self.assertEqual(details['channel'],'websocket')
        resumed=engine.resume(self.records,call['call_id'],'inspect-native',return_mode='wait');self.calls.append(resumed)
        self.assertEqual(json.loads(self.final(resumed)['content'])['project'],details['project'])

    def test_explicit_restricted_access_is_retained_on_resume(self):
        call=self.start('inspect-native',provider_args=['--access-mode','restricted'])
        first=self.final(call)
        self.assertEqual(json.loads(first['content'])['project']['access_mode'],'restricted')
        resumed=engine.resume(self.records,call['call_id'],'inspect-native',return_mode='wait');self.calls.append(resumed)
        value=self.final(resumed)
        self.assertEqual(value['native_session_id'],first['native_session_id'])
        self.assertEqual(json.loads(value['content'])['project']['access_mode'],'restricted')

    def test_skill_install_location_uses_native_configured_workspace(self):
        self.assertEqual(skill_directory(self.records.config()),self.native/'skills/autoswarm')

    def test_model_and_effort_overrides_are_process_local_and_inherited(self):
        first=self.start('inspect-native',model='selected-native',effort='selected-effort')
        self.assertEqual(json.loads(self.final(first)['content'])['effort'],'selected-effort')
        resumed=engine.resume(self.records,first['call_id'],'inspect-native',return_mode='wait');self.calls.append(resumed)
        value=json.loads(self.final(resumed)['content'])
        self.assertEqual(value['model'],'selected-native')
        self.assertEqual(value['effort'],'selected-effort')

    def test_native_errors_incomplete_work_and_missing_sessions_are_not_success(self):
        for text in ('error','incomplete'):
            result=self.final(self.start(text))
            self.assertEqual(result['outcome'],'failed',result)
            self.assertTrue(result['error'])
        result=self.final(self.start('recall',native_session_id='websocket:missing'))
        self.assertEqual(result['outcome'],'failed',result)
        self.assertIn('does not exist',result['error'])

    def test_cancel_of_running_sdk_turn_exits_without_fabricated_final(self):
        call=self.start('gate:'+str(self.root/'gate'))
        self.assertTrue(engine.cancel(self.records,call['call_id'])['cancel_requested'])
        result=self.final(call)
        self.assertEqual(result['outcome'],'cancelled',{'result':result,'runner_log':(self.records.call(call['call_id'])/'runner.log').read_text(errors='replace')})


if __name__=='__main__':unittest.main()
