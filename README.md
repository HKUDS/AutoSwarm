# AutoSwarm v2

[简体中文](README.zh-CN.md)

Use your installed **Codex, Claude Code and OpenCode** from one agent conversation. Your main agent delegates, receives the results and continues the work. AutoSwarm supplies the invocation and return mechanics; it does not choose agents for particular domains.

The complete v1 implementation is frozen in [autoswarm-v1.0.0/](autoswarm-v1.0.0/ARCHIVE.md). v2 does not import it or reuse its database.

## Install

Use the same Python package on Windows, macOS and Linux. From a source checkout with uv:

```bash
uv tool install .
autoswarm setup --host codex
autoswarm doctor
```

Alternatively install the package into a **new** Python 3.10+ environment. Do not reuse the archived v1 environment. Runtime Python dependencies: none. Existing native agents, their credentials and their own permissions are reused; authenticate an agent through its own interface when needed.

`setup --host codex` installs the Skill and its local Python launcher; choose `claude-code` or `opencode` for those hosts. The installer prints the exact launcher argv, so an App can invoke AutoSwarm without finding it on PATH. The Python interpreter used for setup must remain available. For Claude Code setup merges lifecycle hooks into user settings; for OpenCode it installs a native plugin. Use `--skill-dir`, `--settings-file` and `--plugin-dir` to choose explicit destinations. Existing unrelated settings/hooks are preserved. Native hook trust and reload requirements remain in force. No global host settings are changed until you run setup.

An App can act as the main agent if it can read the Skill and run local commands. Children currently require callable `codex`, `claude` or `opencode` CLIs; an App installation alone does not guarantee that interface. On first use or when a requested provider is unavailable, the Skill guides the main agent to check existing paths and ask whether to install that provider's official CLI. Only agreed installations are performed, using current official instructions for the user's OS; unrelated providers are not installed. Setup and `agents` report availability without installing providers. A configured executable path can reuse a CLI outside PATH, including an App's exposed CLI. Version checks do not prove authentication.

## Delegate and collect

```bash
autoswarm agents
autoswarm run --agent codex --input task.txt --cwd /path/to/project
autoswarm run --agent claude-code --input task.txt --cwd /path/to/project --background
autoswarm run --agent opencode --input task.txt --cwd /path/to/project --background

autoswarm wait call_ID call_OTHER_ID
autoswarm wait --any call_ID call_OTHER_ID
autoswarm receive call_ID
autoswarm resume call_ID --input followup.txt
autoswarm inspect call_ID
autoswarm cancel call_ID
```

All operational commands return JSON. `ok` reports whether the AutoSwarm operation succeeded; inspect `data.outcome` for the native invocation's `completed`, `failed`, `cancelled` or `interrupted` outcome, or `kind: approval_required` for a pending native permission. Completion is not a claim that tests passed. `--input -` reads UTF-8 stdin; positional prompts are also accepted. Prompts reach providers through structured stdin or the local native API, without shell interpolation or OS argument-length limits. Results are not truncated.

`resume` uses the saved native session ID and creates a new call record. It does not replay the full conversation or overwrite the earlier result. Native transcripts remain in each agent's native storage. Pass an explicit `--model` or repeated `--provider-arg=--native-flag` only when you want a native override. AutoSwarm adds no context budget, concurrent-agent quota, maximum turn count or task deadline. User/native limits remain authoritative.

Interrupting a wait leaves background work running. `cancel` requests termination of the child process tree; an already returned result is retained. A missing runner without a final receipt is reported as interrupted, never automatically rerun. A host sandbox that destroys all subprocesses after a shell command exits cannot support detached calls through that shell; use its native integration or a host execution environment that permits background processes.

## Native approvals in the main conversation

When a child requests permission, its original operation pauses. AutoSwarm returns an `approval_required` event through `run`/`wait`/`receive`, or sends it through the same exact-parent host integration used for results. Even `wait` for several children returns immediately on an approval so the parent can respond. This event is not a final result or permission to execute the operation. A foreground `run` returns an array if several approvals arrive together.

Parent approval delegation is **on by default**, and is configurable globally or per child:

```bash
autoswarm config                              # Read the current default
autoswarm config --delegate-approvals off     # Require human decisions
autoswarm config --delegate-approvals on
autoswarm run --agent claude-code --input task.txt --delegate-approvals off
autoswarm approvals call_ID
autoswarm approve call_ID approval_ID
autoswarm deny call_ID approval_ID --message "Use another approach"
autoswarm approve call_ID approval_ID --by user
autoswarm wait call_ID
```

Enabled delegation lets the main agent decide within the user's task and its host's authorization; it does not automatically approve everything. With delegation off, parent-authored decisions are rejected. The main agent asks in the current conversation, waits for the user's answer, and relays that actual answer with `--by user`. This actor label records the source of the decision; a CLI running as the same local user cannot independently authenticate a chat message. The installed Skill explicitly forbids impersonating a user decision or changing delegation to bypass confirmation. Per-child overrides take precedence and carry over on resume; otherwise changes to the global default also apply to pending requests.

The default grant covers the native request. `approve --scope session` is available only when that request supports a session grant. Native explicit denials, managed restrictions and existing auto-approval rules remain authoritative. Codex configurations using its native auto-review may settle a request before AutoSwarm receives it. Denial follows the native offered choices; some Codex requests offer cancellation rather than continuing after rejection. Unanswered requests have no AutoSwarm-imposed deadline. Cancellation or native withdrawal invalidates a pending request. A decision is tied to its exact call/request and cannot be overwritten by a conflicting decision.

Child transport details: Codex uses an owned `codex app-server` over stdio; Claude uses its bidirectional stream-JSON control protocol (`can_use_tool`); OpenCode uses an owned loopback `opencode serve` with native permission events/replies. These processes are started and stopped by the per-call runner, reuse native configuration/authentication and need no separately deployed service or Python SDK. Provider arguments must be supported by that transport; common Codex sandbox/approval and OpenCode model/agent flags are translated into native request fields. Notification and final-result receipts are separate. `deliver call_ID --request approval_ID` retries an approval notification.

The portable UI is a question in the original conversation. A native App approval popup depends on host capabilities. This implements the native command/file/tool permission paths exposed by these transports; OS prompts, managed-policy changes and provider-specific prompts outside those paths are not converted into generic approvals. A Skill cannot independently prove human consent or create every host's native popup.

## Return to the original main conversation

The three interaction paths share the same durable result file:

| Main-agent state | Return path |
| --- | --- |
| Waiting for the child | `run` / `wait` returns through the normal host tool result |
| Working on something else | Collect with `wait` / `receive`, or use the native host integration |
| Finished its current turn | Native integration delivers the output and requests/observes continuation in that same conversation |

A result saved on disk, a submitted notification and an observed continuation are reported separately. AutoSwarm never guesses the latest conversation or opens a replacement parent thread.

**Codex:** `CODEX_THREAD_ID` binds the current conversation when exported by the host. Native return additionally needs the *existing* app-server control socket or WebSocket endpoint. The standard local control socket is detected; `AUTOSWARM_CODEX_ENDPOINT` overrides it. The bridge steers an active turn or starts a turn in the same idle thread. It does not treat `codex queue` success as proof of continuation. Socket access depends on the actual host and sandbox.

```bash
autoswarm bind --host codex --session ORIGINAL_THREAD_ID --endpoint unix:///absolute/control.sock --output parent.json
autoswarm run --agent claude-code --input task.txt --background --parent parent.json
```

Use `ws://` for local endpoints and `wss://` for authenticated remote endpoints. If needed, bind `--token-env VARIABLE_NAME`; credentials are read from that environment variable rather than stored in the binding. `--no-parent` selects explicit wait/receive.

**Claude Code:** setup installs `SessionStart`, `PostToolUse` with native `asyncRewake`, and `Stop` hooks. SessionStart binds the exact parent; the async hook signals new output even when the native session is idle. The Stop hook returns available results or asks the main agent to wait for outstanding calls. Async submission is recorded as `submitted`, not an invented observed turn. Native Claude hook availability and lifecycle limits still apply. Start a new session after setup. The implementation targets the installed 2.1.304 interface.

**OpenCode:** restart after setup and use the native `autoswarm` tool with an `arguments` array containing the CLI arguments. It binds `context.sessionID`, waits for children asynchronously, defers busy-session return to the native `session.idle` event and submits output through the existing native client. It records the returned assistant message as the continuation receipt. Plain shell calls can still use wait/receive; they do not automatically gain the plugin's session binding.

Reloading the plugin restores watchers belonging to that native host instance. A new host process recovers an older conversation when native input confirms that exact session is being continued; another CLI in the same workspace does not take it over automatically. Internal watchers use `wait --peek --unseen`, deliver each approval separately and continue watching for the final result.

For an already reachable OpenCode server, `bind --host opencode --session ID --endpoint http://127.0.0.1:PORT --workspace /project --output parent.json` enables the native HTTP bridge. It observes an assistant response rather than treating HTTP queue acceptance as completed work.

OpenCode's native API must return an assistant message with the final `stop` reason for normal completion. Intermediate `tool-calls` is not completion; the native session ID is retained if the invocation fails. The owned server's exit code describes server shutdown, while the task outcome comes from the native assistant response.

`inspect` exposes `delivery`. A disconnected native endpoint leaves a pending receipt and intact result; transient connection failures can be retried while the runner lives. Permanent binding errors remain explicit. `deliver call_ID` retries native endpoint delivery without rerunning the child; lifecycle-hook returns are handled by the installed host integration. A custom `command` host binding can supply an argv bridge that receives the parent/result JSON on stdin and returns an explicit delivery/continuation receipt.

Native app sidebar visibility is separate from session persistence and return. AutoSwarm exposes the native session ID; whether the native UI lists, opens or attaches it is a provider/client capability. It does not rewrite native databases or source classifications.

## Small local receipts

The data directory defaults to the platform's user state/application-data location. Override it with `AUTOSWARM_HOME` or the global `--home PATH` option.

```text
calls/call_ID/
  task.json       # Original delegation, parent binding, native invocation
  handle.json     # Runner identity and native child session ID
  result.json     # Actual native result and termination information
  delivery.json   # When a return integration is involved
  stderr.log      # Native diagnostics
  approvals/approval_ID/
    request.json  # Exact native request and session, not a copied transcript
    decision.json # Decision and actor, when answered
    resolution.json # Reply, withdrawal, cancellation or interruption
    delivery.json # Optional notification receipt
```

Files are published after complete writes. Calls have independent directories; no SQLite server, shared mutable JSON queue or background scheduler is required. A small OS lease prevents simultaneous AutoSwarm writes to the same resumed native session; this is a session correctness condition, not an agent-count limit. Invocation and return locks release on process exit. Records are not silently expired or pruned.

Optionally configure native executable locations/arguments in the user-owned `config.json`:

```json
{
  "providers": {
    "codex": {"command": ["/path/to/codex"], "args": []},
    "claude-code": {"command": ["/path/to/claude"], "args": []},
    "opencode": {"command": ["/path/to/opencode"], "args": []}
  }
}
```

Executable detection does not claim authentication is valid. Keys, models, project instructions, native permissions and native memory are not migrated into AutoSwarm.

## Develop and verify

```bash
python -m unittest discover -s tests -v
node --test tests/test_opencode_plugin.mjs
python scripts/check_distribution.py -- autoswarm
```

Tests use controlled CLI peers and real local HTTP/WebSocket connections. They exercise delegation, parallel collection, Unicode/large prompts, native session continuation, cancellation, interrupted runners, relocation, exact parent routing, busy/idle races, hook returns and disconnected delivery. macOS/Linux/Windows checks are defined in CI; running tests locally only verifies the current platform.

Optional live checks use configured providers and spend model calls:

```bash
python scripts/live_smoke.py --output /tmp/autoswarm-live.json
python scripts/live_codex_return.py --output /tmp/autoswarm-codex-return.json
python scripts/live_claude_return.py --output /tmp/autoswarm-claude-return.json
python scripts/live_opencode_return.py --output /tmp/autoswarm-opencode-return.json
python scripts/live_approvals.py --output /tmp/autoswarm-approvals.json
python scripts/live_approvals.py --decision deny --output /tmp/autoswarm-denials.json
```

They create isolated native test conversations, not messages in an existing user chat. Run in an environment that permits native network/auth access and subprocess persistence.

Desktop UI behavior still needs validation in the actual installed App; a protocol fixture does not prove that all client versions behave identically.

See [the implementation architecture](docs/architecture.zh-CN.md) for layers, return guarantees and adding a provider. [MIT](LICENSE).
