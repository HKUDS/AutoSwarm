# Native Agent 真实模型验证记录

2026-09-11。补充 [优化实施记录](native-agent-hardening-2026-09-10.md) 中的自动化回归，验证修改后的 AutoSwarm 工作区。没有修改 Talen，也没有提交、推送或部署代码。

## 实跑结果

使用现有凭据调用 `openai/glm-5.3`，没有使用脚本化 LLM 响应。通过生产 NativeAgent、文件工具、ContextAssembler、MemoryManager、事件总线及 SQLite 持久化执行。测试数据和数据库全部位于新建临时目录，没有运行用户现有公司任务。

| 执行 | 实际工作与独立验收 | 结果 | Runtime 模型轮数 | 工具结果 | 耗时 |
| --- | --- | --- | --- | --- | --- |
| Task mode | 读取订单；编写 Decimal 金额汇总函数和 report.json；验证正常输入、空列表、精度、报告四项断言 | DONE，4/4 通过 | 5 | 6 | 121.30 秒 |
| Company 上下文：engineer | 在有效公司租约、WorkItem attempt 和产物归属校验下完成同一编程任务 | DONE，4/4 通过 | 6 | 6 | 152.58 秒 |
| Company 上下文：reviewer | 检查 engineer 实际生成的代码和报告；调用验收工具；生成 review.json | DONE，verdict=pass，独立验收 4/4 通过 | 6 | 9 | 146.87 秒 |

三个成功执行合计 21 次工具结果，没有失败工具结果。正确答案为 `{"units": 6, "total": "33.79"}`。评审未修改交接的 sales.py、report.json，三个执行均保留原始输入与验收测试。

这些是三个 NativeAgent 执行，包含一个“实现→评审”的小型案例，并非三个完整公司流程。Company 使用 corporate 上下文和 `multi_team_org` 的 durable run/WorkItem 身份；公司租约、claim、role session、seat、attempt 和文件产物归属检查均通过正式 Store API 生效。角色之间的文件交接由测试脚本复制实际产物，**没有启动正式 company dispatcher**。

`verify` 是测试脚本注册的固定验收工具，执行固定的四项 Python 断言；它不是生产 shell_exec/python_exec 审批路径。因此这里不把验收工具成功算作公司 shell 审批验证。

## 持久化与 UI 复核

执行后重新打开独立数据库，调用实际 transcript 恢复和 UI snapshot 代码检查：

- 21 个工具调用恢复为 21 条工具消息，调用 ID 顺序匹配，没有重复；完整 success/result/error 信封逐条与落盘值一致。
- 新数据的 `tool_output` part 数量为 0，规范 `tool_result` 数量与调用数一致。
- Task 记录了 4 轮 thinking，长度分别为 217、517、4394、858 字符；4 个 stream ID 独立，summary UI 保留全部四条对应内容；恢复给模型的历史不包含这些 thinking。
- Company 按原有策略不持久化这些 task thinking parts。本次没有将 task thinking 的展示策略扩展到公司原始执行消息。
- Company 的最终回复有独立 ID，在 summary 中可见。完整视图的中间轮共享 UI ID 是原有的单行合并设计：同一真实 transcript 在当前代码与未修改 HEAD 的 snapshot 实现中均为 6 行、3 个不同 ID。没有把“每个原始公司迭代必须有独立 UI ID”当作本次兼容契约，也没有改变该机制。

本轮核查的是实际会话数据经过后端 UI 投影的结果。浏览器布局、滚动及实时/历史 thinking 去重另有上轮 Playwright 和前端契约测试；本轮没有连接浏览器重放整个真实任务的 WebSocket 流。

## 遇到的问题及归因

在正式的成功 company 试跑前，测试入口有两次构造问题：

1. 最初只设置 company mode，未建立 controller lease。生产 fence 拒绝执行并抛出 CompanyRunControllerLeaseLost。
2. 补齐 lease/claim 后，测试 Task 缺少 role session、seat，run 缺少工作目录。文件写入被产物归属检查拒绝。确认原因后终止该轮，补齐与正式 Store 契约一致的数据，并增加模型调用前的归属预检。

这些是新增测试脚本的初始化错误，不计为成功样本；没有放宽生产 fence 或修改公司控制器来通过测试。初次失败目录仍保留，避免掩盖试跑过程。

未发现此次 native 优化造成的新增执行缺陷，但现有结果不足以证明整个产品没有 bug。上轮两项基线问题仍需单独处理：

- `test_ordinary_company_tests_use_work_item_fixture_names`：原有测试字符串触发命名检查失败。
- `test_initialize_fails_closed_when_startup_checkpoint_write_fails`：故障注入测试等待不退出，未修改 HEAD 基线也在 45 秒内未结束。这一项没有算通过，也未归因于本次 native 变更。

“统一 runtime 恢复判断”仍未迁移。本次没有改变恢复触发、公司权限与调度语义。

## 覆盖边界与性能

已验证小型任务的实际执行，以及公司角色上下文中的读写、验收、历史恢复和终态显示。尚未实跑所有 company profile 的规划、编制、并行派工、owner 审批、返工、崩溃接管与最终交付完整生命周期；这些路径目前主要依据上轮自动化回归。长上下文压缩、空/截断回复、取消清理、旧双 part 兼容等异常分支也主要由确定性测试覆盖，不能从这三个正常结束的真实执行推断全部分支无误。

为了限制单次试跑开销，测试内存配置设置为最多 10 次迭代、每次输出最多 8192 tokens，没有写回用户配置。表中轮数仅为 Native runtime 事件记录的轮数；Memory 等辅助调用可能另有 provider 用量。

耗时是单次端到端观测，不是同模型同输入的修改前后 A/B 测试，不能据此宣称 token、缓存或速度改善。Provider 统计中的 estimated_cost=0 不代表免费，不能作为实际账单。

## 复现与证据

新增 opt-in 脚本 [scripts/native_live_smoke.py](../scripts/native_live_smoke.py)。它使用新的输出目录，检查文件验收、保护文件未变、工具结果 round trip 和 UI 摘要契约。它需要显式 `--execute` 才调用模型：

```bash
cd /data2/bjdwhzzh/project-hku/OpenOPC
source .venv/bin/activate
python scripts/native_live_smoke.py --execute \
  --output /data2/bjdwhzzh/tmp/native-live-smoke-new-run --timeout 300
```

必须使用尚不存在的输出目录。凭据从现有配置读取，不复制到证据文件。

- Task 成功数据：`/data2/bjdwhzzh/tmp/native-live-smoke-20260911/`，包含 evidence.json、roundtrip-check.json、实际产物和 SQLite 数据库。该轮后续 company 初始化失败，不能将整个初次脚本退出视为成功。
- Company 两个成功角色执行：`/data2/bjdwhzzh/tmp/native-live-company-20260911-v2/`，包含相同证据类型；该次脚本退出码为 0。
- 被终止的 company 归属拒绝试跑：`/data2/bjdwhzzh/tmp/native-live-company-20260911/`。
- 这轮新增脚本的后续文件保护、transcript 检查已对上述真实数据库和产物重新验证；没有为脚本检查调整而重复调用模型。

本轮最终改动为测试脚本及验证文档；没有为了修正测试入口而新增生产 native/company/UI 行为修改。
