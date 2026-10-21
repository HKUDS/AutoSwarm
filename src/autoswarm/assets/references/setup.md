# Configure and recover

Use the installed launcher argv in SKILL.md. Keep technical investigation in the agent's work; report verified availability, results, and anything that actually needs the user's action.

## Configuration

1. Run `agents` from the current App/tool environment. Its `check: executable` verifies CLI discovery, not model access. Confirm the actual command and version; terminals and Apps may resolve different installations.
2. Reuse existing native settings, credentials, project directory, proxy and certificate configuration. If a working CLI is outside PATH, merge its absolute argv into `providers.<provider>.command` in the AutoSwarm home/config.json reported by `agents`. Preserve other settings. Do not copy secrets into prompts or reports.
3. If no usable CLI exists, ask whether to install the requested provider's official CLI. With agreement, follow its current official OS-specific instructions. Do not install other providers or replace the requested agent to hide a failure.
4. Run `doctor --agent <provider> --probe --cwd <workspace>` using the same AutoSwarm home and project as the real task. This creates a native test conversation, checks a reply and native resume, and spends model calls. Only report verification after `verified: true`; an `approval_required` probe is pending, not failed. Answer its original request, collect the result and continue verification. Normal task success is evidence too; do not run extra probes for every task.

## Hermes, Nanobot, DSH and Grok Build

Use provider names `hermes`, `nanobot`, `dsh`, `grok`; `grok-build` is a child-provider alias. They use the same run/resume, messaging and result collection commands. With these main hosts, collect work using wait in the current turn.

| Provider | Native entry | Skill installation |
|---|---|---|
| Hermes | `hermes acp` | `$HERMES_HOME/skills/autoswarm`, default `~/.hermes/skills/autoswarm` |
| Nanobot | Installed Python SDK through the bundled local bridge | Native configured workspace's `skills/autoswarm`, resolved by its own config loader |
| DSH | `dsh --profile acp` | `$DSH_AGENTS_HOME/skills/autoswarm`, default `~/.agents/skills/autoswarm` |
| Grok Build | `grok agent --no-leader stdio` | `~/.grok/skills/autoswarm` |

`setup --host <provider> --skill-dir <path>` overrides the destination when native skill roots differ. Native profile/tool policies still determine which skills, memory and tools are available; AutoSwarm does not replace them. A native transcript/session ID does not guarantee that every product App lists that session.

DSH's ACP profile can select a different route from its configured headless/web profile. Inspect the existing native profile and reuse its provider configuration and credential references. Add compatible overlays with `providers.dsh.args: ["--patch", "<existing-route-patch>", "--patch", "<acp-route-patch>"]`; do not switch to a web/headless application that cannot speak ACP. The ACP route patch replaces the native `acp` entry's complete `config` with the already-configured `provider` and `model` (retain any other desired native ACP fields). Explicit `--model provider/model` can instead select a route advertised by the composed ACP profile. Do not infer missing credentials from the untouched ACP default. Keep machine-specific patches outside the distributed package.

Nanobot runs in the environment that installed its CLI, without another SDK installation. The adapter finds its Python from the launcher or neighboring venv executables. For wrappers where this cannot be inferred, set `providers.nanobot.python` to the existing native Python argv, for example `["<native-venv>/bin/python"]` or `["<native-venv>/Scripts/python.exe"]`. Native `--config`/`--workspace` arguments can go in `providers.nanobot.args`; do not replace its memory/skills workspace with the project cwd. The bridge supplies the project through native session workspace scope. This SDK has no native interactive approval channel, reported as `approval_relay: false`, so AutoSwarm defaults each new/resumed Nanobot session to native Full Access (`access_mode: full`). This is session metadata, not a global native config edit. For restricted access, set `providers.nanobot.args` to include `["--access-mode", "restricted"]` or pass `--provider-arg=--access-mode --provider-arg=restricted` on the call. Native OS/enforced sandbox boundaries still apply; SDK/tool errors remain visible to the parent. Hermes, DSH and Grok permissions use the shared ACP relay without unconditional approval flags.

## Recovery

Start with `inspect <call-id>`, its result diagnostics, handle phase, stdout.log and stderr.log. Check the actual executable and workspace before changing installations.

Some native transports do not copy their transcript to stdout.log. An empty log is not evidence of failure; use the result and native session ID. After final results arrive, verify the requested deliverables and integrate them; additional protocol investigation needs an actual unresolved error.

- CLI missing: resolve/configure an existing path before offering installation.
- Startup: check process exit and startup logs. OpenCode readiness uses its owned loopback health endpoint, not banner wording. A live process awaiting readiness is not proof of successful invocation; inspect or cancel it when investigating.
- Protocol: inspect the failing method/path and installed native version. OpenCode advertises its API at `/doc`. An unsupported API requires a compatible AutoSwarm adapter, not new model credentials.
- Connection: distinguish the local control service from the remote model endpoint. Local loopback traffic must bypass external proxies; remote model traffic keeps native proxy and CA settings. Do not disable TLS verification to hide a certificate problem.
- Authentication: an empty `auth list` does not cover API keys in native config, environment variables or other native credential sources. Judge by the real request. A local OpenCode server 401 concerns its control-channel authentication; a model-provider rejection concerns model access. Ask for native login only when the evidence requires it.
- Environment: compare the working native CLI's profile with the App's tool process. Shell startup exports may be absent; native model keys may reference those variables. Reuse the existing profile through a configured argv launcher instead of copying credentials into AutoSwarm. Copilot BYOK needs its provider environment; CodeBuddy ACP may require `CODEBUDDY_API_KEY` even when a custom model has a key reference. Match the already-selected native provider and endpoint; verify a real call before claiming success.
- Approval: relay the original request using the Skill's approval rules; do not restart the child to bypass it.
- Sandbox: the main host must be able to run the installed launcher and write AutoSwarm's record directory. A read-only filesystem error before a call is accepted is a host permission issue; use its specific approval mechanism for the command/path. Do not disable the native sandbox or replace the supplied launcher paths to work around it.
- Return: a saved result with a failed notification is already available via `wait`/`receive`. Use it; ordinary tasks do not require configuring an App wake-up interface.

Fix routine configuration within the existing authorization and repeat the relevant check. Resume an existing native session when appropriate; if acceptance of a previous operation is uncertain, inspect it before retrying. Ask the user only for missing authorization, login/credentials they must supply through the native interface, or a decision the agent cannot make.
