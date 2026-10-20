"""Native transcript round trips, legacy compatibility and bounded final replies."""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from opc.core.config import OPCConfig
from opc.core.models import Task, TaskStatus
from opc.database.store import OPCStore
from opc.layer3_agent.runtime_v2.runtime import NativeRuntimeV2
from opc.layer4_tools.registry import ToolDefinition, ToolRegistry
from opc.layer5_memory.memory_manager import MemoryManager

def _event(event_type: str, payload: dict[str, object]):
    return type('Evt', (), {'event_type': event_type, 'payload': payload, 'model': 'stub'})()

class _ScriptedLLM:
    """Two-turn script: thinking + tool call, then thinking + final text."""

    def __init__(self, *, turns_with_tools: int=1, final_text: str='final answer') -> None:
        self.calls = 0
        self.turns_with_tools = turns_with_tools
        self.final_text = final_text
        self.prompts: list[list[dict[str, object]]] = []
        self.config = type('Cfg', (), {'max_tokens': 2048})()

    def prepare_user_message_content(self, content, attachment_refs=None):
        _ = attachment_refs
        return content

    def get_tool_definitions(self, tools):
        return tools

    def is_context_overflow_error(self, error: Exception) -> bool:
        _ = error
        return False

    def count_input_tokens(self, messages, tools=None):
        _ = (messages, tools)
        return 100

    def get_context_window(self):
        return 100000

    async def chat_stream(self, messages, tools=None):
        _ = tools
        self.calls += 1
        self.prompts.append([dict(item) for item in messages])
        yield _event('message_start', {})
        yield _event('thinking_delta', {'text': f'THINK-{self.calls}'})
        if self.calls <= self.turns_with_tools:
            yield _event('assistant_delta', {'text': f'working {self.calls}'})
            yield _event('tool_call_delta', {'index': 0, 'id': f'tool-{self.calls}', 'name': 'demo_tool', 'arguments': json.dumps({'value': f'go-{self.calls}'})})
        else:
            yield _event('assistant_delta', {'text': self.final_text})
        yield _event('usage', {'prompt_tokens': 100, 'completion_tokens': 10})
        yield _event('message_stop', {'finish_reason': 'stop'})

class _LengthTruncatedLLM(_ScriptedLLM):
    """First N turns: no action, empty content; then a normal final.

    ``finish_reason`` is configurable: "length" models output-cap truncation,
    "stop" models a reasoning-only turn that spent everything on thinking.
    """

    def __init__(self, *, truncated_turns: int, final_text: str='recovered final', finish_reason: str='length') -> None:
        super().__init__(turns_with_tools=0, final_text=final_text)
        self.truncated_turns = truncated_turns
        self.finish_reason = finish_reason

    async def chat_stream(self, messages, tools=None):
        _ = tools
        self.calls += 1
        self.prompts.append([dict(item) for item in messages])
        yield _event('message_start', {})
        if self.calls <= self.truncated_turns:
            yield _event('thinking_delta', {'text': 'runaway thinking'})
            yield _event('usage', {'prompt_tokens': 100, 'completion_tokens': 0})
            yield _event('message_stop', {'finish_reason': self.finish_reason})
        else:
            yield _event('assistant_delta', {'text': self.final_text})
            yield _event('usage', {'prompt_tokens': 100, 'completion_tokens': 10})
            yield _event('message_stop', {'finish_reason': 'stop'})

def _registry() -> ToolRegistry:

    async def demo_tool(value: str) -> dict[str, str]:
        return {'echo': f'RESULT-PAYLOAD-{value}'}
    registry = ToolRegistry()
    registry.register(ToolDefinition(name='demo_tool', description='Demo runtime tool', parameters={'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}, func=demo_tool, concurrency_safe=True, read_only=True))
    return registry

def _task(session_id: str='sess-integrity') -> Task:
    return Task(id=f'task-{session_id}', title='integrity', session_id=session_id, project_id='proj-integrity', metadata={'mode': 'task', 'execution_mode': 'task_mode'})

class SessionPersistenceRoundTripTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        self.store = OPCStore(db_path=root / 'store.db')
        await self.store.initialize()
        self.memory = MemoryManager(root, 'proj-integrity', store=self.store)

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def _run(self, llm, *, task: Task, max_iterations: int=10) -> object:
        await self.store.save_task(task)
        runtime = NativeRuntimeV2(llm=llm, tool_registry=_registry(), config=OPCConfig(), memory_manager=self.memory, max_iterations=max_iterations)
        return (runtime, await runtime.run(system_prompt='You are a runtime.', user_message='do the work', task=task))

    async def _thinking_parts(self, session_id: str) -> list[str]:
        transcript = await self.store.get_session_transcript(session_id)
        texts: list[str] = []
        for item in transcript:
            for part in item['parts']:
                if part.part_type == 'thinking':
                    texts.append(str(dict(part.payload or {}).get('text', '')))
        return texts

    async def test_length_truncated_turns_are_retried_with_feedback(self) -> None:
        llm = _LengthTruncatedLLM(truncated_turns=2)
        (_, result) = await self._run(llm, task=_task('sess-length-recover'))
        self.assertEqual(result.status, TaskStatus.DONE)
        self.assertEqual(llm.calls, 3)
        self.assertIn('output token limit', json.dumps(llm.prompts[1], ensure_ascii=False))
        final_prompt = json.dumps(llm.prompts[2], ensure_ascii=False)
        self.assertEqual(final_prompt.count('output token limit'), 1)
        self.assertNotIn('"role": "assistant", "content": ""', final_prompt)

    async def test_exhausted_length_truncation_budget_fails_the_run(self) -> None:
        llm = _LengthTruncatedLLM(truncated_turns=10)
        (_, result) = await self._run(llm, task=_task('sess-length-fail'))
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(llm.calls, 3)
        self.assertIn('no tool calls and no answer text', result.content)

    async def test_reasoning_only_stop_turn_is_retried_not_accepted_as_final(self) -> None:
        llm = _LengthTruncatedLLM(truncated_turns=1, finish_reason='stop')
        (_, result) = await self._run(llm, task=_task('sess-empty-stop'))
        self.assertEqual(result.status, TaskStatus.DONE)
        self.assertEqual(result.content, 'recovered final')
        self.assertEqual(llm.calls, 2)
        self.assertIn('was empty', json.dumps(llm.prompts[1], ensure_ascii=False))

    async def test_exhausted_empty_stop_budget_fails_the_run(self) -> None:
        llm = _LengthTruncatedLLM(truncated_turns=10, finish_reason='stop')
        (_, result) = await self._run(llm, task=_task('sess-empty-stop-fail'))
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(llm.calls, 3)

    async def test_usage_counts_deltas_but_context_pressure_uses_provider_total(self) -> None:
        class LLM(_ScriptedLLM):
            async def chat_stream(self, messages, tools=None):
                async for event in super().chat_stream(messages, tools):
                    if event.event_type == "usage" and self.calls == 1:
                        yield _event("usage", {"prompt_tokens": 100, "prompt_tokens_total": 100})
                        yield _event("usage", {"prompt_tokens": 20, "prompt_tokens_total": 120})
                    else:
                        yield event
        task = _task("usage-context")
        await self.store.save_task(task)
        runtime = NativeRuntimeV2(llm=LLM(), tool_registry=_registry(), memory_manager=self.memory)
        pipeline = runtime._apply_context_pipeline
        runtime._apply_context_pipeline = AsyncMock(wraps=pipeline)
        result = await runtime.run("system", "user", task=task)
        self.assertEqual(result.token_usage["prompt_tokens"], 220)
        self.assertEqual(runtime._apply_context_pipeline.call_args_list[1].kwargs["observed_tokens"], 120)

    async def test_nonempty_truncation_does_not_publish_an_incomplete_company_final(self) -> None:
        class LLM(_ScriptedLLM):
            async def chat_stream(self, messages, tools=None):
                self.calls += 1
                yield _event("assistant_delta", {"text": "incomplete company report" if self.calls == 1 else "complete report"})
                yield _event("message_stop", {"finish_reason": "length" if self.calls == 1 else "stop"})
        task = _task("company-truncation")
        task.metadata = {"mode": "company", "execution_mode": "company_mode", "company_profile": "corporate"}
        llm = LLM()
        _, result = await self._run(llm, task=task)
        self.assertEqual(result.status, TaskStatus.DONE)
        self.assertEqual(result.content.split("\n\nVerification:", 1)[0], "complete report")
        transcript = await self.store.get_session_transcript(task.session_id)
        finals = [item for item in transcript if item["message"].metadata.get("company_final_turn")]
        self.assertEqual(len(finals), 1)
        self.assertNotIn("incomplete company report", json.dumps([part.payload for part in finals[0]["parts"]]))

    async def test_each_turn_persists_only_its_own_thinking(self) -> None:
        task = _task('sess-thinking')
        (_, result) = await self._run(_ScriptedLLM(turns_with_tools=2), task=task)
        self.assertEqual(result.status, TaskStatus.DONE)
        thinking = await self._thinking_parts(task.session_id)
        self.assertEqual(thinking, ['THINK-1', 'THINK-2', 'THINK-3'])
        transcript = await self.store.get_session_transcript(task.session_id)
        metadata_thinking = [str(dict(item['message'].metadata or {}).get('runtime_thinking', '')) for item in transcript if dict(item['message'].metadata or {}).get('runtime_thinking')]
        self.assertEqual(metadata_thinking, ['THINK-1', 'THINK-2', 'THINK-3'])

    async def test_tool_result_is_stored_exactly_once(self) -> None:
        task = _task('sess-single-copy')
        await self._run(_ScriptedLLM(turns_with_tools=1), task=task)
        transcript = await self.store.get_session_transcript(task.session_id)
        part_types = [part.part_type for item in transcript for part in item['parts']]
        self.assertNotIn('tool_output', part_types)
        self.assertEqual(part_types.count('tool_result'), 1)
        payload_hits = sum((1 for item in transcript for part in item['parts'] if 'RESULT-PAYLOAD-go-1' in json.dumps(dict(part.payload or {}), ensure_ascii=False, default=str)))
        self.assertEqual(payload_hits, 1)

    async def test_restore_yields_one_tool_message_per_call(self) -> None:
        task = _task('sess-restore')
        (runtime, _) = await self._run(_ScriptedLLM(turns_with_tools=2), task=task)
        restored = await runtime._restore_transcript_messages(task)
        tool_messages = [m for m in restored if m.get('role') == 'tool']
        self.assertEqual(len(tool_messages), 2)
        self.assertEqual(sorted((m.get('tool_call_id') for m in tool_messages)), ['tool-1', 'tool-2'])
        flattened = json.dumps(restored, ensure_ascii=False)
        self.assertEqual(flattened.count('RESULT-PAYLOAD-go-1'), 1)
        self.assertNotIn('THINK-1', flattened)

class LegacyDoublePartCompatibilityTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        self.store = OPCStore(db_path=root / 'store.db')
        await self.store.initialize()
        self.memory = MemoryManager(root, 'proj-legacy', store=self.store)

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self._tmpdir.cleanup()

    async def _seed_legacy_session(self, session_id: str) -> None:
        await self.memory.append_session_message(session_id, 'user', text='legacy request', project_id='proj-legacy')
        call_message = await self.memory.append_session_message(session_id, 'assistant', text='legacy step', project_id='proj-legacy')
        await self.memory.append_session_part(session_id, call_message.message_id, 'tool_call', {'tool_call_id': 'legacy-call-1', 'tool_name': 'demo_tool', 'arguments': {'value': 'x'}})
        result_message = await self.memory.append_session_message(session_id, 'assistant', text=json.dumps({'success': True, 'result': {'echo': 'LEGACY-RESULT'}}), part_type='tool_output', project_id='proj-legacy', metadata={'kind': 'runtime_v2_tool_output', 'tool_name': 'demo_tool'})
        await self.memory.append_session_part(session_id, result_message.message_id, 'tool_result', {'tool_call_id': 'legacy-call-1', 'tool_name': 'demo_tool', 'result': {'echo': 'LEGACY-RESULT'}})

    async def test_restore_deduplicates_legacy_double_parts(self) -> None:
        session_id = 'sess-legacy'
        await self._seed_legacy_session(session_id)
        runtime = NativeRuntimeV2(llm=_ScriptedLLM(), tool_registry=ToolRegistry(), config=OPCConfig(), memory_manager=self.memory)
        task = _task(session_id)
        restored = await runtime._restore_transcript_messages(task)
        tool_messages = [m for m in restored if m.get('role') == 'tool']
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0]['tool_call_id'], 'legacy-call-1')
        flattened = json.dumps(restored, ensure_ascii=False)
        self.assertEqual(flattened.count('LEGACY-RESULT'), 1)

    async def test_rendered_history_has_single_copy_and_no_thinking(self) -> None:
        session_id = 'sess-legacy-render'
        await self._seed_legacy_session(session_id)
        transcript = await self.store.get_session_transcript(session_id)
        result_item = transcript[-1]
        await self.memory.append_session_part(session_id, result_item['message'].message_id, 'thinking', {'text': 'PRIVATE-THINKING-STREAM'})
        messages = await self.memory.build_session_history_tail_messages(session_id, include_latest_user_turn=True)
        flattened = '\n'.join((str(m.get('content', '')) for m in messages))
        self.assertEqual(flattened.count('LEGACY-RESULT'), 1)
        self.assertNotIn('PRIVATE-THINKING-STREAM', flattened)
