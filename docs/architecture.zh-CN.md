# AutoSwarm v2 实现架构

AutoSwarm 将一次委派变成原生 CLI 调用，将返回值送回发起它的主会话。主 agent 负责分工、选择 agent、管理工作区、判断结果及后续动作。主子角色属于当前调用；Codex、Claude Code、OpenCode 都可以处于任一角色。

## 层级与文件

| 层 | 实现 | 职责与理由 |
| --- | --- | --- |
| 宿主入口 | `assets/SKILL.md`、`setup.py` | 安装 Skill、可搬迁运行文件及宿主集成；把实际启动 argv 写入安装后的 Skill，减少 GUI PATH 差异 |
| 统一工具 | `cli.py`、`engine.py` | 提供 run、wait、receive、resume、cancel、inspect；让模型通过一个接口调用本地 agents |
| 原生 agent 适配 | `providers.py`、`native.py` | 构造 argv，以原生双向接口传递任务、批准和结果，保留 session ID |
| 批准交接 | `approvals.py` | 每个请求独立保存、回传及回复，主 agent 可配置代批；复用现有宿主通道 |
| 调用执行 | `runner.py`、`platform.py` | 每次调用独立运行，收集最终结果、处理取消和进程身份；无需常驻调度服务 |
| 原主会话回传 | `hosts.py`、`wire.py`、`hooks.py`、`assets/opencode.js` | 连接实际原主会话，处理等待、忙碌、空闲三个场景；宿主能力与子 agent 品牌相互独立 |
| 小型记录 | `records.py` | 保存委派、结果及交付凭据；使用原子文件发布与操作系统锁，不复制原生完整历史 |

MCP 不作为当前依赖。CLI 已经给支持命令工具的宿主提供统一调用面；原生 hooks/插件解决 CLI 工具返回之外的异步唤醒。未来需要 MCP 的宿主可在同一核心外增加薄工具层，不必更换执行与记录机制。

## 一次调用

1. 主 agent 用 Skill 调用 `run`，给出任务、agent 和工作目录。宿主集成附带精确的原主会话绑定。
2. 引擎先校验 CLI 可执行文件，再发布 `task.json`，启动独立 runner。任务通过结构化 stdin 或本地原生 API 交给 agent，不拼接 shell 命令。
3. runner 沿用原生认证、模型和项目配置，解析原生事件，取得原生子会话 ID。原生完整历史仍由产品自身保留。
4. 子任务完成、失败或取消后，runner 原子发布 `result.json`。即使回传失败，结果也已存在。
5. 前台调用通过正常工具返回；后台调用使用原生集成，或由主 agent 随后 wait/receive。
6. 主 agent 验证结果并继续。需要追问同一子 agent 时，resume 使用原生 session ID，另建调用记录，保留前一轮结果。

一轮原生执行完成与用户任务通过验收分开处理。代码没有自动评审、固定重试轮次或预设上下文压缩策略。

## 原生批准交接

Codex 子端使用自有 app-server 的 stdio JSON-RPC，接收 commandExecution、fileChange、permissions 的 requestApproval，在同一连接、同一请求 ID 上回复。该连接属于本次子调用，与向主 Codex 会话投递消息的连接分开。

Claude 子端使用与原生 SDK 相同的双向 stream-JSON 控制协议，`--permission-prompt-tool stdio` 接收 can_use_tool 请求，回写 control_response。启动时保留原生系统提示、设置来源和权限模式，不引入 SDK 的额外依赖或默认配置。相较于让同步 PermissionRequest hook 长期等人，这条通道避免新增 hook 等待生命周期。

OpenCode 子端启动本次调用自有的 `serve --hostname 127.0.0.1 --port 0`，先订阅该服务的原生 SSE，再提交任务。permission.asked 产生请求；原生 reply 接口回复决定。最终 assistant 响应结束本次调用，runner 回收自己启动的服务。无需用户部署或管理常驻服务，也不附着到猜测的已有服务。

`approvals/approval_ID/request.json` 保存原生请求、原生会话/请求 ID、工作目录和可用批准范围。主工具收到 `kind: approval_required`；主会话忙、闲及等待时均复用现有回传机制。批准通知使用独立交付凭据，不能标记最终结果已收到。前台同时到达多个请求时完整返回数组。

主 agent 用 approve/deny 发布 decision.json，持有原生连接的执行实例将决定回复给原请求，并保存 resolution.json。决定不会执行命令或重新派任务。批准前操作仍由原生权限系统暂停；原生撤回、任务取消或进程中断后不接受迟到决定。一个请求的相反决定不能覆盖已保存的决定。

配置 `approvals.delegate_to_parent` 默认 true；CLI config 可持久修改，run/resume 可按调用覆盖。关闭后拒绝 parent 来源的决定，主 agent 在原窗口询问用户后以 user 来源转交回答。来源字段是对本地调用者的约定，并非独立的人类身份认证。Skill 规定只有实际用户回答才能标记为 user；不能自行启用代批以绕过确认。原生禁止、管理限制和已有自动批准仍保持有效。

wait 不等待批准中的子任务结束：任一未回答请求都会立即交还主 agent。Claude Stop hook 首次交付请求后，允许主 agent 结束当前轮以等待用户，不再强制 wait 造成死锁。OpenCode watcher 为每个请求取得独立原生消息凭据后，继续等待后续请求或最终结果。请求未答复时不增加 AutoSwarm 超时，也不停止其他子任务。

## 三种返回场景

| 场景 | Codex 主 agent | Claude Code 主 agent | OpenCode 主 agent |
| --- | --- | --- | --- |
| 正在等待 | run/wait 工具返回 | run/wait 工具返回 | run/wait 工具返回 |
| 正在工作 | 原 app-server 的 turn/steer；也可稍后收取 | 原生异步 PostToolUse hook 提交结果；Stop 阶段收取 | 插件等待 session.idle 后交付；也可主动收取 |
| 当前轮次已结束 | 原 app-server 对同一 thread 执行 turn/start | 原生 asyncRewake 请求继续；Stop hook 避免遗留必要结果 | 原生 client 在同一 session 提交结果，观察 assistant 回应 |

Codex 的 thread ID 本身不足以唤醒正在使用的 App；需要它实际所在的控制 socket/服务。实现自动检测标准控制 socket，也允许显式绑定。没有连接时结果仍可收取，但不会声称成功唤醒。

Claude Code 的 SessionStart 生成本会话绑定；PostToolUse 异步 watcher 以退出码 2 触发原生 asyncRewake。Stop hook 将必要结果交给模型，或要求其等待仍在执行的子任务。异步 hook 只能确认提交，因此凭据写为 submitted；不会假装已观察到 assistant 后续输出。

OpenCode 插件从 tool context 取得 session ID。后台 watcher 只读取结果，不提前写主会话接收标记；原生 prompt 返回实际 assistant 消息后才发布 delivered 凭据。插件重载只恢复同一原生宿主实例的 watcher；新进程须通过原生 chat.message 确认正在继续的精确会话。另一个同工作区 CLI 不会自动接管原主会话。

普通 CLI 不具备向任意 GUI 会话注入消息的通用能力。App、IDE 扩展与终端能否主动回传取决于其实际暴露的宿主连接。安装 Skill 后所有具备命令执行能力的宿主可以显式调用和收取；原生异步回传需相应连接或 hooks/插件。

## 状态保持最少

调用状态由事实推导：runner 的实际进程身份仍存在时为 running；已有结果时为 returned；没有结果且 runner 消失时为 interrupted。结果 outcome 为 completed、failed、cancelled；异常中断可通过收取接口报告 interrupted。

交付凭据描述独立事实：pull 表示可主动收取，pending 表示尚未交付，submitted 表示宿主已接受唤醒通知，delivered 表示相应原生机制已返回交付凭据。continued 表示该机制是否确认启动/追加了继续执行；不表示工作已经正确完成。不同宿主的凭据强度在 mechanism 中保留。

`received.json` 表示主工具已经读取，避免又被自动推送；不表示模型验收。插件内部读取使用 wait --peek，不写这个标记。

没有公司、派卡、全局任务队列或额外的调度状态机。自动回传失败不会重新执行子任务。进程异常消失也不擅自重跑，避免重复修改文件。

## 文件、并发与恢复

每个 call 一个目录，保存任务、运行句柄、结果、可选交付凭据及诊断。先完整写入临时文件，flush/fsync 后在同目录原子替换。不同 call 不竞争同一份 JSON 队列。

Windows 记录读句柄允许其他进程原子替换文件，并保留正在读取的完整快照。读写仅针对原生共享冲突重试，真实访问拒绝直接报错，沿用系统权限。

操作系统锁保护同一原生会话的续写、同一事件的交付及决定发布，以及配置更新。锁随进程退出释放，无需锁租期和后台清理；不限制调用数量。Linux/macOS 使用 flock，Windows 使用字节范围锁。

进程身份使用系统提供的创建时间，而非只记录 PID，避免 PID 重用后误认执行仍存活。取消针对 runner 实际持有的子进程树；没有全局进程搜索或按名称杀进程。

结果发布与宿主处理不构成跨进程事务。保存了 delivered 凭据的调用不会重复投递；连接在宿主接受后、回执保存前中断时，不能跨所有宿主承诺严格 exactly-once。保留 call ID 及原生凭据以便检查，不靠另开主会话掩盖问题。

核心不设置上下文大小、agent 数量、执行轮次、任务超时或记录过期策略。内部检查进程/文件的轮询间隔仅是实现方式，不会终止任务。原生产品和用户自身设置继续生效。

## 扩展方式

新增子 agent：添加 CLI 命令构造及事件解析，返回原生 session ID、最终内容和错误；没有原生 resume 能力时明确报告不可续接，不复制历史冒充原生会话。

新增主宿主：优先使用原生事件/插件/会话 API 绑定原 session，分别实现工具返回与异步继续。可通过现有 command bridge 接收 stdin JSON 并返回交付凭据；不要仅通知用户后标记为已继续。

子 agent 之间的直接通信暂未引入。主 agent 可将一个调用结果交给另一调用，或 resume 原子会话继续讨论；这已经支持基础协作，无需先建设共享聊天室。领域选择知识也不进入基础设施。

## 验证范围

自动化测试使用受控 CLI、真实本地 WebSocket/HTTP 与子进程；真实联调脚本另外调用本机已配置的原生产品。CI 定义 Linux、macOS、Windows 验证与对应独立程序构建。本机测试只证明本机平台，App UI 的会话可见性仍需实际客户端验收。
