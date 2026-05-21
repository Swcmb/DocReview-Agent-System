# DocReview Agent System

> 基于 LangGraph 框架的多智能体文档审查系统

## 特性

- 🤖 **自动化审查**：AI 驱动的六步法文档审查
- 🔄 **迭代修订**：支持多轮审查与自动修订
- ⏸️ **执行门禁**：审查通过后需用户确认才执行
- 🔍 **深度分析**：集成 Sequential Thinking 和 Context7 MCP
- 📊 **结构化报告**：生成标准化审查报告

## 安装

```bash
# 克隆项目
git clone <repository-url>
cd docreview-agent-system

# 安装依赖
pip install -e .

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API 密钥
```

## 快速开始

```bash
# 审查文档
docreview review --doc-path ./docs/prd.md

# 根据任务生成规格
docreview generate-spec "设计一个用户认证系统"

# 查看审查历史
docreview status
```

## 项目结构

```
docreview-agent-system/
├── src/
│   ├── agents/          # 智能体实现
│   ├── workflows/       # LangGraph 工作流
│   ├── tools/          # 工具模块
│   ├── mcp/            # MCP 客户端
│   ├── schemas/        # 数据模型
│   └── config.py       # 配置管理
├── prompts/            # 提示词模板
├── tests/              # 测试套件
└── main.py            # CLI 入口
```

## 文档

- [规格文档](doc/specification.md)
- [CLI 使用指南](doc/cli-guide.md)

## 许可证

MIT
