# DocReview 智能体系统规格文档

> **版本**: 1.7  
> **日期**: 2026-05-20  
> **状态**: ✅ 已批准 — 第8轮审查通过，可进入开发实施阶段

---

## 1. 系统概述

### 1.1 项目名称
**DocReview Agent System** — 基于 LangGraph 框架的多智能体文档审查系统。

### 1.2 项目目标
构建一个生产级的多智能体 AI 系统，用于对产品需求文档（PRD）、技术方案、实施计划、验收清单等技术文档进行自动化质量审查。系统通过 Supervisor-DocReview 双智能体协作，配合 Sequential Thinking 和 Context7 MCP 服务，实现文档的迭代审查与自动修订闭环，确保文档在进入开发或正式审查前具备完整性、一致性和可执行性。

### 1.3 适用场景（基于 WHENTOCALL.md）
| 编号 | 场景 | 触发条件 | 输入类型 |
|------|------|----------|----------|
| S1 | PRD预审查 | 产品经理完成PRD后、团队审查前 | PRD文档 |
| S2 | 技术方案验证 | 技术主管完成技术方案后 | 技术方案/实施计划 |
| S3 | 智能体文档审计 | 其他智能体（如SOLO Coder）生成文档后 | 自动化生成的文档 |
| S4 | 质量门禁 | 新功能开发或重构项目启动前 | 任意结构化技术文档 |
| S5 | 修订后重新审查 | 文档修订后验证修改质量 | 修订版文档 |

### 1.4 核心能力
1. **规格文档自动生成**：Supervisor Agent 根据用户任务自动生成结构化规格文档
2. **六步法深度审查**：DocReview 子智能体执行严格的六步审查流程
3. **迭代修订闭环**：审查未通过时自动修订并重新提交，直至通过
4. **执行门禁机制**：审查通过后必须经过用户确认才能开始实际执行
5. **MCP 服务集成**：内嵌 Sequential Thinking（多步推理）和 Context7（上下文检索）

### 1.5 非目标与范围边界（Non-Goals / Out of Scope）

以下功能**不在当前产品 v1.0 版本范围内**，明确排除以避免范围蔓延：

| 编号 | 排除项 | 说明 |
|------|--------|------|
| NG1 | 实时协作审查 | 不支持多用户同时在线协作审查同一文档 |
| NG2 | 非 Markdown 格式解析 | 不支持 PDF、DOCX、HTML 等格式的原生解析（需用户提供纯文本或 Markdown） |
| NG3 | 多语言审查 | 审查报告和规格文档仅支持中英文混合场景，不支持其他语言 |
| NG4 | 自定义审查规则 DSL | 不支持用户通过 DSL/配置文件添加自定义审查规则（预留扩展点但不实现） |
| NG5 | 审查仪表盘 UI | 不包含 Web 可视化仪表盘，仅 CLI 交互 |
| NG6 | 自动化 CI/CD 集成 | 不提供 GitHub Actions/Jenkins 插件，需手动调用 CLI |
| NG7 | 多文档并行审查 | 当前仅支持单文档串行审查，多文档支持为 v1.1 规划范围。**与 SPAC_prompt.md 的"multi-document review"形成已知差距**，该需求将在 v1.1 中通过多文档队列 + 分组审查机制实现。 |
| NG8 | LLM 微调 | 不提供基于审查数据对 LLM 进行微调的能力 |
| NG9 | HTTP 端口暴露 | v1.0 不监听任何 HTTP 端口，所有交互仅通过 CLI。若内部组件（如 LangGraph debug server）默认开启 HTTP，须显式禁用。 |

---

## 2. 功能需求

### 2.1 Supervisor Agent（主管智能体）

| 编号 | 功能 | 描述 | 优先级 |
|------|------|------|--------|
| FR-S1 | 任务接收 | 接收用户通过 CLI 提交的任务/请求 | P0 |
| FR-S2 | 规划分解 | 将用户任务分解为可执行的子任务 | P0 |
| FR-S3 | 规格生成 | 自动生成或更新结构化规格文档 | P0 |
| FR-S4 | 提交审查 | 将规格文档提交给 DocReview 子智能体 | P0 |
| FR-S5 | 反馈处理 | 根据审查报告自动修订规格文档 | P0 |
| FR-S6 | 循环控制 | 管理审查迭代循环，控制终止条件 | P0 |
| FR-S7 | 执行门禁 | 审查通过后中断等待用户确认 | P0 |
| FR-S8 | 任务执行 | 用户确认后开始实际开发/执行任务 | P0 |
| FR-S9 | 迭代追踪 | 维护审查轮次计数、问题状态、修订历史 | P1 |

### 2.2 DocReview 子智能体（文档审查智能体）

| 编号 | 功能 | 描述 | 优先级 |
|------|------|------|--------|
| FR-D1 | 核心闭环提取 | 识别主业务流程/核心功能链，验证闭环完整性 | P0 |
| FR-D2 | 一致性检查 | 交叉检查术语、FR/AC对应关系、目标/非目标、约束/假设 | P0 |
| FR-D3 | 需求原子化 | 分解模糊需求为可量化原子需求，检查NFR覆盖 | P0 |
| FR-D4 | 技术可行性推导 | 基于指定技术栈模拟实施步骤，验证依赖和决策点 | P0 |
| FR-D5 | 风险检测与回退推导 | 以"what if"思维识别漏洞，分类风险严重程度 | P0 |
| FR-D6 | 可执行性审查 | 从开发者/测试者视角判断文档是否可直接执行 | P0 |
| FR-D7 | 审查报告生成 | 输出结构化 Markdown 审查报告 | P0 |
| FR-D8 | 迭代重新审查 | 识别修订版本文档，生成修订对比摘要 | P1 |
| FR-D9 | 严格独立性 | 只发现问题、给出建议，绝不直接重写文档 | P0 |

### 2.3 阅读模块

| 编号 | 功能 | 描述 | 优先级 |
|------|------|------|--------|
| FR-R1 | 文件读取 | 读取指定路径的文件内容，支持多种编码 | P0 |
| FR-R2 | 文本搜索 | 在文档中按正则表达式搜索内容 | P0 |
| FR-R3 | 目录列表 | 列出指定目录下的文件和子目录 | P0 |
| FR-R4 | 版本对比 | 比较两个版本文档的差异（diff分析） | P1 |
| FR-R5 | 路径安全 | 限制文件访问在工作目录范围内，防止路径遍历 | P0 |

### 2.4 终端模块

| 编号 | 功能 | 描述 | 优先级 |
|------|------|------|--------|
| FR-T1 | 命令执行 | 在系统终端中执行命令 | P0 |
| FR-T2 | 输出捕获 | 捕获 stdout 和 stderr | P0 |
| FR-T3 | 退出码返回 | 返回命令执行的退出状态码 | P0 |
| FR-T4 | 超时控制 | 支持命令执行超时设置 | P1 |
| FR-T5 | 重试机制 | 命令失败时支持重试（含指数退避） | P1 |
| FR-T6 | 命令白名单 | 危险命令拦截机制 | P0 |

### 2.5 联网搜索模块

| 编号 | 功能 | 描述 | 优先级 |
|------|------|------|--------|
| FR-W1 | 网页搜索 | 根据查询关键词搜索相关网页 | P0 |
| FR-W2 | URL内容获取 | 获取指定URL的网页内容并转换为Markdown | P0 |
| FR-W3 | API验证 | 验证第三方API/framework的可用性 | P1 |
| FR-W4 | 依赖检测 | 检测项目中过时的依赖库版本 | P1 |

### 2.6 MCP 服务集成

| 编号 | 功能 | 描述 | 优先级 |
|------|------|------|--------|
| FR-M1 | Sequential Thinking | 多步推理、结构化分解、迭代思维链 | P0 |
| FR-M2 | Context7 | 长上下文检索、跨文档链接、依赖追踪 | P0 |
| FR-M3 | MCP协议通信 | 标准MCP JSON-RPC协议通信 | P0 |

---

## 3. 技术架构

### 3.1 技术栈

| 层级 | 技术选型 | 版本要求 | 说明 |
|------|----------|----------|------|
| 编程语言 | Python | ≥ 3.11 | 主流 AI 开发生态 |
| 智能体框架 | LangGraph | ≥ 0.2.0, < 0.3.0 | 状态图驱动的多智能体编排。上限锁定因 0.3.x 中 SqliteSaver→AsyncSqliteSaver API 变更不兼容 v1.0 |
| LLM 接口 | LangChain | ≥ 0.3.0 | 统一的 LLM 调用抽象 |
| MCP 适配 | langchain-mcp-adapters | ≥ 0.1.0 | LangChain 与 MCP 的桥接 |
| 数据验证 | Pydantic | ≥ 2.0 | 状态和模型定义 |
| 异步支持 | asyncio | 标准库 | 异步工作流执行 |
| 持久化 | SQLite / JSON 文件 | — | 状态和审查历史持久化 |
| 日志 | logging | 标准库 | 结构化日志输出 |
| MCP 运行时 | Node.js | ≥ 18 LTS | MCP Server（Sequential Thinking / Context7）通过 npx 启动的运行时环境 |

### 3.2 架构分层图

```
┌─────────────────────────────────────────────────┐
│                   CLI 入口层                      │
│                  (main.py)                        │
├─────────────────────────────────────────────────┤
│                LangGraph 工作流层                  │
│         (workflows/review_workflow.py)            │
│  ┌─────────────┐  ┌──────────────┐               │
│  │ Supervisor   │  │  DocReview   │               │
│  │   Agent      │  │  Sub-Agent   │               │
│  │ (agents/     │  │ (agents/     │               │
│  │ supervisor/) │  │ docreview/)  │               │
│  └──────┬──────┘  └──────┬───────┘               │
├─────────┼────────────────┼───────────────────────┤
│         │        工具层   │                        │
│  ┌──────┴──────┬─────────┴───────┬──────────────┐│
│  │  阅读工具    │   终端工具       │ 联网搜索工具   ││
│  │  (reading)  │  (terminal)     │ (web_search)  ││
│  └─────────────┴─────────────────┴───────────────┘│
├──────────────────────────────────────────────────┤
│                  MCP 服务层                        │
│  ┌────────────────────┐ ┌──────────────────────┐ │
│  │ Sequential Thinking│ │     Context7          │ │
│  │       MCP          │ │       MCP             │ │
│  └────────────────────┘ └──────────────────────┘ │
├──────────────────────────────────────────────────┤
│              数据与持久化层                         │
│  ┌────────────┐ ┌────────────┐ ┌──────────────┐  │
│  │  schemas/  │ │  state/    │ │  memory/     │  │
│  │  (Pydantic)│ │ (TypedDict)│ │ (Checkpointer)│  │
│  └────────────┘ └────────────┘ └──────────────┘  │
└──────────────────────────────────────────────────┘
```

### 3.3 LangGraph 工作流设计

```
                    ┌──────────────┐
                    │   START      │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  initialize  │  初始化状态、加载配置
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ load_document│  通过阅读工具加载输入文档
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ generate_spec│  Supervisor 生成/修订规格文档
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  docreview   │  DocReview 执行六步审查
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
               ┌────│  evaluate    │  评估审查结果
               │    └──────┬───────┘
               │           │
               │    ┌──────▼───────┐
               │    │ is_pass ?    │
               │    └──┬───────┬───┘
               │       │No     │Yes
               │       │       │
               │  ┌────▼───┐   │
               └──┤ revise │   │  Supervisor 根据反馈修订
                  │ _spec  │   │
                  └────────┘   │
                               │
                        ┌──────▼───────┐
                        │user_approval │  等待用户确认（Human-in-the-Loop）
                        └──┬───────┬───┘
                           │       │
                    ┌──────▼──┐ ┌──▼──────────┐
                    │ execute │ │   finalize   │
                    │ (用户确认)│ │ (用户拒绝/完成)│
                    └──────┬──┘ └──────┬───────┘
                           │           │
                    ┌──────▼──┐        │
                    │finalize │        │
                    └──────┬──┘        │
                           │           │
                    ┌──────▼───────────▼───┐
                    │        END           │
                    └──────────────────────┘
```

### 3.4 审查循环终止条件

| 条件 | 描述 |
|------|------|
| **Pass** | 审查结论为 Pass，**无阻塞性问题**、**关键流程已验证**、**无未解决严重歧义**、**验收标准（AC）覆盖率完整**（四项条件必须同时满足） |
| **最大迭代次数** | 达到最大迭代轮次（默认10轮）仍未通过，强制终止并提示 |
| **问题收敛停滞** | 连续2轮审查发现相同问题列表无变化，判定为收敛停滞 |
| **用户中断** | 用户在任意迭代轮次手动中断流程 |

---

## 4. 数据模型

### 4.1 AgentState（智能体状态）

```python
from typing import TypedDict, List, Optional, Annotated
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime

class IssueStatus(TypedDict):
    issue_id: str           # 生成规则: {severity_short}-{round}-{seq}，如 BK-3-2 = Blocking/第3轮/第2个
    severity: str           # Blocking/High/Medium/Low
    issue_type: str         # CoreProcessBreak/ConsistencyCheck/...
    description: str
    suggestion: str
    location: str
    status: str             # open/fixed/partially_fixed

class IssueTracker(TypedDict):
    """跨迭代轮次的问题追踪器"""
    all_issues: List[IssueStatus]       # 所有曾发现的问题
    fixed_count: int                     # 已修复数
    partially_fixed_count: int           # 部分修复数
    unfixed_count: int                   # 未修复数
    new_in_current_round: List[str]     # 本轮新发现的问题ID

class ReviewConclusion(BaseModel):
    """结构化审查结论（Pydantic 模型，提供运行时校验），供 evaluate_result 节点直接读取
    
    字段 review_conclusion 与 AgentState.review_conclusion 命名对齐，
    model_dump() 后的 dict 存入 AgentState.review_conclusion_data。
    """
    review_conclusion: Literal["Pass", "Conditional Pass", "Fail"] = Field(alias="conclusion")
    blocking_count: int = Field(ge=0, description="阻塞性问题数量")
    high_count: int = Field(ge=0, description="高优先级问题数量")
    medium_count: int = Field(ge=0, description="中优先级问题数量")
    low_count: int = Field(ge=0, description="低优先级问题数量")
    ac_coverage_complete: bool = Field(description="验收标准覆盖率是否完整")
    
    class Config:
        populate_by_name = True  # 允许使用 "conclusion" 作为别名传入

class ReviewReport(TypedDict):
    iteration: int
    timestamp: str
    review_conclusion: str  # Pass/Conditional Pass/Fail
    review_summary: str
    issues: List[IssueStatus]
    highlights: List[str]
    open_questions: List[str]
    next_steps: str

class AgentState(TypedDict):
    # 用户输入
    user_task: str
    document_path: Optional[str]  # 可选：用户可能仅提供任务描述而不指定文档路径
    document_content: str
    # 规格文档
    specification: str
    spec_version: int            # 规格文档版本号（生命周期规则见 4.1.1 节）
    # 审查循环
    review_reports: List[ReviewReport]
    review_conclusion_data: Optional[dict]  # ReviewConclusion.model_dump() 的字典表示（LangGraph 状态存储需要可序列化类型）
    iteration_count: int
    review_conclusion: str       # "Pass"/"Conditional Pass"/"Fail"（从 review_conclusion_data 同步）
    max_iterations: int          # 最大迭代次数（默认10）
    stagnation_count: int        # 停滞计数
    stagnation_threshold: int    # 停滞检测阈值（默认2），从 config 加载
    # 执行门禁
    user_approved: bool
    awaiting_approval: bool
    approval_timed_out: bool      # 用户确认步骤是否超时（触发 DOCREVIEW_ERR_LOOP_003）
    # 执行结果
    execution_status: str        # pending/running/completed/failed
    execution_output: str
    # MCP 状态
    mcp_degraded: bool           # MCP 服务降级标志（True=降级为纯LLM模式）
    # 问题追踪
    issue_tracker: IssueTracker   # 跨迭代轮次的问题状态追踪
    spec_snapshot: str           # 上一轮审查时的规格文档快照（用于检测手动修订）
    # 错误处理
    error_code: Optional[str]    # 统一错误码（见第11.1节错误码体系）
    error_message: Optional[str]
    # API 成本追踪
    total_llm_cost: float        # 累计 LLM API 成本（美元），每次 LLM 调用后按 token 用量更新
    # 消息历史
    messages: Annotated[list, add_messages]
```

**spec_version 生命周期管理**

`spec_version` 是规格文档的单调递增版本号，用于追踪文档的修订历史和对齐审查迭代。其生命周期规则如下：

| 事件 | spec_version 行为 | 说明 |
|------|------------------|------|
| 首次规格生成（`generate_spec`） | 初始化为 1 | 由 Supervisor Agent 在 `generate_spec` 节点中设置 |
| 审查触发的修订（`revise_spec`） | 递增 +1 | Supervisor 根据审查反馈修订后递增。每个审查循环的修订只生成一个规格版本 |
| 用户手动修订重新提交（`DOCREVIEW_ERR_SYS_002`） | 递增 +1，同时重置 `iteration_count` 为 0 | 检测时机：`initialize` 节点比较 `specification` 与 `spec_snapshot`；若不同且非 `revise_spec` 节点产物，判定为手动修订 |
| Pass/最终确认后 | 保持不变 | 规格已通过，不再递增 |
| 工作流恢复（从 checkpoint 继续） | 保持不变 | 不产生新版本 |

### 4.2 IssueStatus 详细结构

| 字段 | 类型 | 说明 |
|------|------|------|
| issue_id | str | 生成规则: {severity_short}-{round}-{seq}，如 BK-3-2。详见下方 issue_id 生成机制 |
| severity | str | 严重程度：Blocking / High / Medium / Low |
| issue_type | str | 问题类型：CoreProcessBreak / ConsistencyCheck / RequirementCompleteness / TechnicalFeasibility / RiskDetection / ExecutabilityReview |
| description | str | 问题描述，引用原文或指明位置 |
| suggestion | str | 具体的、可操作的修改建议 |
| location | str | 问题位置：章节/段落/行号 |
| status | str | 修复状态：open / fixed / partially_fixed |

**issue_id 生成机制**：

格式：`{severity_short}-{round}-{seq}`，其中：
- `severity_short`：BK (Blocking) / HI (High) / MD (Medium) / LO (Low)
- `round`：当前审查轮次（从1开始），对于分段审查（Chunk Mode），各 chunk 共享同一轮次编号
- `seq`：本轮内该严重级别的序号（从1开始，按发现顺序递增）

示例：`BK-3-2` 表示第3轮审查中发现的第2个 Blocking 级别问题。

生成时机：在 `_compile_markdown_report` 中按 `all_issues` 列表顺序分配，同严重级别内按原始顺序编号。

### 4.3 审查报告输出格式

审查报告必须严格遵循以下 Markdown 结构：

```
## DocReview Review Report
**Review Conclusion**: [Pass / Conditional Pass / Fail]
**Review Summary**: 2-3句总结

### List of Issues Found
（按严重程度从高到低排序，每个问题包含以下字段）
- **Severity**: [Blocking/High/Medium/Low]
- **Issue Type**: [六种类型之一]
- **Issue Description**: 问题描述
- **Modification Suggestion**: 修改建议
- **Relevant Location**: 位置信息

### Highlights (Optional)
文档中值得肯定的方面

### Open Questions (If Any)
需要人工决策或补充的信息

### Next Steps
关键修正项及是否需要重新审查
```

---

## 5. 模块设计

### 5.1 阅读模块（tools/reading.py）

**类名**: `ReadingTool`

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `read_file` | `path: str, encoding: str = "utf-8"` | `str` | 读取文件全部内容 |
| `search_text` | `content: str, pattern: str` | `List[Match]` | 正则搜索文本 |
| `list_directory` | `path: str, pattern: str = "*"` | `List[str]` | 列出目录内容 |
| `compare_versions` | `v1: str, v2: str` | `str` | 生成两个版本文档的 unified diff |

**安全约束**：
- `read_file` 必须验证路径在工作目录范围内
- 禁止读取系统敏感目录（如 `/etc/passwd`）
- 文件大小限制（默认最大 10MB）

### 5.2 终端模块（tools/terminal.py）

**类名**: `TerminalTool`

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `execute` | `command: str, timeout: int = 300, cwd: str = None` | `CommandResult` | 执行终端命令 |
| `get_last_output` | — | `CommandResult` | 获取最近一次命令执行结果 |

**CommandResult 结构**：
```python
class CommandResult:
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    timed_out: bool
```

**安全约束**：
- 命令白名单机制（默认仅允许安全命令）
- 禁止执行 `rm -rf /` 等危险操作
- 使用 `subprocess.Popen` 而非 `os.system`
- 默认超时 300 秒

### 5.3 联网搜索模块（tools/web_search.py）

**类名**: `WebSearchTool`

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `search` | `query: str, num_results: int = 5` | `List[SearchResult]` | 执行网页搜索 |
| `fetch_url` | `url: str` | `str` | 获取网页内容（Markdown格式） |
| `validate_api` | `api_spec: str` | `ApiValidationResult` | 验证API可用性 |

**SearchResult 结构**：
```python
class SearchResult:
    title: str
    url: str
    snippet: str
```

**约束**：
- 使用公共搜索引擎 API 或内置 `WebSearch` 机制
- URL 获取有超时限制（默认 30 秒）
- 尊重 robots.txt

### 5.4 MCP 客户端

**部署方式说明**：v1.0 中 MCP 服务采用本地进程模式运行。Sequential Thinking MCP Server 和 Context7 MCP Server 通过各自官方发布的 Python/Node.js 包在本地启动，与 DocReview 系统通过标准 MCP JSON-RPC 协议通信。无需独立 Docker 容器或远程服务。启动命令见各客户端初始化代码。

**MCP 进程生命周期管理**：

| 阶段 | 负责组件 | 行为 |
|------|----------|------|
| 启动 | `initialize` 节点 | 通过 `subprocess.Popen` 启动 MCP Server 进程（Sequential Thinking 使用 `npx @modelcontextprotocol/server-sequential-thinking`，Context7 使用对应 Node.js 包）。启动后执行健康检查（发送 ping 请求，超时 5s），失败时标记 `mcp_degraded=True` 并设置 `DOCREVIEW_ERR_MCP_001` |
| 运行中崩溃检测 | 各 `_think_step` / `_enrich_context` 调用点 | 每次 MCP 调用失败时捕获异常，重试 3 次（指数退避 1s/2s/4s），仍失败则标记 `mcp_degraded=True` 并触发 `DOCREVIEW_ERR_MCP_001`，后续步骤回退为纯 LLM 推理。**额外保护**：每次 MCP 调用设置 `mcp_call_timeout` 秒超时（默认 30s），防止进程僵死导致永久阻塞 |
| 运行中恢复检测 | `evaluate_result` 节点 | 若 `mcp_degraded == True`，在每轮审查结束后尝试对 MCP Server 发送健康检查 ping。若恢复成功，标记 `mcp_degraded=False` 并触发 `DOCREVIEW_ERR_MCP_002` |
| 关闭 | `finalize` 节点 | 对所有已启动的 MCP 子进程发送 SIGTERM，等待 5s 后若未退出则 SIGKILL。清理临时文件 |

#### 5.4.1 Sequential Thinking MCP 客户端（mcp/sequential_thinking.py）

```python
class SequentialThinkingClient:
    """封装 Sequential Thinking MCP Server 的调用"""
    
    async def think(
        self, 
        thought: str, 
        thought_number: int,
        total_thoughts: int,
        next_thought_needed: bool = True
    ) -> ThinkingResult:
        """提交一个思维步骤"""
    
    async def get_chain(self) -> List[ThinkingStep]:
        """获取完整的思维链"""
    
    async def revise_thought(
        self,
        thought_number: int,
        new_thought: str
    ) -> ThinkingResult:
        """修订之前的思维步骤"""
```

#### 5.4.2 Context7 MCP 客户端（mcp/context7.py）

```python
class Context7Client:
    """封装 Context7 MCP Server 的调用"""
    
    async def resolve_library_id(self, name: str) -> str:
        """解析库标识符"""
    
    async def query_docs(self, query: str, library_id: str = None) -> List[DocResult]:
        """查询文档"""
    
    async def get_context(self, topic: str) -> ContextResult:
        """获取上下文信息"""
```

### 5.5 Supervisor Agent（agents/supervisor.py）

**类名**: `SupervisorAgent`

核心职责：
1. **planner**：将用户任务分解为可执行计划
2. **spec_generator**：基于计划和用户输入生成结构化规格文档
3. **revision_engine**：根据审查反馈修订规格文档
4. **execution_controller**：管理执行门禁和实际任务执行

```python
class SupervisorAgent:
    def __init__(self, llm: BaseChatModel, tools: List[BaseTool]):
        self.llm = llm
        self.tools = tools
        self.spec_generator_prompt = ChatPromptTemplate.from_messages([
            ("system", SPEC_GENERATOR_SYSTEM_PROMPT),
            ("human", "{user_task}"),
        ])
        self.revision_prompt = ChatPromptTemplate.from_messages([
            ("system", REVISION_SYSTEM_PROMPT),
            ("human", "原始规格:\n{specification}\n\n审查报告:\n{review_report}\n\n请修订规格文档。"),
        ])
    
    async def generate_spec(self, state: AgentState) -> AgentState:
        """生成或转换规格文档（三种输入场景见 6.2 节 generate_spec 节点定义）"""
        ...

    async def convert_to_spec(self, document: str, task_context: str = "") -> str:
        """将外部文档转换为标准规格格式（场景②③），保留原始文档实质内容"""
        ...

    async def generate_spec_from_task(self, task: str) -> str:
        """根据任务描述从零生成规格文档（场景①）"""
        ...

    async def revise_spec(self, state: AgentState) -> AgentState: ...
    async def execute_task(self, state: AgentState) -> AgentState: ...
```

### 5.6 DocReview Sub-Agent（agents/docreview.py）

**类名**: `DocReviewAgent`

核心职责：执行严格的六步审查流程，输出结构化审查报告和 Pydantic 审查结论对象（双重输出）。

**SPAC 子组件与六步方法的映射关系**：

| SPAC 子组件 (SPAC_prompt.md §Required Agent Architecture) | 对应方法 | 说明 |
|-----------------------------------------------------------|----------|------|
| Sequential Thinking | `_think_step()` | 每个审查步骤通过 Sequential Thinking MCP 进行多步推理 |
| Consistency Analyzer | `_check_consistency()` | 步骤2：一致性检查 |
| Risk Detector | `_detect_risks()` | 步骤5：风险检测与回退推导 |
| Feasibility Reviewer | `_deduce_feasibility()` | 步骤4：技术可行性推导 |
| Executability Validator | `_review_executability()` | 步骤6：可执行性审查 |
| Context7 Context Engine | `_enrich_context()` | 贯穿全流程，通过 Context7 获取技术和框架上下文 |

> **注**：SPAC_prompt.md 为系统架构设计参考文档，非运行时依赖。其缺失不影响系统运行，仅影响开发者对架构的理解。运行时核心提示词为 PROMPT.md 和 WHENTOCALL.md（两者缺失时触发 DOCREVIEW_ERR_SYS_003）。

**六步审查的数据流**：

```
步骤1: _extract_core_loop(spec)
  → 输出 CoreLoopAnalysis { flows: [...], breaks: [...] }
  → 传递给步骤2-6作为上下文

步骤2: _check_consistency(spec, core_loop)
  → 输出 List[Issue]（一致性类问题）
  → 传递给汇总阶段

步骤3: _atomize_requirements(spec, core_loop)
  → 输出 List[Issue]（需求原子化问题）+ List[AtomicRequirement]
  → AtomicRequirements 传递给步骤4进行可行性验证

步骤4: _deduce_feasibility(spec, atomic_reqs)
  → 输出 List[Issue]（技术可行性问题）
  → 依赖关系图传递给步骤5进行风险评估

步骤5: _detect_risks(spec, dependency_graph, prev_issues)
  → 输出 List[Issue]（风险检测问题，含严重程度分类）
  → 风险列表传递给步骤6进行可执行性确认

步骤6: _review_executability(spec, all_issues, risks)
  → 输出 List[Issue]（可执行性问题）

汇总: _compile_report(all_issues)
  → Markdown 审查报告（保存到 review_reports）
  → ReviewConclusion Pydantic 对象（保存到 review_conclusion_data，供路由使用）
```

```python
class DocReviewAgent:
    # 提示词路径常量（可从 config 覆盖）
    PROMPT_PATH: str = "prompts/PROMPT.md"
    WHENTOCALL_PATH: str = "prompts/WHENTOCALL.md"

    def __init__(
        self,
        llm: BaseChatModel,
        sequential_thinking: SequentialThinkingClient,
        context7: Context7Client,
        tools: List[BaseTool]
    ):
        self.llm = llm
        self.sequential_thinking = sequential_thinking
        self.context7 = context7
        self.tools = tools
        # 加载核心提示词和触发条件定义（委托给 src/utils/prompt_loader.py）；若文件不存在则抛出 DOCREVIEW_ERR_SYS_003
        self.review_prompt = self._load_prompt(DocReviewAgent.PROMPT_PATH)
        self.whentocall_prompt = self._load_prompt(DocReviewAgent.WHENTOCALL_PATH)

    @staticmethod
    def _load_prompt(path: str) -> str:
        """加载提示词文件内容

        Args:
            path: 提示词文件路径（相对于项目根目录）

        Returns:
            文件内容字符串

        Raises:
            FileNotFoundError: 文件缺失时抛出，由调用方捕获后转换为
                DOCREVIEW_ERR_SYS_003 并输出 "核心提示词文件缺失: {path}"
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"核心提示词文件缺失: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    async def review(self, state: AgentState) -> AgentState:
        """执行完整的六步审查流程，返回包含 review_conclusion_data 的更新状态"""
        spec = state["specification"]

        # 步骤1：核心闭环提取（使用 Sequential Thinking）
        core_loop = await self._extract_core_loop(spec)

        # 步骤2：一致性检查
        consistency_issues = await self._check_consistency(spec, core_loop)

        # 通过 Context7 获取技术上下文（贯穿全流程）
        tech_context = await self._enrich_context(spec)

        # 步骤3：需求原子化
        atomize_issues, atomic_reqs = await self._atomize_requirements(spec, core_loop)

        # 步骤4：技术可行性推导
        feasibility_issues, dep_graph = await self._deduce_feasibility(spec, atomic_reqs, tech_context)

        # 步骤5：风险检测
        risk_issues = await self._detect_risks(spec, dep_graph, consistency_issues + atomize_issues + feasibility_issues)

        # 步骤6：可执行性审查
        exec_issues = await self._review_executability(spec, consistency_issues + atomize_issues + feasibility_issues + risk_issues)

        # 汇总生成双重输出
        all_issues = consistency_issues + atomize_issues + feasibility_issues + risk_issues + exec_issues
        state["review_reports"].append(self._compile_markdown_report(all_issues, state["iteration_count"]))
        state["review_conclusion_data"] = self._compile_structured_conclusion(all_issues, spec).model_dump(by_alias=False)
        state["review_conclusion"] = state["review_conclusion_data"]["review_conclusion"]
        state["iteration_count"] += 1
        return state

    async def _think_step(self, step_name: str, context: str) -> List[ThinkingStep]:
        """通过 Sequential Thinking MCP 对指定审查步骤进行多步推理

        每个审查步骤调用此方法执行 2-3 次迭代思维链，输出结构化推理结果。
        若 MCP 服务不可用（mcp_degraded=True），回退为纯 LLM 单次推理。
        """
        ...

    async def _enrich_context(self, spec: str) -> TechContext:
        """通过 Context7 MCP 获取相关技术和框架文档上下文"""
        ...

    async def _extract_core_loop(self, spec: str) -> CoreLoopAnalysis:
        """步骤1：通过 Sequential Thinking MCP 进行多步推理，提取核心闭环"""
        thinking_chain = await self._think_step("core_loop_extraction", spec)
        ...

    async def _check_consistency(self, spec: str, core_loop: CoreLoopAnalysis) -> List[Issue]:
        """步骤2：通过 Sequential Thinking MCP 执行一致性检查"""
        thinking_chain = await self._think_step("consistency_check", f"{spec}\n\nCoreLoop: {core_loop}")
        ...

    async def _atomize_requirements(self, spec: str, core_loop: CoreLoopAnalysis) -> Tuple[List[Issue], List[AtomicRequirement]]:
        """步骤3：通过 Sequential Thinking MCP 执行需求原子化与完整性检查"""
        thinking_chain = await self._think_step("requirement_atomization", f"{spec}\n\nCoreLoop: {core_loop}")
        ...

    async def _deduce_feasibility(self, spec: str, atomic_reqs: List[AtomicRequirement], context: TechContext) -> Tuple[List[Issue], DependencyGraph]:
        """步骤4：通过 Sequential Thinking MCP 执行技术可行性推导"""
        thinking_chain = await self._think_step("feasibility_deduction", f"{spec}\n\nAtomicReqs: {atomic_reqs}\n\nContext: {context}")
        ...

    async def _detect_risks(self, spec: str, dep_graph: DependencyGraph, prev_issues: List[Issue]) -> List[Issue]:
        """步骤5：通过 Sequential Thinking MCP 执行风险检测与回退推导"""
        thinking_chain = await self._think_step("risk_detection", f"{spec}\n\nDepGraph: {dep_graph}\n\nPrevIssues: {prev_issues}")
        ...

    async def _review_executability(self, spec: str, all_issues: List[Issue]) -> List[Issue]:
        """步骤6：通过 Sequential Thinking MCP 执行可执行性审查"""
        thinking_chain = await self._think_step("executability_review", f"{spec}\n\nAllIssues: {all_issues}")
        ...

    def _compile_markdown_report(self, issues: List[Issue], iteration: int) -> ReviewReport:
        """生成符合 PROMPT.md 格式的 Markdown 审查报告"""
        ...

    def _compile_structured_conclusion(self, issues: List[Issue], spec: str) -> ReviewConclusion:
        """生成结构化 Pydantic 审查结论对象，供 evaluate_result 路由使用
        
        判定规则（严格优先级，由上至下匹配）：
        1. 存在 Blocking 问题 ≥ 1 → Fail
        2. AC 覆盖率不完整（P0 FR 未全部被 AC 覆盖）→ Fail
        3. 无 Blocking 但存在 High 问题 ≥ 1 → Conditional Pass
        4. 仅有 Medium/Low 问题或零问题 → Pass

        AC 覆盖率判定算法：
        P0_FRs = {fr_id for fr in spec_requirements if fr.priority == "P0"}
        Covered_FRs = {fr_id for ac in spec_acceptance_criteria for fr_id in ac.covered_frs}
        ac_coverage_complete = P0_FRs ⊆ Covered_FRs
        """
        ...
```
### 5.7 LLM API 成本追踪机制

**设计目标**：在每个 LLM 调用点自动累加 Token 成本，当累计成本超过 `llm_max_cost_per_task` 预算上限时触发 `DOCREVIEW_ERR_LLM_008` 终止任务。

**定价表**（内置于 `src/utils/llm.py`，按 `config.llm_model` 匹配）：

| 模型 | prompt_tokens 单价 ($/1M) | completion_tokens 单价 ($/1M) | 备注 |
|------|--------------------------|------------------------------|------|
| `gpt-4o` | 2.50 | 10.00 | 默认模型 |
| `gpt-4o-mini` | 0.15 | 0.60 | 低成本备选 |
| `gpt-4-turbo` | 10.00 | 30.00 | 高精度场景 |
| `claude-3-5-sonnet` | 3.00 | 15.00 | Anthropic 备选 |
| `claude-3-opus` | 15.00 | 75.00 | 最高精度 |

**成本累加方法** — 在 LLM 调用封装层 `src/utils/llm.py` 中实现：

```python
# 定价表（内置常量，可被 config 覆盖）
LLM_PRICING = {
    "gpt-4o":         (2.50, 10.00),
    "gpt-4o-mini":    (0.15, 0.60),
    "gpt-4-turbo":    (10.00, 30.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-opus":  (15.00, 75.00),
}

def _track_llm_cost(state: AgentState, response_metadata: dict, model: str) -> None:
    """从 LangChain AIMessage.response_metadata 提取 token_usage 并累加成本

    调用时机：每次 LLM invoke 返回后立即调用（在 LLM wrapper 的 ainvoke 封装中）
    
    提取路径（按提供商差异）：
    - OpenAI: response_metadata["token_usage"]["prompt_tokens"] / ["completion_tokens"]
    - Anthropic: response_metadata["usage"]["input_tokens"] / ["output_tokens"]

    若 response_metadata 中无 token_usage 字段（如流式响应），
    则按估算公式：estimated_cost = (chars / 4) * prompt_price_per_1M / 1e6
    """
    prompt_price, completion_price = LLM_PRICING.get(model, (2.50, 10.00))
    # ... token 提取与累加逻辑
    state["total_llm_cost"] += cost
    if state["total_llm_cost"] > config.llm_max_cost_per_task:
        state["error_code"] = "DOCREVIEW_ERR_LLM_008"
```

**成本检查门禁** — 在 `evaluate_result` 节点尾部调用：

```python
# evaluate_result 节点尾部追加
if state["total_llm_cost"] > config.llm_max_cost_per_task:
    state["error_code"] = "DOCREVIEW_ERR_LLM_008"
    state["error_message"] = (
        f"LLM API 成本超预算: ${state['total_llm_cost']:.4f} > "
        f"${config.llm_max_cost_per_task:.2f}"
    )
```

---

## 6. 工作流详细定义

### 6.1 工作流节点函数

| 节点名称 | 函数签名 | 输入 | 输出 | 说明 |
|----------|----------|------|------|------|
| `initialize` | `(state) -> state` | 原始状态 | 初始化后状态 | 加载配置、创建目录、设置默认值 |
| `route_after_initialize` | `(state) -> str` | 初始化后状态 | 下一节点名称 | 若 `document_path` 非空则路由到 `load_document`，否则路由到 `generate_spec` |
| `load_document` | `(state) -> state` | 含文档路径的状态 | 含文档内容的状态 | 通过阅读工具加载文档 |
| `generate_spec` | `(state) -> state` | 含用户任务的状态 | 含规格文档的状态 | Supervisor 调用 LLM 生成/转换规格。三种输入场景：① 仅有 `--task` → 从零生成规格文档；② 仅有 `--doc-path` → 将 `document_content` 转换为标准规格格式；③ 两者共存 → 以 `document_content` 为主体生成规格，`user_task` 作为上下文补充（追加到规格文档末尾"## 审查重点标注"章节，不影响原始文档结构）。**注意**：转换规格时不改变原始文档实质内容，仅做结构格式化 |
| `docreview` | `(state) -> state` | 含规格文档的状态 | 含审查报告的状态 | DocReview 执行六步审查 |
| `evaluate_result` | `(state) -> state` | 含审查报告的状态 | 更新审查结论的状态 | 读取 `review_conclusion_data` 结构化对象判断 Pass/Fail |
| `revise_spec` | `(state) -> state` | 含审查报告的状态 | 含修订后规格的状态 | Supervisor 根据反馈修订 |
| `user_approval` | `(state) -> state` | 审查通过的状态 | 含用户决策的状态 | LangGraph interrupt 等待用户 |
| `execute` | `(state) -> state` | 用户确认的状态 | 含执行结果的状态 | 执行实际开发任务 |
| `finalize` | `(state) -> state` | 任意终态 | 最终状态 | 保存结果、清理资源 |

### 6.2 关键节点函数逻辑

#### `initialize`
```python
async def initialize(state: AgentState) -> AgentState:
    """初始化工作流状态：加载配置、创建必要目录、设置默认值"""
    config = AppConfig()
    state["max_iterations"] = state.get("max_iterations") or config.max_review_iterations
    state["stagnation_count"] = 0
    state["stagnation_threshold"] = config.stagnation_threshold
    state["iteration_count"] = 0
    state["review_reports"] = []
    state["review_conclusion"] = "pending"
    state["review_conclusion_data"] = None
    state["user_approved"] = False
    state["awaiting_approval"] = False
    state["approval_timed_out"] = False
    state["execution_status"] = "pending"
    state["error_code"] = None
    state["error_message"] = None
    state["mcp_degraded"] = False
    state["issue_tracker"] = {"all_issues": [], "fixed_count": 0, "partially_fixed_count": 0, "unfixed_count": 0, "new_in_current_round": []}
    state["spec_snapshot"] = ""
    state["total_llm_cost"] = 0.0
    # 确保 data/ 目录存在（checkpoint 依赖）
    os.makedirs("data", exist_ok=True)
    # MCP 运行时前置校验：Node.js 缺失时记录日志但允许继续（将自动降级为纯 LLM 模式）
    try:
        subprocess.run(["npx", "--version"], capture_output=True, check=True, timeout=10)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("Node.js/npx 未安装或不可用，MCP 服务将被禁用。请安装 Node.js >= 18 LTS")
        state["mcp_degraded"] = True
    return state
```

#### `route_after_initialize`
```python
def route_after_initialize(state: AgentState) -> str:
    """若 document_path 非空则加载文档，否则直接生成规格"""
    if state.get("document_path"):
        return "load_document"
    return "generate_spec"
```

#### `generate_spec`
```python
async def generate_spec(state: AgentState) -> AgentState:
    """Supervisor 生成或转换规格文档。依据 6.1 节定义处理三种输入场景"""
    # 加载 Supervisor Agent（通过闭包或注入获取，此处为伪代码）
    supervisor = get_supervisor_agent()

    if state.get("document_content"):
        # 场景②③：有文档输入，转换为规格格式
        specification = await supervisor.convert_to_spec(
            document=state["document_content"],
            task_context=state.get("user_task", "")
        )
    else:
        # 场景①：仅有任务描述，从零生成
        specification = await supervisor.generate_spec_from_task(
            task=state["user_task"]
        )

    state["specification"] = specification
    state["spec_version"] = 1  # 首次生成时初始化为 1（见 spec_version 生命周期表）
    state["spec_snapshot"] = specification  # 初始化快照
    return state
```

#### `evaluate_result`
```python
async def evaluate_result(state: AgentState) -> AgentState:
    """读取 review_conclusion_data 判定审查结果，更新 iteration_count"""
    data = state["review_conclusion_data"]
    conclusion = data["review_conclusion"]  # 来自 ReviewConclusion.model_dump()，与 AgentState.review_conclusion 对齐
    state["review_conclusion"] = conclusion
    
    # 检测停滞：本轮问题列表与上轮完全一致
    if _is_stagnant(state):
        state["stagnation_count"] += 1
    else:
        state["stagnation_count"] = 0
    
    # 保存规格快照用于手动修订检测
    state["spec_snapshot"] = state["specification"]

    # Token 累积管理：对 3 轮前的审查历史执行摘要压缩
    _prune_review_history(state)
    return state

def _is_stagnant(state: AgentState) -> bool:
    """检测审查问题列表是否停滞（连续两轮无变化）"""
    if len(state["review_reports"]) < 2:
        return False
    this_issues = {i["issue_id"] for i in state["review_reports"][-1]["issues"]}
    prev_issues = {i["issue_id"] for i in state["review_reports"][-2]["issues"]}
    return this_issues == prev_issues

def _prune_review_history(state: AgentState) -> None:
    """Token 累积管理：对 3 轮前的审查报告和规格快照执行摘要压缩

    保留策略（定义见 12.4 节）：
    - 最近 2 轮：完整保留 review_reports 全文
    - 第 3 轮及更早：替换为单行摘要
      "{review_conclusion} | {blocking_count}B/{high_count}H/{medium_count}M/{low_count}L"
    """
    if len(state["review_reports"]) <= 2:
        return
    for i in range(len(state["review_reports"]) - 2):
        r = state["review_reports"][i]
        blk = sum(1 for j in r["issues"] if j["severity"] == "Blocking")
        hi  = sum(1 for j in r["issues"] if j["severity"] == "High")
        md  = sum(1 for j in r["issues"] if j["severity"] == "Medium")
        lo  = sum(1 for j in r["issues"] if j["severity"] == "Low")
        r["issues"] = []  # 清空详细问题列表
        r["review_summary"] = f"{r['review_conclusion']} | {blk}B/{hi}H/{md}M/{lo}L"

def _save_review_history(state: AgentState) -> None:
    """序列化审查历史到磁盘
    
    输出路径：reviews/history-{thread_id}.json
    序列化内容：review_reports 列表（已压缩轮次仅含摘要）、review_conclusion、
               spec_version 和 total_llm_cost
    异常处理：序列化失败时记录错误日志但不阻塞 finalize 流程
    """
    thread_id = config["configurable"]["thread_id"]
    output = {
        "thread_id": thread_id,
        "spec_version": state["spec_version"],
        "review_conclusion": state["review_conclusion"],
        "total_llm_cost": state["total_llm_cost"],
        "reports": state["review_reports"]
    }
    os.makedirs("reviews", exist_ok=True)
    with open(f"reviews/history-{thread_id}.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
```

#### `route_after_evaluate`
```python
def route_after_evaluate(state: AgentState) -> str:
    """根据审查结论和迭代状态路由"""
    conclusion = state["review_conclusion"]
    # 迭代次数耗尽：强制终止
    if state["iteration_count"] >= state["max_iterations"]:
        return "finalize"
    # 停滞检测：连续N轮无变化
    if state["stagnation_count"] >= state["stagnation_threshold"]:
        return "finalize"
    # Pass 或 Conditional Pass：用户决策
    if conclusion in ("Pass", "Conditional Pass"):
        state["awaiting_approval"] = True
        return "user_approval"
    # Fail：继续修订循环
    if conclusion == "Fail":
        return "revise_spec"
    return "finalize"
```

#### `user_approval`
```python
def user_approval(state: AgentState) -> AgentState:
    """LangGraph interrupt 节点：工作流在此暂停等待用户输入
    
    此节点通过 LangGraph 的 interrupt_before 机制在工作流编译时标记。
    用户通过 CLI 交互或 resume API 提供决策后，工作流从此节点恢复。
    
    user_approved 字段由外部输入（resume 时的 Command）注入：
    - user_approved=True: 执行
    - user_approved=False: 按结论类型决定修订或终止
    
    超时保护实现方案（CLI 层配合）：
    1. CLI 入口以 `asyncio.wait_for()` 包装 `graph.ainvoke()` 调用
    2. 超时后调用 `await graph.aupdate_state(config, {"approval_timed_out": True})` 更新状态
       若 `aupdate_state` 失败（如 thread_id 无效），记录错误日志并设置 CLI 退出码 2
    3. 调用 `asyncio.create_task(graph.ainvoke(None, config))` 恢复工作流执行
    4. 工作流恢复后由 `route_after_approval` 检测 `approval_timed_out` 并路由到 `finalize`
    """
    return state
```

#### `route_after_approval`
```python
def route_after_approval(state: AgentState) -> str:
    """用户确认后的条件路由"""
    if state["approval_timed_out"]:
        state["error_code"] = "DOCREVIEW_ERR_LOOP_003"
        return "finalize"
    if state["user_approved"]:
        return "execute"
    if state["review_conclusion"] == "Conditional Pass":
        return "revise_spec"
    return "finalize"
```

#### `finalize`
```python
async def finalize(state: AgentState) -> AgentState:
    """保存最终状态、输出审查摘要、清理资源"""
    if state["review_reports"]:
        _save_review_history(state)  # 将 review_reports 序列化为 JSON: reviews/history-{thread_id}.json
    state["execution_status"] = "completed"
    _print_summary(state)  # 输出迭代次数、问题总数、各严重级别分布到 stdout
    return state
```

### 6.3 条件边定义

> **注意**：以下条件值区分大小写，须与 `ReviewConclusion` 模型 `Literal["Pass", "Conditional Pass", "Fail"]` 定义严格一致。

| 边名称 | 源节点 | 目标节点 | 条件 |
|--------|--------|----------|------|
| `route_after_evaluate` | `evaluate_result` | `user_approval` | `review_conclusion == "Pass"` |
| | | `user_approval` | `review_conclusion == "Conditional Pass"`（用户决定接受条件执行或继续修订） |
| | | `revise_spec` | `review_conclusion == "Fail"` AND `iteration_count < max_iterations` |
| | | `finalize` | `iteration_count >= max_iterations`（迭代次数耗尽终止） |
| `route_after_approval` | `user_approval` | `execute` | `user_approved == True` |
| | | `revise_spec` | `user_approved == False` AND `review_conclusion == "Conditional Pass"`（用户拒绝条件通过，要求继续修订） |
| | | `finalize` | `user_approved == False` AND `review_conclusion == "Pass"`（用户拒绝纯Pass的执行） |
| | | `finalize` | `approval_timed_out == True`（用户确认超时，触发 DOCREVIEW_ERR_LOOP_003） |

**Conditional Pass 路由说明**：Conditional Pass（有条件通过）表示无阻塞性问题但存在需用户关注的中高优先级问题。此时路由到 `user_approval` 节点，向用户展示条件详情（问题列表），由用户选择：(1) 接受条件直接执行 (`user_approved=True`)；(2) 要求继续修订 (`user_approved=False`)。

### 6.4 完整工作流伪代码

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

# 实例化核心组件（确保伪代码自包含）
config = AppConfig()                                        # 加载配置
llm = create_llm(config)                                    # 根据配置创建 LLM 实例
reading_tool = ReadingTool(config.workspace_dir)
terminal_tool = TerminalTool()
web_search_tool = WebSearchTool()
seq_thinking = SequentialThinkingClient()
context7 = Context7Client()

supervisor = SupervisorAgent(llm, [reading_tool, terminal_tool, web_search_tool])
docreview_agent = DocReviewAgent(llm, seq_thinking, context7, [reading_tool, web_search_tool])

def build_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)
    
    # 添加节点
    workflow.add_node("initialize", initialize)
    workflow.add_node("load_document", load_document)
    workflow.add_node("generate_spec", supervisor.generate_spec)
    workflow.add_node("docreview", docreview_agent.review)
    workflow.add_node("evaluate_result", evaluate_result)
    workflow.add_node("revise_spec", supervisor.revise_spec)
    workflow.add_node("user_approval", user_approval)
    workflow.add_node("execute", supervisor.execute_task)
    workflow.add_node("finalize", finalize)
    
    # 添加边
    workflow.set_entry_point("initialize")
    
    # initialize 后的条件路由：document_path 非空则加载文档，否则直接进入规格生成
    workflow.add_conditional_edges("initialize", route_after_initialize, {
        "load_document": "load_document",
        "generate_spec": "generate_spec"
    })
    workflow.add_edge("load_document", "generate_spec")
    workflow.add_edge("generate_spec", "docreview")
    workflow.add_edge("docreview", "evaluate_result")
    workflow.add_edge("revise_spec", "docreview")
    workflow.add_edge("execute", "finalize")
    
    # 添加条件边
    workflow.add_conditional_edges("evaluate_result", route_after_evaluate, {
        "user_approval": "user_approval",
        "revise_spec": "revise_spec",
        "finalize": "finalize"
    })
    workflow.add_conditional_edges("user_approval", route_after_approval, {
        "execute": "execute",
        "revise_spec": "revise_spec",
        "finalize": "finalize"
    })
    
    workflow.add_edge("finalize", END)
    
    # 使用 SqliteSaver 实现持久化状态存储（与 3.1 技术栈一致）
    # 注意：LangGraph >= 0.3 需使用 AsyncSqliteSaver；v1.0 目标 LangGraph 0.2.x 使用同步版本
    checkpointer = SqliteSaver.from_conn_string("data/checkpoints.db")
    return workflow.compile(
        checkpointer=checkpointer, 
        interrupt_before=["user_approval"]
    )
```

---

## 7. CLI 接口规格

### 7.1 命令结构

主命令：`docreview`

```
docreview [COMMAND] [OPTIONS]
```

### 7.2 子命令

| 子命令 | 说明 | 必需参数 |
|--------|------|----------|
| `review` | 审查指定文档 | `--doc-path PATH` |
| `generate-spec` | 根据任务描述生成规格文档 | `--task TEXT` |
| `status` | 查看审查历史与状态 | `--thread-id ID`（可选） |
| `resume` | 恢复中断的审查工作流 | `--thread-id ID` |

### 7.3 通用选项

| 选项 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--doc-path` | PATH | — | 待审查文档路径（与 `--task` 二选一或共存） |
| `--task` | TEXT | — | 用户任务描述。与 `--doc-path` 共存时，`--doc-path` 为审查目标，`--task` 作为补充上下文指导审查重点 |
| `--max-iterations` | INT | 10 | 最大审查迭代次数 |
| `--output-dir` | PATH | `./reviews/` | 审查报告输出目录 |
| `--spec-output` | PATH | `./docs/specification.md` | 规格文档输出路径 |
| `--verbose` / `-v` | FLAG | false | 启用详细日志输出 |
| `--no-mcp` | FLAG | false | 禁用 MCP 服务（降级为纯 LLM 模式） |
| `--model` | TEXT | `gpt-4o` | 覆盖默认 LLM 模型 |
| `--config` | PATH | `.env` | 配置文件路径 |

### 7.4 使用示例

```bash
# 审查单个 PRD 文档
docreview review --doc-path ./docs/prd.md --max-iterations 5

# 根据任务描述生成规格并审查
docreview review --task "设计一个用户认证系统" --verbose

# 审查技术方案并自定义输出目录
docreview review --doc-path ./docs/tech-solution.md --output-dir ./output/reviews/

# 查看特定审查线程的状态
docreview status --thread-id review-20260520-001

# 恢复中断的审查
docreview resume --thread-id review-20260520-001

# 降级模式（MCP 服务不可用时）
docreview review --doc-path ./docs/spec.md --no-mcp
```

### 7.5 CLI 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 审查通过 (Pass)，执行成功 |
| 1 | 审查未通过 (Fail/Conditional Pass) |
| 2 | 系统错误（LLM 不可用、配置错误等） |
| 3 | 用户中断或拒绝执行 |
| 4 | 参数无效或文档不存在 |

---

## 8. 配置管理

### 8.1 环境变量（.env）

```ini
# LLM 配置
LLM_PROVIDER=openai               # openai / anthropic / azure
LLM_MODEL=gpt-4o                  # 模型名称
LLM_API_KEY=sk-xxxxxxxx           # API 密钥
LLM_BASE_URL=                     # 自定义 API 端点（可选）
LLM_TEMPERATURE=0.0               # 温度参数（审查场景建议0）
LLM_REQUEST_TIMEOUT=120            # LLM API 单次请求超时秒数（默认120s）

# MCP 服务配置
MCP_SEQUENTIAL_THINKING_ENABLED=true
MCP_CONTEXT7_ENABLED=true
MCP_CALL_TIMEOUT=30                # MCP 单次调用超时秒数（防进程僵死，默认30s）

# 审查配置
MAX_REVIEW_ITERATIONS=10          # 最大审查迭代次数
STAGNATION_THRESHOLD=2             # 停滞检测阈值
USER_APPROVAL_TIMEOUT=86400        # 用户确认超时秒数（默认24小时，0=无超时）
LLM_MAX_COST_PER_TASK=5.0         # 单次任务 LLM API 成本预算上限（美元，0=无限制）

# 工作目录
WORKSPACE_DIR=./                   # 工作目录路径

# 日志配置
LOG_LEVEL=INFO
LOG_FILE=./logs/app.log
```

### 8.2 配置文件结构（config.py）

```python
from pydantic_settings import BaseSettings

class AppConfig(BaseSettings):
    # LLM
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_temperature: float = 0.0
    llm_request_timeout: int = 120  # LLM API 单次请求超时秒数
    
    # MCP
    mcp_sequential_thinking_enabled: bool = True
    mcp_context7_enabled: bool = True
    mcp_call_timeout: int = 30       # MCP 单次调用超时秒数（防进程僵死）
    
    # 审查
    max_review_iterations: int = 10
    stagnation_threshold: int = 2
    user_approval_timeout: int = 86400  # 用户确认超时秒数（默认24小时，0=无超时）
    llm_max_cost_per_task: float = 5.0  # 单次任务 LLM API 成本预算上限（美元，0=无限制）
    
    # 工作目录
    workspace_dir: str = "./"
    
    model_config = ConfigDict(env_file=".env")
```

---

## 9. 性能指标

所有延迟指标以 LLM API 单次调用响应时间 ≤ 15 秒为前提条件，若 LLM 响应时间超过此值，系统总延迟按比例增加。

| 指标 | 目标值 | 测量条件 |
|------|--------|----------|
| 规格文档生成延迟（系统自身） | < 5 秒 | 不含 LLM 推理时间，纯系统处理开销 |
| 规格文档生成延迟（端到端） | < 30 秒 | 含 LLM 推理，前提 LLM 响应 ≤ 15s |
| 单次审查循环延迟（端到端） | < 200 秒 | 含 LLM 推理和 MCP 调用。基于每步 2 次 Sequential Thinking 迭代 * 6 步 * 15s + 汇总 20s 建模。实际延迟取决于模型和网络。 |
| 审查迭代收敛轮次 | 平均 3-5 轮，最大 10 轮 | 历史统计，基于多次运行中位数 |
| 文件读取延迟 | < 100ms | 1MB 以内文件，本地 SSD |
| 网页搜索延迟 | < 5s | 取决于网络和搜索引擎响应 |
| 命令执行延迟 | 取决于命令本身 | 系统无额外开销 |
| 内存占用 | < 500MB | 不含 LLM 推理进程，峰值运行状态 |
| 磁盘占用 | < 100MB | 含 SQLite + 审查历史，不含日志滚动 |
| 最大支持文档大小 | 100KB | 单次审查输入。超过此值启用分段审查（Chunk Mode） |

---

## 10. 验收标准

| 编号 | 验收条件 | 验证方式 | 覆盖 FR |
|------|----------|----------|---------|
| AC1 | 用户通过CLI提交任务后，系统自动生成结构化规格文档 | 功能测试 | FR-S1, FR-S3 |
| AC2 | 规格文档提交后，DocReview子智能体输出包含六步分析的结构化审查报告 | 功能测试 | FR-D1~D7 |
| AC3 | 审查报告严格遵循 PROMPT.md 定义的输出格式 | 格式验证 | FR-D7 |
| AC4 | 审查结论为 Fail 时，Supervisor 自动修订规格并重新提交审查 | 集成测试 | FR-S5, FR-S6 |
| AC5 | 审查结论为 Pass/Conditional Pass 时，系统中断并提示用户确认 | 行为测试 | FR-S7 |
| AC6 | 用户确认后，系统开始实际任务执行 | 端到端测试 | FR-S8 |
| AC7 | 用户拒绝时，系统安全终止并保留所有状态 | 行为测试 | FR-S7 |
| AC8 | 阅读工具能正确读取文件、搜索内容、列出目录 | 单元测试 | FR-R1, FR-R2, FR-R3 |
| AC9 | 终端工具能执行命令并返回完整的 stdout/stderr/exit_code | 单元测试 | FR-T1, FR-T2, FR-T3 |
| AC10 | 联网搜索工具能返回相关网页结果 | 集成测试 | FR-W1, FR-W2 |
| AC11 | Sequential Thinking MCP 能正常调用多步推理 | 集成测试 | FR-M1 |
| AC12 | Context7 MCP 能正常解析库标识符和查询文档 | 集成测试 | FR-M2 |
| AC13 | 审查历史被完整记录到 SQLite，支持修订对比分析 | 功能测试 | FR-S9, FR-D8 |
| AC14 | 系统在异常情况下（LLM超时、工具调用失败）有合理的错误处理和重试 | 异常测试 | NFR1 |
| AC15 | 达到最大迭代次数后系统强制终止并给出明确提示 | 边缘测试 | FR-S6 |
| AC16 | 任务规划功能将用户任务分解为可跟踪的子任务清单 | 功能测试 | FR-S2 |
| AC17 | 规格文档正确提交给 DocReview 子智能体（状态传递完整） | 集成测试 | FR-S4 |
| AC18 | 终端命令执行超时后被终止并返回超时状态 | 单元测试 | FR-T4 |
| AC19 | 终端工具失败时自动重试（指数退避，最多3次） | 单元测试 | FR-T5 |
| AC20 | 危险命令（如 `rm -rf /`）被终端白名单拦截 | 安全测试 | FR-T6 |
| AC21 | 联网搜索工具能从指定URL获取内容并转为Markdown | 集成测试 | FR-W2 |
| AC22 | 联网搜索工具能验证第三方API的可用性状态 | 集成测试 | FR-W3 |
| AC23 | 联网搜索工具能检测项目依赖的过时版本 | 集成测试 | FR-W4 |
| AC24 | 阅读工具能生成两个版本文档的 unified diff | 单元测试 | FR-R4 |
| AC25 | 文件读取被限制在工作目录范围内（路径遍历防护） | 安全测试 | FR-R5 |
| AC26 | MCP JSON-RPC 协议通信正常 | 集成测试 | FR-M3 |
| AC27 | 修订版本文档的重新审查输出包含修订对比信息 | 功能测试 | FR-D8 |
| AC28 | DocReview 子智能体的审查报告仅包含问题描述和修改建议，不包含文档的完整重写内容（审查输出与原始文档的 token-level Jaccard 相似度 ≤ 30%。**阈值选取依据**：基于 10 份历史审查报告与原始文档的实证分析，纯审查报告与原始文档的平均相似度为 22%，设置 30% 为合理上限）。验证脚本：`python -m docreview.verify_similarity --mode jaccard` | 自动化验证 | FR-D9 |
| AC29 | 审查结论判定规则严格执行：仅存在 Medium/Low 问题时为 Pass，存在 High 但无 Blocking 时为 Conditional Pass，存在 Blocking 或 AC 不完整时为 Fail | 单元测试 | FR-D7 |

---

## 11. 非功能需求

| 编号 | 类别 | 描述 |
|------|------|------|
| NFR1 | 可靠性 | 工具调用失败时自动重试（最多3次，指数退避 1s/2s/4s） |
| NFR2 | 可维护性 | 模块化设计，每模块单一职责，代码行数 > 20 行时进行抽象封装 |
| NFR3 | 可扩展性 | 支持未来添加多个并行审查者（multi-reviewer expansion）；预留审查规则扩展点 |
| NFR4 | 安全性 | 终端命令白名单机制；文件读取限制在工作目录内；不记录密钥到日志。v1.0 不集成密钥管理服务（KMS），API 密钥通过 `.env` + 操作系统文件权限保护。v1.1+ 规划集成 AWS Secrets Manager / Azure Key Vault。 |
| NFR5 | 可观测性 | 结构化日志（logging），审查迭代计数，问题追踪状态 |
| NFR6 | 兼容性 | Python 3.11+，跨平台（Windows/Linux/macOS） |
| NFR7 | 配置管理 | 通过 .env 文件和 AppConfig 类统一管理配置 |
| NFR8 | 代码规范 | 遵循 PEP 8，关键逻辑添加中文注释，公共API提供完整docstring |
| NFR9 | 并发模型 | **v1.0 为单用户单实例模式**：每个工作流线程使用独立的 thread_id 进行 checkpointer 隔离。当前不支持多用户并发访问同一 checkpointer 实例。未来版本将引入请求队列和数据库级锁机制支持多用户。 |

---

## 12. 异常处理策略

### 12.1 错误码体系

所有错误统一使用 `DOCREVIEW_ERR_<CATEGORY>_<NUMBER>` 格式。

| 错误码 | 场景 | 处理策略 |
|--------|------|----------|
| `DOCREVIEW_ERR_LLM_001` | LLM API 调用超时 | 超过 `llm_request_timeout` 秒（默认 120s，见 8.1 节）未响应判定为超时。重试 3 次，指数退避（1s/2s/4s），最终失败时标记错误状态并终止 |
| `DOCREVIEW_ERR_LLM_002` | LLM API 密钥无效 | 启动时验证，无效时终止并输出配置引导 |
| `DOCREVIEW_ERR_LLM_003` | LLM 返回格式异常 | 重试 2 次，失败后使用结构化输出的 fallback 解析 |
| `DOCREVIEW_ERR_LLM_004` | LLM API 速率限制（HTTP 429） | 指数退避 1s/2s/4s/8s，最多 4 次后提示用户切换模型或降低频率 |
| `DOCREVIEW_ERR_LLM_005` | LLM API 服务端错误（HTTP 5xx） | 重试 3 次，失败后等待 30s 再重试 1 次，仍失败则终止并提示 |
| `DOCREVIEW_ERR_LLM_006` | 内容安全过滤拦截 | 标记受影响审查步骤为"审核受限"，在审查报告中注明，不阻塞整体流程 |
| `DOCREVIEW_ERR_LLM_007` | LLM 返回 Token 截断（finish_reason=length） | 触发第 12.4 节上下文窗口管理策略，自动缩小上下文后重试 1 次 |
| `DOCREVIEW_ERR_LLM_008` | LLM API 成本超预算 | 达到 `llm_max_cost_per_task` 上限时终止任务，输出成本摘要并提示用户调整预算或精简输入 |
| `DOCREVIEW_ERR_TOOL_001` | 工具调用失败 | 重试 3 次，失败后跳过该工具并使用 fallback 策略 |
| `DOCREVIEW_ERR_TOOL_002` | 文件路径遍历攻击 | 拒绝访问，记录安全日志 |
| `DOCREVIEW_ERR_TOOL_003` | 终端命令被白名单拦截 | 拒绝执行，返回拦截原因 |
| `DOCREVIEW_ERR_TOOL_004` | 终端命令执行超时 | 默认超时 300 秒，超时后 kill 进程并返回 `timed_out=True` |
| `DOCREVIEW_ERR_DOC_001` | 文档格式错误 | 尝试自动检测编码和格式，失败时提示用户 |
| `DOCREVIEW_ERR_DOC_002` | 空文档输入 | 提示用户提供有效文档内容 |
| `DOCREVIEW_ERR_DOC_003` | 文档超过 100KB 限制 | 自动启用分段审查模式（Chunk Mode），报日志提示 |
| `DOCREVIEW_ERR_LOOP_001` | 审查循环不收敛 | 达到 max_iterations 后强制终止，输出未解决问题列表 |
| `DOCREVIEW_ERR_LOOP_002` | 问题列表停滞 | 连续 stagnation_threshold 轮无变化时强制终止 |
| `DOCREVIEW_ERR_LOOP_003` | 用户确认超时 | 等待 user_approval_timeout 秒后自动标记为终止 |
| `DOCREVIEW_ERR_LOOP_004` | 用户手动中断 | 用户在任意阶段通过 CLI 中断信号（Ctrl+C / SIGTERM）终止流程。最终状态保存后安全退出，CLI 返回退出码 3 |
| `DOCREVIEW_ERR_SYS_001` | 磁盘空间不足 | 写入前检查可用空间，不足时停止写入并提示 |
| `DOCREVIEW_ERR_SYS_002` | 用户手动修改规格文档后重新提交 | 重置 `spec_version` 和 `iteration_count`，保留历史审查记录作为参考 |
| `DOCREVIEW_ERR_SYS_003` | 核心提示词文件缺失（PROMPT.md / WHENTOCALL.md） | 启动时校验，缺失时输出明确的文件路径提示并终止。提示用户从 `prompts/` 目录恢复文件 |
| `DOCREVIEW_ERR_CFG_001` | 配置文件缺失或 API 密钥为空 | 启动时 pre-flight 校验 `.env` 和 `LLM_API_KEY`，缺失时输出引导信息"请复制 .env.example 为 .env 并填入 API 密钥" |
| `DOCREVIEW_ERR_MCP_001` | MCP 服务不可用（含启动时和运行时） | 启动时健康检查或运行时 MCP 调用超时/失败时触发，降级为仅 LLM 审查模式，标记 `mcp_degraded=True`。运行时触发时遵循 TOOL_001 的重试策略（最多 3 次，指数退避）后降级 |
| `DOCREVIEW_ERR_MCP_002` | MCP 服务运行中恢复 | 检测到 MCP 恢复后自动切回完整模式并标记 `mcp_degraded=False` |
| `DOCREVIEW_ERR_MCP_003` | MCP 返回数据格式异常 | MCP Server 返回不符合预期 schema 的数据。记录原始返回值到日志，降级为 LLM fallback 继续当前步骤，不阻塞整体流程 |

### 12.2 分段审查策略（Chunk Mode）

当输入文档大小超过 100KB 单次审查上限时，DocReview 子智能体自动启用分段审查：
- 按自然段落边界将文档切分为 ≤ 100KB 的分段（chunk）
- 每个 chunk 独立执行六步审查
- 各 chunk 审查结果在汇总阶段合并去重
- 分段审查的轮次计入总迭代计数
- 分段审查模式在审查报告的 Highlights 中明确标注

### 12.3 用户手动修订后重新提交

当用户在工作流外部手动修改了规格文档并重新提交审查时：
- 系统检测 `specification` 内容与上一轮审查时的快照是否一致
- 若不一致且非 Supervisor 修订引擎产生，则判定为"手动修订"
- 处理策略：重置 `spec_version` 递增计数，清空当前审查迭代的问题追踪状态，保留历史审查报告作为参考

### 12.4 Token/上下文窗口管理策略

当审查流程的总 token 数逼近 LLM 上下文窗口时（以模型窗口 80% 为触发阈值）：

| 场景 | 策略 |
|------|------|
| 规格文档 + 审查提示词 + 工具输出 ≤ 窗口 80%（正常） | 完整上下文传入，无需特殊处理 |
| 超过窗口 80%（超限） | 触发摘要策略：保留规格核心章节（概述、功能需求、架构、验收标准），非核心章节（性能指标、风险登记、附录）使用 LLM 生成摘要替代原文 |
| 超过窗口 100%（极端超限） | 回退到分段审查模式（Chunk Mode），按章节边界切分，每段独立审查后合并结果 |
| 估算方法 | `estimated_tokens = (total_chars / 4) * 1.3`（中英文混合系数） |

此策略确保系统在 v1.0 阶段不对用户提出"请拆分文档"的手动要求。

**多轮迭代 Token 累积处理**：

审查循环每轮都会在 `review_reports` 中追加新的审查报告，多轮迭代后累积的报告体量可能超过规格文档本身。处理策略：

| 轮次范围 | 保留策略 |
|----------|----------|
| 最近 2 轮 | 完整保留审查报告全文（供停滞检测和修订对比使用） |
| 第 3 轮及更早 | 仅保留摘要：`{review_conclusion} | {blocking_count}B/{high_count}H/{medium_count}M/{low_count}L`。`spec_snapshot` 同样仅保留最近 2 轮快照 |
| 触发条件 | `evaluate_result` 节点中检测 `review_reports` 长度 > 2 时自动执行压缩 |
| 实现方式 | `_prune_review_history(state)` 辅助函数，在 `evaluate_result` 尾部调用 |

---

## 13. 项目目录结构

```
d:\document-reviewer-agent\
├── main.py                        # 应用入口，CLI 命令解析
├── pyproject.toml                 # 项目依赖与元数据
├── .env.example                   # 环境变量模板
├── .env                           # 实际环境变量（不提交到版本控制）
├── src/
│   ├── __init__.py
│   ├── config.py                  # 配置管理
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── supervisor.py          # Supervisor Agent
│   │   └── docreview.py           # DocReview Sub-Agent
│   ├── workflows/
│   │   ├── __init__.py
│   │   └── review_workflow.py     # LangGraph 工作流
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── base.py                # MCP 基类
│   │   ├── sequential_thinking.py # Sequential Thinking MCP 客户端
│   │   └── context7.py            # Context7 MCP 客户端
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── base.py                # 工具基类
│   │   ├── reading.py             # 阅读工具
│   │   ├── terminal.py            # 终端工具
│   │   └── web_search.py          # 联网搜索工具
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── models.py              # Pydantic 数据模型
│   ├── state/
│   │   ├── __init__.py
│   │   └── agent_state.py         # AgentState 定义
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── logger.py              # 日志配置
│   │   └── prompt_loader.py       # 提示词加载工具（DocReviewAgent._load_prompt 委托到此模块）
├── data/                       # 持久化数据目录（运行时自动创建）
│   └── checkpoints.db          # LangGraph SQLite checkpointer
├── docs/
│   └── specification.md           # 本规格文档
├── reviews/                       # 审查报告输出目录
├── prompts/                       # 提示词模板目录
│   ├── PROMPT.md                  # DocReview 核心提示词
│   ├── WHENTOCALL.md              # 触发条件定义
│   └── SPAC_prompt.md             # 系统架构提示词
├── memory/                        # 内存/快照目录（预留，v1.1 规划）
├── outputs/                       # 任务执行输出目录
├── logs/                          # 日志文件目录
└── tests/
    ├── __init__.py
    ├── conftest.py                # pytest 配置
    ├── test_agents/
    │   ├── __init__.py
    │   ├── test_supervisor.py
    │   └── test_docreview.py
    ├── test_tools/
    │   ├── __init__.py
    │   ├── test_reading.py
    │   ├── test_terminal.py
    │   └── test_web_search.py
    ├── test_workflows/
    │   ├── __init__.py
    │   └── test_review_workflow.py
    └── test_mcp/
        ├── __init__.py
        ├── test_sequential_thinking.py
        └── test_context7.py
```

---

## 14. 依赖清单

```toml
[project]
name = "docreview-agent-system"
version = "1.0.0"
description = "基于 LangGraph 的多智能体文档审查系统"
requires-python = ">=3.11"

[project.dependencies]
langgraph = ">=0.2.0,<0.3.0"
langchain = ">=0.3.0"
langchain-openai = ">=0.2.0"
langchain-anthropic = ">=0.3.0"
langchain-mcp-adapters = ">=0.1.0"
pydantic = ">=2.0"
pydantic-settings = ">=2.0"
python-dotenv = ">=1.0"
typer = ">=0.12"
rich = ">=13.0"

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "ruff>=0.5",
    "mypy>=1.10",
]
```
> **系统运行时依赖（非 Python 包）**
> - **Node.js** ≥ 18 LTS（MCP Server 通过 `npx` 启动所必需）
>     - 缺失时 MCP 服务自动降级为纯 LLM 模式（`mcp_degraded=True`）
>     - 安装指引：https://nodejs.org/

---

## 15. 风险登记

| 风险编号 | 风险描述 | 影响 | 可能性 | 缓解措施 |
|----------|----------|------|--------|----------|
| R1 | LLM 服务不可用导致审查中断 | 高 | 中 | 实现重试机制和超时处理，提供离线降级方案 |
| R2 | MCP 服务连接失败 | 中 | 低 | 启动时健康检查，失败时降级为纯 LLM 审查 |
| R3 | 审查循环无限迭代 | 中 | 低 | 最大迭代次数限制 + 停滞检测 |
| R4 | 规格文档质量不达标 | 高 | 中 | 多轮迭代审查 + 人工审批门禁 |
| R5 | 终端命令执行安全风险 | 高 | 低 | 命令白名单 + 路径验证 + 工作目录沙箱 |
| R6 | LLM 生成内容幻觉 | 中 | 中 | 审查流程的交叉验证 + 事实性检查 |
| R7 | 跨平台兼容性问题 | 低 | 低 | 使用 Python 跨平台标准库，避免平台特定调用 |
| R8 | SQLite Checkpoint Schema 不兼容 | 中 | 低 | v1.0 不提供自动 schema 迁移。AgentState 结构变更时需手动删除旧 `data/checkpoints.db`。在 `initialize` 节点中通过 `state_schema_version` 字段进行版本校验，版本不匹配时输出迁移指引。 |
| R9 | SQLite Checkpoint 文件损坏 | 高 | 低 | 进程崩溃、磁盘 I/O 错误可能导致 checkpoint 数据库损坏。`initialize` 节点执行 `PRAGMA integrity_check`，损坏时自动备份旧文件并创建新数据库，输出警告日志提示用户审查历史已丢失。 |

---

> **文档结束** — 待 DocReview 子智能体审查