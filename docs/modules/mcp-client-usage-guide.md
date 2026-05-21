# MCP 客户端模块

DocReview Agent System 的 MCP（Model Context Protocol）客户端实现，支持与 MCP Server 的标准 JSON-RPC 协议通信。

## 📁 模块结构

```
src/mcp/
├── __init__.py              # 模块导出
├── base.py                  # MCP 基类和公共功能
├── sequential_thinking.py   # Sequential Thinking MCP 客户端
└── context7.py             # Context7 MCP 客户端
```

## 🚀 快速开始

### 1. 导入模块

```python
from src.mcp import (
    SequentialThinkingClient,
    Context7Client,
    MCPError,
)
```

### 2. 使用 Sequential Thinking

```python
import asyncio
from src.mcp import SequentialThinkingClient

async def main():
    client = SequentialThinkingClient(timeout=30, max_retries=3)
    
    try:
        # 启动 MCP 服务
        await client.start()
        
        if client.is_degraded:
            print("MCP 服务处于降级模式")
        
        # 执行多步推理
        result = await client.think(
            thought="分析问题的第一步",
            thought_number=1,
            total_thoughts=3,
            next_thought_needed=True
        )
        
        # 获取完整思维链
        chain = await client.get_chain()
        print(f"共 {len(chain)} 个思维步骤")
        
    finally:
        await client.stop()

asyncio.run(main())
```

### 3. 使用 Context7

```python
import asyncio
from src.mcp import Context7Client

async def main():
    client = Context7Client()
    
    try:
        # 启动服务
        await client.start()
        
        # 解析库标识符
        lib_id = await client.resolve_library_id("React")
        print(f"React -> {lib_id}")  # /facebook/react
        
        # 查询文档
        docs = await client.query_docs(
            query="authentication",
            library_id=lib_id,
            num_results=5
        )
        
        # 获取上下文信息
        context = await client.get_context("FastAPI")
        print(f"主题: {context.topic}")
        
    finally:
        await client.stop()

asyncio.run(main())
```

## 🔧 核心功能

### Sequential Thinking MCP 客户端

- ✅ 启动本地 MCP Server（通过 npx）
- ✅ 多步推理调用
- ✅ 思维链管理
- ✅ 思维修订
- ✅ 自动降级模式
- ✅ 健康检查
- ✅ 超时和重试机制
- ✅ 进程清理

### Context7 MCP 客户端

- ✅ 库标识符解析（支持 20+ 常用库）
- ✅ 文档查询
- ✅ 上下文信息获取
- ✅ 本地回退查询
- ✅ 自动降级模式
- ✅ 健康检查
- ✅ 进程清理

## 📋 验收标准

所有验收标准均已实现：

- ✅ Sequential Thinking MCP 服务能正常启动
- ✅ Sequential Thinking 多步推理调用成功
- ✅ Context7 MCP 服务能正常启动
- ✅ Context7 能解析库标识符和查询文档
- ✅ MCP JSON-RPC 协议通信正常
- ✅ MCP 健康检查机制正常
- ✅ MCP 失败时自动降级为纯 LLM 模式
- ✅ MCP 恢复时自动切回完整模式
- ✅ MCP 进程在 finalize 时正确关闭
- ✅ MCP 调用超时机制正常（默认 30 秒）
- ✅ 所有代码遵循 PEP 8
- ✅ 关键逻辑有中文注释

## 🔄 降级模式

当 MCP 服务不可用时（例如没有安装 npx 或 MCP Server），客户端会自动切换到降级模式：

```python
client = SequentialThinkingClient()

started = await client.start()
if client.is_degraded:
    print("MCP 服务不可用，使用降级模式")
    # 客户端仍然可以正常工作，但某些功能可能受限
```

降级模式下的行为：

- **SequentialThinkingClient**: 
  - `think()` 方法会抛出 `MCPError`
  - `get_chain()` 返回空列表
  - 建议配合纯 LLM 推理使用

- **Context7Client**:
  - 使用本地回退查询
  - 提供基本的信息摘要
  - 仍可正常解析库标识符

## ⚙️ 配置选项

### SequentialThinkingClient

```python
SequentialThinkingClient(
    timeout=30,           # 请求超时时间（秒）
    max_retries=3,        # 最大重试次数
    default_thoughts=3    # 默认思考步骤数
)
```

### Context7Client

```python
Context7Client(
    timeout=30,     # 请求超时时间（秒）
    max_retries=3   # 最大重试次数
)
```

## 🔍 健康检查

定期检查 MCP 服务的健康状态：

```python
client = SequentialThinkingClient()

await client.start()

# 健康检查
is_healthy = await client.health_check()
print(f"服务健康状态: {is_healthy}")

# 检查降级模式
if client.is_degraded:
    print("服务处于降级模式")
```

## 📝 错误处理

```python
from src.mcp import MCPError, MCPTimeoutError, MCPConnectionError

try:
    result = await client.think("test", 1, 1)
except MCPTimeoutError as e:
    print(f"请求超时: {e.message}")
except MCPConnectionError as e:
    print(f"连接错误: {e.message}")
except MCPError as e:
    print(f"MCP 错误: {e.message} (代码: {e.error_code})")
```

## 🧪 运行测试

### 基本验证

```bash
python verify_mcp_clients.py
```

### 使用示例

```bash
python examples/mcp_usage_examples.py
```

## 📚 支持的库

Context7 客户端支持以下常用库的标识符解析：

- LangChain
- OpenAI
- Anthropic
- React
- Next.js
- Vue.js
- PyTorch
- TensorFlow
- FastAPI
- Django
- Flask
- Node.js
- TypeScript
- Rust
- Go
- Docker
- Kubernetes
- ...（更多）

## 🔗 相关资源

- [Sequential Thinking MCP Server](https://github.com/modelcontextprotocol/servers)
- [Context7](https://context7.com)
- [MCP 协议规范](https://modelcontextprotocol.io)

## 📄 许可证

本项目采用 MIT 许可证。