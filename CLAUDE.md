# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

DocReview Agent System 是一个基于 LangGraph 的多智能体文档评审系统，支持 MCP (Model Context Protocol) 协议。系统使用两个专门的智能体（SupervisorAgent 和 DocReviewAgent）协作完成文档的自动化审查、规格说明生成与修订。

## 常用命令

### 安装与依赖
```bash
pip install -e .          # 安装项目
pip install -e ".[dev]"   # 安装开发依赖
```

### 运行 CLI
```bash
docreview review --doc-path ./docs/prd.md --task "评审这份文档"
docreview generate-spec --task "设计用户认证系统" --spec-output ./specs/auth.md
docreview status
docreview resume --thread-id review-YYYYMMDD-HHMMSS --approve
```

### 启动 MCP Server
```bash
python mcp_server_start.py --host 127.0.0.1 --port 8000   # HTTP 模式
python mcp_stdio_start.py                                   # stdio 模式（供 Claude Desktop 等客户端使用）
uvicorn src.mcp_server.server:app --host 127.0.0.1 --port 8000 --reload  # 开发模式（自动重载）
```

### 测试
```bash
pytest                                        # 全部测试
pytest tests/test_agents/                     # 指定目录
pytest tests/test_agents/test_docreview.py    # 单个文件
pytest -k "test_name"                         # 按名称匹配
pytest --cov=src/                             # 带覆盖率
python test_stdio.py                          # stdio 模式集成测试
```

### 代码质量
```bash
ruff check .       # Lint 检查
ruff fix .         # 自动修复
mypy src/          # 类型检查
```

## 架构

### 工作流流程

```
initialize → load_document(可选) → generate_spec → docreview → evaluate_result
                                                                        ↓
finalize ← execute ← user_approval ← revise_spec ← (如需循环)
```

工作流由 LangGraph `StateGraph` 驱动，`review_workflow.py` 中的 `build_workflow()` 函数定义完整图结构。`user_approval` 是唯一的中断点（`interrupt_before`），等待用户确认后恢复执行。

### 核心组件

- **`src/workflows/review_workflow.py`** — 工作流定义与编排。`build_workflow()` 构建图，`create_workflow_runtime()` 初始化所有依赖（LLM、工具、MCP 客户端、智能体）并返回运行时。checkpoint 使用 SQLite (`data/checkpoints.db`)，支持自动损坏检测与恢复。
- **`src/schemas/models.py`** — 所有数据模型。`AgentState` 是 LangGraph 的全局状态 TypedDict；`IssueStatus` / `IssueTracker` 跨轮次追踪问题；`ReviewConclusion` 是结构化审查结论。问题 ID 格式为 `{severity_short}-{round}-{seq}`（如 `BK-3-2`）。
- **`src/state/agent_state.py`** — 状态初始化 (`create_initial_state()`) 和旧版 Pydantic 状态模型 (`AgentStateModel`，保留向后兼容)。
- **`src/config.py`** — 使用 `pydantic-settings` 管理配置，支持 `.env` 文件和环境变量，嵌套配置用 `__` 分隔。`get_config()` 返回单例。

### 智能体

- **SupervisorAgent** (`src/agents/supervisor.py`) — 负责任务规划、规格说明生成/修订、执行门控
- **DocReviewAgent** (`src/agents/docreview.py`) — 执行六步文档审查流程，依赖 SequentialThinking 和 Context7 MCP 服务

### 工具层 (`src/tools/`)

- `ReadingTool` — 文件读取
- `TerminalTool` — 终端命令执行
- `WebSearchTool` — 网络搜索

### MCP 客户端 (`src/mcp/`)

- `BaseMCPClient` — 基类，提供进程管理、重试机制、降级模式
- `SequentialThinkingClient` — 顺序思考 MCP 服务
- `Context7Client` — Context7 上下文服务

MCP 服务依赖 Node.js/npx；如果不可用，系统自动降级（`mcp_degraded=True`）。

### 关键机制

- **停滞检测**：比较最近两轮问题 ID 集合，相同则判定停滞；超过阈值后强制终止
- **Token 累积管理**：3 轮前的审查报告自动压缩为单行摘要（`_prune_review_history`）
- **成本控制**：追踪 `total_llm_cost`，超过 `max_cost_per_task` 时设置错误码
- **错误码体系**：`DOCREVIEW_ERR_*` 格式（如 `DOCREVIEW_ERR_DOC_001` 表示文档加载失败，`DOCREVIEW_ERR_MCP_001` 表示 MCP 超时）

## 配置

通过 `.env` 文件配置（参考 `.env.example`），主要配置项：
- `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` — LLM 连接
- `LLM_TEMPERATURE` / `LLM_REQUEST_TIMEOUT` — 生成参数
- `MAX_REVIEW_ITERATIONS` / `STAGNATION_THRESHOLD` — 审查循环控制
- `MAX_COST_PER_TASK` — 成本上限（美元）
- `MCP_HOST` / `MCP_PORT` — MCP Server HTTP 地址

## 项目约定

- Python 3.11+，使用 `StrEnum`、`match` 语句等新特性
- Ruff 配置行宽 120，启用 pycodestyle/pyflakes/isort/pep8-naming/pyupgrade/bugbear 等规则
- 异步优先：工作流节点和 MCP 调用均为 `async`，CLI 入口通过 `asyncio.run()` 桥接
- 测试使用 `pytest-asyncio`，`asyncio_mode = "auto"`，fixtures 定义在 `tests/conftest.py`
- 代码注释使用中文
