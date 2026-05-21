#!/usr/bin/env python3
"""DocReview MCP Server - stdio 模式启动脚本

此脚本用于启动基于标准输入输出的 MCP Server，
可被支持 MCP 协议的 AI 客户端（如 Claude Desktop、Continue 等）接入。

使用方式：
1. 设置环境变量配置：
   export LLM_API_KEY=your-api-key
   export LLM_MODEL=gpt-4o

2. 直接运行（用于测试）：
   python mcp_stdio_start.py

3. 作为 MCP Server 被客户端调用（客户端配置示例）：
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
"""

import sys

from src.mcp_server.stdio_server import main

if __name__ == "__main__":
    main()