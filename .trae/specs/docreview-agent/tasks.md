# DocReview 智能体系统 - 实施计划

## [x] 任务 1：项目基础结构和配置 ✅
- **优先级**：P0
- **依赖**：None
- **描述**：
  - 创建项目目录结构 ✅
  - 创建 pyproject.toml 配置文件 ✅
  - 创建 .env.example 模板 ✅
  - 创建基础配置模块（config.py）✅
  - 创建日志配置工具 ✅
  - 创建提示词加载工具 ✅
- **验收标准覆盖**：AC14, AC25
- **测试需求**：
  - `programmatic` TR1.1：AppConfig 能正确加载环境变量 ✅
  - `programmatic` TR1.2：日志模块正常输出结构化日志 ✅
  - `programmatic` TR1.3：提示词加载器能正确读取文件 ✅
- **注意**：确保遵循 PEP 8 规范

## [x] 任务 2：数据模型和状态定义 ✅
- **优先级**：P0
- **依赖**：任务 1
- **描述**：
  - 创建 schemas/models.py（Pydantic 模型：ReviewConclusion 等）✅
  - 创建 state/agent_state.py（AgentState TypedDict）✅
  - 定义 IssueStatus、IssueTracker、ReviewReport 等数据结构 ✅
  - 实现 spec_version 生命周期管理逻辑 ✅
- **验收标准覆盖**：AC29
- **测试需求**：
  - `programmatic` TR2.1：Pydantic 模型验证通过 ✅
  - `programmatic` TR2.2：AgentState 类型定义完整 ✅
  - `programmatic` TR2.3：spec_version 递增逻辑正确 ✅
- **注意**：确保与 LangGraph 状态序列化兼容

## [x] 任务 3：工具模块开发 ✅
- **优先级**：P0
- **依赖**：任务 2
- **描述**：
  - 创建 tools/base.py 工具基类 ✅
  - 创建 tools/reading.py（阅读工具，含路径安全校验）✅
  - 创建 tools/terminal.py（终端工具，含命令白名单、超时、重试）✅
  - 创建 tools/web_search.py（联网搜索工具）✅
- **验收标准覆盖**：AC8, AC9, AC10, AC18, AC19, AC20, AC21, AC22, AC23, AC24, AC25
- **测试需求**：
  - `programmatic` TR3.1：阅读工具读写文件正常，路径遍历防护有效 ✅
  - `programmatic` TR3.2：终端工具执行命令并正确返回结果，白名单拦截危险命令 ✅
  - `programmatic` TR3.3：终端超时和重试机制正常 ✅
  - `programmatic` TR3.4：联网搜索基本功能可用 ✅
- **注意**：终端命令默认超时 300 秒，重试采用指数退避

## [x] 任务 4：MCP 客户端开发 ✅
- **优先级**：P0
- **依赖**：任务 3
- **描述**：
  - 创建 mcp/base.py MCP 基类 ✅
  - 创建 mcp/sequential_thinking.py（Sequential Thinking 客户端）✅
  - 创建 mcp/context7.py（Context7 客户端）✅
  - 实现 MCP 进程生命周期管理（启动、健康检查、崩溃恢复、关闭）✅
  - 实现降级模式（mcp_degraded=True 时回退纯 LLM）✅
- **验收标准覆盖**：AC11, AC12, AC26
- **测试需求**：
  - `programmatic` TR4.1：MCP 服务启动和健康检查正常 ✅
  - `programmatic` TR4.2：Sequential Thinking 多步推理调用成功 ✅
  - `programmatic` TR4.3：Context7 文档查询调用成功 ✅
  - `programmatic` TR4.4：MCP 降级模式正常工作 ✅
- **注意**：MCP 调用超时默认 30 秒，重试 3 次（1s/2s/4s）

## [x] 任务 5：Supervisor Agent 开发 ✅
- **优先级**：P0
- **依赖**：任务 4
- **描述**：
  - 创建 agents/supervisor.py ✅
  - 实现 generate_spec（支持三种输入场景）✅
  - 实现 convert_to_spec（文档转规格）✅
  - 实现 generate_spec_from_task（任务转规格）✅
  - 实现 revise_spec（根据审查报告修订规格）✅
  - 实现 execute_task（执行实际任务）✅
  - 集成提示词模板 ✅
- **验收标准覆盖**：AC1, AC4, AC6, AC7, AC16, AC17
- **测试需求**：
  - `programmatic` TR5.1：三种输入场景规格生成正常 ✅
  - `programmatic` TR5.2：规格修订功能根据审查报告正确更新 ✅
  - `programmatic` TR5.3：spec_version 在修订时正确递增 ✅
- **注意**：转换规格时不改变原始文档实质内容，仅结构格式化

## [x] 任务 6：DocReview Sub-Agent 开发 ✅
- **优先级**：P0
- **依赖**：任务 5
- **描述**：
  - 创建 agents/docreview.py ✅
  - 实现 _think_step（Sequential Thinking 封装）✅
  - 实现 _enrich_context（Context7 封装）✅
  - 实现六步审查 ✅
  - 实现 _compile_markdown_report（生成审查报告）✅
  - 实现 _compile_structured_conclusion（生成结构化结论，含 AC 覆盖率检查）✅
  - 严格执行独立性原则（不直接重写文档）✅
- **验收标准覆盖**：AC2, AC3, AC13, AC27, AC28, AC29
- **测试需求**：
  - `programmatic` TR6.1：六步审查流程完整执行 ✅
  - `programmatic` TR6.2：审查报告格式符合 PROMPT.md ✅
  - `programmatic` TR6.3：结构化结论判定规则正确（Pass/Conditional/Fail）✅
  - `programmatic` TR6.4：审查输出与原文档 Jaccard 相似度 ≤ 30% ✅
- **注意**：issue_id 格式为 {severity_short}-{round}-{seq}

## [x] 任务 7：LangGraph 工作流开发 ✅
- **优先级**：P0
- **依赖**：任务 6
- **描述**：
  - 创建 workflows/review_workflow.py ✅
  - 实现所有节点函数（initialize, load_document, generate_spec, docreview, evaluate_result, revise_spec, user_approval, execute, finalize）✅
  - 实现路由函数（route_after_initialize, route_after_evaluate, route_after_approval）✅
  - 实现辅助函数（停滞检测、历史压缩、成本检查）✅
  - 使用 SqliteSaver 持久化 ✅
  - 设置 interrupt_before=["user_approval"] ✅
- **验收标准覆盖**：AC5, AC7, AC13, AC14, AC15
- **测试需求**：
  - `programmatic` TR7.1：完整工作流从 initialize 到 finalize 正常执行 ✅
  - `programmatic` TR7.2：条件路由根据审查结论正确跳转 ✅
  - `programmatic` TR7.3：停滞检测在连续 2 轮问题不变时触发 ✅
  - `programmatic` TR7.4：审查历史在第 3 轮后正确压缩 ✅
  - `programmatic` TR7.5：user_approval 中断点正常工作 ✅
- **注意**：LangGraph 版本锁定 0.2.x，不使用 0.3.x

## [x] 任务 8：CLI 入口开发 ✅
- **优先级**：P0
- **依赖**：任务 7
- **描述**：
  - 创建 main.py（CLI 入口）✅
  - 使用 Typer 实现子命令：review、generate-spec、status、resume ✅
  - 实现通用选项 ✅
  - 实现 CLI 退出码处理（0/1/2/3/4）✅
  - 实现用户确认超时处理 ✅
  - 实现启动前预检查 ✅
- **验收标准覆盖**：AC1, AC6, AC7
- **测试需求**：
  - `programmatic` TR8.1：CLI 子命令和选项正确解析 ✅
  - `programmatic` TR8.2：review 子命令能启动完整审查流程 ✅
  - `programmatic` TR8.3：退出码根据结果正确返回 ✅
  - `programmatic` TR8.4：resume 子命令能正确恢复中断工作流 ✅
- **注意**：启动时检查 Node.js 可用性，缺失时提示但允许继续

## [x] 任务 9：提示词文件和文档 ✅
- **优先级**：P0
- **依赖**：任务 8
- **描述**：
  - 创建 prompts/ 目录 ✅
  - 创建 prompts/PROMPT.md（DocReview 核心提示词）✅
  - 创建 prompts/WHENTOCALL.md（触发条件定义）✅
  - 创建 prompts/SPAC_prompt.md（架构参考）✅
  - 创建项目根目录 README.md（使用说明）✅
- **验收标准覆盖**：AC3
- **测试需求**：
  - `human-judgment` TR9.1：提示词文件内容完整，格式正确 ✅
  - `human-judgment` TR9.2：README 文档清晰可用 ✅
- **注意**：PROMPT.md 和 WHENTOCALL.md 为运行时必需

## [x] 任务 10：单元测试和集成测试 ✅
- **优先级**：P1
- **依赖**：任务 9
- **描述**：
  - 创建 tests/ 目录结构 ✅
  - 创建 tests/conftest.py（pytest 配置）✅
  - 编写工具模块单元测试 ✅
  - 编写 Agent 单元测试 ✅
  - 编写工作流集成测试 ✅
  - 编写 MCP 客户端测试 ✅
- **验收标准覆盖**：AC8, AC9, AC10, AC11, AC12, AC18, AC19, AC20, AC21, AC22, AC23, AC24, AC25, AC26
- **测试需求**：
  - `programmatic` TR10.1：所有单元测试通过 ✅
  - `programmatic` TR10.2：集成测试覆盖主要流程 ✅
  - `programmatic` TR10.3：测试覆盖率达到合理水平 ✅
- **注意**：使用 pytest-asyncio 测试异步代码

## [x] 任务 11：LLM 成本追踪 ✅
- **优先级**：P1
- **依赖**：任务 10
- **描述**：
  - 创建 src/utils/llm.py ✅
  - 实现 _track_llm_cost（从 AIMessage.response_metadata 提取 token 并累加）✅
  - 实现 LLM 定价表 ✅
  - 在 evaluate_result 节点追加成本检查门禁 ✅
- **验收标准覆盖**：AC14
- **测试需求**：
  - `programmatic` TR11.1：成本累加计算正确 ✅
  - `programmatic` TR11.2：超过预算时正确触发 DOCREVIEW_ERR_LLM_008 ✅
- **注意**：支持按字符估算（流式响应时）

## [x] 任务 12：异常处理和边缘场景 ✅
- **优先级**：P1
- **依赖**：任务 11
- **描述**：
  - 实现完整错误码体系（DOCREVIEW_ERR_*）✅
  - 实现分段审查策略（Chunk Mode，文档 >100KB）✅
  - 实现 Token/上下文窗口管理 ✅
  - 实现用户手动修订检测 ✅
  - 实现 SQLite checkpoint 完整性检查 ✅
- **验收标准覆盖**：AC14, AC15
- **测试需求**：
  - `programmatic` TR12.1：各种异常场景有合理处理 ✅
  - `programmatic` TR12.2：大文档分段审查正常执行 ✅
  - `programmatic` TR12.3：checkpoint 损坏时自动备份重建 ✅
- **注意**：上下文窗口以 80% 为触发阈值

## [x] 任务 13：最终集成测试和文档完善 ✅
- **优先级**：P2
- **依赖**：任务 12
- **描述**：
  - 端到端测试完整流程 ✅
  - 完善代码注释和 docstring ✅
  - 检查 PEP 8 合规 ✅
  - 最终验证所有验收标准 ✅
- **验收标准覆盖**：所有 AC
- **测试需求**：
  - `programmatic` TR13.1：端到端流程正常完成 ✅
  - `human-judgment` TR13.2：代码质量和可维护性良好 ✅
  - `human-judgment` TR13.3：所有文档完整准确 ✅
- **注意**：关键逻辑添加中文注释

---

# ✅ 实施计划完成

**所有 13 个任务已成功完成！**
