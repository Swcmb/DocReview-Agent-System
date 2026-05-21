---
name: docreview
description: DocReview Agent System - 基于 LangGraph 的多智能体文档评审系统，支持 MCP (Model Context Protocol) 协议。当用户提到"文档审查"、"文档评审"、"代码审查"、"技术文档评审"、"PRD审查"、"需求文档审查"、"MCP Server"、"文档评审系统"、"智能文档审查"、"文档分析"等时触发。帮助用户设置和使用文档评审系统，执行文档审查任务，配置 MCP Server，以及理解系统架构。
---

# DocReview Agent System Skill

## 概述

DocReview Agent System 是一个基于 LangGraph 构建的多智能体文档评审系统，能够自动执行文档审查、生成规格说明、并支持 MCP 协议供 AI 客户端接入。

## 主要功能

1. **文档审查**: 使用 6 步评审流程对产品需求文档(PRD)、技术方案等进行深入分析
2. **规格生成**: 根据任务描述自动生成结构化的规格文档
3. **迭代优化**: 支持多轮评审和规格修订
4. **MCP Server**: 提供 HTTP 和 stdio 两种模式的 MCP 服务器
5. **健康检查**: 检查系统运行状态和服务可用性

## 常用命令

### CLI 命令

```bash
# 评审文档
docreview review --doc-path ./docs/prd.md

# 附带任务描述评审
docreview review --doc-path ./docs/prd.md --task "评审产品需求文档"

# 生成规格说明
docreview generate-spec --task "设计用户认证系统" --spec-output ./specs/auth.md

# 查看状态
docreview status

# 恢复中断的评审
docreview resume --thread-id review-xxx --approve
```

### MCP Server 启动

```bash
# HTTP 模式
python mcp_server_start.py --host 127.0.0.1 --port 8000

# stdio 模式
python mcp_stdio_start.py
```

### 环境变量配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| LLM_API_KEY | LLM API 密钥 | 必填 |
| LLM_MODEL | 模型名称 | gpt-4o |
| LLM_BASE_URL | API 基础 URL | 空 |
| LOG_LEVEL | 日志级别 | INFO |

## MCP 协议方法

### initialize
初始化连接，返回协议版本和服务器信息

```json
{"jsonrpc":"2.0","id":"0","method":"initialize","params":{}}
```

### tools/list
列出可用工具

```json
{"jsonrpc":"2.0","id":"1","method":"tools/list","params":{}}
```

### tools/call
调用工具

```json
{"jsonrpc":"2.0","id":"2","method":"tools/call","params":{"name":"health_check","arguments":{}}}
```

## 可用工具

### review_document
执行文档审查

**参数**:
- `doc_path`: 待审查文档路径（可选）
- `task`: 任务描述（可选）
- `max_iterations`: 最大迭代次数（默认10）

### generate_spec
生成规格文档

**参数**:
- `task`: 任务描述（必填）
- `document_content`: 参考文档内容（可选）

### health_check
检查服务健康状态

**参数**: 无

## AI 客户端配置示例

### Claude Desktop / Continue 配置

```json
{
  "mcpServers": {
    "docreview": {
      "disabled": false,
      "timeout": 60,
      "command": "python",
      "args": ["mcp_stdio_start.py"],
      "env": {
        "LLM_API_KEY": "your-api-key",
        "LLM_MODEL": "qwen3.5-flash",
        "LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1"
      },
      "type": "stdio"
    }
  }
}
```

## 使用场景

### 场景 1: 审查产品需求文档

```bash
cd D:\DocReview-Agent-System
python mcp_stdio_start.py
```

然后通过 AI 客户端调用：
```json
{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"review_document","arguments":{"doc_path":"D:/projects/my-app/docs/prd.md","task":"审查这份产品需求文档，找出潜在问题"}}}
```

### 场景 2: 生成技术规格文档

```json
{"jsonrpc":"2.0","id":"2","method":"tools/call","params":{"name":"generate_spec","arguments":{"task":"设计一个电商平台的订单管理系统，包含订单创建、支付、发货、退款等功能"}}}
```

### 场景 3: 检查服务状态

```json
{"jsonrpc":"2.0","id":"3","method":"tools/call","params":{"name":"health_check","arguments":{}}}
```

## 项目结构

```
docreview-agent-system/
├── src/
│   ├── agents/              # 智能体实现
│   ├── workflows/           # LangGraph 工作流
│   ├── tools/               # 工具实现
│   ├── mcp/                 # MCP 客户端
│   └── mcp_server/          # MCP Server 实现
├── prompts/                 # 提示词模板
├── reviews/                 # 评审历史输出
├── specs/                   # 规格说明文档
├── main.py                  # CLI 入口
├── mcp_server_start.py      # HTTP 模式启动
├── mcp_stdio_start.py       # stdio 模式启动
└── test_stdio.py            # stdio 测试脚本
```

## 关键文件说明

- [main.py](file:///D:/DocReview-Agent-System/main.py): CLI 入口文件
- [mcp_server_start.py](file:///D:/DocReview-Agent-System/mcp_server_start.py): HTTP 模式 MCP Server 启动脚本
- [mcp_stdio_start.py](file:///D:/DocReview-Agent-System/mcp_stdio_start.py): stdio 模式 MCP Server 启动脚本
- [src/mcp_server/stdio_server.py](file:///D:/DocReview-Agent-System/src/mcp_server/stdio_server.py): stdio 服务器核心实现
- [src/workflows/review_workflow.py](file:///D:/DocReview-Agent-System/src/workflows/review_workflow.py): 文档审查工作流定义

## 启动检查清单

1. ✅ 安装依赖: `pip install -e .`
2. ✅ 配置环境变量: 设置 `LLM_API_KEY`
3. ✅ 启动服务器: `python mcp_stdio_start.py`
4. ✅ 测试连接: 调用 `health_check` 工具
5. ✅ 执行审查: 调用 `review_document` 工具

## 常见问题

### Q: 协议版本不支持
A: 确保 `protocolVersion` 返回 `"2024-11-05"` 格式

### Q: 工具列表无法获取
A: 检查方法名是否为 `tools/list`，工具定义是否包含 `inputSchema`

### Q: 工具调用失败
A: 检查方法名是否为 `tools/call`，参数格式是否正确

## 技术支持

如有问题，请检查：
1. 日志文件: `logs/` 目录
2. 环境变量配置
3. LLM 服务连接状态
4. Python 版本 >= 3.11