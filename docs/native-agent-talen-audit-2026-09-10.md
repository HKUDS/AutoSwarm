> 实施状态更新：后续已按用户确认落实本报告第 1、2、4、5 点及七项 P1 改进；第 3 点统一恢复判断暂缓。下文保留最初审计时的状态，实施结果见 [迁移记录](native-agent-hardening-2026-09-10.md)。

> 名称说明：本文是 2026-09-10 的历史审计记录，其中 OpenOPC 指当前的 AutoSwarm。历史名称、提交和实际路径沿用审计时的记录。

**Talen → OpenOPC native agent 对比审计（2026-09-10）**

结论：值得迁移的首先是执行和持久化正确性修复，其次是提示词与流式事件的效率改进。不能整体替换 native runtime，也不适合直接 cherry-pick Talen 的大提交。当前 OpenOPC 在 company 执行身份、审批续跑、文件边界、失败收敛和验证职责方面已有比 Talen 更新的实现。

本轮完成源码、历史、调用链与隔离复现分析，仅新增本报告，没有修改运行代码、用户配置或现有测试。以下“建议”均为待实施方案；现有回归通过不代表尚未实施的迁移已经完成验证。

**对比基准与证据范围**

| 对象 | 本轮基准 |
| --- | --- |
| OpenOPC | 本地 `/data2/bjdwhzzh/project-hku/OpenOPC`，HEAD `14ab6d7a` |
| Talen | GitHub HEAD `353d2e9dba952be11376ab678dbcd5dd81b95f98`，完整历史克隆位于 `/data2/bjdwhzzh/tmp/talen-native-history` |
| 分叉依据 | Talen 初始复制提交 `03f6627` 的 `opc/layer3_agent/runtime_v2/runtime.py`，blob 与 OpenOPC `326d3052` 对应文件完全一致；这是文件级确认，不把两库误当作共享 Git 祖先 |
| 比较方法 | 将 `talen/opc` 包名及品牌差异归一化后按文件和 AST 函数对比，再用 Talen 增量提交区分功能改进、公司模式删除及后续实验 |
| 覆盖 | NativeAgent、runtime_v2 全套、prompt_harness、context_assembler、memory_manager、LLM provider、工具注册/预算/计划、事件 Store，以及 OpenOPC company controller、权限续跑、角色会话、WorkItem 和外部团队边界 |

关键 Talen 提交：[持久化完整性 0156e3e](https://github.com/LZH-YS1998/Talen/commit/0156e3ed69ae9acdd222d66b802baccce9f74f20)、[动态状态后移 0f18c39](https://github.com/LZH-YS1998/Talen/commit/0f18c39)、[截断与 prefetch 6482ac8](https://github.com/LZH-YS1998/Talen/commit/6482ac8)、[真实 usage 067ed8b](https://github.com/LZH-YS1998/Talen/commit/067ed8b)、[审批语义 91c7073](https://github.com/LZH-YS1998/Talen/commit/91c7073)、[精简与 update_plan 3d3ce8f](https://github.com/LZH-YS1998/Talen/commit/3d3ce8f2d63ff415d3f230e535c6de5fb583f242)、[事件合并 c1a7bfc](https://github.com/LZH-YS1998/Talen/commit/c1a7bfc)、[工具结果匹配 a412cfe](https://github.com/LZH-YS1998/Talen/commit/a412cfe)。

优先级：P0 表示执行/恢复正确性优先；P1 表示随后应做的可靠性或效率修复；P2 表示需独立评估的产品/架构调整。它们不是安全漏洞评级。

| 改进 | 当前 OpenOPC | 建议 |
| --- | --- | --- |
| 每轮 thinking 单独保存 | 仍跨轮累积 | P0，迁移 |
| 工具结果单份会话表示、旧数据去重 | 仍重复写入及恢复 | P0，迁移并保留完整结果信封 |
| 统一 resume 判断 | 只识别显式 runtime_resume | P0，迁移规则，保留 company 身份校验 |
| 迭代耗尽返回完整恢复状态 | failure artifacts 只带 runtime_session_id | P0，补全 |
| 空回复/输出截断不能假成功 | 无工具调用即进入完成路径 | P0，迁移有限恢复逻辑 |
| 工具输出截断幂等 | 重复截断会改变旧消息 | P1，迁移并改进实现 |
| 压缩 no-op 不计失败 | no-op 会耗尽熔断预算 | P1，迁移 |
| 提前执行结果按 tool_call_id 归并 | 按列表位置对应流式 index | P1，迁移并补异常输入校验 |
| usage/cache 观测 | 没显式请求 streaming usage，丢失原始 usage 字段 | P1，迁移 |
| provider 输出上限自适应 | 只有静态模型上限 | P1，按端点隔离后迁移 |
| 易变上下文移到尾部 | prefetch、memory、artifact 仍会扰动前缀 | P1，分区适配 |
| 流式 delta 合并 | 每片段存储并发事件 | P1，先移植事件层合并 |
| Store 延迟批量提交 | company 事务模型已有变化 | 不原样迁移 |
| update_plan 代替 todo 双工具 | 仍用 task_ledger/todo | P2，兼容迁移，禁止成为公司任务图 |
| 提示词/工具 schema 精简、环境信息 | 有可减重复，角色限制较复杂 | P2，按模式和角色做 |
| 新的工具结果生命周期、context checkpoint | 未实现 Talen A–H 实验体系 | 单独项目，company 默认保持现有策略 |
| 验证失败阻塞、审批三级化 | 已有更新的 OpenOPC 实现 | 保留 OpenOPC，不倒退 |

**逐项分析与安全迁移要求**

1. **P0：thinking 必须按模型迭代保存，不能累计写入。**

   OpenOPC 在循环中重置 `assistant_text` 和 delta 序号，却没有重置 `runtime_notes["latest_thinking_text"]`；收到 thinking 后不断拼接，之后又把累计文本传给 `_persist_assistant_turn`。复现三轮分别产生 `THINK-1/2/3`，实际保存为 `THINK-1`、`THINK-1THINK-2`、`THINK-1THINK-2THINK-3`。等长输出会导致会话中这部分存储量近似二次增长。

   Talen 在每轮开始重置 thinking。应采用这一小修复，同时使 `runtime_thinking` metadata 与对应 thinking part 都保持“当前迭代”的范围。OpenOPC 已修正流式 thinking 的 iteration 级 `item_id/stream_id`，不要用 Talen 的其他事件身份差异覆盖它。

   另一个独立问题在 MemoryManager：`_render_session_parts` 对未知 part 使用 `payload.text` 兜底，使 thinking 进入普通历史文本。应显式排除 thinking；保留审计/UI 数据，通过专用渲染显示。模型协议若需要专门的 reasoning continuity，应在 provider 专用协议层处理，不通过普通会话文本拼回。

   证据：[OpenOPC 累积位置](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:673)、[历史渲染](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer5_memory/memory_manager.py:1460)、[Talen 重置](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/runtime_v2/runtime.py:809)。

2. **P0：会话中的工具结果只保留一份规范表示，兼容旧格式。**

   OpenOPC `_persist_tool_result` 先生成带文本的 `tool_output` part，再追加 `tool_result` part。恢复函数把两种 part 都当工具结果；普通历史渲染也都输出。复现两次工具调用恢复出四条 tool message；一条旧格式结果被恢复和渲染两次。不能指望最终的相邻文本去重解决，因为两种结构的 JSON、配对信息并不相同。

   建议：新写入只创建一个带 `tool_call_id` 的会话结果 part；读取历史数据时优先规范结果，并按 call 身份兼容旧 `tool_output`。Talen 使用“同一 message 有 tool_result 就不读 tool_output”的简单兼容规则；OpenOPC 应补充同一 message 含多项结果时的逐 call 测试，不能误删不同调用的数据。

   **不能把这个建议理解为所有表只存一份。** OpenOPC 的 runtime transcript、runtime_tool_results、审批 receipt/执行账本有不同消费者，尤其 `InteractionCoordinator.persist_exact_tool_result` 负责持久副作用结算，必须保留。还需注意当前 `tool_result` payload 使用 `result.get("result", result)`：去掉另一份 wrapper 时，应确保 `success/error/approval/exit_code` 等结果语义仍完整可恢复，不能只留下业务 body。

   公司验证点：审批通过的工具结果只结算一次；拒绝结果也持久化；旧会话可恢复；report/review 消费证据时不丢成功/失败状态；UI 不出现重复结果。

   证据：[OpenOPC 写入和 exact-result 结算](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:4238)、[OpenOPC 恢复](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:2115)、[Talen 写入](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/runtime_v2/runtime.py:4440)。

3. **P0：统一“存在可恢复 native runtime”的判断，但不能扩大跨任务继承。**

   当前 NativeAgent 的 history 注入判断与 runtime bootstrap 都主要依赖 `context_snapshot.runtime_resume`。Engine 正常归档 runtime 状态却写入 `metadata.runtime_v2` 和 `context_snapshot.runtime_v2`。某些显式恢复入口会补 marker，因此不是所有续跑都坏；但只有 canonical runtime_v2、没有显式 marker 的重试入口仍可能把已有会话渲染成普通 context，而不走结构化 transcript restore。这些 context 又会计入保护前缀，难以正常压缩。

   Talen 的 `native_runtime_resume_payload` 统一采用显式 marker → context_snapshot.runtime_v2 → metadata.runtime_v2。建议提取共享 helper，让 native context builder、bootstrap、runtime_session_id 和相关 checkpoint 构建使用一致规则。

   company 适配要求：fallback 只能识别属于当前执行主体的状态；保持 run、Task、WorkItem/projection、role/session、attempt 的校验，不因同一角色复用 session 就把上一个 WorkItem 的计划或 permit 自动当作本次执行状态。保留 approved ToolCall 恢复时“不插入新 user 消息打断 tool-call/result 配对”的逻辑，以及 `continue_after_tool_result`、每 attempt correction 的去重规则。

   **不要复制 Talen 最新的另一项行为：fresh task 也完全不注入此前会话历史。** OpenOPC 多轮任务、角色连续会话和 report turn 仍依赖会话上下文。本项只修复“已有 runtime 的恢复走错入口”，不改变 fresh task 产品语义。

   证据：[NativeAgent 判断](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/native_agent.py:513)、[runtime 判断](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:1233)、[Engine 状态落点](/data2/bjdwhzzh/project-hku/OpenOPC/opc/engine.py:5017)、[Talen helper](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/runtime_v2/runtime.py:99)。

4. **P0：迭代耗尽要返回完整恢复信封，不能只返回 runtime_session_id。**

   OpenOPC 的 max_iterations 出口只返回 ID，保存 session 时只传 reason。Engine `_extract_runtime_state_from_artifacts` 对缺失字段补空列表/空字典，随后整体赋给 runtime_v2。这会削弱已积累的 task_ledger、cursor、active_subagents、compaction、verification 等恢复状态。Talen 已补全此出口。

   应统一正常、暂停和失败出口的恢复状态构建，继续使用 OpenOPC 的 `task_ledger` 字段以及现有身份元数据，避免顺手换成 Talen 的 `plan`。状态仍是 FAILED，由现有 owner 决定重试/返工；完整恢复数据不意味着自动重新执行所有工具。ToolCall permit、controller lease 和副作用记录仍由 Store 判断有效性。

   当前隔离用例用 max_iterations=0 直接命中出口，确认缺少 resume_state/cursor；实施时还应增加执行若干工具后耗尽、重试后不重复副作用的端到端 Store 用例。

   证据：[OpenOPC 失败出口](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:1132)、[Talen 完整出口](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/runtime_v2/runtime.py:1564)。

5. **P0：空回复、reasoning-only 和 length 截断不能直接宣布完成。**

   当前 native loop 在 `not tool_calls` 时进入完成分支，未用 finish_reason 检查回复是否完整，也未要求存在可交付文本。隔离模型只输出 thinking、以 `stop` 或 `length` 结束，OpenOPC 都会第一轮返回 DONE，内容可为空。公司层的输出契约可能拦截部分场景，但 native 层本身不应制造这类成功结果。

   Talen 的 3d3ce8f 增加有限次数反馈重试，仍然无回答时返回 FAILED。建议移植：在持久化最终 assistant/turn_completed 之前分类空输出与输出上限截断，给清晰反馈，限制恢复次数，并在失败时带完整状态。非空但 length 截断的结构化 report/review 同样不能当完整交付。

   不能机械地把任何 `not tool_calls` 都判失败：正常纯文本回答、规范拒答、显式 request_user_input/peer wait、runtime 自己生成的合法完成信号均有不同语义。中途已执行的 streaming read 也须 join/cancel 后再结束，不能以模型重试重复 effectful 工具。company 状态提交仍由现有 WorkItem owner 完成。

   证据：[OpenOPC 完成分支](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:932)、[Talen 检测与反馈](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/runtime_v2/runtime.py:1153)。

6. **P1：工具结果截断必须幂等，并在边界统一执行。**

   OpenOPC 截断保留 budget 字符的头尾后，又额外加入 marker，因此新文本仍超过 budget。下一轮再次截断，会改变 omitted 字数/旧文本；`f(f(x)) != f(x)` 已复现。这同时影响缓存前缀和结果证据稳定性。

   Talen 引入 `_bound_tool_result_text`，在 live append 和 restore 两条入口统一截断，遇到 marker 则跳过。应采用“截断一次”的契约，但不要照搬任意子串判断：工具输出本身可能含相同文本。较稳妥的是受信任的内部 bounded 标记，或把 marker 长度计入预算、让纯函数天然幂等；内部字段不要漏进 provider schema。

   必须保留现有完整输出持久化、准确 output_ref、尾部 exit code/错误摘要，以及 SecureWorkspace 的读写边界。可以让模型看到稳定预览，不能截掉审批结算所需的规范结果。

   证据：[OpenOPC 预算函数](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:2907)、[Talen 统一函数](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/runtime_v2/runtime.py:3263)。

7. **P1：压缩“没有可压内容”和“摘要失败”应分开计数。**

   当前 `_apply_durable_compaction` 对无 summarizer、历史过短、摘要失败均返回 applied=False，调用者全部累加 durable_compaction_failures。复现只有 system/user 两条消息，summarizer 一次都没调用，连续两次 forced pipeline 却将失败数加到 2，达到默认熔断门槛。

   迁移 Talen 的 applied/attempted 区分，或用结构化 `skipped/succeeded/failed` 结果。只有实际尝试且失败才消耗预算。该修复与是否采用工具结果过期机制无关。保留现有阈值触发与真实 overflow 下的兜底，不重新启用每轮机械剪裁；“没有可压内容”也不能被记录成成功腾出空间。

   证据：[OpenOPC pipeline](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:2824)、[Talen attempted 返回值](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/runtime_v2/runtime.py:3461)。

8. **P1：提前执行的结果按 tool_call_id 归并，不按枚举位置。**

   `_maybe_start_streaming_tool_calls` 使用 provider 的 chunk index 作为 early_tool_runs 的 key；最终 `_collect_execution_results` 却使用 `enumerate(tool_calls)`。稀疏 index 或过滤重排时，两者不等。复现 index=4 的 read 已完成，归并时又执行一次。它是条件性兼容缺陷；正常连续 0-based index 不一定触发。

   Talen 在 a412cfe 改用 call ID 映射。建议保留 OpenOPC 的函数/参数签名二次比较、只读/可并发准入和权限预测，增加 ID 匹配。对缺失/重复 ID 应拒绝歧义或明确规范化；Talen 的实现并不是所有异常 ID 情况都天然正确。孤立的 early task 必须清理。

   不扩大提前执行范围。company 的 delegate_work、写入、审批工具必须继续经过 controller effect fence，不能套用 read 的提前执行路径。

   证据：[OpenOPC 归并](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:2522)、[Talen 归并](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/runtime_v2/runtime.py:2847)。

9. **P1：补齐真实 usage/cache 统计，作为优化效果的测量基础。**

   Talen 明确请求 streaming usage，并把 provider 的原始 usage 放入事件，保留 cache hit/miss、reasoning 等供应商扩展字段。OpenOPC 当前 normalize_stream_event 只抽取 prompt/completion token，chat_stream 也未默认设置 include_usage。因此某些端点可能没有最后一块实际 usage，且已有扩展统计会丢失。

   应添加原始 usage 和稳定规范化指标，保留 OpenOPC cost event 的 org_id、role/task/run/session 归属。区分估算与 provider 实测；cache 字段未返回不等于命中为零。对供应商累计 usage、多次 usage chunk、空 choices 的最终 usage、流失败后的重试要防重复计费。不能把 reasoning tokens 在 completion 之外无条件再次相加。

   这里只确认缺失的观测能力，未调用真实供应商测量缓存命中或成本，不给出迁移后的百分比收益承诺。

   证据：[OpenOPC provider](/data2/bjdwhzzh/project-hku/OpenOPC/opc/llm/provider.py:632)、[Talen provider](/data2/bjdwhzzh/tmp/talen-native-history/talen/llm/provider.py:652)、[Talen usage 事件](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/runtime_v2/runtime.py:943)。

10. **P1：provider 返回确定的 max_tokens 上限时，做一次受控适配。**

    OpenOPC 已有 litellm 模型目录的静态上限裁剪。Talen 增加解析端点返回的 max_tokens ceiling，降低后重试一次。这有助于别名/兼容 API 的实际输出上限与目录不一致的场景。

    移植时需修正 Talen 的模型名全局缓存：至少按实际端点、provider/model/deployment 配置隔离，避免 company 内多个角色使用同名模型、不同端点时互相污染。只处理明确的输出参数上限错误，不把 quota/rate limit、输入超限或任意错误都当作降低输出预算的理由。统一 stream/non-stream 入口，保留 OpenOPC 的 `reasoning_effort` 传递和 `ProviderQuotaExhaustedError` park/recovery。

    Talen 同一文件 diff 删除了 OpenOPC 后来添加的 reasoning_effort 默认转发；不应随本改进移植。Talen 的原始请求 dump 调试开关也不属于必要迁移内容。

    证据：[OpenOPC clamp](/data2/bjdwhzzh/project-hku/OpenOPC/opc/llm/provider.py:92)、[Talen cap 适配](/data2/bjdwhzzh/tmp/talen-native-history/talen/llm/provider.py:96)。

11. **P1：将易变的观测状态移到尾部，同时真正稳定前缀。**

    OpenOPC 已有 prefix stability 配置，但 prefetch 插到前缀内并增加 base_prefix_len；session memory 和 runtime artifacts 也会在历史前方刷新。长运行中，prefetch 的旧块还可能不断成为受保护前缀的一部分。仅“配置名叫 prefix stability”不等于请求前缀实际稳定。

    建议拆成稳定规则/本次 assignment、可压缩的规范历史、当前 runtime 观测尾块。skills 内容在一次执行内只加载一次，工具 schema 按实际权限生成并稳定排序；易变的子 agent 状态、memory 命中、局部计划进度放尾部，按来源/版本避免重复注入。

    company 约束：WorkItem contract、角色职责、当前 turn_mode、allowed_tools、审批和所有权限制仍必须按执行边界及时生成，不能为了缓存冻结旧权限，也不能把权限仅交给尾部文字。rework correction 继续由 `_build_user_message` 写入本 attempt 的 canonical user turn。report/handoff、最新 peer 回复、交付和审核契约不能因为 Talen 没有公司模式而删除。搬动 artifact 后要重新计算真实 prefix 边界，不能沿用已包含旧 artifact 的 base_prefix_len。

    缓存验证应比较实际两次请求的共同前缀及真实 cache usage；模型、schema、company assignment 或权限变化导致前缀失效有时是正确行为。Talen 当前还对 task mode 直接关闭 prefetch/后台 memory，这属于策略取舍，不应作为本修复的必要部分全局套用。

    证据：[prefetch](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:2389)、[session memory](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:3177)、[artifact 注入](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:3462)、[attempt correction](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/native_agent.py:431)。

12. **P1：可以合并流式 delta，不能直接移植 Store 的延迟 commit。**

    Talen 增加约 80ms/512 chars 的 delta 合并以及 64 条/250ms 的事件提交批处理。OpenOPC 当前每个 assistant/thinking delta 都走 store + event bus，Store 逐条 commit。事件层合并有实际优化价值，尤其并行角色输出时。

    建议第一步只合并 assistant_delta/thinking_delta，保留顺序、最后 seq 和原始 stream 身份；遇到 message_stop、工具调用边界、审批、失败、取消、最终交付前 flush。flush timer 要在异常和 shutdown 中可靠回收，多个 Task/角色/attempt 的缓冲不可混用。逻辑 ToolCall、ToolResult、checkpoint、claim、交付事件不进入可丢失的展示缓冲。

    **Store batching 必须另做事务设计。** OpenOPC 现在使用同步 sqlite adapter 包装 async 接口，有大量 BEGIN IMMEDIATE、controller/interaction 的原子命令，部分使用独立 transaction connection。Talen 将 INSERT 后的 commit 推迟到后续 tick，会延长共享连接持有未提交事务的时间；只给 runtime_events 加局部 asyncio.Lock，不能覆盖其他公司事务。可能出现事务嵌套冲突、锁等待，或无关 commit/rollback 影响事件批。这是源码可见的迁移风险，本轮没有在生产 Store 上试验该改法。

    如后续确需批处理，应选择不延长共享业务事务的 writer/独立观测存储设计，并验证跨连接可见性、controller lease、审批 CAS、shutdown drain 和崩溃边界。

    证据：[OpenOPC 事件发送](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:4838)、[逐条提交](/data2/bjdwhzzh/project-hku/OpenOPC/opc/database/store.py:30856)、[SQLite adapter](/data2/bjdwhzzh/project-hku/OpenOPC/opc/database/store.py:716)、[Talen 合并](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/runtime_v2/runtime.py:4690)。

13. **P2：update_plan 值得采用，但只能表示 agent 内部计划。**

    Talen 用完整快照 `update_plan(plan, explanation)` 代替 todo_read/todo_write，计划留在正常 tool-call history 中，成功结果可只回简短确认，避免计划在调用、返回、动态 ledger 中多份重复。参数约束更明确，并兼容读取旧 task_ledger。

    OpenOPC 需要“新增入口 + 旧会话兼容”，而非删除 todo 模块和改名所有 runtime metadata。涉及 tools/schema、runtime handler、Engine `_extract_runtime_state_from_artifacts`、checkpoint payload、UI 事件/进度、subagent 工具白名单和旧计划恢复。先让 plan 与旧 ledger 共用一个内部模型，再考虑退休旧入口，避免双份可变真相。

    company 内 `update_plan.completed` 只表示当前 agent 内部步骤完成，不能驱动 WorkItem phase、创建 delegation、替代 manager board/review 或提前完成父任务。协调角色应遵循当前 turn contract，不能因计划工具变得好用而绕开 delegate_work。长会话跨 WorkItem 时计划要按 Task/attempt 隔离。

    证据：[Talen plan schema](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer4_tools/plan.py:15)、[OpenOPC Engine 状态提取](/data2/bjdwhzzh/project-hku/OpenOPC/opc/engine.py:5017)。

14. **P2：精简提示词、环境信息和工具 schema，采用能力感知配置。**

    可借鉴的方向：合并重复的工作/验证提醒；对没有公司身份且没有依赖的 standalone task 跳过无效 company context builder；把技能静态加载；补充实际 cwd/platform/date 与轻量 git 状态；按任务/角色提供必要 schema。

    不能全局复制 Talen 删除 list_dir/file_search/git 工具、默认不注册 browser、删除 NativeToolStrategyBuilder 的做法。OpenOPC 协调 turn 禁止 shell，但允许部分文件读写；让它“改用 shell”会留下能力缺口。配置中已有 browser 的角色也不能静默失去工具。新的精简档位应显式开启，默认保持旧能力；注册、实际权限、schema 和 prompt 四者必须一致。

    Talen `_environment_context` 同步执行多次 git 命令，每次有 timeout；直接塞进 async prompt builder 会阻塞并行角色，宜通过线程/异步子进程，按本次 workspace 缓存。OpenOPC company contract 已有本地日期/时区，不要重复或以不同 timezone 冲突；子 agent 必须使用其实际 worktree 的 cwd。

    当前 Talen 默认 `preserve_short_results` 分支甚至直接用生命周期契约作为 stable prompt。那是实验 harness 的选择，不能拿来替换 OpenOPC 的角色、公司、memory trust 和自验证契约。

    证据：[OpenOPC 权限集合](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/native_agent.py:610)、[公司工具策略](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/prompt_harness/tool_strategy.py:81)、[Talen 环境构建](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/prompt_harness/builder.py:14)、[Talen prompt 分支](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/native_agent.py:171)。

15. **新的上下文机制应单独落地，首先借鉴 canonical transcript / request view 分离。**

    Talen 已不止“一轮结果过期”：当前包含 retain_all、expire_results、preserve_short_results/receipt、adaptive_checkpoint、rolling_checkpoint、rolling_summary、file_summary、plan_step_checkpoint 共八种模式；还有独立的实验性 DeepSeek Harness PTC。因此不能把最新 HEAD 全部差异当成一组已验证的通用优化，也不能用 standalone benchmark 的结论推定公司任务收益。

    最适合先借鉴的是 `context_view.py` 的设计：规范历史保持可追溯，请求视图选择保留内容。对 OpenOPC 应保持现有策略为默认，先开放 task mode 对照实验；company 实验另设开关、版本与恢复兼容。

    在公司模式下，需要独立于普通工具结果保留：当前 assignment/acceptance criteria、run/WorkItem/attempt 身份、rework correction、审批等待与决定、controller 所有权、peer wait/reply、delegation 的 WorkItem ID、report/review/delivery 证据。不能让 `release_context` 总结替代这些规范状态，也不能把 summary/receipt 当新的调度指令。

    尤其禁止用“结果看不见了就重调工具”的通用提示：重复 file_read 通常只是成本，重复 delegate_work/send_dm/modify_work_item 或写命令则可能重复业务副作用。输出归档的 output_ref 必须沿用 OpenOPC 的 exact-read 和 SecureWorkspace 边界，不能把 Talen 的路径写入代码整体覆盖过来。

    证据：[Talen 模式定义](/data2/bjdwhzzh/tmp/talen-native-history/talen/core/config.py:552)、[请求视图](/data2/bjdwhzzh/tmp/talen-native-history/talen/layer3_agent/runtime_v2/context_view.py:1)。

**已经解决或不能倒退的差异**

| 差异 | 保留 OpenOPC 的原因 |
| --- | --- |
| Talen 的验证门和 hidden-card deadlock 特判 | OpenOPC `_run_verification_audit` 已统一为 advisory，不创建 owner wait；质量处分由 company review/rework/delivery 决定，默认额外 verifier 关闭。Talen 旧 gate 反而可能重新引入无 checkpoint 的等待 |
| Talen 三档 approval_policy | OpenOPC 已有 session scoped `read-only/auto/full-access`、权限继承、明确 deny 优先和沙箱档位；不能用全局配置替换，也不能把 read-only 简化为“每次询问” |
| “拒绝是反馈” | OpenOPC `_handle_pause_or_peer_wait` 已只对真实 input/peer/approval wait 返回等待；exact denial 会生成规范 ToolResult。pre-hook 的 stop_execution/stop_batch_on_failure 控制当前调用/批次，不能仅凭名字推断整个 runtime 终止后把它们全部设 False |
| 实验性 enable_permission_gate 关闭开关 | 不把 benchmark 的权限绕过路径带入普通执行；工具 hooks、声明式权限和 company effect fence 不能用一个实验开关整体关闭 |
| Talen 自动 sandbox retry | OpenOPC 明确不在失败后自动升为 elevated/off，且使用 coroutine-local override 和冻结执行信封。恢复 Talen 的共享 Task.metadata 改写会扩大权限并影响并行调用 |
| 删除 RuntimeCompanyControllerToolFence | 会丢失 controller lease、文件 ownership、opaque execution envelope 与 effect 边界；full-access 也不应绕过组织所有权 |
| 精简 subagent 任务/session 构建 | OpenOPC 为子任务保留 scope/父 WorkItem 关联但清除父 projection/claim 等身份，并持久化 child session；Talen 删除这些路径不适用于公司辅助 agent |
| 精简 NativeAgent `_build_user_message` | 会删除 rework 的 per-attempt canonical correction 与 revision 去重，导致重复提交旧产物或重复注入纠正要求 |
| 简化 provider 错误处理 | 保留 quota typed signal、park/backoff、runtime abort settlement；不能把配额失败改成普通终态失败 |
| 简化 file/output/registry | 保留 SecureWorkspace、路径/符号链接保护、ToolInvocationValidationError、company effect metadata 和执行前验证。Talen 较少代码不是这些能力的替代 |
| 移除公司 memory/filter/context 分支 | 保留角色/项目记忆范围、已处理 checkpoint 的过滤、latest inbox 和 report/review 专用上下文 |

关键保护点：[company fence](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/tool_hooks.py)、[审批恢复及 abort settlement](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py)、[子任务身份](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/subagents.py:416)、[权限模型](/data2/bjdwhzzh/project-hku/OpenOPC/opc/core/native_permissions.py)、[验证审计](/data2/bjdwhzzh/project-hku/OpenOPC/opc/layer3_agent/runtime_v2/runtime.py:5076)。

**迁移顺序与 company 验收矩阵**

建议拆成五批可独立 review、回退的改动，而不是一次 runtime 重写：

1. 完整性修复：thinking、单份会话 tool result/兼容读取、空回复/截断分类、完整失败信封。
2. 恢复统一：共享 resume helper 与 Engine 接线，覆盖 explicit marker、canonical fallback、approval resume、same-attempt retry 和新 WorkItem，不改公司状态机。
3. 低耦合 runtime/provider：幂等预算、压缩 no-op、call ID 归并、usage 与 max_tokens。provider 更改保留现有 reasoning/quota 行为。
4. 性能优化：动态上下文分区、prefetch 去重和 delta 合并。先测真实请求体、提交次数、首 token/总延时，再判断收益；Store batching 暂不并入。
5. 产品实验：update_plan、精简 schema/提示词、新 context view/lifecycle，先 task mode，随后按 company 角色/turn 放开。

| 场景 | 实施后的必要断言 | 现有测试基础 |
| --- | --- | --- |
| Standalone task、多轮用户追问 | 正常回答可结束；空/截断不假成功；前轮事实仍可用；附件保留 | native_agent_integration、native_runtime_v2、session_context_compression |
| Corporate/custom org worker | 只完成当前 WorkItem；结果可进入 report/review；不能写他人产物 | actor_runtime_company_mode、company_collaboration、company_artifact_ownership、custom_org_* |
| 多团队 manager | manager_decide/monitor/synthesize/deliver 的工具集合不扩大；无重复 delegation；内部计划不改任务图 | company_role_session_behavior、kanban_push_runtime、company_review_flow |
| report/review/rework | 执行证据可恢复；review verdict 不被尾注/截断破坏；纠正要求每 attempt 一次 | worker_report_handoff、runtime_attempt_user_seed、review_verdict_and_dep_refresh、company_review_flow |
| user input / peer wait / meeting | 只有真实 interaction 创建等待；回复释放原 claim；不重问已回答问题 | durable_interactions、request_user_input_collab、company_runtime_suspend_resume |
| 审批通过/拒绝及多待批调用 | 精确原 ToolCall 续跑；无重新生成或重复 effect；deny 为证据而非隐式 allow | company_controller_interaction_claim_fence、native_permission_profiles、runtime_v2_controller_tool_fence |
| stop/resume、崩溃和 quota | 执行 agent pin、role/session/attempt 不变；正确 park/backoff；取消后无悬挂权限/任务 | stop_resume_native_pin、provider_quota_park、company_controller_shutdown_atomic、run_failure_settlement |
| 同角色多个 WorkItem / 多角色并行 | 计划、thinking、prefetch、event buffers 不串；权限 override 不通过共享 Task 泄漏 | parallel_runtime_isolation、company_role_session_behavior、claim_release_invariant |
| native + 外部/opaque team | manager 保留路由能力；团队内部身份不混入 native；provider result 仍过公司提交边界 | jiuwen_integration、external_team_activity、external_session_continuity |
| 旧数据库 + 新版程序 | 旧双 part 去重但不丢结果；旧 todo/marker 能读；幂等升级/恢复 | 隔离 legacy probes、runtime_v2_migration、session/store 相关测试 |
| 平台与沙箱 | cwd/worktree 正确；符号链接边界保留；Windows/macOS 能真实启动与停止 | native_file_workspace_boundary、secure_workspace_platform、runtime_execution_environment；另补真实平台 smoke |

其中“恢复入口统一”和“事件缓冲”最需要 company 交叉验证：前者容易把角色会话连续性误当执行身份连续性，后者容易把展示优化误扩展到持久控制状态。

**本轮实际验证结果**

使用用户指定的 OpenOPC `.venv`，Python 3.12.9 / pytest 9.0.2。没有使用真实 LLM 或启动真实外部团队执行。

| 检查组 | 结果 |
| --- | --- |
| Native/runtime/prompt/权限/恢复/quota 首批 | 198 passed，3 subtests passed |
| Company/WorkItem/controller/approval 等 | 初次 836 passed、3 failed；两项 init 测试受本次隔离 OPC_HOME 环境影响，移除该变量后均通过；最终这一组为 838 passed、1 个原有失败，176 subtests passed |
| Jiuwen/外部团队边界、handoff/review、执行环境、压缩等 | 182 passed、3 skipped，2 subtests passed |
| 去重后的现有测试结果 | **1218 passed、1 个原有失败、3 skipped；另有 181 subtests passed** |
| 定向缺口 probes | **OpenOPC 15 项断言失败；同等契约在 Talen 15 项通过**。这是缺口复现，不是迁移后回归结果 |

唯一保留的现有失败是 `test_ordinary_company_tests_use_work_item_fixture_names`：扫描到三个测试文本中的 `staged_resume`、`workflow.updated` 和 `resume stage`，违反命名扫描规则。它发生在未修改生产代码的基线，不能据此声称 company 执行回归，也不能把整个基线称为全绿。两项 init 环境干扰已单独复跑排除。

15 项 probes 包括：每轮 thinking 1 项、单份写入/恢复和旧格式兼容 4 项、空/截断恢复 4 项、context builder 恢复判断 1 项，以及预算幂等、canonical resume、no-op breaker、稀疏 stream index、max-iteration 信封各 1 项。Talen 持久化用例迁入隔离目录时只适配包名，并为 OpenOPC 补上真实 Task 保存前置条件；未 monkeypatch 生产方法以制造结果。新 runtime probes 通过相同接口/等价参数在两库执行。max-iteration 用例直接测试耗尽出口，稀疏 index 用例测试归并边界，均不代表已观测到线上发生频率。

可复核产物：

- [OpenOPC 持久化 probes](/data2/bjdwhzzh/tmp/native-audit/test_integrity_port_probe.py) / [复现日志](/data2/bjdwhzzh/tmp/native-audit/probe-integrity.log)。
- [双库 runtime probes](/data2/bjdwhzzh/tmp/native-audit/test_runtime_port_probe.py) / [OpenOPC 日志](/data2/bjdwhzzh/tmp/native-audit/probe-runtime-openopc.log) / [Talen 日志](/data2/bjdwhzzh/tmp/native-audit/probe-runtime-talen.log)。
- [Talen 持久化对照](/data2/bjdwhzzh/tmp/native-audit/test_integrity_talen_probe.py) / [最终 15 项全部通过日志](/data2/bjdwhzzh/tmp/native-audit/probe-talen-final.log)。
- [Native 基线](/data2/bjdwhzzh/tmp/native-audit/baseline-native.log)、[Company 基线](/data2/bjdwhzzh/tmp/native-audit/baseline-company.log)、[失败项复核](/data2/bjdwhzzh/tmp/native-audit/baseline-company-recheck.log)、[相邻链路基线](/data2/bjdwhzzh/tmp/native-audit/baseline-adjacent.log)。
- [归一化函数差异索引](/data2/bjdwhzzh/tmp/native-audit/function-diffs.json)，同目录保留关键历史补丁与函数级差异。

实施后的验收仍需新增本文列出的 company 交叉用例，再做受控的真实 provider/公司流程 smoke。当前证据足以确定优先修复点与禁止覆盖的边界；不能用 standalone Talen 测试通过替代这些 company 验收。
