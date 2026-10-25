<div align="center">

<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/autoswarm/autoswarm-icon.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/autoswarm/autoswarm-icon-on-light.svg">
    <img src="docs/assets/autoswarm/autoswarm-icon-on-light.svg" width="48" height="48" align="absmiddle" alt="">
  </picture>
  AutoSwarm: All Your Agents. One AI Swarm
</h1>

[简体中文](README.zh-CN.md)

**Unify your agents. Unify your context. Work as One**

[![Feishu](https://img.shields.io/badge/Feishu-Group-E9DBFC?style=flat)](./COMMUNICATION.md) [![WeChat](https://img.shields.io/badge/WeChat-Group-C5EAB4?style=flat&logo=wechat&logoColor=white)](./COMMUNICATION.md)

### Why AutoSwarm

**No single agent can do it all. You don’t need another agent—you need all your agents working as one.**

</div>

Different agents come with different:
- **Capabilities** — coding, research, browsing, office work, writing, computer use, and social networking;
- **Context and Memory** — each agent maintains its own context, with little or no sharing across agents;
- **Tools and Workflows** — each agent follows its own interaction patterns and execution process;
- **Data and Application Ecosystems** — agents are connected to different platforms, services, files, and data sources.

Today, you are forced to switch interfaces, repeat information, coordinate subtasks, and manually combine results.

**AutoSwarm turns fragmented agents into one coordinated team.**

Give AutoSwarm a task once. It unifies all your agents into one coordinated team:
-**Selects the right agents** based on their capabilities;
-**Shares context and memory** across agents;
-**Connects complementary tools** and workflows;
-**Bridges data** and application ecosystems;
-**Runs tasks in parallel** for faster execution;
-**Coordinates agent collaboration** and keeps everyone aligned;
-**Verifies and synthesizes results** into one coherent answer.

## 🤖 Supported Agents

<table width="100%">
  <tr>
    <td align="center" width="25%"><p><img src="docs/assets/agents/codex.svg" width="48" height="48" alt=""><br><strong>Codex</strong></p></td>
    <td align="center" width="25%"><p><img src="docs/assets/agents/claude-code.svg" width="48" height="48" alt=""><br><strong>Claude Code</strong></p></td>
    <td align="center" width="25%"><p><img src="docs/assets/agents/opencode.svg" width="48" height="48" alt=""><br><strong>OpenCode</strong></p></td>
    <td align="center" width="25%"><p><img src="docs/assets/agents/cursor.svg" width="48" height="48" alt=""><br><strong>Cursor</strong></p></td>
  </tr>
  <tr>
    <td align="center" width="25%"><p><img src="docs/assets/agents/copilot.svg" width="48" height="48" alt=""><br><strong>GitHub Copilot CLI</strong></p></td>
    <td align="center" width="25%"><p><img src="docs/assets/agents/muse.svg" width="48" height="48" alt=""><br><strong>Meta Muse Code</strong></p></td>
    <td align="center" width="25%"><p><img src="docs/assets/agents/codebuddy.svg" width="48" height="48" alt=""><br><strong>CodeBuddy</strong></p></td>
    <td align="center" width="25%"><p><img src="docs/assets/agents/hermes.svg" width="48" height="48" alt=""><br><strong>Hermes</strong></p></td>
  </tr>
  <tr>
    <td align="center" width="25%"><p><img src="docs/assets/agents/nanobot.png" width="48" height="48" alt=""><br><strong>Nanobot</strong></p></td>
    <td align="center" width="25%"><p><img src="docs/assets/agents/dsh.svg" width="48" height="48" alt=""><br><strong>DeepSeek Harness</strong></p></td>
    <td align="center" width="25%"><p><img src="docs/assets/agents/grok.svg" width="48" height="48" alt=""><br><strong>Grok Build</strong></p></td>
    <td align="center" width="25%"><p><a href="#whats-next"><img src="docs/assets/agents/more.svg" width="48" height="48" alt=""><br><strong>More agents coming</strong></a></p></td>
  </tr>
</table>

Works on Windows, macOS and Linux. Use it in an App, CLI or IDE extension with Skill support and local command execution; delegated agents need callable local CLIs.

<!-- GitHub-native video attachment, hosted in https://github.com/HKUDS/AutoSwarm/issues/67. -->
https://github.com/user-attachments/assets/4f6ee896-5f25-4c49-bce2-d5351f78521d

## 🚀 Get started

Give your current agent this repository and ask:

> Install or update AutoSwarm for this agent. Reuse my existing configuration and verify the availability of my locally installed agents.

For everyday use, start a task with **“Use autoswarm”**:

> Use autoswarm to read these 100 documents and summarize the technical approaches and key disagreements.

Your agent chooses collaborators and adapts parallel and dependent work as it progresses.

## 📰 News

- **OpenOPC → AutoSwarm:** v2 brings the swarm into the agents you already use. The complete v1.0.0 implementation is preserved in the [archive](autoswarm-v1.0.0/ARCHIVE.md).

## What's next

- Support more agent products and their native capabilities.
- Continue improving coordination, communication, reliability and setup experience.

## For agents

<details>
<summary>Installation and updates — instructions for the configuring agent (click to expand)</summary>

### Install

1. Inspect the current host and existing Python 3.10+ / CLI installations. Reuse native models, credentials, proxy settings and project context. Ask before installing a missing provider CLI; configure only the agents needed.
2. For a first installation, use `git clone` to obtain the official repository, then install the package and Skill for the **current host**. Reuse an existing checkout when available. Example for Codex:

   ```bash
   git clone https://github.com/HKUDS/AutoSwarm.git
   cd AutoSwarm
   uv tool install --force .
   autoswarm setup --host codex
   ```

   Other host IDs: `claude-code`, `opencode`, `cursor`, `copilot`, `muse`, `codebuddy`, `hermes`, `nanobot`, `dsh`, `grok`. Without uv, use a dedicated Python 3.10+ environment and `python -m pip install .`; keep its interpreter available. Do not reuse the archived v1 environment.
3. Read the installed `SKILL.md` at the path returned by setup. It contains the launcher argv and usage instructions. Use that exact launcher when the App cannot find `autoswarm`. Run `agents` in the actual host environment; if needed, merge an existing CLI's absolute argv into `providers.<provider>.command` in the reported AutoSwarm home/config.json. Preserve unrelated settings.
4. Verify requested providers in the actual project, replacing the example provider and directory as appropriate:

   ```bash
   autoswarm doctor --agent codex --probe --cwd /path/to/project
   ```

   Repeat `--agent` for several providers. This spends model calls to check a reply and native resume; `verified: true` confirms the probe. Version checks only establish executable discovery. An empty native authentication list does not prove that login is required. Handle pending native approvals/questions under the installed Skill's rules, then finish verification.

Setup installs the Skill, its runtime and the host's integration. Follow the returned reload/trust instructions. See [configuration and recovery](src/autoswarm/assets/references/setup.md) for native profiles, permissions and connection diagnostics.

### Update an existing installation

Obtain the requested revision from the original source. For an existing Git checkout, preserve local edits, collect active delegated work, then run on the intended branch:

```bash
git pull --ff-only
uv tool install --reinstall .
autoswarm setup --host codex
```

Use the actual host, existing AutoSwarm home and original installation destinations. Put `--home <existing-home>` before `setup` when needed; retain custom `--skill-dir`, `--settings-file` and `--plugin-dir` values. Refresh each host installation in use. For pip installations, use the existing dedicated interpreter to run `python -m pip install --force-reinstall .`, then `python -m autoswarm setup --host codex` with the same options.

Run setup **from the updated package**: the installed Skill has its own runtime copy, so updating the checkout or package alone is insufficient. The old Skill launcher cannot fetch a new version. Keep configuration, records and native authentication, reload the host as required, read the refreshed Skill and verify the requested providers. There is no automatic updater. See [upgrade details](src/autoswarm/assets/references/setup.md#upgrades).

### Use and recover

Follow the installed Skill to select agents, delegate ready work, collect results with `wait --any`, handle intermediate questions/approvals and verify deliverables before answering. Ordinary tasks use the current conversation; native wake-up is optional and needs a supported host binding.

Read only the relevant references: [capabilities and data scope](src/autoswarm/assets/references/agent-capabilities.md), [communication](src/autoswarm/assets/references/communication.md), [native interactions](src/autoswarm/assets/references/interactions.md), [model selection](src/autoswarm/assets/references/models.md). Diagnose retained calls and logs before retrying uncertain operations.

</details>

[Architecture](docs/architecture.zh-CN.md) · [Contributing and validation](CONTRIBUTING.md) · [MIT License](LICENSE)
