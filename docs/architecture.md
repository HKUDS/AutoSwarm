# AutoSwarm v2 Architecture

AutoSwarm turns one delegation into a native CLI invocation and sends its result back to the main conversation that started it. The main agent divides the work, chooses agents, manages workspaces, judges results and decides what comes next. The main and child roles belong to the current invocation. Supported agents: Codex, Claude Code, OpenCode, Cursor, GitHub Copilot CLI, Meta Muse Code, CodeBuddy, Hermes, Nanobot, DSH, Grok Build and Kimi Code.

AutoSwarm can be used in two ways: **as a plugin** (one Skill plus the CLI, installed into any main agent and used inside its own conversation) and through **AutoSwarm Desk** (a Windows desktop app, `desktop/`). Desk is just another user of the same CLI; the plugin does not depend on it. The boundary between the two is described in "Plugin core and Desk". Chinese version: [architecture.zh-CN.md](architecture.zh-CN.md).

## Layers and files

| Layer | Implementation | Responsibility and rationale |
| --- | --- | --- |
| Host entry | `assets/skill-template.md`, `setup.py` | Installs the single Skill entry, the relocatable runtime and host integrations; writes the actual launcher argv into the installed Skill to avoid GUI PATH differences |
| Unified tool | `cli.py`, `engine.py` | Provides run, wait, receive, resume, cancel and inspect, so a model reaches local agents through one interface |
| Native agent adapters | `providers.py`, `native.py`, `acp.py`, `muse.py` | Build the argv and pass tasks, approvals and results over each product's native two-way interface, keeping its session ID; one ACP client serves several products |
| Approval handoff | `approvals.py` | Stores, returns and answers each request separately; the main agent can be allowed to decide; reuses existing host channels |
| Conversation messages | `messages.py` | Stores directed messages, enforces the peer rules set by the parent, and handles input during a run, native interrupts and continuing the original conversation; communication failures go back to the assigning agent |
| Invocation execution | `runner.py`, `platform.py` | Runs each invocation on its own, collects the final result, handles cancellation and process identity; no resident scheduler |
| Return to the original conversation | `hosts.py`, `wire.py`, `hooks.py`, `assets/opencode.js` | Connects to the actual original conversation and handles the waiting, busy and idle cases; host abilities are independent of the child agent's brand |
| Small records | `records.py` | Stores delegations, results and delivery receipts with atomic file publication and OS locks; never copies native full histories |
| Model and Skill queries | `models.py`, `skills.py` | `models` lists the models and reasoning efforts an agent reports itself (no model call); `skills` checks, verifies and refreshes the Skill each host can find; shared by the plugin and Desk |

MCP is not a current dependency. The CLI already gives every host that can run commands one calling surface; native hooks/plugins handle asynchronous wake-ups beyond a tool's return. A host that needs MCP later can add a thin tool layer outside the same core without replacing execution or records.

## Plugin core and Desk

| Layer | Location | Contents |
| --- | --- | --- |
| Plugin core | `src/autoswarm/` (without `desk/`) | The layers above: Skill, CLI, native adapters, approvals, messages, execution and records. Plugin use needs only this layer |
| Desk backend | `src/autoswarm/desk/` | Desk's identity and private assistant (`control.py`), the `autoswarm desk …` subcommands (`cli.py`), the record snapshot for the window (`watch.py`), parsing of process events (`lens.py`, `streams.py`), reading Claude Code/Codex/Cursor's own session records (`native_history.py`, `native_calls.py`), reading the current Windows user environment (`environment.py`) |
| Desk app | `desktop/` | Electron main process (`electron/`) and interface (`renderer/`); the list of agents is `renderer/agents.json` |

Dependencies point one way: the Desk backend calls the core, the core never calls the Desk backend. The only connection is `cli.py` registering the `desk` subcommands (defined and dispatched in `desk/cli.py`). The Desk app uses AutoSwarm only through the CLI (`python -m autoswarm …`) and the `autoswarm.desk.watch` process; it never reads or writes the record directory itself.

A few things are **turned on by Desk but carried out in the core**, because they must take effect when an agent starts or a message is delivered. None of them does anything without Desk:

- Permission levels (`permissions.py`): translated into each product's native settings only when a call carries Desk's permission table (`desk start/assistant --permissions`); otherwise `apply` returns at once and agents keep their own settings.
- Keeping process events (`capture.py`): `events.capture` is off by default; Desk sets it to `auto`, which keeps events only for agents without readable local records.
- Human input (message kind `user_message`): produced only by `desk message`, delivered by continuing the original conversation.
- ACP session modes (such as Cursor's Plan): set only from a Desk permission level.

The `models` and `skills` commands and the fixes described below (Windows sandbox accounts, job objects, Hermes stalling, completing permission requests and so on) apply to both ways of use. `setup` copies the whole package, `desk/` included, into each host's Skill. `desk/` is never called in plugin use; keeping it lets the private assistant read its other conversations (`desk transcript`) through any launcher.

Tests follow the layers: `tests/` covers the core, `tests/desk/` the Desk backend, `desktop/tests/` the Desk app (Node unit tests and Electron tests).

## One invocation

1. The main agent calls `run` through the Skill with the task, the agent and the workspace. The host integration attaches the exact binding of the original conversation.
2. The engine checks the CLI executable, publishes `task.json` and starts an independent runner. The task reaches the agent through structured stdin or a local native API, never through an assembled shell command.
3. The runner keeps the native authentication, model and project settings, parses native events and obtains the native child session ID. The full native history stays with the product itself.
4. When the child completes, fails or is cancelled, the runner atomically publishes `result.json`. The result exists even if returning it fails.
5. A foreground call returns through the normal tool return; a background call uses the native integration, or the main agent collects it later with wait/receive.
6. The main agent verifies the result and continues. To ask the same child again, resume uses the native session ID and creates a new invocation record, keeping the previous result.

A finished native turn and an accepted user task are handled separately. The code has no automatic review, fixed retry count or preset context compaction.

The optional --model and --effort of run/resume are stored as the task's selection; without them native defaults apply, and resume and idle message continuations inherit earlier explicit choices. Codex sends the selection with every turn/start; Claude/Copilot use native launch flags; ACP sets the model and the effort control the agent advertises before the first prompt; Muse sets the session model and reasoningEffort in order; OpenCode maps the effort to a variant of the current model and checks it against live metadata. Model and effort values come from the native interfaces; there is no shared enumeration or model registry. Errors are kept and handed to the parent agent, never silently downgraded. requested_selection in inspect/result records the request; it does not claim what the backend applied.

Cursor negotiates parameterizedModelPicker and uses the parameter controls its models return to set effort and explicit bracket parameters. Its ACP setters were observed to write global preferences, so an explicit selection uses the native CURSOR_CONFIG_DIR: a private temporary directory reuses the existing root JSON settings while native data/session directories stay unchanged, and the temporary copy is deleted on exit; no full history, plugins or long-lived configuration branch is copied. Native authentication and permissions stay in force.

## Installation and CLI availability

All three operating systems share the Python package and the same Skill. setup copies the local runtime and writes the full launcher argv; there is no separate per-platform package. An App acting as main host needs a local command tool; a child agent needs its CLI, and the App itself is not guaranteed to provide a callable interface.

On first use, or when a requested agent is unavailable, the Skill guides the main agent to check existing commands, PATH and any CLI the App exposes. If one is really missing, it asks the user whether to install that agent's official CLI, installs it following the current official instructions for the system once the user agrees, checks agents again and continues the original task. An existing CLI can be configured by path; authentication or permission errors are not treated as a missing installation. Discovery and launch failures come with diagnostics; there is no installation service, extra state machine, or automatic installation of every product.

When a command is not on PATH (for example, installed but not yet picked up by a new login), the default folders of the official installers are checked: Hermes (`%LOCALAPPDATA%\hermes\bin`), Grok (`~/.grok/bin`), Muse (`%LOCALAPPDATA%\Programs\muse`), `~/.local/bin` and `%APPDATA%\npm`. A command the user configured explicitly is used as given, never replaced by a guess.

The runner records the system account it runs as. A native sandbox may run the main agent's commands as another account (Codex on Windows uses `CodexSandbox*` accounts); an agent started from there would lack the user's environment, logins and network, so when the accounts differ the start is refused with the reason, and the main agent re-runs the same command outside the sandbox through its own escalation. The sandbox is never disabled or bypassed. On Windows, runner and message-delivery processes ask to leave their parent's job object (granted only where the job allows it), so an agent that ends each command's processes does not end a background runner with them.

The CLI's --background only controls whether the tool returns at once; the default return_mode is wait. --notify-parent turns on native return explicitly; without a usable binding of the original conversation it tells the agent to use wait/receive. Ordinary tasks dispatch, collect, handle approvals and summarize within the current turn, without a wake-up interface. The native return abilities underneath stay available for verified hosts.

agents/doctor's quick discovery only reports the executable check, and invocation_verified defaults to false. An explicit doctor --agent NAME --probe uses a normal invocation and resume to verify a reply and session memory with the current AutoSwarm configuration, the real project directory and the native environment, never a temporary environment that loses project settings. Verification is a fact at one moment, not a permanent certification. The template of an installed runtime uses plain file names and removes the nested SKILL.md of old versions; installing again from an installed launcher keeps working.

## Native approval handoff

Cursor, Copilot, CodeBuddy, Hermes, DSH, Grok Build and Kimi Code share the stdio ACP client: initialize negotiates, session/new is followed by session/prompt; continuation chooses session/load or session/resume by native ability, the stopReason of the prompt response decides the final state, and replayed history is kept out of the new result. session/request_permission uses the same approval broker and answers with the native optionId. allow_always may persist, so it is never presented as a session grant; only one-time approvals are relayed. Kimi runs `kimi acp` and keeps its native config.toml, permissions, Skills and MCP; model and thinking choices use the native ACP controls. A native AskUserQuestion is matched by tool call ID and answered through the form reply, so a main agent may answer it within its authorization; unmatched forms and URL consent still need the user.

When a permission request names only its tool call ID (DSH) or only the tool's name (Kimi's Bash, Write), the ACP client fills in the title, kind, input and locations that the call reported in earlier tool_call updates; the request's own fields win, so whoever decides sees the command or file. On Windows, Hermes reads its stdin on one thread with a blocking read; a child it starts (git, a shell command) inherits that pipe and cannot start until the read returns. AutoSwarm sends Hermes a JSON-RPC reply it silently drops every second so the read returns. The Nanobot bridge moves the ACP wire to descriptors children do not inherit (a tool's stdin is the null device and its stdout goes to stderr) and passes AutoSwarm's call identity variables to its shell, so the agents it starts belong to the right call.

Nanobot has no native ACP entry. The Python that owns its CLI runs the local `nanobot_bridge.py`, which maps the same ACP wire onto the native SDK's run_streamed, sessions and cancellation. Separate native session keys keep invocations from sharing a conversation; the native configuration owns models, memory, Skills and tools, and the project directory is passed as native workspace_scope metadata without replacing the agent's memory workspace. The SDK has no native interactive approval channel, so AutoSwarm writes access_mode: full when creating or resuming a session and uses native Full Access; the Nanobot provider argument --access-mode restricted selects the restricted mode explicitly. The access mode lives in session metadata; model/effort overrides apply only inside the process. The bridge adds no third-party dependency and does not modify native global configuration; errors reach the main agent through the existing diagnostics. DSH keeps its native ACP profile and configuration overlays; Grok uses a dedicated agent entry, so cancellation never leaves work with a shared leader.

Muse uses its native MSP: initialize, session/start or session/resume, turn/start, with turn/completed marking the end. The display receipt of approval/request is kept apart from the real decision in approval/decide; currentRequirementId is kept, and an old request is withdrawn when the approval stage changes. Each write command generates the UUIDv7 idempotency key Muse requires and picks the native option matching the granted scope.

These new main hosts install the same Skill and collect results and approvals with run/wait within the current turn. No wake-up interface for idle Apps is added, and CLI success is never taken as proof that a GUI sidebar shows the conversation.

The Codex child uses its own app-server over stdio JSON-RPC, receives requestApproval for commandExecution, fileChange and permissions, and replies on the same connection with the same request ID. That connection belongs to this child invocation and is separate from the connection that delivers messages to the main Codex conversation.

The Claude child uses the same two-way stream-JSON control protocol as the native SDK: `--permission-prompt-tool stdio` receives can_use_tool requests and control_response answers them. Native system prompts, settings sources and permission modes are kept, without the SDK's extra dependencies or defaults. Compared with a synchronous PermissionRequest hook waiting a long time for a person, this channel adds no hook waiting lifecycle.

The OpenCode child starts its own serve for this invocation on a local port chosen by the operating system, confirms the service and the required interfaces through /global/health and /doc, then creates a session, subscribes to SSE and submits the task. Startup text and stdout/stderr placement play no part in readiness. There is a race window between choosing the port and the actual bind; a native bind failure is reported as a startup failure and no other process is taken over. permission.asked creates a request; the native reply interface answers it. The final assistant response ends the invocation and the runner reclaims its own service. Users deploy or manage no resident service.

Local HTTP control and SSE use the same authentication and proxy policy: the native service password is kept and loopback traffic bypasses external proxies (`providers.opencode_environment` adds localhost to NO_PROXY whenever a proxy is set); remote model requests stay with the native product. Clear HTTP authentication, protocol and connection errors keep the stage and interface they came from; before readiness, handle.phase, endpoint and diagnostic can be inspected, and cancellation does not wait for the handshake. No fixed startup deadline is added; a live process alone does not count as a verified invocation. When the native API does not fit the adapter, a version/protocol problem is reported plainly instead of being hidden by reinstalling or re-authenticating.

A cold-started service may accept a connection but not answer the first health request, so a single health GET yields and retries. This network polling does not limit the total startup wait, the model task or approval waits; normal message POSTs are never retried automatically, to avoid running anything twice.

`approvals/approval_ID/request.json` stores the native request, the native session/request IDs, the workspace and the available approval scopes. The main tool receives `kind: approval_required`; busy, idle and waiting main conversations all reuse the existing return mechanisms. Approval notices use their own delivery receipts and can never mark the final result as received. Several requests arriving together in the foreground are returned as one complete array.

The main agent publishes decision.json with approve/deny; the process holding the native connection answers the original request with it and stores resolution.json. A decision never runs a command or re-dispatches a task. The operation stays paused by the native permission system until it is approved; after a native withdrawal, task cancellation or process interruption, late decisions are refused. An opposite decision cannot overwrite a stored one.

`approvals.delegate_to_parent` defaults to true; CLI config changes it persistently and run/resume can override it per call. When it is off, decisions from the parent are refused; the main agent asks the user in the original window and relays the answer with source user. The source field is a convention between local callers, not an independent authentication of a human. The Skill allows marking only the user's actual answer as user, and forbids turning on delegation to avoid asking. Native prohibitions, managed restrictions and existing automatic approvals stay in force.

wait does not wait for a child that is waiting for approval: any unanswered request returns to the main agent at once. After the Claude Stop hook first delivers a request, it lets the main agent end the current turn to wait for the user instead of forcing a wait that would deadlock. The OpenCode watcher obtains a separate native message receipt for each request and keeps waiting for later requests or the final result. Unanswered requests add no AutoSwarm timeout and stop no other child.

## Three return situations

| Situation | Codex main agent | Claude Code main agent | OpenCode main agent |
| --- | --- | --- | --- |
| Waiting | run/wait tool returns | run/wait tool returns | run/wait tool returns |
| Working | turn/steer on the original app-server; or collect later | native async PostToolUse hook submits the result; collected at Stop | plugin delivers after session.idle; or collect actively |
| Current turn has ended | the original app-server runs turn/start on the same thread | native asyncRewake asks to continue; the Stop hook keeps necessary results from being left behind | the native client submits the result in the same session and observes the assistant's response |

A Codex thread ID alone cannot wake an App that is in use; that needs the control socket/service it actually runs on. The standard control socket is detected automatically and can also be bound explicitly. Without a connection the result can still be collected, but no successful wake-up is claimed.

Claude Code's SessionStart creates the binding of this conversation; the asynchronous PostToolUse watcher triggers the native asyncRewake with exit code 2. The Stop hook gives necessary results to the model, or asks it to wait for children still running. An asynchronous hook can only confirm submission, so its receipt is written as submitted; it never pretends to have observed the assistant's next output.

The OpenCode plugin takes the session ID from the tool context. The background watcher only reads results and does not write the main conversation's received marker early; the delivered receipt is published only after the native prompt returns an actual assistant message. A plugin reload resumes only the watchers of the same native host instance; a new process must confirm through the native chat.message which exact conversation is continuing. Another CLI in the same workspace never takes over the original conversation automatically.

An ordinary CLI has no general way to inject a message into an arbitrary GUI conversation. Whether an App, IDE extension or terminal can return results actively depends on the host connections it actually exposes. Once the Skill is installed, every host that can run commands can call and collect explicitly; native asynchronous return needs the corresponding connection or hooks/plugins.

## Minimal state

Invocation state is derived from facts: returned when a result exists; running while the runner holds the invocation's `runner.lock` system file lock, regardless of whether the observer can see its PID. Finishing or process exit releases the lock; process identity checks remain for exit handling and old invocations; interrupted only when there is no result and neither lock nor process shows it alive. Result outcomes are completed, failed and cancelled; an abnormal interruption can be reported as interrupted through the collection interfaces. No heartbeat or liveness timeout is added.

Delivery receipts describe separate facts: pull means it can be collected actively, pending means not yet delivered, submitted means the host accepted the wake-up notice, delivered means the native mechanism returned a delivery receipt. continued says whether that mechanism confirmed starting/appending further execution; it does not mean the work was done correctly. The strength of each host's receipt is kept in mechanism.

`received.json` means the main tool has read the result, so it is not pushed again automatically; it does not mean the model accepted it. Internal reads by plugins use wait --peek and do not write this marker.

There are no companies, task cards, global task queue or extra scheduling state machine. A failed automatic return never re-runs a child. A process that disappears abnormally is not re-run on its own either, to avoid modifying files twice.

## Files, concurrency and recovery

Each call has one directory holding the task, the run handle, the result, optional delivery receipts and diagnostics. Files are written completely to a temporary file, flushed/fsynced and atomically replaced within the same directory. Different calls never compete for one JSON queue. On Windows a call directory inherits the record directory's permissions (already private to the user), so a call created from inside an agent's sandbox stays readable to the user and to Desk; elsewhere it is created with mode 0o700.

On Windows, record read handles let other processes replace files atomically while keeping the complete snapshot being read. Reads and writes retry only on native sharing conflicts; a real access denial is reported directly and system permissions apply.

OS locks protect continuing the same native session, delivering the same event, publishing decisions and updating configuration. Locks are released when a process exits, so there are no lease periods or background cleanup, and no limit on the number of invocations. Linux/macOS use flock; Windows uses byte-range locks.

Process identity uses the creation time the system provides, not just the PID, so PID reuse is never mistaken for a live run. Cancellation targets the child process tree the runner actually holds, establishing ownership only from parent/child relations and creation times; other conversations are never ended by process name.

Windows uses Toolhelp process snapshots and native process handles to end descendants and wait for them to exit. Handles are kept during cleanup so PIDs cannot be reused; a second enumeration reclaims children created after the first snapshot. Remaining children are reclaimed even when their parent exited first. Native enumeration, access and termination errors are passed up.

Publishing a result and handling it in the host are not one cross-process transaction. A call with a stored delivered receipt is never delivered again; if a connection breaks after the host accepted but before the receipt was saved, strict exactly-once cannot be promised across all hosts. Call IDs and native receipts are kept for inspection instead of hiding the problem by opening another main conversation.

The core sets no context size, agent count, turn count, task timeout or record expiry. Polling intervals for processes/files are an implementation detail and never end a task. Native product settings and the user's own settings stay in force.

## Extending

A new child agent: add its CLI command construction and event parsing, and return the native session ID, final content and errors; without native resume, report that it cannot be continued instead of copying history to imitate a native session.

A new main host: prefer native events/plugins/session APIs to bind the original session, and implement the tool return and asynchronous continuation separately. The existing command bridge can take stdin JSON and return a delivery receipt; never mark a conversation continued after merely notifying the user.

Domain knowledge for choosing agents stays out of the infrastructure. Different brands use the same directed message protocol, with no shared chat room, database or resident message server.

## Directed messages and continuing the original task

`send`, `send --interrupt` and `reply` reuse the existing CLI. The assigning parent sets names, contacts and duties with name, allow-peer and communication-rules; names are unique within that parent's conversation, and peers that start later can be declared in advance. A child inherits its own identity automatically, never the parent's. Contacting the parent and replying to messages actually received need no extra peer permission; other peers must be allowed by the assigning agent. This is a local collaboration convention, not a security boundary against malicious processes of the same user.

The first call ID is also the stable agent_id; a native continuation gets a new call_id and keeps the agent_id. Without a native host identity a controller_id is returned, and the main agent keeps using it through the global --controller. agents/agent_ID/agent.json stores only the current call, the assigning agent, contacts and instructions. Each message is written separately to messages/msg_ID/message.json, and delivery.json records queued, submitted, delivered or needs_attention. A reply points to the original message with reply_to; no global conversation table or copy of native history is needed.

The runner checks the mailbox while the native session is alive. Codex and Muse prefer turn/steer; OpenCode uses native message input with noReply. ACP v1 and Claude, lacking an equivalent in-run steer interface, let ordinary messages wait for the native turn boundary; urgent messages stop the current turn through session/cancel or the native interrupt, wait for the end event, then submit the message and an instruction to continue the original task in the same native session. A CLI is never killed to deliver a message. The model returns early answers with reply; the original task's result is published only when the later execution ends. A result already completed is kept while messages are handled afterwards and is not replaced by the communication answer.

wait --message exposes related replies, other incoming messages and approvals of tasks the agent delegated; when both sides wait, either can answer the other's question first. wait call_ID likewise returns intermediate events, including approvals of child calls continued because of messages; the main agent must check the kind and keep collecting the final result. A main host can collect within its current tool flow; when an exact Codex/OpenCode connection to the original conversation exists, a one-off background executor reuses the existing return adapters to deliver messages without blocking the sender's own tool. Messages whose notification failed stay available to the tools; it is never pretended that any App can be woken while idle.

When a native turn ends, its old approval requests are withdrawn; new approvals from continuing the same conversation are still handled by the original assignment, and messages never widen permissions. A completed, idle conversation is continued natively by its own runner to receive messages; the dispatch OS lock prevents starting it twice. Messages that interleave with finishing are handled by the current runner or by the wake-up after finishing. Failed, cancelled and lost conversations are never re-run automatically; their messages are kept and a communication_issue goes to the assigning parent, which inspects the diagnostics and chooses to continue, redeliver or rearrange. A native refusal of one input does not end the original task or other calls.

A child's native sandbox may not see the runner's process namespace, so a peer is never judged dead because its PID is not visible from the sandbox. Conversations in progress without a final result keep their messages, and the assigning main agent diagnoses interruptions from the call records. CodeBuddy's native cancellation barrier may still refuse a prompt right after the current turn ended; only when the response carries the old request ID and no new userMessageId, confirming the new input was not accepted, does the executor yield one polling round and submit the same input again. Accepted work is never re-run, and no fixed retry count or total wait is added.

submitted only proves that the native side accepted the input; reply_to links the actual answer. Strict exactly-once between native acceptance and saving the local receipt cannot be provided across all products; when in doubt, IDs and errors are kept for the parent to inspect rather than hidden by a silent re-run. Message records have no automatic expiry, fixed wait or limit on communication rounds.

## Validation scope

Automated tests use controlled CLIs, real local WebSocket/HTTP and subprocesses; live scripts additionally call the native products configured on the machine. CI defines package installation and behaviour checks on Linux, macOS and Windows. Local tests only prove the local platform; whether an App's UI shows a conversation still needs acceptance in the real client.

`scripts/check.py` builds and installs the package in an isolated virtual environment and runs `tests/` (including `tests/desk/`), the OpenCode plugin tests, a relocatable Skill installation and the installed distribution check (`scripts/check_distribution.py`: twelve providers, approval and question round trips, native resume, messaging through the installed launcher, reinstallation). This check does not need Desk. The Desk app has its own `npm test` and `npm run test:*` Electron tests under `desktop/`.

## Native interaction relay

Permissions and questions share the per-call file records and waiting flow, with no new service or database. `approval_required` still uses approve/deny; `interaction_required` carries the original request, the response format and whether the user must answer, and is answered with a format-checked JSON through answer. Supported: Codex blocking questions, asynchronous question items (answered by steer in the same turn) and elicitation; Claude AskUserQuestion/elicitation; Cursor questions/plans; OpenCode question; Muse MSP userInput; ACP elicitation; CodeBuddy AskUserQuestion is answered through its native resolveInterruption. Only implemented abilities are negotiated; unknown Claude dialog kinds are not declared.

Muse's display receipt is kept apart from the actual answer; after the parent conversation reads the question it sends engaged and follows the native timing rules. Muse/CodeBuddy commands wait for the native ACK, and OpenCode checks the HTTP receipt. Native withdrawal, cancellation and the end of a turn invalidate old requests; a new turn on the same connection gets a different interaction record even when the native RPC ID is reused. Repeated notices share the original record, and late/duplicate RPC responses are not treated as new requests.

Each delivery keeps its attempts and errors. After an explicit refusal it can be corrected by hand; an uncertain operation is never replayed automatically, and one failed answer does not end the child task. Unknown interactions keep their full payload and send a communication issue to the assigning parent, which can inspect the native session, correct it, cancel and continue, or rearrange the work while independent tasks go on. wait/receive, Claude hooks, the OpenCode plugin and any available return to the original conversation all recognize intermediate interactions; diagnostics in wait mode never wake the host unexpectedly. User authorization, host permissions and native safety rules stay in force.
