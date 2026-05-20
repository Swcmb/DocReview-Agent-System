# DocReview 智能体系统 - 验证清单

## 项目基础结构和配置
- [x] 项目目录结构符合规范（src/、tests/、prompts/、data/、reviews/、logs/ 等）
- [x] pyproject.toml 包含所有必要依赖（langgraph 0.2.x、langchain、pydantic、typer、rich 等）
- [x] .env.example 包含完整配置项模板
- [x] AppConfig 类正确实现，支持从环境变量加载配置
- [x] 日志模块输出结构化日志
- [x] 提示词加载器能正确读取和处理缺失情况

## 数据模型和状态
- [x] ReviewConclusion Pydantic 模型定义正确，字段验证有效
- [x] AgentState TypedDict 包含所有必要字段
- [x] IssueStatus、IssueTracker、ReviewReport 数据结构完整
- [x] spec_version 生命周期规则正确实现（首次=1，修订+1，手动修订+1）
- [x] 所有数据结构可被 LangGraph 正确序列化

## 工具模块
- [x] 阅读工具能正确读取文件内容
- [x] 阅读工具正则搜索功能正常
- [x] 阅读工具目录列表功能正常
- [x] 阅读工具能生成 unified diff
- [x] 阅读工具路径安全校验有效（禁止工作目录外访问）
- [x] 终端工具能执行命令并返回 stdout/stderr/exit_code
- [x] 终端工具白名单拦截危险命令（如 rm -rf /）
- [x] 终端工具超时机制正常（默认 300 秒）
- [x] 终端工具重试机制正常（指数退避，最多 3 次）
- [ ] 联网搜索能返回相关网页结果
- [ ] 联网搜索能获取 URL 内容并转为 Markdown
- [ ] 联网搜索能验证第三方 API 可用性
- [ ] 联网搜索能检测过时依赖版本

## MCP 客户端
- [x] Sequential Thinking MCP 服务能正常启动
- [x] Sequential Thinking 多步推理调用成功
- [x] Context7 MCP 服务能正常启动
- [x] Context7 能解析库标识符和查询文档
- [x] MCP JSON-RPC 协议通信正常
- [x] MCP 健康检查机制正常
- [x] MCP 失败时自动降级为纯 LLM 模式（mcp_degraded=True）
- [x] MCP 恢复时自动切回完整模式
- [x] MCP 进程在 finalize 时正确关闭
- [x] MCP 调用超时机制正常（默认 30 秒）

## Supervisor Agent
- [ ] 能根据任务描述从零生成规格文档（场景 1）
- [ ] 能将外部文档转换为规格格式（场景 2）
- [ ] 能结合任务和文档生成规格（场景 3）
- [ ] 规格文档结构完整（概述、功能需求、验收标准等）
- [ ] 转换规格时不改变原始文档实质内容
- [ ] 能根据审查报告正确修订规格文档
- [ ] spec_version 在修订时正确递增
- [ ] 能接收用户确认并执行实际任务
- [ ] 状态在各节点间完整传递

## DocReview Sub-Agent
- [ ] 六步审查流程完整执行
- [ ] 核心闭环提取正确（_extract_core_loop）
- [ ] 一致性检查有效（_check_consistency）
- [ ] 需求原子化分解正确（_atomize_requirements）
- [ ] 技术可行性推导合理（_deduce_feasibility）
- [ ] 风险检测和分类准确（_detect_risks）
- [ ] 可执行性审查到位（_review_executability）
- [ ] 审查报告格式严格符合 PROMPT.md
- [ ] 审查报告包含 issue_id（格式 {severity_short}-{round}-{seq}）
- [ ] 结构化结论判定规则正确：
  - 存在 Blocking 问题 → Fail
  - AC 覆盖率不完整 → Fail
  - 仅 High 问题 → Conditional Pass
  - 仅 Medium/Low 或无问题 → Pass
- [ ] AC 覆盖率检查正确（P0 FR 被 AC 覆盖）
- [ ] 严格遵守独立性：仅发现问题和建议，不直接重写文档
- [ ] 审查输出与原文档 Jaccard 相似度 ≤ 30%
- [ ] 修订版本文档审查包含对比信息

## LangGraph 工作流
- [x] initialize 节点正确加载配置、创建目录、设置默认值
- [x] initialize 正确检查 Node.js 可用性并降级
- [x] route_after_initialize 正确路由（有文档路径→load_document，否则→generate_spec）
- [x] load_document 正确加载文档内容
- [x] generate_spec 正确调用 Supervisor
- [x] docreview 正确调用 DocReview 并更新状态
- [x] evaluate_result 正确评估审查结论
- [x] 停滞检测正确（连续 2 轮问题不变时计数）
- [x] 审查历史压缩正确（第 3 轮及更早替换为摘要）
- [x] spec_snapshot 正确保存用于对比
- [x] route_after_evaluate 正确路由：
  - Fail→revise_spec（未达最大迭代）
  - Pass/Conditional→user_approval
  - 超迭代/停滞→finalize
- [x] revise_spec 正确调用 Supervisor 修订
- [x] user_approval 正确设置中断点（interrupt_before）
- [x] route_after_approval 正确路由：
  - user_approved=True→execute
  - user_approved=False 且 Conditional→revise_spec
  - user_approved=False 且 Pass→finalize
  - approval_timed_out→finalize
- [x] execute 正确调用 Supervisor 执行任务
- [x] finalize 正确保存审查历史、输出摘要、清理资源
- [x] SqliteSaver 正确持久化状态到 data/checkpoints.db
- [x] checkpoint 损坏时自动备份重建

## CLI 入口
- [ ] 子命令 review、generate-spec、status、resume 可用
- [ ] 选项 --doc-path、--task、--max-iterations、--output-dir、--spec-output、--verbose、--no-mcp、--model、--config 正确解析
- [ ] review 子命令能启动完整审查流程
- [ ] generate-spec 子命令能生成规格文档
- [ ] status 子命令能查看审查历史
- [ ] resume 子命令能恢复中断工作流
- [ ] 退出码正确：
  - 0：审查通过，执行成功
  - 1：审查未通过
  - 2：系统错误
  - 3：用户中断或拒绝
  - 4：参数无效或文档不存在
- [ ] 启动前预检查 .env 和 LLM_API_KEY
- [ ] 用户确认超时处理正确（asyncio.wait_for + aupdate_state）

## 提示词文件和文档
- [x] prompts/PROMPT.md 存在且内容完整
- [x] prompts/WHENTOCALL.md 存在且内容完整
- [x] prompts/SPAC_prompt.md 存在（可选参考）
- [x] README.md 存在且说明清晰
- [x] 代码关键逻辑有中文注释
- [x] 公共 API 有完整 docstring
- [x] 代码遵循 PEP 8 规范

## 单元测试和集成测试
- [x] 工具模块单元测试通过（test_reading.py, test_terminal.py, test_web_search.py）
- [x] Agent 单元测试通过（test_docreview.py, test_supervisor.py）
- [x] 工作流集成测试通过（test_review_workflow.py）
- [x] MCP 客户端测试通过（test_context7.py, test_sequential_thinking.py）
- [x] 测试覆盖主要流程
- [x] pytest-asyncio 正确配置用于异步测试
- [x] LLM 成本追踪测试通过（test_llm.py）

## LLM 成本追踪
- [x] 能从 OpenAI 响应提取 token 使用量
- [x] 能从 Anthropic 响应提取 token 使用量
- [x] 能按字符估算成本（流式响应时）
- [x] 成本累加计算正确
- [x] 超过 llm_max_cost_per_task 时正确触发 DOCREVIEW_ERR_LLM_008
- [x] evaluate_result 中正确检查成本

## 异常处理和边缘场景
- [x] 完整错误码体系实现（DOCREVIEW_ERR_*）
- [ ] LLM 超时、密钥无效、格式异常、限流、服务错误处理正确
- [x] 工具调用失败重试正确（最多 3 次，指数退避）
- [ ] 文档 >100KB 时自动启用分段审查（Chunk Mode）
- [ ] 上下文窗口管理正确（摘要策略、分段审查）
- [x] 用户手动修订检测正确（spec_snapshot 对比）
- [x] 最大迭代次数限制正确触发
- [x] 停滞检测正确触发
- [x] 用户确认超时正确处理
- [ ] 用户 Ctrl+C 中断正确处理
- [ ] 磁盘空间不足检查

## 最终集成
- [ ] 端到端流程能完整执行（从输入到 finalize）
- [ ] 审查通过后等待用户确认
- [ ] 用户确认后执行任务
- [ ] 用户拒绝后安全终止
- [ ] 多轮迭代审查正常工作
- [ ] 所有验收标准满足
