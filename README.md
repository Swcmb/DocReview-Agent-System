# DocReview Agent System

> 一个基于 LangGraph 构建的多智能体文档评审系统，支持 MCP (Model Context Protocol) 协议

## 概述

DocReview Agent System 是一款智能文档评审工具，利用 AI 智能体对技术文档进行分析、评审和改进。它基于 LangGraph 构建，提供结构化的工作流，并可作为 MCP Server 对外提供服务。

## 特性

- **自动化文档评审**：基于 AI 的 6 步文档评审流程
- **迭代优化**：支持多轮评审和规格说明的自动修订
- **执行门控**：在执行已批准的任务前需要用户确认
- **深度分析**：集成了 Sequential Thinking 和 Context7 MCP 服务
- **结构化报告**：生成标准化的评审报告，提供可操作的见解
- **多智能体协作**：监督智能体和文档评审智能体无缝协作
- **MCP Server 支持**：提供 HTTP 和 stdio 两种模式的 MCP Server，支持 Claude Desktop、Continue 等 AI 客户端接入

## 安装

### 前置要求

- Python 3.11+
- LLM API 密钥（OpenAI、Anthropic 等）

### 安装步骤

```bash
# 克隆仓库
git clone <repository-url>
cd docreview-agent-system

# 安装依赖
pip install -e .

# 安装开发依赖（可选）
pip install -e ".[dev]"

# 配置环境
cp .env.example .env
# 编辑 .env 文件，填写您的 API 密钥
```

## 快速开始

### 使用 CLI

```bash
# 评审已有文档
docreview review --doc-path ./docs/prd.md

# 附带任务描述进行评审
docreview review --doc-path ./docs/prd.md --task "评审这份产品需求文档"

# 根据任务描述生成规格说明
docreview generate-spec --task "设计一个用户认证系统" --spec-output ./specs/auth-system.md

# 查看评审状态
docreview status

# 恢复中断的评审
docreview resume --thread-id review-20260520-140010 --approve
```

### 使用 MCP Server (HTTP 模式)

```bash
# 启动 MCP Server
python mcp_server_start.py --host 127.0.0.1 --port 8000

# 健康检查
curl http://127.0.0.1:8000/health

# 获取工具列表（REST）
curl http://127.0.0.1:8000/tools

# 调用文档审查工具（JSON-RPC）
curl -X POST http://127.0.0.1:8000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"invoke","params":{"tool":{"name":"review_document","arguments":{"doc_path":"path/to/document.md"}}}}'
```

### 使用 MCP Server (stdio 模式)

stdio 模式允许 AI 客户端通过标准输入输出来与服务器通信，适用于 Claude Desktop、Continue 等支持 MCP 协议的客户端。

**客户端配置示例（如 Claude Desktop、Continue）：**

```json
{
  "mcpServers": {
    "docreview": {
      "disabled": false,
      "timeout": 60,
      "command": "python",
      "args": ["mcp_stdio_start.py"],
      "env": {
        "LLM_API_KEY": "your-api-key"
      },
      "type": "stdio"
    }
  }
}
```

**环境变量配置：**

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `LLM_API_KEY` | LLM 服务的 API 密钥 | 必填 |
| `LLM_MODEL` | LLM 模型名称 | `gpt-4o` |
| `LLM_BASE_URL` | LLM 服务基础 URL | 空 |
| `LOG_LEVEL` | 日志级别 | `INFO` |

**测试 stdio 模式：**

```bash
# 设置环境变量
export LLM_API_KEY=your-api-key

# 启动服务器（测试模式）
python mcp_stdio_start.py

# 然后在另一个终端发送测试请求
echo '{"jsonrpc":"2.0","id":"1","method":"list_tools","params":{}}' | python mcp_stdio_start.py
```

## MCP Server API

### 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/` | POST | MCP JSON-RPC 协议端点 |
| `/health` | GET | 健康检查 |
| `/tools` | GET | 获取工具列表 |
| `/review` | POST | 执行文档审查 |
| `/generate-spec` | POST | 生成规格文档 |
| `/invoke` | POST | 通用工具调用 |

### 工具列表

1. **review_document** - 执行文档审查
   - 参数：`doc_path`（可选）、`task`（可选）、`max_iterations`（默认10）

2. **generate_spec** - 生成规格文档
   - 参数：`task`（必填）、`document_content`（可选）

3. **health_check** - 检查服务状态
   - 参数：无

### JSON-RPC 示例

```json
// 列出工具
{"jsonrpc":"2.0","id":"1","method":"list_tools","params":{}}

// 调用工具
{"jsonrpc":"2.0","id":"2","method":"invoke","params":{"tool":{"name":"review_document","arguments":{"doc_path":"docs/prd.md"}}}
```

## 项目结构

```
docreview-agent-system/
├── src/
│   ├── agents/              # 智能体实现
│   │   ├── supervisor.py    # 监督智能体（规划、规格说明、执行）
│   │   └── docreview.py     # 文档评审智能体（文档分析）
│   ├── workflows/           # LangGraph 工作流定义
│   │   └── review_workflow.py
│   ├── tools/               # 工具实现
│   │   ├── reading.py       # 文件读取工具
│   │   ├── terminal.py      # 终端执行工具
│   │   └── web_search.py    # 网络搜索工具
│   ├── mcp/                 # MCP 客户端
│   │   ├── sequential_thinking.py
│   │   └── context7.py
│   ├── mcp_server/          # MCP Server 实现
│   │   ├── server.py        # FastAPI 服务端（HTTP 模式）
│   │   └── stdio_server.py  # stdio 服务端（支持 AI 客户端接入）
│   ├── schemas/             # 数据模型
│   ├── state/               # 智能体状态管理
│   ├── utils/               # 工具函数
│   │   ├── logger.py
│   │   ├── llm.py
│   │   └── prompt_loader.py
│   └── config.py            # 配置
├── tests/                   # 测试套件
├── prompts/                 # 提示词模板
├── reviews/                 # 评审历史输出
├── logs/                    # 日志文件
├── specs/                   # 系统规格说明
├── docs/                    # 文档目录
├── examples/                # 示例代码
├── .env.example             # 环境变量示例
├── main.py                  # CLI 入口
├── mcp_server_start.py      # MCP Server 启动脚本（HTTP 模式）
├── mcp_stdio_start.py       # MCP Server 启动脚本（stdio 模式）
└── pyproject.toml           # 项目配置
```

## 架构

### 多智能体系统

系统使用两个专门的智能体进行协作：

1. **监督智能体**：负责任务规划、规格说明生成/修订以及执行门控
2. **文档评审智能体**：使用 6 步评审流程执行深入的文档分析

### 工作流

LangGraph 工作流包含以下步骤：

```
初始化 → 加载文档 → 生成规格说明 → 文档评审 → 评估
                                                       ↓
结束 ← 执行 ← 用户批准 ← 修订规格说明 ← （如需）
```

### MCP Server 架构

```
┌─────────────────────────────────────────────────────────┐
│                   MCP Server                            │
│  ┌─────────────┐  ┌─────────────┐  ┌───────────────┐   │
│  │  JSON-RPC   │  │   REST API  │  │  Health Check │   │
│  │    端点     │  │    端点     │  │     端点      │   │
│  └──────┬──────┘  └──────┬──────┘  └───────┬───────┘   │
│         │                 │                 │           │
│         └────────────────┼─────────────────┘           │
│                          ↓                             │
│              ┌───────────────────────┐                 │
│              │     工具路由器        │                 │
│              └──────────┬────────────┘                 │
│                         ↓                             │
│    ┌────────────────────┼────────────────────┐        │
│    ↓                    ↓                    ↓        │
│ ┌──────────┐    ┌─────────────┐    ┌─────────────┐    │
│ │review_   │    │generate_   │    │health_      │    │
│ │document  │    │spec        │    │check        │    │
│ └────┬─────┘    └─────┬───────┘    └─────┬───────┘    │
│      │                │                  │             │
│      └────────────────┼──────────────────┘             │
│                       ↓                               │
│              ┌───────────────────────┐                 │
│              │    LangGraph          │                 │
│              │    Workflow Runtime   │                 │
│              └───────────────────────┘                 │
└─────────────────────────────────────────────────────────┘
```

### 核心概念

- **状态机**：整个流程通过 LangGraph 作为状态机进行管理
- **检查点**：持久化执行状态，支持恢复
- **执行门控**：执行任务前需要用户确认
- **停滞检测**：通过检测未解决问题来防止无限循环
- **成本控制**：跟踪并限制 LLM API 成本

## 配置

创建 `.env` 文件并填写配置：

```env
# LLM 配置
LLM_API_KEY=your-api-key-here
LLM_MODEL=gpt-4o
LLM_BASE_URL=
LLM_TEMPERATURE=0.7
LLM_REQUEST_TIMEOUT=60

# 智能体行为
MAX_REVIEW_ITERATIONS=10
AUTO_APPROVE_THRESHOLD=low
STAGNATION_THRESHOLD=2
MAX_COST_PER_TASK=10.0

# 系统
WORKSPACE_DIR=.

# MCP Server
MCP_HOST=127.0.0.1
MCP_PORT=8000
```

## 开发

### 运行测试

```bash
# 运行所有测试
pytest

# 运行特定测试文件
pytest tests/test_agents/

# 运行测试并生成覆盖率报告
pytest --cov=src/
```

### 代码质量

```bash
# 代码检查
ruff check .

# 自动修复代码问题
ruff fix .

# 类型检查
mypy src/
```

### 启动开发服务器

```bash
# 启动 MCP Server
python mcp_server_start.py --host 127.0.0.1 --port 8000

# 启用自动重载（开发模式）
uvicorn src.mcp_server.server:app --host 127.0.0.1 --port 8000 --reload
```

## 文档

- **系统规格说明**：`./specs/docreview-agent-system/system-specification.md`
- **架构参考**：`./specs/docreview-agent-system/spac-architecture-reference.md`
- **MCP 使用指南**：`./docs/modules/mcp-client-usage-guide.md`
- **示例代码**：`./examples/mcp_usage_examples.py`

## 许可证

MIT License