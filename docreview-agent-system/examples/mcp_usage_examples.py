#!/usr/bin/env python
"""MCP 客户端使用示例 / MCP Client Usage Examples"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.mcp import (
    SequentialThinkingClient,
    Context7Client,
    MCPError,
)


async def example_sequential_thinking():
    """Sequential Thinking 使用示例"""
    print("=" * 60)
    print("Sequential Thinking 使用示例")
    print("=" * 60)
    
    client = SequentialThinkingClient(timeout=30, max_retries=3)
    
    try:
        # 启动 MCP 服务
        print("\n1. 启动 MCP 服务...")
        started = await client.start()
        
        if client.is_degraded:
            print("   ⚠️  MCP 服务处于降级模式，将使用纯 LLM 推理")
        else:
            print("   ✓ MCP 服务已启动")
        
        # 进行多步推理
        print("\n2. 执行多步推理...")
        thoughts = [
            "首先分析问题的核心需求",
            "然后考虑可能的解决方案",
            "最后评估每个方案的优缺点"
        ]
        
        for i, thought in enumerate(thoughts, 1):
            result = await client.think(
                thought=thought,
                thought_number=i,
                total_thoughts=len(thoughts),
                next_thought_needed=(i < len(thoughts))
            )
            print(f"   思维 {i}: {thought[:30]}... ✓")
        
        # 获取完整思维链
        print("\n3. 获取思维链...")
        chain = await client.get_chain()
        print(f"   ✓ 共 {len(chain)} 个思维步骤")
        
    except MCPError as e:
        print(f"\n✗ MCP 错误: {e.message}")
    finally:
        # 清理资源
        print("\n4. 清理资源...")
        await client.stop()
        print("   ✓ 清理完成")


async def example_context7():
    """Context7 使用示例"""
    print("\n" + "=" * 60)
    print("Context7 使用示例")
    print("=" * 60)
    
    client = Context7Client(timeout=30)
    
    try:
        # 启动 MCP 服务
        print("\n1. 启动 MCP 服务...")
        started = await client.start()
        print(f"   ✓ 服务已启动（降级模式: {client.is_degraded}）")
        
        # 解析库标识符
        print("\n2. 解析库标识符...")
        libraries = ["LangChain", "React", "FastAPI", "Vue.js"]
        for lib in libraries:
            lib_id = await client.resolve_library_id(lib)
            print(f"   {lib:15} -> {lib_id}")
        
        # 查询文档
        print("\n3. 查询文档...")
        docs = await client.query_docs(
            query="authentication best practices",
            library_id="/facebook/react",
            num_results=3
        )
        print(f"   ✓ 查询到 {len(docs)} 个文档结果")
        for i, doc in enumerate(docs, 1):
            print(f"   {i}. {doc.library_name}")
            print(f"      {doc.snippet[:60]}...")
        
        # 获取上下文信息
        print("\n4. 获取上下文信息...")
        context = await client.get_context("FastAPI")
        print(f"   主题: {context.topic}")
        print(f"   内容: {context.content[:100]}...")
        print(f"   来源: {len(context.sources)} 个")
        
    except MCPError as e:
        print(f"\n✗ MCP 错误: {e.message}")
    finally:
        # 清理资源
        print("\n5. 清理资源...")
        await client.stop()
        print("   ✓ 清理完成")


async def example_integrated_review():
    """集成文档审查示例"""
    print("\n" + "=" * 60)
    print("集成文档审查示例")
    print("=" * 60)
    
    # 初始化两个 MCP 客户端
    thinking_client = SequentialThinkingClient()
    context_client = Context7Client()
    
    try:
        # 启动服务
        print("\n1. 初始化 MCP 客户端...")
        await thinking_client.start()
        await context_client.start()
        print("   ✓ 两个 MCP 客户端已启动")
        
        # 场景：审查一个关于 React 认证的文档
        print("\n2. 开始文档审查...")
        document = """
        标题：React 应用认证最佳实践
        
        内容：
        1. 使用 JWT 进行身份验证
        2. 实现安全的 token 存储
        3. 添加 CSRF 保护
        4. 实现优雅的错误处理
        """
        
        # 使用顺序思考进行分析
        print("\n3. 使用 Sequential Thinking 进行分析...")
        analysis_steps = [
            "识别文档的主要安全方面",
            "检查认证机制的实现细节",
            "评估建议的实用性",
            "检查是否遗漏重要的安全考虑"
        ]
        
        for i, step in enumerate(analysis_steps, 1):
            await thinking_client.think(
                thought=step,
                thought_number=i,
                total_thoughts=len(analysis_steps),
                next_thought_needed=(i < len(analysis_steps))
            )
            print(f"   步骤 {i}: {step}")
        
        # 获取相关上下文
        print("\n4. 获取相关技术上下文...")
        context = await context_client.get_context("React")
        print(f"   主题: {context.topic}")
        print(f"   相关文档: {len(context.sources)} 个")
        
        # 综合分析结果
        print("\n5. 综合分析结果...")
        print("   ✓ 已完成多步推理分析")
        print("   ✓ 已获取相关技术文档")
        print("   ✓ 文档审查完成")
        
    except MCPError as e:
        print(f"\n✗ MCP 错误: {e.message}")
    finally:
        # 清理资源
        print("\n6. 清理资源...")
        await thinking_client.stop()
        await context_client.stop()
        print("   ✓ 所有 MCP 客户端已清理")


async def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("DocReview Agent System - MCP 客户端使用示例")
    print("=" * 60)
    
    # 示例 1: Sequential Thinking
    await example_sequential_thinking()
    
    # 示例 2: Context7
    await example_context7()
    
    # 示例 3: 集成使用
    await example_integrated_review()
    
    print("\n" + "=" * 60)
    print("✓ 所有示例执行完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
