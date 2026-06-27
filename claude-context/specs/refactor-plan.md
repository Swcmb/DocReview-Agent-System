# DocReview Agent System — 完成与重构规格文档 v2

> 基于 v1 审查反馈修订，解决全部 Blocking 和 High 级别问题。

## 1. 目的

将当前约 70% 完成度的项目补全至可运行状态，进行系统性重构，提升代码质量和可维护性，最终上传到 GitHub。

## 2. 范围边界

### 包含
- 修复工作流核心断点（删除死代码、验证 agent 方法签名）
- 补全缺失实现（解析器、AC 覆盖率、结构化提示词）
- 清理 Context7 为纯降级路径
- TerminalTool 异步包装
- 代码清理与测试更新
- GitHub 上传

### 不包含
- Context7 真实 MCP 集成（决定移除，仅保留 LLM 降级路径）
- 更换 LLM 提供商（保持 ChatOpenAI 兼容接口）
- Web UI / 新功能

## 3. 关键设计决策

### 3.A Context7 处理决策
**决策：移除 Context7 MCP 集成，简化为纯 LLM 分析路径。**
理由：当前 Context7 从未真正启动 MCP 服务器进程，`start()` 仅检查 npm 是否安装。`_query_context7_api` 和 `_fallback_query` 均返回无意义的模拟数据。维护一个永远走降级的空壳增加复杂度而无收益。
方案：DocReviewAgent 中移除 context7 依赖，`_enrich_context` 改为纯文本提取技术栈（不查询外部服务）。

### 3.B TerminalTool 异步策略
**决策：使用 `loop.run_in_executor` 包装，不修改 BaseTool 接口。**
理由：`BaseTool.execute()` 是同步接口，改为 async 会影响所有工具实现。`run_in_executor` 是无侵入的 drop-in 方案。

### 3.C MCP Server 合规范围
**决策：目标为基本工具调用能力（tools/list + tools/call + initialize），不要求完整 MCP 2024-11-05 协议。**
理由：`notifications/initialized`、`resources/list`、`prompts/list` 等方法为可选协议特性，当前无使用场景。

## 4. 问题清单与技术方案

### 4.1 🔴 P0：工作流死代码清理 + 签名验证

**现状**：`build_workflow()` 已正确注册 agent 方法（`supervisor.generate_spec`、`docreview_agent.review`、`supervisor.revise_spec`）。三个模块级函数（`generate_spec` L207、`docreview` L222、`revise_spec` L311）是死代码，从未被 `build_workflow` 引用。

**方案**：
1. 删除三个死代码函数
2. 验证 agent 方法签名符合 LangGraph 节点契约 `async def (state: AgentState) -> AgentState`
3. 当前签名验证结果：
   - `SupervisorAgent.generate_spec(self, state: AgentState) -> AgentState` ✅
   - `SupervisorAgent.revise_spec(self, state: AgentState) -> AgentState` ✅
   - `DocReviewAgent.review(self, state: AgentState) -> AgentState` ✅
4. 如签名不匹配，创建带类型注解的适配函数（非 lambda）

### 4.2 🔴 P0：提示词内联与清理

**现状**：`DocReviewAgent` 从 `.trae/prompts/` 加载提示词，目录不在仓库中。`whentocall_prompt` 加载后从未被任何方法引用。

**方案**：
1. 删除 `whentocall_prompt` 相关代码（死代码）
2. 将 `review_prompt` 内联为模块常量 `REVIEW_SYSTEM_PROMPT`（< 100 行）
3. 内联提示词必须包含：
   - 六步审查方法论指令（核心闭环提取 → 一致性检查 → 需求原子化 → 技术可行性 → 风险检测 → 可执行性审查）
   - 每步的结构化输出格式规范
   - 严重级别分类指南（Blocking/High/Medium/Low）
   - 问题类型分类法（来自 ISSUE_TYPES 常量）
4. 移除 `_load_prompt_safe` 方法和 `PROMPT_PATH`/`WHENTOCALL_PATH` 常量

### 4.3 🟡 P1：解析器实现 + 结构化输出格式

**现状**：`_parse_atomic_requirements` 返回 `[]`，`_parse_dependency_graph` 返回 `{"nodes": [], "edges": []}`。LLM 提示词未指定结构化输出格式。

**方案**：
1. 在 `_think_step` 和 `_llm_think` 的提示词中嵌入结构化输出格式要求：

```
输出格式要求：
- 问题: [ISSUE] type=<类型> severity=<级别> description=<描述> location=<位置> suggestion=<建议>
- 需求: [FR-N] <描述> priority=<P0/P1/P2>
- 依赖: [DEP] <源> depends_on <目标>
- 验收: [AC-N] covers=<FR-ID列表> criteria=<标准>
```

2. `_parse_issues_from_text` 增强：双路径策略
   - 优先级 1：尝试 `[ISSUE]` 结构化格式
   - 优先级 2：若结构化格式返回 0 条，降级到现有宽松模式匹配
   - 优先级 3：若两者均返回 0 条，记录警告并返回空列表
   - 质量门控：单次 LLM 响应解析超过 20 条 issue 时，截断并记录警告（疑似误报）
3. `_parse_atomic_requirements` 实现：解析 `[FR-N]` 格式
4. `_parse_dependency_graph` 实现：解析 `[DEP]` 格式

### 4.4 🟡 P1：AC 覆盖率检查增强

**现状**：`_check_ac_coverage` 只匹配 `**FR-N**(P0)` 格式。

**支持的格式变体**：
| 格式 | 正则 |
|:---|:---|
| `**FR-N**(P0)` | `\*\*FR-(\d+)\*\*.*?\(P0\)` |
| `FR-N (P0)` | `FR-(\d+)\s*\(P0\)` |
| `FR-N: P0` | `FR-(\d+)\s*[:：]\s*P0` |
| `FR-N - P0` | `FR-(\d+)\s*-\s*P0` |
| `FR-N [P0]` | `FR-(\d+)\s*\[P0\]` |
| `**FR-N**: 优先级 P0` | `\*\*FR-(\d+)\*\*.*?优先级.*?P0` |

同时集成 `schemas/models.py` 中已有的 `calculate_ac_coverage()` 函数。

### 4.5 🟡 P1：移除 Context7 真实集成

**方案**：
1. `context7.py`：保留文件但大幅简化。移除 `_query_context7_api` 的模拟数据。`resolve_library_id` 保留为静态映射。`query_docs` 直接调用 `_fallback_query` 并记录降级日志。
2. `docreview.py` 的 `_enrich_context`：简化为从规格文档中提取技术栈关键词（正则匹配），不调用 Context7 服务。返回 `TechContext` 但 `relevant_docs` 和 `best_practices` 为空。
3. 保留 `Context7Client` 类以备未来扩展，但标注为 "当前为降级模式"。

### 4.6 🟡 P1：数据流定义

每个工作流节点的输入/输出契约：

| 节点 | 输入状态字段 | 输出状态字段 |
|:---|:---|:---|
| `initialize` | 原始输入 | 标准化状态 + MCP 可用性检测 |
| `load_document` | `document_path` | `document_content` |
| `generate_spec` | `user_task` + `document_content` | `specification` + `spec_version=1` |
| `docreview` | `specification` | `review_reports` + `review_conclusion_data` + `iteration_count+1` |
| `evaluate_result` | `review_conclusion_data` | `review_conclusion` + `stagnation_count` + `spec_snapshot` |
| `revise_spec` | `specification` + `review_reports[-1]` | `specification`(更新) + `spec_version+1` |
| `user_approval` | - | `user_approved` |
| `execute` | `user_task` + `specification` | `execution_status` + `execution_output` |
| `finalize` | 全部状态 | 保存历史 + `execution_status=completed` |

## 5. 修订后的执行步骤

> 步骤顺序已根据审查反馈调整：先完成 agent 内部变更，再处理工作流连接。

| 序号 | 步骤 | 涉及文件 | 依赖 |
|:-----|:-----|:---------|:-----|
| 1 | 内联提示词 + 清理 DocReviewAgent | `src/agents/docreview.py` | 无 |
| 2 | 实现解析器 + 结构化输出格式 | `src/agents/docreview.py` | 步骤 1 |
| 3 | 增强 AC 覆盖率检查 | `src/agents/docreview.py` | 步骤 2 |
| 4 | 简化 Context7 为降级模式 | `src/mcp/context7.py` | 无 |
| 5 | 删除工作流死代码 + 验证签名 | `src/workflows/review_workflow.py` | 步骤 1 |
| 6 | TerminalTool 异步包装 | `src/tools/terminal.py` | 无 |
| 7 | 代码清理（移除 AgentStateModel、补充 __init__.py） | 多文件 | 步骤 1-6 |
| 8 | 更新测试 | `tests/` | 步骤 1-7 |
| 9 | 运行全量测试验证 | - | 步骤 8 |
| 10 | .gitignore 完善 + GitHub 上传 | `.gitignore`, git | 步骤 9 |

## 6. 回滚策略

1. 执行前创建 git tag：`pre-refactor-v2`
2. 每个步骤完成后 git commit，消息格式：`refactor(step-N): <描述>`
3. 如某步导致测试失败：`git revert HEAD` 回到上一个稳定状态，标记待手动审查
4. 独立可回滚的步骤：4、6、7（不影响核心 agent 逻辑）
5. 链式依赖步骤：1→2→3→5（回滚步骤 5 需同时回滚 1-3）

## 7. 成功标准

- [ ] `pytest` 全部通过（现有测试 + 更新后的测试）
- [ ] `grep -rn "return \[\]" src/agents/docreview.py` 无结果（无空实现）
- [ ] `grep -rn "TODO\|FIXME" src/` 无结果
- [ ] `create_workflow_runtime()` + `build_workflow()` 可正常构建工作流图
- [ ] 工作流对样本文档可产生包含 issue_id/severity/description/suggestion/location 的 ReviewReport
- [ ] MCP Server HTTP 模式可启动并通过 `/health` 检查
- [ ] MCP Server stdio 模式可响应 `initialize` + `tools/list` + `tools/call` 请求
- [ ] 成功推送到 GitHub 远程仓库
