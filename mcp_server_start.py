#!/usr/bin/env python3
"""DocReview MCP Server 启动脚本"""

import argparse
import asyncio
import logging
import sys

from src.mcp_server.server import start_server

def main():
    parser = argparse.ArgumentParser(description="启动 DocReview MCP Server")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="绑定的主机地址（默认: 127.0.0.1）"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="监听端口（默认: 8000）"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启用调试模式"
    )
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    
    logger = logging.getLogger("mcp_server")
    logger.info(f"准备启动 DocReview MCP Server...")
    logger.info(f"绑定地址: http://{args.host}:{args.port}")
    
    try:
        asyncio.run(start_server(host=args.host, port=args.port))
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭服务器...")
    except Exception as e:
        logger.error(f"服务器启动失败: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
