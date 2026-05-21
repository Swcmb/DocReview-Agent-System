#!/usr/bin/env python3
"""测试 DocReview MCP Server (stdio 模式)"""

import subprocess
import json
import sys
import time


def test_stdio_server():
    """测试 stdio server"""
    print("启动测试...")
    
    # 启动服务器进程
    process = subprocess.Popen(
        [sys.executable, "mcp_stdio_start.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd="d:/DocReview-Agent-System"
    )
    
    # 等待服务器启动
    time.sleep(2)
    
    try:
        # 测试 list_tools
        print("\n1. 测试 list_tools...")
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": "1",
            "method": "list_tools",
            "params": {}
        })
        process.stdin.write(request + "\n")
        process.stdin.flush()
        
        # 读取响应
        response_line = process.stdout.readline()
        print(f"响应: {response_line.strip()}")
        
        response = json.loads(response_line)
        assert "result" in response
        assert "tools" in response["result"]
        tools = response["result"]["tools"]
        print(f"工具列表: {[tool['name'] for tool in tools]}")
        
        # 测试 health_check
        print("\n2. 测试 health_check...")
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": "2",
            "method": "invoke",
            "params": {"tool": {"name": "health_check", "arguments": {}}}
        })
        process.stdin.write(request + "\n")
        process.stdin.flush()
        
        response_line = process.stdout.readline()
        print(f"响应: {response_line.strip()}")
        
        response = json.loads(response_line)
        assert "result" in response
        print(f"健康状态: {response['result'].get('status', 'unknown')}")
        
        print("\n✅ 所有测试通过!")
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        # 获取 stderr 输出
        stderr = process.communicate(timeout=1)[1]
        print(f"错误输出: {stderr}")
        return False
    finally:
        # 终止进程
        process.stdin.close()
        process.terminate()
        process.wait()
    
    return True


if __name__ == "__main__":
    success = test_stdio_server()
    sys.exit(0 if success else 1)