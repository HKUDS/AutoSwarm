"""Opt-in real-provider smoke for NativeAgent (not a full company dispatcher E2E).

Uses production NativeAgent, file tools and SQLite persistence in a new directory.
The company cases exercise role contexts only; no fake dispatcher success is reported.
No credentials are copied into evidence. Run with --execute and an explicit output path.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--config-dir', type=Path, default=REPO / '.opc/config')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--cases', default='task_implementation,company_implementation,company_review')
    args = parser.parse_args()
    cases = set(args.cases.split(','))
    if not cases <= {'task_implementation', 'company_implementation', 'company_review'}:
        parser.error('Unknown or empty case name')
    if 'company_review' in cases and 'company_implementation' not in cases:
        parser.error('company_review requires company_implementation for artifact handoff')
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    return args


def transcript_checks(transcript, restored, task):
    """Check persisted tool envelopes and the UI's mode-specific identity contract."""
    from opc.plugins.office_ui.snapshot_builder import build_transcript_ui_messages

    parts = [part for item in transcript for part in item['parts']]
    calls = [p.payload['tool_call_id'] for p in parts if p.part_type == 'tool_call']
    results = {p.payload['tool_call_id']: p.payload['result'] for p in parts if p.part_type == 'tool_result'}
    tools = [message for message in restored if message.get('role') == 'tool']
    thinking = {p.payload['stream_id']: p.payload['text'] for p in parts if p.part_type == 'thinking'}
    ui = build_transcript_ui_messages(transcript, channel_id=task.session_id, task_id=task.id)
    ui_ids = [message['message_id'] for message in ui]
    ui_thinking = {m['metadata']['runtime_thinking_stream_id']: m['metadata']['runtime_thinking']
                   for m in ui if m['metadata'].get('runtime_thinking_stream_id')}
    checks = {
        'tool_result_pairing': len(calls) == len(set(calls)) == len(results) == len(tools)
            and calls == [m['tool_call_id'] for m in tools],
        'canonical_envelopes_preserved': all(json.loads(m['content']) == results.get(m['tool_call_id']) for m in tools),
        'no_duplicate_tool_output_parts': not any(p.part_type == 'tool_output' for p in parts),
        'thinking_stream_ids_unique': len(thinking) == sum(p.part_type == 'thinking' for p in parts),
        'thinking_excluded_from_prompt': all(text not in json.dumps(restored, ensure_ascii=False) for text in thinking.values()),
        'summary_thinking_preserved': ui_thinking == thinking,
        'summary_ids_unique': len(ui_ids) == len(set(ui_ids)),
    }
    if task.metadata.get('execution_mode') == 'company_mode':
        # Intermediate company iterations deliberately share one row in full
        # detail. Only the terminal reply has a separate, summary-visible ID.
        checks['company_final_visible'] = any(identity.startswith('runtime-v2-company-assistant-final:') for identity in ui_ids)
    return checks


async def main(args):
    from loguru import logger
    from opc.core.config import OPCConfig
    from opc.core.events import EventBus
    from opc.core.models import AgentInfo, Task, TaskStatus, DelegationRun, DelegationWorkItem, Phase
    from opc.core.company_controller import CompanyRunControllerLeaseLost
    from opc.layer2_organization.work_item_links import set_linked_work_item_id
    from opc.database.store import OPCStore
    from opc.layer1_perception.context_assembler import ContextAssembler
    from opc.layer3_agent.native_agent import NativeAgent
    from opc.layer4_tools.file_ops import create_file_tools
    from opc.layer4_tools.registry import ToolDefinition, ToolRegistry
    from opc.layer5_memory.memory_manager import MemoryManager
    from opc.layer5_memory.preference import PreferenceManager
    from opc.layer5_memory.skill_library import SkillLibrary
    from opc.llm.provider import LLMProvider

    logger.remove()
    config = OPCConfig.load(args.config_dir, trusted_source=True)
    if not LLMProvider(config.llm).has_credentials():
        raise RuntimeError('No provider credentials available')
    config.system.max_agent_iterations = 10
    config.llm.max_tokens = min(config.llm.max_tokens, 8192)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    home = output / '.opc'
    home.mkdir()
    os.environ['OPC_HOME'] = str(home)
    store = OPCStore(home / 'smoke.db')
    await store.initialize()
    summary = {'model': config.llm.default_model, 'max_tokens': config.llm.max_tokens,
               'scope': 'NativeAgent real-provider role contexts; company dispatcher not exercised', 'cases': []}
    rows = [{'qty': 2, 'price': '10.25'}, {'qty': 3, 'price': '4.10'}, {'qty': 1, 'price': '0.99'}]
    expected = {'units': 6, 'total': '33.79'}
    prompt = (
        'Complete this small programming assignment in the workspace. Read input.json. '
        'Create sales.py with a pure function totals(rows) returning a dict with units (sum of qty) '
        'and total (sum of qty * price, decimal arithmetic, a string with exactly two decimal places). '
        'Handle an empty list as {"units":0,"total":"0.00"}. '
        'Create report.json containing totals(input.json). Use the verify tool and fix failures. '
        'Do not edit input.json or test_sales.py. Finish with the verified totals and created file paths. '
        'Do not delegate or request owner decisions; this small assignment is fully specified.'
    )
    try:
        for case_name, execution_mode, role_id in (
            ('task_implementation', 'task_mode', 'task_generalist'),
            ('company_implementation', 'company_mode', 'engineer'),
            ('company_review', 'company_mode', 'reviewer'),
        ):
            if case_name not in args.cases.split(','):
                continue
            workspace = output / case_name
            workspace.mkdir()
            (workspace / 'input.json').write_text(json.dumps(rows))
            (workspace / 'test_sales.py').write_text(
                'import json,unittest\nfrom sales import totals\n'
                'class TestSales(unittest.TestCase):\n'
                ' def test_input(self):\n'
                '  self.assertEqual(totals(json.load(open("input.json"))), {"units":6,"total":"33.79"})\n'
                ' def test_empty(self):\n'
                '  self.assertEqual(totals([]), {"units":0,"total":"0.00"})\n'
                ' def test_decimal(self):\n'
                '  self.assertEqual(totals([{"qty":3,"price":"0.10"}]), {"units":3,"total":"0.30"})\n'
                ' def test_report(self):\n'
                '  self.assertEqual(json.load(open("report.json")), {"units":6,"total":"33.79"})\n'
            )
            task_prompt = prompt
            if case_name == 'company_review':
                # Actual artifact handoff from the preceding real-model run.
                for filename in ('sales.py', 'report.json'):
                    source = output / 'company_implementation' / filename
                    if source.exists():
                        (workspace / filename).write_bytes(source.read_bytes())
                task_prompt = (
                    'Review sales.py and report.json delivered by the engineer against input.json and test_sales.py. '
                    'Read the files and run verify. Do not modify implementation or tests. '
                    'Write review.json with verdict="pass" only if all tests pass and report.json is correct; '
                    'otherwise verdict="fail". Include a short findings list. Do not delegate. '
                    'Return the verdict and the review artifact path.'
                )
            protected_files = ['input.json', 'test_sales.py']
            if case_name == 'company_review':
                protected_files += ['sales.py', 'report.json']
            original_files = {name: (workspace / name).read_bytes() for name in protected_files}
            memory = MemoryManager(home, 'native-smoke', store=store)
            skills = SkillLibrary(home)
            skills.load_all('native-smoke')
            bus = EventBus()
            events = []
            async def on_event(event):
                events.append(dict(event.payload))
            bus.subscribe('runtime_event', on_event)
            registry = ToolRegistry()
            for tool in create_file_tools():
                if tool.name in {'file_read', 'file_write', 'file_edit', 'list_dir', 'grep', 'glob'}:
                    registry.register(tool)
            async def verify():
                process = await asyncio.create_subprocess_exec(
                    sys.executable, '-B', '-m', 'unittest', '-q', 'test_sales', cwd=workspace,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
                except BaseException:
                    if process.returncode is None:
                        process.kill()
                    await process.communicate()
                    raise
                return {'passed': process.returncode == 0, 'success': process.returncode == 0,
                        'exit_code': process.returncode, 'stdout': stdout.decode(), 'stderr': stderr.decode()}
            registry.register(ToolDefinition(name='verify', description='Run the fixed local acceptance tests; no arguments.',
                parameters={'type':'object','properties':{}}, func=verify, read_only=True, concurrency_safe=False,
                company_effect_kind='no_local_fs'))
            role = AgentInfo(role_id=role_id, name=role_id, responsibility='Complete the assigned local implementation or review.',
                             tools=[tool.name for tool in registry.list_tools()])
            provider = LLMProvider(config.llm, opc_home=home)
            agent = NativeAgent(role, provider, registry, ContextAssembler(memory, store=store), memory,
                                PreferenceManager(home), skills, bus, config=config)
            task = Task(title=case_name, description=task_prompt, project_id='native-smoke',
                        session_id=f'smoke-{case_name}', assigned_to=role_id,
                        metadata={'mode':'task' if execution_mode == 'task_mode' else 'company',
                                  'execution_mode':execution_mode, 'company_profile':'corporate' if execution_mode == 'company_mode' else '',
                                  'workspace_root':str(workspace), 'target_output_dir':str(workspace)})
            if execution_mode == 'company_mode':
                run_id = f'run-{case_name}'
                item_id = f'work-{case_name}'
                owner_token = f'isolated-smoke-{case_name}'
                await store.save_delegation_run(DelegationRun(
                    run_id=run_id, project_id=task.project_id, session_id=task.session_id,
                    execution_model='multi_team_org', status='running', lifecycle_status='active',
                    metadata={'comms_workspace_root': str(workspace)},
                ))
                await store.save_delegation_work_item(DelegationWorkItem(
                    work_item_id=item_id, run_id=run_id, role_id=role_id, seat_id=f'seat-{role_id}',
                    title=case_name, summary=task_prompt, projection_id='execution', phase=Phase.READY,
                ))
                task.metadata.update(delegation_run_id=run_id,
                    delegation_role_session_id=f'role-{case_name}', delegation_seat_id=f'seat-{role_id}')
                await store.save_task(task)
                if not await store.link_work_item_runtime_task(item_id, task.id):
                    raise RuntimeError('Could not link company smoke task')
                set_linked_work_item_id(task, item_id)
                lease = await store.acquire_delegation_run_controller_lease(
                    run_id, project_id=task.project_id, root_session_id=task.session_id,
                    owner_token=owner_token, lease_seconds=args.timeout + 120,
                )
                if not lease.acquired:
                    raise RuntimeError('Could not acquire isolated company controller lease')
                claimed = await store.claim_delegation_work_item_if_dispatchable(
                    item_id, expected_phase=Phase.READY, role_runtime_session_id=f'role-{case_name}',
                    seat_id=f'seat-{role_id}', task_id=task.id, controller_owner_token=owner_token,
                    controller_lease_generation=lease.generation,
                )
                if claimed is None:
                    raise RuntimeError('Could not claim isolated company WorkItem')
                task.metadata.update(company_run_controller_owner_token=owner_token,
                    company_run_controller_lease_generation=lease.generation,
                    claimed_work_item_attempt_seq=claimed.metadata['attempt_seq'])
                task.status = TaskStatus.RUNNING
            await store.save_task(task)
            if execution_mode == 'company_mode':
                # Validate the same durable identity/path guards used by file tools
                # before spending tokens. This reserves a scratch path, without writing it.
                receipt = await store.claim_company_artifact_paths_for_controller(
                    project_id=task.project_id, run_id=run_id, work_item_id=item_id,
                    runtime_task_id=task.id, actual_workspace_root=str(workspace),
                    actual_output_root=str(workspace), owner_token=owner_token,
                    generation=lease.generation, attempt_seq=claimed.metadata['attempt_seq'],
                    raw_paths=['smoke-preflight.txt'],
                )
                if not receipt.claimed:
                    raise RuntimeError(f'Company fixture preflight failed: {receipt.outcome}: {receipt.reason}')
            started = time.monotonic()
            print(json.dumps({'event':'case_started','case':case_name}), flush=True)
            try:
                result = await asyncio.wait_for(agent.execute(task), args.timeout)
                checks = await verify() if (workspace / 'sales.py').is_file() else {'passed':False, 'error':'sales.py missing'}
                if case_name == 'company_review':
                    review = json.loads((workspace / 'review.json').read_text()) if (workspace / 'review.json').exists() else {}
                    artifact_ok = review.get('verdict') == 'pass'
                else:
                    artifact_ok = json.loads((workspace / 'report.json').read_text()) == expected if (workspace / 'report.json').exists() else False
                transcript = await store.get_session_transcript(task.session_id)
                files_unchanged = all((workspace / name).is_file() and
                    (workspace / name).read_bytes() == content for name, content in original_files.items())
                parts = [part for item in transcript for part in item['parts']]
                results = [part for part in parts if part.part_type == 'tool_result']
                thinking = [part for part in parts if part.part_type == 'thinking']
                roundtrip = transcript_checks(transcript, await agent.loop._restore_transcript_messages(task), task)
                record = {'case':case_name, 'status':result.status.value, 'elapsed_seconds':round(time.monotonic()-started,2),
                    'acceptance_passed': bool(result.status.value == 'done' and checks['passed'] and artifact_ok and files_unchanged and all(roundtrip.values())),
                    'checks':checks, 'protected_files_unchanged':files_unchanged, 'transcript_checks':roundtrip,
                    'usage':result.token_usage, 'provider_stats':provider.stats,
                    'llm_rounds':sum(event.get('type') == 'message_start' for event in events),
                    'tool_results':len(results), 'tool_output_parts':sum(part.part_type == 'tool_output' for part in parts),
                    'thinking_parts':len(thinking), 'thinking_lengths':[len(part.payload.get('text','')) for part in thinking],
                    'thinking_stream_ids':[part.payload.get('stream_id') for part in thinking],
                    'tool_calls':[part.payload.get('tool_name') for part in parts if part.part_type == 'tool_call'],
                    'failed_tool_results':[part.payload for part in results if isinstance(part.payload.get('result'),dict) and part.payload['result'].get('success') is False],
                    'final_preview':result.content[:2000]}
            except (Exception, CompanyRunControllerLeaseLost) as exc:
                record = {'case':case_name, 'status':'harness_error', 'acceptance_passed':False,
                          'error_type':type(exc).__name__, 'elapsed_seconds':round(time.monotonic()-started,2)}
            if execution_mode == 'company_mode':
                await store.release_delegation_run_controller_lease(
                    run_id, project_id=task.project_id, root_session_id=task.session_id,
                    owner_token=owner_token, generation=lease.generation,
                )
            summary['cases'].append(record)
            (output / 'evidence.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
            print(json.dumps({key:record.get(key) for key in ('case','status','acceptance_passed','elapsed_seconds','llm_rounds','tool_results','error_type')}, ensure_ascii=False), flush=True)
    finally:
        await store.close()
    return 0 if all(item.get('acceptance_passed') for item in summary['cases']) else 1


if __name__ == '__main__':
    args = arguments()
    if not args.execute:
        raise SystemExit('Use --execute to permit real provider calls in a fresh isolated workspace.')
    raise SystemExit(asyncio.run(main(args)))
