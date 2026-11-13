import json
import tempfile
import unittest
from pathlib import Path
from autoswarm.capture import Capture
from autoswarm.desk.lens import normalize
from autoswarm.desk.streams import page
from autoswarm.records import Records, read_json, write_json
from autoswarm.cli import dispatch, parse_args


class StreamTests(unittest.TestCase):
    def test_recorded_native_samples_have_tools_and_boundaries(self):
        for provider in ['codex', 'claude-code', 'cursor', 'codebuddy', 'opencode', 'muse']:
            path = Path(__file__).parent.parent/'fixtures'/'desk-native'/(provider+'.jsonl')
            events = [event for line in path.read_text(encoding='utf8').splitlines() for event in normalize(provider,json.loads(line))]
            self.assertTrue(any(e['kind']=='tool' for e in events),provider)
            self.assertTrue(any(e['kind']=='turn' for e in events),provider)

    def test_real_muse_tool_updates_keep_ids_inputs_and_outputs(self):
        path = Path(__file__).parent.parent/'fixtures'/'desk-native'/'muse.jsonl'
        events = [e for line in path.read_text(encoding='utf8').splitlines() for e in normalize('muse',json.loads(line))]
        tools = [e for e in events if e['kind']=='tool']
        self.assertEqual(tools[0]['id'],tools[-1]['id'])
        self.assertEqual(tools[-1]['tool'],'read')
        self.assertIn('probe.txt',tools[0]['input'])
        self.assertTrue(tools[-1]['output'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_capture_defaults_off_and_is_raw_when_enabled(self):
        capture = Capture(self.root, {})
        capture.write(b'hello\n'); capture.close()
        self.assertFalse((self.root/'events.jsonl').exists())
        capture = Capture(self.root, {'events': {'capture': True}})
        capture.write(b'{"x": 1}\n'); capture.close()
        self.assertEqual((self.root/'events.jsonl').read_bytes(), b'{"x": 1}\n')
        capture.write(b'after close')

    def test_capture_failure_does_not_abort_native_execution(self):
        (self.root/'events.jsonl').mkdir()
        capture = Capture(self.root, {'events': {'capture': True}})
        capture.write(b'x\n'); capture.close()

    def test_config_opt_in_preserves_unrelated_fields(self):
        records = Records(self.root)
        write_json(self.root/'config.json', {'providers': {'codex': {'model': 'native'}}, 'events': {'other': 1}})
        dispatch(parse_args(['config', '--capture-events', 'on']), records)
        config = read_json(self.root/'config.json')
        self.assertTrue(config['events']['capture'])
        self.assertEqual(config['events']['other'], 1)
        self.assertIn('codex', config['providers'])

    def test_pages_preserve_utf8_partial_line_and_no_duplicates(self):
        path = self.root/'events.jsonl'
        lines = [json.dumps({'type':'assistant','message':{'id':str(i),'content':[{'type':'text','text':f'中文{i}'}]}}, ensure_ascii=False).encode()+b'\n' for i in range(900)]
        path.write_bytes(b''.join(lines)+b'{"partial":')
        latest = page(self.root,'claude-code')
        self.assertEqual(len(latest['events']),400)
        earlier = page(self.root,'claude-code',before=latest['before'])
        first = page(self.root,'claude-code',before=earlier['before'])
        all_events = first['events']+earlier['events']+latest['events']
        self.assertEqual(len(all_events),900)
        self.assertEqual(len({e['seq'] for e in all_events}),900)
        self.assertEqual(all_events[-1]['text'],'中文899')

    def test_unknown_malformed_and_protocol_boundaries(self):
        for provider in ['codex','claude-code','cursor','opencode','muse','new-provider']:
            for value in [None,[],{'params':[]},{'method':'future','params':{'x':1}}]:
                self.assertEqual(normalize(provider,value)[0]['kind'],'notice')
        boundaries = [('codex',{'method':'turn/completed','params':{'turn':{'status':'completed'}}}),('claude-code',{'type':'result'}),('cursor',{'result':{'stopReason':'end_turn'}}),('opencode',{'type':'session.idle'}),('muse',{'method':'turn/completed','params':{'terminal':'completed'}})]
        for provider,value in boundaries:
            self.assertEqual(normalize(provider,value)[-1]['kind'],'turn')

    def test_opencode_ignores_other_sessions_and_handles_late_types(self):
        value={'type':'message.part.updated','properties':{'part':{'sessionID':'other','id':'p1','type':'reasoning','text':'thought'}}}
        self.assertEqual(normalize('opencode',value,'ours'),[])
        self.assertEqual(normalize('opencode',value,'other')[0]['kind'],'thought')

    def test_all_protocol_tools_and_results(self):
        cases=[('codex',{'method':'item/completed','params':{'item':{'type':'commandExecution','id':'t','aggregatedOutput':'ok','status':'completed'}}}),('claude-code',{'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'t','content':'ok'}]}}),('cursor',{'method':'session/update','params':{'update':{'sessionUpdate':'tool_call_update','toolCallId':'t','status':'completed'}}}),('opencode',{'type':'message.part.updated','properties':{'part':{'type':'tool','id':'t','tool':'read','state':{'status':'completed','output':'ok'}}}}),('muse',{'method':'item/completed','params':{'item':{'kind':'toolCall','id':'t','status':'completed'}}})]
        for provider,value in cases:
            result=normalize(provider,value)[0]
            self.assertEqual(result['kind'],'tool')
            self.assertEqual(result['status'],'completed')
            self.assertEqual(result['raw'],value)

    def test_every_acp_agent_shows_its_turn_and_not_its_protocol_traffic(self):
        # Shapes recorded on 2026-11-05 from Kimi Code 2.5, Grok Build 1.0, CodeBuddy 2.170 and DSH 0.2.
        update = lambda body: {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "s", "update": body}}
        for provider in ("kimi", "grok", "codebuddy", "dsh", "hermes", "copilot", "nanobot"):
            with self.subTest(provider=provider):
                self.assertEqual(normalize(provider, update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "PONG"}}))[0]["kind"], "message")
                for quiet in ({"sessionUpdate": "available_commands_update", "availableCommands": []}, {"sessionUpdate": "session_info_update", "title": "t"},
                              {"sessionUpdate": "config_option_update", "configOptions": []}, {"sessionUpdate": "current_mode_update", "currentModeId": "plan"}):
                    self.assertEqual(normalize(provider, update(quiet)), [], quiet)
                self.assertEqual(normalize(provider, {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}), [], "a setup reply")
                self.assertEqual(normalize(provider, {"jsonrpc": "2.0", "id": 0, "method": "session/request_permission", "params": {}}), [], "shown as an approval card")
                self.assertEqual(normalize(provider, {"jsonrpc": "2.0", "method": "_x.ai/session_notification", "params": {"update": {"sessionUpdate": "tool_call_delta_chunk"}}}), [])
                failed = normalize(provider, {"jsonrpc": "2.0", "id": 3, "error": {"code": -32603, "message": "no API key for provider route"}})
                self.assertEqual((failed[0]["kind"], failed[0]["text"]), ("error", "no API key for provider route"))
        # A tool named only by its tool (Grok's run_terminal_command, DSH's pwsh) shows what it runs.
        tool = lambda title, given: normalize("grok", update({"sessionUpdate": "tool_call", "toolCallId": "t", "title": title, "kind": "execute", "rawInput": given}))[0]["title"]
        self.assertEqual(tool("run_terminal_command", {"command": "dir"}), "dir")
        self.assertEqual(tool("pwsh", {"command": ["git", "status"]}), "git status")
        self.assertEqual(tool("Run the tests", {"command": "pytest"}), "Run the tests", "a title in words stays")
        self.assertEqual(tool("Write", {"path": "a.txt"}), "a.txt")

    def test_cursor_command_titles_show_the_command_without_backticks(self):
        # Recorded from Cursor Agent 2026.10 over ACP.
        update = lambda title: {"method": "session/update", "params": {"sessionId": "s", "update": {"sessionUpdate": "tool_call", "toolCallId": "t", "title": title, "kind": "execute", "status": "pending"}}}
        self.assertEqual(normalize("cursor", update("`dir`"))[0]["title"], "dir")
        self.assertEqual(normalize("cursor", update("Read file"))[0]["title"], "Read file")
        self.assertEqual(normalize("cursor", update("```"))[0]["title"], "```")

    def test_claude_partial_messages_stream_into_the_block_that_follows(self):
        # Recorded shape: Claude Code 2.1 with --include-partial-messages (one block per assistant event).
        lines = [
            {"type": "system", "subtype": "init"},
            {"type": "stream_event", "api_message_id": "msg_1", "event": {"type": "message_start", "message": {"id": "msg_1"}}},
            {"type": "stream_event", "api_message_id": "msg_1", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}},
            {"type": "stream_event", "api_message_id": "msg_1", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "先想"}}},
            {"type": "assistant", "uuid": "u1", "message": {"id": "msg_1", "content": [{"type": "thinking", "thinking": "先想一下"}]}},
            {"type": "stream_event", "api_message_id": "msg_1", "event": {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}}},
            {"type": "stream_event", "api_message_id": "msg_1", "event": {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "一二"}}},
            {"type": "stream_event", "api_message_id": "msg_1", "event": {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "三"}}},
            {"type": "assistant", "uuid": "u2", "message": {"id": "msg_1", "content": [{"type": "text", "text": "一二三"}]}},
            {"type": "rate_limit_event"},
        ]
        (self.root / 'live.jsonl').write_text(''.join(json.dumps(line, ensure_ascii=False) + '\n' for line in lines), encoding='utf-8')
        events = page(self.root, 'claude-code', name='live.jsonl')['events']
        self.assertNotIn('notice', [e['kind'] for e in events], 'hooks, init and rate limits are not shown')
        ids = {(e['kind'], e['id']) for e in events if e['kind'] in ('thought', 'message')}
        self.assertEqual(ids, {('thought', 'msg_1:0'), ('message', 'msg_1:1')}, 'deltas and complete blocks share ids; thinking and text stay apart')
        self.assertEqual([e.get('text') for e in events if e['kind'] == 'message'], ['一二', '三', '一二三'])
        self.assertTrue(events[-1].get('replace'))

    def test_a_replayed_transcript_before_the_prompt_mark_is_dropped(self):
        lines = [
            {"method": "session/update", "params": {"sessionId": "s", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "old turn"}}}},
            {"autoswarm": "prompt"},
            {"method": "session/update", "params": {"sessionId": "s", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "new"}}}},
        ]
        (self.root / 'live.jsonl').write_text(''.join(json.dumps(line) + '\n' for line in lines), encoding='utf-8')
        value = page(self.root, 'cursor', 's', name='live.jsonl')
        self.assertEqual(([e.get('text') for e in value['events']], value['before']), (['new'], 0))
