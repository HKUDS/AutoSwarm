# AutoSwarm v2

[English](README.md)

在当前 agent 窗口中调用本机的 **Codex、Claude Code 和 OpenCode**，取得结果后继续工作。主 agent 决定如何分工；AutoSwarm 负责调用、原生会话关联和回传，不内置领域路由策略。

旧版完整实现冻结在 [autoswarm-v1.0.0/](autoswarm-v1.0.0/ARCHIVE.md)，v2 不导入旧实现，也不使用旧数据库。

## 安装

使用对应系统的独立可执行文件时，执行 `autoswarm setup --host codex`，或将 host 换为 `claude-code`、`opencode`。独立程序包含 Python 运行环境。安装器返回启动器位置，GUI App 无需依赖终端的 PATH。

从源码安装可使用：

```bash
uv tool install .
autoswarm setup --host codex
autoswarm doctor
```

也可以安装到新建的 Python 3.10+ 环境。不要复用归档 v1 的环境。核心没有第三方 Python 运行依赖；沿用原生 agents 的认证、模型、权限、记忆和项目设置。

`setup` 安装可搬迁的 Skill；Claude Code 同时合并生命周期 hooks，OpenCode 安装原生插件。可用 `--skill-dir`、`--settings-file`、`--plugin-dir` 指定目标，原有无关设置保持保留。只有实际执行 setup 才会修改相应宿主配置；宿主自己的 hook 信任和重载要求仍然生效。

## 委派、等待和继续

```bash
autoswarm agents
autoswarm run --agent codex --input task.txt --cwd /项目路径
autoswarm run --agent claude-code --input task.txt --cwd /项目路径 --background
autoswarm run --agent opencode --input task.txt --cwd /项目路径 --background

autoswarm wait call_ID call_OTHER_ID
autoswarm wait --any call_ID call_OTHER_ID
autoswarm receive call_ID
autoswarm resume call_ID --input followup.txt
autoswarm inspect call_ID
autoswarm cancel call_ID
```

操作命令默认返回 JSON。`ok` 表示 AutoSwarm 操作是否成功；最终执行结果看 `data.outcome`：`completed`、`failed`、`cancelled`、`interrupted`；中途原生批准请求则为 `kind: approval_required`。一轮正常结束并不代表工作已通过验收。`--input -` 从标准输入读取 UTF-8，也支持直接传入任务文本；任务通过结构化 stdin 或原生本地 API 交给 agent，不经过 shell 拼接，不受提示词 argv 长度限制，也不强制压缩返回内容。

`resume` 使用保存的原生会话 ID，新建一次调用记录，保留上一轮结果。完整对话仍归原生 agent 管理。需要明确覆盖原生配置时才使用 `--model` 或重复的 `--provider-arg=--原生参数`。

AutoSwarm 不额外设置上下文预算、并发 agent 数量、执行轮次、任务时长等上限。中断等待不会停止后台任务；`cancel` 请求停止对应子进程树。执行进程消失但没有结果时明确报告中断，不擅自重跑。如果宿主沙箱会在命令结束后销毁全部子进程，该 shell 无法承载独立后台任务，需要使用宿主集成或允许后台进程的执行环境。

## 在主窗口处理子 agent 批准

子 agent 发出原生批准请求时，原操作暂停。AutoSwarm 将 `approval_required` 事件通过 `run`、`wait`、`receive` 或原主会话回传机制交给主 agent。即使同时等待多个子任务，遇到批准请求也会立即返回，避免等到子任务结束才处理批准。如果前台 `run` 同时收到多个请求，返回数组。批准事件不代表任务完成。

**主 agent 代批准默认开启**，可以全局配置或按子任务覆盖：

```bash
autoswarm config                              # 查看默认值
autoswarm config --delegate-approvals off     # 关闭代批，由用户决定
autoswarm config --delegate-approvals on
autoswarm run --agent claude-code --input task.txt --delegate-approvals off
autoswarm approvals call_ID
autoswarm approve call_ID approval_ID
autoswarm deny call_ID approval_ID --message "换一种处理方式"
autoswarm approve call_ID approval_ID --by user
autoswarm wait call_ID
```

开启时由主 agent 根据用户任务和宿主授权决定，并非运行时无条件放行。关闭时 CLI 拒绝主 agent 自行作出的决定：主 agent 应在当前窗口询问用户，结束当前轮等待回答，再使用 `--by user` 转交用户的实际决定。该标签记录决定来源；同一系统用户运行的 CLI 无法独立验证聊天中的人类身份。Skill 明确禁止冒充用户回答或自行修改配置以绕过确认。子任务覆盖值在 resume 时继承；没有覆盖值时，全局设置的变化也适用于待批准请求。

默认只批准原生请求；支持时可明确使用 `--scope session`。原生明确禁止、管理策略及已有自动批准规则继续有效。Codex 已配置原生自动审查时，可能在请求交给 AutoSwarm 前就已处理。拒绝使用原生提供的选项，部分 Codex 请求只能取消当前轮。AutoSwarm 不给未答复请求增加超时；取消任务或原生撤回会使请求失效。决定绑定具体 call/request，已保存的决定不能被另一个相反决定覆盖。

子端分别采用 Codex 自有 app-server 的 stdio、Claude 双向 stream-JSON 控制协议的 `can_use_tool`、OpenCode 自有 loopback 服务的权限事件及回复接口。这些原生进程按调用启动和回收，无需用户单独部署服务，也没有新增 Python SDK 依赖。原生参数须适用于相应传输，常用 Codex 沙箱/批准、OpenCode 模型/agent 参数会转换为原生请求字段。批准通知与最终结果分开记录；`deliver call_ID --request approval_ID` 可重投批准通知。

通用交互是在原主会话中询问；是否显示成 App 自带批准弹窗取决于宿主接口。当前覆盖相应接口暴露的命令、文件和工具批准，不把操作系统弹窗、管理策略变更或接口之外的产品提示转换成通用批准。

## 三种主子交互

| 主 agent 所处情况 | 返回方式 |
| --- | --- |
| 正在等待 | `run` / `wait` 通过正常工具返回交付结果 |
| 正在做其他工作 | 稍后 `wait` / `receive`，或宿主原生集成投递 |
| 已结束当前轮 | 宿主集成把结果交给同一原会话并请求或观察继续执行 |

结果已保存、通知已提交和已经观察到继续执行分别记录。不猜测“最近会话”，也不另开会话冒充原主会话。

**Codex：** 宿主提供 `CODEX_THREAD_ID` 时自动绑定当前会话。主动回传还需要原会话实际所在的 app-server 控制 socket 或 WebSocket。会检测标准本地控制 socket，也可用 `AUTOSWARM_CODEX_ENDPOINT` 指定。忙碌时向实际活动轮次追加输入，空闲时在同一 thread 开启后续轮次，不把 `codex queue` 返回成功当作已继续执行。实际 socket 权限取决于宿主和沙箱。

```bash
autoswarm bind --host codex --session 原主会话ID --endpoint unix:///绝对路径/control.sock --output parent.json
autoswarm run --agent claude-code --input task.txt --background --parent parent.json
```

本地可用 `ws://`，远程使用带认证的 `wss://`。`--token-env 环境变量名` 指定认证来源，不把密钥写进绑定文件。`--no-parent` 使用显式等待模式。

**Claude Code：** 安装 `SessionStart`、带原生 `asyncRewake` 的 `PostToolUse`、`Stop` hooks。SessionStart 绑定本次原会话；异步 hook 在结果到达时通过原生唤醒机制返回；Stop hook 交付结果或让主 agent 等待尚未完成的调用。异步提交标记为 `submitted`，不会伪造“已经观察到后续轮次”。原生 hook 版本、信任与生命周期限制仍适用，安装后需新建会话。当前以本机 2.1.304 接口为目标。

**OpenCode：** 安装后重启，使用原生 `autoswarm` 工具，其 `arguments` 为 CLI 参数数组。插件从 `context.sessionID` 绑定原会话，在后台等待子结果；主会话忙碌时延后到原生 `session.idle` 事件，再通过已有 client 继续会话，并将真实 assistant 消息写入交付凭据。普通 shell 调用仍可等待收取，但不会自动获得插件的会话绑定。

插件重载时恢复属于该原生宿主实例的 watcher；新宿主进程在原生输入确认正在继续同一会话时恢复旧调用，不会让同一工作区内另一个 CLI 自动接管原主会话。内部 watcher 使用 `wait --peek --unseen`，逐次交付批准请求，之后继续等待最终结果。

已有 OpenCode HTTP 服务也可通过 `bind --host opencode --session ID --endpoint http://127.0.0.1:端口 --workspace /项目 --output parent.json` 绑定。HTTP 桥接等待实际 assistant 响应，不把仅接收请求当成执行完成。

OpenCode 原生 API 返回的 assistant 消息必须以 `stop` 正常结束；中间的 `tool-calls` 不视为任务完成，失败时保留原生会话 ID。自有服务进程的退出码反映服务回收，任务 outcome 由原生 assistant 返回决定。

`inspect` 显示 `delivery`。连接中断时保留结果及待交付凭据，运行中的 runner 可以重试临时连接故障；绑定或协议错误明确返回。`deliver call_ID` 重投原生 endpoint 回传，不重新执行子任务；hook 方式由已安装的宿主集成继续处理。还提供接收 stdin JSON、返回交付凭据的 argv 命令桥接接口，以便扩展其他宿主。

原生 App 的侧边栏可见性、历史恢复、实时附着是不同能力。AutoSwarm 暴露原生会话 ID，不篡改原生数据库或会话来源字段。

## 本地文件与结构

数据默认位于系统对应的用户状态目录。通过 `AUTOSWARM_HOME` 或全局 `--home 路径` 指定其他位置。

```text
calls/call_ID/
  task.json       # 委派、工作目录和原主会话绑定
  handle.json     # 执行实例和原生子会话 ID
  result.json     # 实际返回和结束原因
  delivery.json   # 使用回传集成时的凭据
  stderr.log      # 原生诊断
  approvals/approval_ID/
    request.json  # 原生请求和所属会话，不复制完整对话
    decision.json # 决定与来源，有回答时保存
    resolution.json # 已回复、撤回、取消或中断
    delivery.json # 可选的批准通知凭据
```

完整写入后再发布文件。调用目录相互独立，不使用 SQLite、共享 JSON 队列或常驻调度服务。操作系统锁仅用于避免同一原生会话被 AutoSwarm 同时写入，不限制 agent 数量；进程退出后自动释放。记录不按固定时间自动删除。

如需指定可执行文件，可在数据目录的 `config.json` 配置 `providers`，每个产品支持 `command` argv 数组和 `args` 数组。默认直接查找 `codex`、`claude`、`opencode`。检测到可执行文件不等于认证有效，认证仍由各原生产品处理。

## 验证与发布

```bash
python -m unittest discover -s tests -v
node --test tests/test_opencode_plugin.mjs
python scripts/check_distribution.py -- autoswarm
```

测试覆盖调用、并行收取、会话恢复、取消、中断、长文本、安装搬迁、原会话精确回传、忙闲竞争、hooks 和断线。回传测试需要允许本地 HTTP/WebSocket。CI 配置覆盖 Linux、macOS、Windows；本机运行只证明当前平台。

以下真实联调会使用已配置 agents，消耗模型调用，只创建隔离测试会话：

```bash
python scripts/live_smoke.py --output /tmp/autoswarm-live.json
python scripts/live_codex_return.py --output /tmp/autoswarm-codex-return.json
python scripts/live_claude_return.py --output /tmp/autoswarm-claude-return.json
python scripts/live_opencode_return.py --output /tmp/autoswarm-opencode-return.json
python scripts/live_approvals.py --output /tmp/autoswarm-approvals.json
python scripts/live_approvals.py --decision deny --output /tmp/autoswarm-denials.json
```

应在允许原生认证、网络和后台进程的环境中运行。安装 PyInstaller 后，`python scripts/build_release.py` 在当前系统构建独立程序；手动触发 CI 也会构建各平台产物。真实 App UI 仍需在对应客户端验收，协议测试不能证明所有客户端版本行为相同。

层级职责、实现理由及扩展方式见[实现架构](docs/architecture.zh-CN.md)。[MIT](LICENSE)。
