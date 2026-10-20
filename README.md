# AutoSwarm v2

[简体中文](README.zh-CN.md)

Use Codex, Claude Code, OpenCode, Cursor, GitHub Copilot CLI, Meta Muse Code and CodeBuddy together from your current agent conversation. The main agent configures the available CLIs, delegates distinct work, handles intermediate approvals, collects results and delivers the answer.

Give your main agent this repository and ask:

> Configure AutoSwarm for this agent. Reuse my installed CLIs and native configuration, verify the agents I need through a real call and native resume, then handle my tasks in this conversation.

Windows, macOS and Linux use the same lightweight Python package. A main App needs local command tools and Skill support; children need callable native CLIs. A separate wake-up interface is optional for ordinary tasks.

## Instructions for the configuring agent

1. Inspect the current host and existing Python 3.10+ / CLI installations. Reuse them and their native model, credential, proxy and project settings. Ask before installing a missing provider CLI; do not install every provider.
2. From this checkout, install the package and Skill for the **current host**:

   ```bash
   uv tool install --force .
   autoswarm setup --host codex
   ```

   Other host names: `claude-code`, `opencode`, `cursor`, `copilot`, `muse`, `codebuddy`. Without uv, use a dedicated Python 3.10+ environment and `python -m pip install .`. Keep its interpreter available. The package has no third-party Python runtime dependencies; do not reuse the archived v1 environment.
3. Read the installed `SKILL.md` at the path returned by setup. Use its exact launcher argv when the App cannot find `autoswarm` on PATH. The installed Skill includes its runtime and a [configuration/recovery guide](src/autoswarm/assets/references/setup.md); only its top-level SKILL.md is discoverable.
4. Run `agents` in the current App's tool environment. Confirm the actual executable and version. If a working CLI is outside PATH, merge its absolute argv into `providers.<provider>.command` in the AutoSwarm home/config.json reported by `agents`. Preserve unrelated settings and reuse native authentication.
5. Verify each requested provider in the **real project context**:

   ```bash
   autoswarm doctor --agent opencode --probe --cwd /path/to/project
   ```

   Repeat `--agent` to select several providers. This spends model calls to check a reply and native resume. `verified: true` confirms those checks in this environment. `agents` and doctor without `--probe` only discover executables; an empty native authentication list does not prove that login is required.
6. Report which requested agents were verified, handle unresolved configuration using the recovery guide, then accept the user's task. Keep technical troubleshooting in the agent's work. Ask the user only for missing authorization, required native login or decisions they must make.

`setup` merges Claude lifecycle hooks or installs the OpenCode plugin when those are the main host. Existing unrelated settings are preserved. Native reload/trust requirements still apply. `--skill-dir`, `--settings-file` and `--plugin-dir` choose explicit destinations. After upgrading, rerun setup from the updated package; updating the checkout alone does not update an installed Skill's copied runtime.

Cursor, Copilot and CodeBuddy children use native ACP; Muse uses its native MSP host. The same Skill and run/wait flow work when these agents are the main host; no idle-session wake-up integration is installed for them. Reuse the environment of the working native CLI: App tool processes may not inherit shell startup exports. When necessary, configure `providers.<provider>.command` to use an existing profile-loading launcher; keep credentials in their native source. See the recovery guide before requesting login.

## Handle a task in the current conversation

Read the installed Skill. Prepare shared inputs once and give each child its distinct deliverable. Use parallel calls where useful; the main agent integrates findings, resolves disagreements and verifies the final work.

```bash
autoswarm run --agent claude-code --input implementation.txt --cwd /path/to/project --background
autoswarm run --agent opencode --input review.txt --cwd /path/to/project --background
autoswarm wait call_A call_B
```

Retain the returned call IDs. `wait` can return intermediate `approval_required` events; answer the original request with `approve`/`deny` under the Skill's authorization rules, then wait again until required calls have final outcomes. Check `outcome`, errors and artifacts before answering the user. `ok` describes the CLI operation, not task acceptance.

```bash
autoswarm inspect call_A
autoswarm resume call_A --input followup.txt
autoswarm receive call_B
autoswarm cancel call_A
```

`resume` continues the native child session and keeps the earlier receipt. Interrupting a wait leaves the child running. Native agents retain their full histories; AutoSwarm keeps small file receipts. A missing runner is reported as interrupted and is not automatically rerun. No context, concurrency, turn or task-duration caps are added.

## Recover without making the user debug

Use `inspect` and the call's result diagnostics, handle phase, stdout.log and stderr.log. Distinguish a missing CLI, startup failure, protocol mismatch, local control authentication, remote model access and notification failure. Reuse working paths/configuration; do not respond to every error with reinstall/login. Local loopback requests bypass external proxies while native model requests keep their own network settings.

OpenCode uses an owned loopback service and verifies its health/API before creating a native session. Startup banner wording and stdout/stderr placement do not control readiness. Startup progress remains inspectable and cancellable; definite failures return diagnostics. Task execution and unanswered approvals have no AutoSwarm deadline.

Read [configuration and troubleshooting](src/autoswarm/assets/references/setup.md) when needed. Results saved despite notification failure remain available through wait/receive. Inspect uncertain execution before retrying; do not duplicate work to recover a lost notification.

## Optional original-session callbacks and approvals

`--background` starts parallel work for explicit collection. Add `--notify-parent` only when the original host binding supports callbacks. Codex needs its existing app-server endpoint; Claude uses installed lifecycle hooks; OpenCode uses its installed native plugin. Normal tasks can finish through wait/receive without configuring wake-up.

```bash
autoswarm run --agent claude-code --input task.txt --background --notify-parent --parent parent.json
autoswarm config --delegate-approvals off
autoswarm approvals call_A
autoswarm approve call_A approval_ID --by user
```

Parent approval delegation defaults to on and is configurable globally or per child. A parent decides within the user's task and host authorization; `--by user` relays an actual explicit user answer. Native restrictions remain effective. Follow the installed Skill; details of binding, permission transports and return guarantees are in [the architecture](docs/architecture.zh-CN.md).

## Develop and verify

```bash
python -m unittest discover -s tests -v
node --test tests/test_opencode_plugin.mjs
python scripts/check_distribution.py -- autoswarm
python scripts/live_smoke.py --providers opencode --cwd /path/to/project --output report.json
```

The first checks use controlled peers and local connections; the live check spends model calls and reuses the normal AutoSwarm configuration. CI checks package behavior on Linux, macOS and Windows. Local success verifies only the current platform; App sidebar visibility and wake-up require the actual host interface.

The complete v1 implementation is frozen in [autoswarm-v1.0.0/](autoswarm-v1.0.0/ARCHIVE.md). See [CONTRIBUTING.md](CONTRIBUTING.md) and [MIT](LICENSE).
