<div align="center">

<h1>
  <img src="docs/assets/autoswarm/AutoSwarm-logo.png" width="48" height="48" align="absmiddle" alt="AutoSwarm logo">
  AutoSwarm: All Your Agents. One AI Swarm
</h1>

<!-- GitHub-native video attachment, hosted in https://github.com/HKUDS/AutoSwarm/issues/67. -->
https://github.com/user-attachments/assets/89a077a9-168e-441f-8aac-48de321aa968

[English](README.md)

**让你常用的 agent，带领多个 agent 协作。**

将 AutoSwarm 作为轻量插件接入正在使用的 agent，由它按需组织本地多个 agent 产品协作，负责分工、沟通与结果整合。你只需像往常一样提出任务。

[![Paper](https://img.shields.io/badge/Paper-arXiv-B31B1B?style=flat&logo=arxiv&logoColor=white)](https://arxiv.org/abs/2610.33048) [![Feishu](https://img.shields.io/badge/Feishu-Group-E9DBFC?style=flat)](./COMMUNICATION.md) [![WeChat](https://img.shields.io/badge/WeChat-Group-C5EAB4?style=flat&logo=wechat&logoColor=white)](./COMMUNICATION.md)

</div>

## ✨ 三个适合 AutoSwarm 的场景

- **🧩 跨产品能力互补**：任务需要不同 agent 或模型的优势、工具与数据入口时，组织合适的 agent 协作完成。
- **⚡ 大量任务并行处理**：例如快速阅读 100 份资料，让多个 agent 并行提炼，再由主 agent 统一汇总；也支持同时调用多个同款 agent。
- **🔍 重要任务一做一审**：例如关键代码开发，由一个 agent 实现，另一个独立审查与验证，提高交付质量。

## 🚀 开始使用

把本仓库地址发给你正在使用的 agent，直接说：

> 请为当前 agent 安装或更新 AutoSwarm，复用已有配置，验证本地已有 agent 的可用性。

日常需要 swarm 时，在任务前加上 **“使用 autoswarm”**，例如：

> 使用 autoswarm，阅读这 100 份资料，归纳技术路线与主要分歧。

由主 agent 自行选择协作者、灵活安排串并行。

## 🤖 支持的 agent

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
    <td align="center" width="25%"><p><a href="#后续计划"><img src="docs/assets/agents/more.svg" width="48" height="48" alt=""><br><strong>更多 agent，持续接入</strong></a></p></td>
  </tr>
</table>

支持 Windows、macOS、Linux。可在支持 Skill 和本地命令执行的 App、CLI 或 IDE 扩展中使用；被调用的 agent 需有可用的本地 CLI。

## 📰 News

- **OpenOPC → AutoSwarm**：v2 将 swarm 能力带入用户已有的 agent。旧版完整实现保留在 [v1.0.0 归档](autoswarm-v1.0.0/ARCHIVE.md)中。

## 后续计划

- 接入更多 agent 产品及原生能力。
- 持续完善协作、通信、可靠性与安装使用体验。

## 🌟 Citation

If you find this work helpful to your research, please kindly consider citing our paper.

```bibtex
@misc{li2026autoswarm,
      title={AutoSwarm: Automating Agent Swarms},
      author={Zhonghang Li and Zongwei Li and Chao Huang},
      year={2026},
      eprint={2610.33048}
}
```

## 给 agent 的说明

<details>
<summary>安装与更新指南——供负责配置的 agent 阅读（点击展开）</summary>

### 安装

1. 确认当前宿主及已有 Python 3.10+、CLI 安装，复用原生模型、认证、代理和项目上下文。确实缺少所需 CLI 才询问是否安装，只配置需要的 agent。
2. 首次安装使用 `git clone` 获取官方仓库，再安装包并为**当前宿主**安装 Skill；已有仓库时直接复用。以 Codex 为例：

   ```bash
   git clone https://github.com/HKUDS/AutoSwarm.git
   cd AutoSwarm
   uv tool install --force .
   autoswarm setup --host codex
   ```

   其他宿主标识：`claude-code`、`opencode`、`cursor`、`copilot`、`muse`、`codebuddy`、`hermes`、`nanobot`、`dsh`、`grok`。没有 uv 时，在专用 Python 3.10+ 环境执行 `python -m pip install .`，保留该解释器，不复用归档 v1 环境。
3. 读取 setup 返回路径下的 `SKILL.md`，其中包含完整启动参数与使用方法。App 找不到 `autoswarm` 时使用该启动器。在实际宿主环境运行 `agents`；已有 CLI 不在 PATH 时，将其绝对 argv 合并到返回的 AutoSwarm home/config.json 的 `providers.<provider>.command`，保留无关配置。
4. 在实际项目中验证所需 agent，按实际情况替换示例产品与目录：

   ```bash
   autoswarm doctor --agent codex --probe --cwd /项目路径
   ```

   可重复 `--agent` 选择多个产品。该操作消耗模型调用，检查回复和原生续接；`verified: true` 才确认本次验证通过。版本检查仅发现可执行文件，原生认证列表为空不代表必须登录。遇到原生批准或提问，按已安装 Skill 的规则处理后完成验证。

Setup 安装 Skill、运行时与宿主集成；按返回说明完成重载和信任操作。原生配置、权限及连接排障见[配置与恢复指引](src/autoswarm/assets/references/setup.md)。

### 更新已有安装

从原来源获取所需版本。使用 Git 仓库时，先保留本地修改、收齐正在执行的子任务，再在目标分支执行：

```bash
git pull --ff-only
uv tool install --reinstall .
autoswarm setup --host codex
```

替换为实际宿主，沿用原 AutoSwarm home 和安装位置。需要时在 `setup` 前保留 `--home <原home>`，并保留自定义 `--skill-dir`、`--settings-file`、`--plugin-dir`；分别刷新使用中的宿主安装。使用 pip 时，在原专用环境执行 `python -m pip install --force-reinstall .`，再用 `python -m autoswarm setup --host codex` 携带相同选项更新。

必须**从新版包运行 setup**：已安装 Skill 自带运行时副本，只更新仓库或 Python 包不会刷新它；旧 Skill 启动器不能自行获取新版。保留配置、记录和原生认证，按宿主要求重载，读取新版 Skill 并验证所需 agent。当前不自动更新。详见[更新指引](src/autoswarm/assets/references/setup.md#upgrades)。

### 使用与恢复

按已安装 Skill 选择 agent、分派已就绪的工作、用 `wait --any` 收集结果，处理中途提问与批准，并在交付前核验产物。普通任务在当前会话收齐结果即可；原生唤醒是可选能力，需要对应宿主接口。

按需读取[能力与数据范围](src/autoswarm/assets/references/agent-capabilities.md)、[通信](src/autoswarm/assets/references/communication.md)、[原生交互](src/autoswarm/assets/references/interactions.md)、[模型选择](src/autoswarm/assets/references/models.md)。遇到不确定的执行结果，先检查保留的调用记录与日志，再决定如何恢复。

</details>

[实现架构](docs/architecture.zh-CN.md) · [贡献与验证](CONTRIBUTING.md) · [MIT 许可证](LICENSE)
