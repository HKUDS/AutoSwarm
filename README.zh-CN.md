# AutoSwarm v2

[English](README.md)

在当前 agent 窗口中协同使用 Codex、Claude Code、OpenCode、Cursor、GitHub Copilot CLI、Meta Muse Code 和 CodeBuddy。主 agent 负责配置可用 CLI、分工、处理中途批准、收齐结果并交付答案。

把本仓库交给主 agent，直接说：

> 请为当前 agent 配置 AutoSwarm，复用我已有的 CLI 和原生配置，验证所需 agent 的真实调用及原生续接，然后在当前窗口处理我的任务。

Windows、macOS、Linux 共用轻量 Python 包。App 主宿主需要本地命令工具和 Skill 支持；子 agent 需要可调用的原生 CLI。普通任务无需额外的主会话唤醒接口。

## 给负责配置的主 agent

1. 确认当前宿主及已有 Python 3.10+、CLI 路径，复用原生模型、认证、代理和项目配置。确实缺少所需 CLI 才询问是否安装，不安装所有产品。
2. 在仓库中安装 AutoSwarm，并为**当前宿主**安装 Skill：

   ```bash
   uv tool install --force .
   autoswarm setup --host codex
   ```

   其他宿主名称：`claude-code`、`opencode`、`cursor`、`copilot`、`muse`、`codebuddy`。没有 uv 时，在专用 Python 3.10+ 环境执行 `python -m pip install .`，保留该解释器。核心没有第三方 Python 运行依赖，不复用归档 v1 的环境。
3. 读取 setup 返回路径下的 `SKILL.md`。App 找不到 `autoswarm` 时，使用其中的完整启动参数。Skill 自带运行时及[配置排障指引](src/autoswarm/assets/references/setup.md)，只有顶层 SKILL.md 可被发现。
4. 在当前 App 的命令环境运行 `agents`，确认实际命令和版本。已有 CLI 不在 PATH 时，将其绝对 argv 合并到 `agents` 返回的 AutoSwarm home/config.json 的 `providers.<provider>.command`；保留无关设置，沿用原生认证。
5. 在**实际项目上下文**验证所需 agent：

   ```bash
   autoswarm doctor --agent opencode --probe --cwd /项目路径
   ```

   可重复 `--agent` 选择多个产品。该操作消耗模型调用，验证回复及原生会话续接；`verified: true` 才表示本次环境验证通过。`agents` 和不带 `--probe` 的 doctor 仅发现可执行文件；原生认证列表为空不代表必须登录。
6. 告知用户哪些所需 agent 已验证可用；遇到配置问题按排障指引处理，然后直接接受任务。技术排障由主 agent 完成，仅在缺少授权、必须由用户原生登录或需要用户决策时询问。

Claude 主宿主会合并生命周期 hooks；OpenCode 主宿主会安装插件。原有无关设置保持保留，原生信任和重载要求仍然生效。可用 `--skill-dir`、`--settings-file`、`--plugin-dir` 指定目标。升级后须从新版包重新执行 setup；只更新仓库不会更新已安装 Skill 的运行时副本。

Cursor、Copilot、CodeBuddy 子端使用原生 ACP，Muse 使用原生 MSP。这些产品作为主宿主时使用同一 Skill，通过 run/wait 在当前轮收齐结果，不额外安装空闲会话唤醒集成。App 工具进程可能没有加载终端启动文件中的环境变量；应复用原生 CLI 成功运行时的环境，必要时把已有配置加载器设为 `providers.<provider>.command`，凭据仍保留在原生来源中。要求用户登录前先按排障指引核对。

## 在当前窗口完成任务

读取已安装 Skill。公共输入准备一次，给每个子 agent 明确其独有交付物；有价值的独立复核可以重复检查。主 agent 负责整合、分歧处理和最终核验。

```bash
autoswarm run --agent claude-code --input implementation.txt --cwd /项目路径 --background
autoswarm run --agent opencode --input review.txt --cwd /项目路径 --background
autoswarm wait call_A call_B
```

保存实际返回的 call ID。`wait` 可能返回中间的 `approval_required`；按照 Skill 的授权规则，用 approve/deny 回答原请求，然后继续等待，直到所需调用都有最终结果。检查 outcome、错误和产物后再向用户交付；`ok` 只表示 CLI 操作成功。

```bash
autoswarm inspect call_A
autoswarm resume call_A --input followup.txt
autoswarm receive call_B
autoswarm cancel call_A
```

resume 续接原生子会话，保留之前的凭据。中断等待不会停止子任务。完整历史由原生产品保存，AutoSwarm 仅保存小型文件凭据。runner 消失时明确报告中断，不擅自重跑；不增加上下文、并发、轮次或任务时长上限。

## 主 agent 自行排障

先看 inspect、结果 diagnostics、handle.phase、stdout.log 和 stderr.log。区分缺少 CLI、启动失败、协议不匹配、本地服务认证、远端模型访问和通知失败；复用已有可用配置，不把所有错误都当成需要重装或登录。本机控制请求绕过外网代理，原生模型请求继续沿用其网络设置。

OpenCode 使用本次调用自有的本机服务，先验证健康状态和接口，再创建原生会话；启动文字变化或日志发往 stderr 不影响就绪判断。启动进度可检查、可取消；明确失败返回诊断。任务执行和未回答批准不增加 AutoSwarm 截止时间。

需要时读取[配置与排障指引](src/autoswarm/assets/references/setup.md)。通知失败但已保存的结果可直接 wait/receive；不确定操作是否执行时先检查，不能为了恢复通知重复执行任务。

## 可选的原会话回传与批准

`--background` 仅表示并行执行，由主 agent 主动收取；原宿主绑定支持回调时才额外使用 `--notify-parent`。Codex 需要其现有 app-server 接口，Claude 使用已安装 hooks，OpenCode 使用原生插件。普通任务可以直接等待完成，无需配置唤醒。

```bash
autoswarm run --agent claude-code --input task.txt --background --notify-parent --parent parent.json
autoswarm config --delegate-approvals off
autoswarm approvals call_A
autoswarm approve call_A approval_ID --by user
```

主 agent 代批准默认开启，可全局配置或按子调用覆盖；决定须符合用户任务和宿主授权。`--by user` 仅转交实际的明确用户回答，原生限制继续生效。操作以安装后的 Skill 为准；绑定、批准传输和回传保证见[实现架构](docs/architecture.zh-CN.md)。

## 开发验证

```bash
python -m unittest discover -s tests -v
node --test tests/test_opencode_plugin.mjs
python scripts/check_distribution.py -- autoswarm
python scripts/live_smoke.py --providers opencode --cwd /项目路径 --output report.json
```

前几项使用受控程序和本地连接；真实联调消耗模型调用，复用正常 AutoSwarm 配置。CI 验证 Linux、macOS、Windows 包行为；本机通过只证明当前平台，App 侧边栏和唤醒仍取决于实际宿主接口。

旧版完整实现冻结在 [autoswarm-v1.0.0/](autoswarm-v1.0.0/ARCHIVE.md)。参见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [MIT](LICENSE)。
