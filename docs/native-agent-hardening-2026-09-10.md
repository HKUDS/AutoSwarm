# AutoSwarm Native Agent 可靠性优化实施记录

2026-09-10。基于 AutoSwarm `14ab6d7a` 工作区实施，只修改 AutoSwarm。Talen 两份对照克隆均保持干净。保留用户已有 company 配置与其他未提交文件；没有进行 Git 提交、推送或部署。

原始依据：[Talen 对比审计](native-agent-talen-audit-2026-09-10.md)。本次落实其中第 1、2、4、5 点与七项 P1 改进，不整体覆盖 Talen 的 runtime。

## 已实施

| 项目 | 最终行为及兼容边界 |
| --- | --- |
| Thinking 按轮保存 | 每次 LLM 迭代清空暂存，只保存本轮文本；持久化 `runtime_thinking_stream_id` 与实时事件一致。历史提示词不包含 thinking。 |
| Thinking UI 配套 | 按 stream ID 去重，不再按整个 conversation turn 去重。摘要分页将新的 task 中间轮投影成仅承载 thinking 的记录；不显示原始中间回复。完整视图保留中间回复，刷新后也能补回各轮思考。旧累计记录按 turn 保留最新快照；company 原始执行消息保持既有可见性。 |
| 工具结果规范表示 | 新会话只保存一个 `tool_result` part，`result_format=envelope_v1`，完整保留 success/result/error/approval。移除同一会话消息内重复的 tool_output；不删除 runtime transcript、tool result 执行回执或审批结算记录。 |
| 旧工具数据兼容 | 读取时按调用 ID、工具名和内容匹配旧双 part；保留旧 tool_output 中原本被丢失的错误/审批信封。无匹配的旧结果不删除；不同调用 ID 即使内容相同也不合并。不迁移或重写历史数据库。 |
| 失败恢复信息 | 迭代耗尽、空回复重试耗尽和普通 provider 失败出口保存完整 ledger、manifest、压缩记录、resume_cursor/state、subagent、权限列表及结果交付身份；TaskResult 和 runtime 元数据一致。保留错误事件的迭代标识及 provider error 字段。 |
| 空回复/输出截断 | 无工具调用的空回复或 length/max_tokens 截断回复最多反馈重试两次，随后失败；反馈替换而非累计。未完成的回复不发布 company_final_turn，不进入 DONE/正常记忆提取；合法工具调用继续原有执行流程。 |
| 输出预算幂等 | 截断提示也计入总预算，保留头尾，重复预算处理不再改写历史；不会因正文包含类似截断标识就绕过预算。 |
| 压缩熔断 | 区分未尝试、成功和真实失败。没有可压缩历史或没有 summarizer 不占失败次数；真实异常/空摘要仍触发原有熔断和紧急回退。 |
| 提前工具执行 | 用 call ID 及参数签名匹配已执行结果，支持稀疏 index、顺序变化；参数解析失败不复用早期结果，无匹配任务会取消并等待退出。 |
| Usage/cache 观测 | 请求 streaming usage；保留 provider 原始 usage、缓存 token、reasoning token 和来源。重复/回退的累计 token 不重复计费；上下文压力使用 provider prompt 总量，费用累计使用增量。非流式退化响应中的多个工具调用保留不同 index。 |
| 输出上限自适应 | 仅针对明确的 max_tokens 上限拒绝，在尚未产生流式内容时重试一次；按 provider 实例、端点、模型及部署隔离缓存。只降低上限，不改全局模型配置；不把 quota 错误当作上限错误。自动添加的 stream_options 若被明确拒绝，可移除后重试；用户显式传入的选项不被静默删除。 |
| 动态上下文后移 | prefetch、运行期 memory、artifact 后移，替换旧 prefetch，保护原始用户请求和公司策略前缀；不会把动态 system 消息插入待恢复调用与精确工具结果之间。压缩时先移出这些运行期块，再补回最新状态。 |
| 流式文本事件合并 | 只合并连续且身份一致的 assistant/thinking delta，保留全部文本、最后 seq、首片时间。工具/审批/usage/结束等边界先冲刷；每次 run 独立缓冲，取消/返回时等待冲刷；不引入 Store 延迟事务提交。 |

新增 29 项 Python 专项测试，另在现有 UI snapshot 和分页测试中新增 2 项；前端新增 3 项 thinking 测试。

新增主要实现位于 `opc/core/session_parts.py`、`opc/layer3_agent/runtime_v2/stream_events.py`、`runtime_v2/runtime.py`、`llm/provider.py`，以及 Office UI 的 thinking 投影和去重链路。

## 第三点暂缓

未统一或扩大 runtime 恢复判断：仅有 `metadata.runtime_v2.runtime_session_id` 仍不自动触发恢复，显式 `runtime_resume`、审批精确续跑和 WorkItem attempt seed 保持原判定。

这项改进有价值，主要处理隐式重试和崩溃后上下文重建的一致性，但不是本次基础实验优化的前置条件。它涉及恢复来源、公司任务身份、审批消费与新一轮任务边界，值得独立设计和验证。新增测试锁定了当前显式恢复契约，避免补全失败 artifacts 时顺带改变恢复语义。

同样没有迁移 Talen 的新上下文生命周期、context checkpoint、update_plan，也没有改 company 控制器、权限策略、Store 审批事务、工具能力边界或组织调度。

## 验证

使用项目 `.venv`。各组有少量重叠，以下结果不相加作为独立用例总数。

| 检查 | 结果 |
| --- | --- |
| Native、runtime、prompt、provider、UI transcript、内存、Jiuwen/外部代理、handoff/review | 704 passed，2 skipped，50 subtests passed |
| Company、WorkItem、controller、approval、durable interaction | 832 passed，135 subtests passed；1 个基线已存在的命名检查失败 |
| 恢复、并行隔离、quota parking | 77 passed；1 个初始化测试因基线也等待而单独隔离 |
| Usage 总量/增量及相关运行路径 | 81 passed，3 subtests passed |
| 最终完整性/provider/压缩复核（含非空截断 company final、字典 usage） | 52 passed |
| Thinking 前端专项 | 3 项通过，覆盖多轮、实时/历史去重及旧累计数据 |
| UI 契约测试与类型检查 | 11 个测试文件通过，`tsc -b` 通过 |
| Playwright 滚动回归 | 通过：follow/browsing、翻页、稳定 DOM 标识、审批卡片、company 结果替换 |
| UI 发布资源 | Vite 构建通过，已更新 frontend_dist；存在原有大 chunk 提示 |

Company 两项精确审批恢复测试，以及 WorkItem 新 attempt 提示测试，原先把工具结果/用户纠正写死为请求最后一条。动态上下文后移后更新为检查最后的会话消息，继续断言调用 ID、前后配对、精确执行一次、拒绝不执行、审批完成一次、每次纠正只出现一次。并补充独立测试保护动态块不进入未决调用/结果边界。

保留的命名失败：`tests/test_work_item_identity_guard.py::test_ordinary_company_tests_use_work_item_fixture_names`，仍扫描到基线的 `staged_resume`、`workflow.updated`、`resume stage` 三处文本，没有为迁移修改这些无关测试。

隔离的初始化测试：`test_initialize_fails_closed_when_startup_checkpoint_write_fails`。修改后运行出现等待；从 HEAD 导出的未修改基线也在同一测试等待，45 秒超时退出，故未把它算作通过，也未把它归因于本次 native 修改。

测试日志位于 `/data2/bjdwhzzh/tmp/native-audit/hardening-*.log`；基线复核副本位于 `/data2/bjdwhzzh/tmp/openopc-native-hardening-baseline`。上述自动化回归未调用真实 LLM 或运行真实的长期公司任务；这些结果证明覆盖范围内的行为和兼容性，不等同于任意 provider、模型及公司长流程的无缺陷保证。2026-09-11 已补充 [真实模型案例验证](native-agent-live-validation-2026-09-11.md)，包含 Task 实现与 Company 上下文中的实现、评审，均通过；完整 company dispatcher 生命周期仍未实跑。

## 实验中的行为差异

- 流式文本最多增加约 80 ms 的正常展示等待，或达到 512 字符就冲刷；工具和审批边界不等待这个窗口。可用 `system.native_runtime.delta_coalesce_ms: 0` 关闭合并，不需要回退代码。
- 动态后移遵循现有 `system.native_runtime.prompt_prefix_stability.enabled/separate_dynamic_context` 开关；比较实验应保持同一设置。
- 空/截断回复增加最多两次有界尝试；迭代总上限仍生效。失败现在返回更多恢复信息，但不会因此自动续跑。
- 缓存 token 是 provider 观测值，费用字段仍为估算，不能当作实际账单。本次没有测量真实缓存命中率或模型质量收益。
