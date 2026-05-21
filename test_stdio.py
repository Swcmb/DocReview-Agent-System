#!/usr/bin/env python3
"""测试 DocReview MCP Server (stdio 模式) - 符合 MCP 协议规范"""

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
        # 测试 initialize
        print("\n1. 测试 initialize...")
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": "0",
            "method": "initialize",
            "params": {}
        })
        process.stdin.write(request + "\n")
        process.stdin.flush()
        
        response_line = process.stdout.readline()
        print(f"响应: {response_line.strip()}")
        
        response = json.loads(response_line)
        assert "result" in response
        assert "protocolVersion" in response["result"]
        assert "capabilities" in response["result"]
        assert "serverInfo" in response["result"]
        assert response["result"]["protocolVersion"] == "2024-11-05"
        print(f"协议版本: {response['result'].get('protocolVersion')}")
        print(f"服务器名称: {response['result']['serverInfo'].get('name')}")
        
        # 测试 tools/list
        print("\n2. 测试 tools/list...")
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/list",
            "params": {}
        })
        process.stdin.write(request + "\n")
        process.stdin.flush()
        
        response_line = process.stdout.readline()
        print(f"响应: {response_line.strip()}")
        
        response = json.loads(response_line)
        assert "result" in response
        assert "tools" in response["result"]
        tools = response["result"]["tools"]
        tool_names = [tool["name"] for tool in tools]
        print(f"工具列表: {tool_names}")
        
        # 验证工具格式
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert "type" in tool["inputSchema"]
            assert "properties" in tool["inputSchema"]
            assert "required" in tool["inputSchema"]
        
        # 测试 tools/call health_check
        print("\n3. 测试 tools/call health_check...")
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": "2",
            "method": "tools/call",
            "params": {"name": "health_check", "arguments": {}}
        })
        process.stdin.write(request + "\n")
        process.stdin.flush()
        
        response_line = process.stdout.readline()
        print(f"响应: {response_line.strip()}")
        
        response = json.loads(response_line)
        assert "result" in response
        result = response["result"]
        assert "content" in result
        assert isinstance(result["content"], list)
        assert len(result["content"]) > 0
        assert "type" in result["content"][0]
        assert "text" in result["content"][0]
        print(f"响应内容类型: {result['content'][0]['type']}")
        print(f"响应文本: {result['content'][0]['text']}")
        
        print("\n✅ 所有测试通过!")
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
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