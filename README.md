# DocReview Agent System

> 一个基于 LangGraph 构建的多智能体文档评审系统

## 概述

DocReview Agent System 是一款智能文档评审工具，利用 AI 智能体对技术文档进行分析、评审和改进。它基于 LangGraph 构建，为文档分析、规格说明生成和迭代优化提供了结构化的工作流。

## 特性

- **自动化文档评审**：基于 AI 的 6 步文档评审流程
- **迭代优化**：支持多轮评审和规格说明的自动修订
- **执行门控**：在执行已批准的任务前需要用户确认
- **深度分析**：集成了 Sequential Thinking 和 Context7 MCP 服务
- **结构化报告**：生成标准化的评审报告，提供可操作的见解
- **多智能体协作**：监督智能体和文档评审智能体无缝协作

## 安装

### 前置要求

- Python 3.11+
- Node.js（用于 MCP 服务，可选）
- LLM API 密钥（OpenAI、Anthropic 等）

### 安装步骤

```bash
# 克隆仓库
git clone <repository-url>
cd docreview-agent-system

# 安装依赖
pip install -e .

# 配置环境
cp .env.example .env
# 编辑 .env 文件，填写您的 API 密钥
```

## 快速开始

### 评审文档

```bash
# 评审已有文档
docreview review --doc-path ./docs/prd.md

# 附带任务描述进行评审
docreview review --doc-path ./docs/prd.md --task "评审这份产品需求文档"
```

### 生成规格说明

```bash
# 根据任务描述生成规格说明
docreview generate-spec --task "设计一个用户认证系统" --spec-output ./specs/auth-system.md
```

### 查看评审状态

```bash
# 查看评审历史
docreview status

# 查看特定评审线程
docreview status --thread-id review-20260520-140010
```

### 恢复中断的评审

```bash
# 恢复评审线程并批准执行
docreview resume --thread-id review-20260520-140010 --approve
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
├   ├── modules/             # 模块文档
│   │   └── mcp-client-usage-guide.md
├── examples/                # 示例代码
├── .env.example             # 环境变量示例
├── main.py                  # CLI 入口
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
```

## 开发

### 运行测试

```bash
# 运行所有测试
pytest

# 运行特定测试文件
pytest tests/test_agents/
```

### 代码质量

```bash
# 代码检查
ruff check .

# 类型检查
mypy src/
```

## 文档

- **系统规格说明**：`./specs/docreview-agent-system/system-specification.md`
- **架构参考**：`./specs/docreview-agent-system/spac-architecture-reference.md`
- **MCP 使用指南**：`./docs/modules/mcp-client-usage-guide.md`

## 许可证

MIT License