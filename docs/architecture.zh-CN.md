# AutoSwarm v2 实现架构

AutoSwarm 将一次委派变成原生 CLI 调用，将返回值送回发起它的主会话。主 agent 负责分工、选择 agent、管理工作区、判断结果及后续动作。主子角色属于当前调用；支持 Codex、Claude Code、OpenCode、Cursor、GitHub Copilot CLI、Meta Muse Code、CodeBuddy、Hermes、Nanobot、DSH 和 Grok Build。

## 层级与文件

| 层 | 实现 | 职责与理由 |
| --- | --- | --- |
| 宿主入口 | `assets/skill-template.md`、`setup.py` | 安装唯一的 Skill 入口、可搬迁运行文件及宿主集成；把实际启动 argv 写入安装后的 Skill，减少 GUI PATH 差异 |
| 统一工具 | `cli.py`、`engine.py` | 提供 run、wait、receive、resume、cancel、inspect；让模型通过一个接口调用本地 agents |
| 原生 agent 适配 | `providers.py`、`native.py`、`acp.py`、`muse.py` | 构造 argv，以原生双向接口传递任务、批准和结果，保留 session ID；同一 ACP 客户端供多个产品复用 |
| 批准交接 | `approvals.py` | 每个请求独立保存、回传及回复，主 agent 可配置代批；复用现有宿主通道 |
| 会话通信 | `messages.py` | 保存定向消息，校验父 agent 指定的同伴规则，处理运行中输入、原生中断和原会话续作；通信异常交回分派者 |
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

run/resume 的可选 --model、--effort 保存为任务选择；不传时沿用原生默认配置，resume 和空闲消息续接继承之前的显式选择。Codex 每个 turn/start 都带回选择；Claude/Copilot 使用原生启动参数，ACP 在首次 prompt 前设置模型及原生公开的强度控件，Muse 按序设置会话模型及 reasoningEffort，OpenCode 映射为当前模型的 variant 并按实时元数据校验。模型和强度取值由原生接口决定，不维护共同枚举或模型注册表；错误保留并交回父 agent，不降级替换。inspect/result 的 requested_selection 是请求记录，不冒充后端生效观测。

Cursor 协商 parameterizedModelPicker，使用模型返回的参数控件设置强度和显式 bracket 参数。其实测 ACP setter 会写全局偏好，因此显式选择时使用原生 CURSOR_CONFIG_DIR：在私有临时目录中复用现有根目录 JSON 配置，保持原生数据/会话目录不变，退出后删除临时副本；不复制完整历史、插件或创建长期配置分支。原生认证与权限继续有效。

## 安装与 CLI 可用性

三个系统共用 Python 包和同一份 Skill。setup 复制本地执行器并写入完整启动参数，不另建平台独立包。App 主宿主需要本地命令工具；子 agent 需要其 CLI，App 本身不保证提供可调用接口。

首次使用或指定 agent 不可用时，Skill 引导主 agent 检查已有命令、PATH 和 App 暴露的 CLI。确实缺少时询问用户是否安装该 agent 的官方 CLI，同意后按其系统的当前官方说明安装，复查 agents 后继续原任务。已有 CLI 可直接配置路径；认证或权限错误不当作缺少安装。发现和启动失败都会提供诊断，不增加安装服务或另一个状态机，也不自动安装所有产品。

agents/doctor 的快速发现只报告 executable 检查，invocation_verified 默认 false。显式 doctor --agent NAME --probe 使用正常调用和 resume，在当前 AutoSwarm 配置、实际项目目录及原生环境中验证回复和会话记忆；不换成丢失项目配置的临时环境。验证是一次事实，不是永久认证状态。已安装运行时的模板使用普通文件名，并清理旧版嵌套 SKILL.md；从已安装启动器再次安装仍然可用。

CLI 的 --background 仅控制工具是否立即返回，默认 return_mode=wait。--notify-parent 显式开启原生回传；没有可用原主绑定时直接提示使用 wait/receive。普通任务在当前轮派发、收取、处理批准并汇总，不依赖唤醒接口。底层原生回传能力继续用于已验证宿主。

## 原生批准交接

Cursor、Copilot、CodeBuddy、Hermes、DSH 和 Grok Build 共用 stdio ACP 客户端：initialize 协商，session/new 后发起 session/prompt；续接按原生能力选择 session/load 或 session/resume，以 prompt 响应的 stopReason 判断最终状态，旧历史回放不混入新结果。session/request_permission 复用同一个批准 Broker，按原生 optionId 回答。allow_always 可能持久化，不能冒充会话级授权；当前只转交一次性批准。

Nanobot 没有原生 ACP 入口，由其 CLI 所属 Python 运行本地 `nanobot_bridge.py`，把同一 ACP 通道映射到原生 SDK 的 run_streamed、sessions 和取消接口。独立原生 session key 避免 HTTP 服务共用会话；原生配置负责模型、记忆、Skill 和工具，项目目录通过原生 workspace_scope 元数据传入，不替换 agent 的记忆工作区。因 SDK 无原生交互式批准通道，AutoSwarm 在新建及续接会话时默认写入 access_mode: full，使用原生 Full Access；可通过 Nanobot provider 参数 --access-mode restricted 显式选择受限模式。访问模式保存在会话元数据中，模型/强度覆盖仅在进程内生效。桥接没有增加第三方依赖或修改原生全局配置；错误沿原有诊断交给主 agent。DSH 保留原生 ACP profile 及配置叠加方式；Grok 使用独占 agent 入口，取消不会把工作留给共享 leader。

Muse 使用原生 MSP：initialize、session/start 或 session/resume、turn/start，以 turn/completed 判断结束。approval/request 的展示回执与 approval/decide 的实际决定分开；保留 currentRequirementId，批准阶段变化后撤回旧请求。每个写入命令生成原生要求的 UUIDv7 幂等键，选择与授权范围一致的原生选项。

这四种新主宿主安装同一个 Skill，在当前轮 run/wait 收取结果与批准。没有加入空闲 App 唤醒接口，也不把 CLI 成功当作 GUI 侧边栏可见性的证明。

Codex 子端使用自有 app-server 的 stdio JSON-RPC，接收 commandExecution、fileChange、permissions 的 requestApproval，在同一连接、同一请求 ID 上回复。该连接属于本次子调用，与向主 Codex 会话投递消息的连接分开。

Claude 子端使用与原生 SDK 相同的双向 stream-JSON 控制协议，`--permission-prompt-tool stdio` 接收 can_use_tool 请求，回写 control_response。启动时保留原生系统提示、设置来源和权限模式，不引入 SDK 的额外依赖或默认配置。相较于让同步 PermissionRequest hook 长期等人，这条通道避免新增 hook 等待生命周期。

OpenCode 子端用操作系统选择的本机端口启动本次调用自有的 serve，通过 /global/health 和 /doc 确认服务及必要接口，再创建会话、订阅 SSE 并提交任务。启动文字及 stdout/stderr 位置不参与就绪判断。端口选择与实际绑定之间存在竞争窗口，原生绑定失败会作为启动失败暴露，不接管其他进程。permission.asked 产生请求；原生 reply 接口回复决定。最终 assistant 响应结束调用，runner 回收自有服务。无需用户部署或管理常驻服务。

本机 HTTP 控制及 SSE 使用相同认证和代理策略：沿用原生服务密码，loopback 绕过外网代理；远端模型请求继续由原生产品处理。明确的 HTTP 认证、协议和连接错误保留发生阶段与接口依据；尚未就绪时 handle.phase、endpoint 和 diagnostic 可检查，取消不依赖握手完成。不增加固定启动截止时间；进程存活本身不视为调用已验证。原生 API 不满足适配器时明确报告版本/协议问题，不通过重复安装认证掩盖。

冷启动服务可能接收连接但不回答第一次健康请求，因此单次健康 GET 会让出并重试。这个网络轮询不限制总启动等待、模型任务或批准等待；正常消息 POST 不自动重试，避免重复执行。

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

领域选择知识不进入基础设施。不同品牌沿用同一个定向消息协议，不需要共享聊天室、数据库或常驻消息服务器。

## 定向消息与原任务续作

`send`、`send --interrupt` 和 `reply` 复用现有 CLI。分派父 agent 用 name、allow-peer 和 communication-rules 指定名称、联系人和职责；名称在该父会话内唯一，可先声明稍后启动的同伴。子 agent 自动继承自己的身份，不继承父会话身份。联系父 agent 和回复实际收到的消息无需额外同伴授权；其他同伴必须由分派者允许。这是本机协作约定，不是防御同用户恶意进程的安全隔离。

首次调用 ID 同时作为稳定 agent_id；原生续接产生新 call_id，继续沿用 agent_id。没有原生宿主身份时返回 controller_id，主 agent 通过全局 --controller 沿用它。agents/agent_ID/agent.json 只保存当前调用、分派者、联系人和说明。每条消息独立写入 messages/msg_ID/message.json，delivery.json 记录 queued、submitted、delivered 或 needs_attention。回复用 reply_to 指向原消息，无需全局对话表或重新复制原生历史。

runner 在原生会话存活时检查信箱。Codex 和 Muse 优先 turn/steer；OpenCode 用原生 message 的 noReply 输入。ACP v1 与 Claude 在缺少等价运行中 steer 接口时，普通消息等待原生轮次边界；紧急消息通过 session/cancel 或原生 interrupt 停止当前轮，等待终止事件，再在同一原生会话提交消息和继续原任务的说明。不会为投递消息杀掉 CLI。模型使用 reply 返回早期答复，原任务的结果只在后续执行结束后发布。先前已完成的结果在后续收信期间保留，不被通信回答替换。

wait --message 同时暴露相关回复、收到的其他消息和自己委派任务的批准；双方同时等待时，可以先回答对方问题。wait call_ID 同样返回中间事件，包括因收信而新续接的子调用批准，主 agent 必须检查种类并继续收取最终结果。主宿主可在当前工具流中收取；存在精确的 Codex/OpenCode 原主会话连接时，单次后台执行器复用既有回传适配器投递消息，避免阻塞发信者自己的工具。通知失败的消息继续保留供工具收取，不假装任意 App 都能在空闲时被唤醒。

原生轮次结束时撤销旧批准请求；同一会话续作产生的新批准仍由原分派关系处理，消息不会扩大权限。完成且空闲的会话由独立 runner 原生续接以收信；dispatch 的操作系统锁避免重复启动。消息与收尾交错时由当前 runner 或收尾后的唤醒处理。失败、取消和丢失的会话不自动重跑，保留信件并给分派父 agent 产生 communication_issue，由其检查诊断后选择续接、重投或重新安排。原生拒绝单次输入不会使原任务或其他调用退出。

子 agent 的原生沙箱可能看不到 runner 的进程命名空间，因此发信时不以沙箱中查不到 PID 判定同伴已死。没有最终结果的在途会话仍保留消息，分派主 agent 通过调用记录诊断中断。CodeBuddy 的原生取消屏障可能在当前轮终止后继续拒绝紧接着的 prompt；仅当响应回带旧请求 ID 且没有新 userMessageId，确认新输入尚未被接受时，执行器让出一轮轮询再提交同一输入，不重跑已接受的工作，也不增加固定重试次数或总等待时长。

submitted 只证明原生输入已接受，reply_to 才关联实际答复。原生接受与本地保存回执之间无法跨所有产品提供严格 exactly-once；不确定时保留 ID 和错误供父 agent 检查，不能用静默重跑掩盖。消息记录没有自动过期策略、固定等待时长或通信轮次上限。

## 验证范围

自动化测试使用受控 CLI、真实本地 WebSocket/HTTP 与子进程；真实联调脚本另外调用本机已配置的原生产品。CI 定义 Linux、macOS、Windows 的包安装和行为验证。本机测试只证明本机平台，App UI 的会话可见性仍需实际客户端验收。


## 原生交互转接

权限与提问共用每次调用的文件记录和等待流程，不新增服务或数据库。`approval_required` 仍用 approve/deny；`interaction_required` 携带原始请求、响应格式与是否需要用户回答，使用 answer 提交经过格式校验的 JSON。支持 Codex 阻塞提问、异步 question item（同轮 steer 回答）及 elicitation、Claude AskUserQuestion/elicitation、Cursor 问题/计划、OpenCode question、Muse MSP userInput 和 ACP elicitation；CodeBuddy AskUserQuestion 使用其原生 resolveInterruption 回填答案。只协商已实现的能力，不声明未知 Claude dialog kind。

Muse 的展示回执与实际答案分开；父会话读到问题后发送 engaged，遵守原生计时规则。Muse/CodeBuddy 命令等待原生 ACK，OpenCode 检查 HTTP 回执。原生撤回、取消和轮次结束使旧请求失效；同一连接的新轮次即使复用原生 RPC ID，也生成不同的交互记录。重复通知共用原记录，迟到/重复 RPC 响应不当作新请求。

每次投递保留尝试和错误。明确拒绝后可人工修正，异常不自动重放不确定的操作，也不因一次答案失败结束子任务。未知交互保存完整载荷并向分派父 agent 发通信异常；父 agent 可检查原生会话、修正、取消后续接或调整分工，独立任务可继续。wait/receive、Claude hooks、OpenCode 插件与可用的原会话回传均识别中间交互；wait 模式的诊断不会意外唤醒宿主。用户授权、宿主权限和原生安全规则继续生效。
