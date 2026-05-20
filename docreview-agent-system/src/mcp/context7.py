"""Context7 MCP 客户端

Context7 MCP Server: 官方 Node.js 包
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from .base import BaseMCPClient, MCPError, MCPTimeoutError

logger = logging.getLogger(__name__)

@dataclass
class DocResult:
    """文档查询结果"""
    library_id: str
    library_name: str
    snippet: str
    url: Optional[str] = None

@dataclass
class ContextResult:
    """上下文结果"""
    topic: str
    content: str
    sources: List[str]

class Context7Client(BaseMCPClient):
    """Context7 MCP 客户端
    
    用于解析库标识符和查询文档。
    """
    
    SERVER_PACKAGE = "@context7/mcpserver"
    
    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 3
    ):
        super().__init__(timeout, max_retries)
        self._connected = False
        self._cached_library_ids: Dict[str, str] = {}
    
    async def start(self) -> bool:
        """启动 Context7 MCP 服务"""
        try:
            # 检查 npm 是否可用
            proc = await asyncio.create_subprocess_exec(
                "npm", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            
            self._connected = True
            self.logger.info("Context7 MCP 客户端已初始化（本地模式）")
            return True
            
        except FileNotFoundError:
            self.logger.warning("npm 未找到，Context7 将使用本地解析模式")
            self._degraded = True
            return True  # 不算完全失败，可以降级使用
        except Exception as e:
            self.logger.error(f"启动 Context7 MCP 客户端失败: {e}")
            self._degraded = True
            return False
    
    async def stop(self) -> None:
        """停止 MCP 服务"""
        self._connected = False
        await self._cleanup_all()
        self.logger.info("Context7 MCP 客户端已停止")
    
    async def health_check(self) -> bool:
        """健康检查"""
        if self._degraded:
            return False
        return self._connected
    
    async def resolve_library_id(self, name: str) -> str:
        """解析库标识符
        
        Args:
            name: 库/框架名称（如 "LangChain", "React", "Next.js"）
            
        Returns:
            Context7 兼容的库标识符（如 "/langchain/langchain"）
        """
        # 缓存查询
        if name in self._cached_library_ids:
            return self._cached_library_ids[name]
        
        # 常见库的映射
        LIBRARY_MAPPING = {
            "langchain": "/langchain/langchain",
            "openai": "/openai/openai-node",
            "anthropic": "/anthropics/anthropic-sdk-python",
            "react": "/facebook/react",
            "next.js": "/vercel/next.js",
            "nextjs": "/vercel/next.js",
            "vue": "/vuejs/core",
            "pytorch": "/pytorch/pytorch",
            "tensorflow": "/tensorflow/tensorflow",
            "fastapi": "/tiangoto/fastapi",
            "django": "/django/django",
            "flask": "/pallets/flask",
            "node.js": "/nodejs/node",
            "nodejs": "/nodejs/node",
            "typescript": "/microsoft/TypeScript",
            "rust": "/rust-lang/rust",
            "go": "/golang/go",
            "docker": "/moby/moby",
            "kubernetes": "/kubernetes/kubernetes",
        }
        
        normalized = name.lower().strip()
        
        if normalized in LIBRARY_MAPPING:
            library_id = LIBRARY_MAPPING[normalized]
        else:
            # 尝试使用模糊匹配
            library_id = f"/{normalized.replace(' ', '-')}/{name.lower().replace(' ', '-')}"
        
        self._cached_library_ids[name] = library_id
        return library_id
    
    async def query_docs(
        self,
        query: str,
        library_id: Optional[str] = None,
        num_results: int = 5
    ) -> List[DocResult]:
        """查询文档
        
        Args:
            query: 查询内容
            library_id: 库标识符（可选）
            num_results: 返回结果数量
            
        Returns:
            List[DocResult]: 文档查询结果列表
        """
        if library_id:
            # 使用 Context7 API 查询（如果有 API key）
            return await self._query_context7_api(query, library_id, num_results)
        else:
            # 回退到本地解析
            return self._fallback_query(query, num_results)
    
    async def _query_context7_api(
        self,
        query: str,
        library_id: str,
        num_results: int
    ) -> List[DocResult]:
        """通过 Context7 API 查询"""
        try:
            # 实际实现应使用 Context7 MCP Server 或 API
            # 这里使用模拟数据作为示例
            results = [
                DocResult(
                    library_id=library_id,
                    library_name=library_id.split("/")[-1],
                    snippet=f"关于 '{query}' 的文档内容...",
                    url=f"https://docs.example.com/{query}"
                )
                for _ in range(num_results)
            ]
            return results
        except Exception as e:
            self.logger.error(f"Context7 API 查询失败: {e}")
            return self._fallback_query(query, num_results)
    
    def _fallback_query(
        self,
        query: str,
        num_results: int
    ) -> List[DocResult]:
        """本地回退查询"""
        # 当 MCP 不可用时，提供基本的信息
        results = []
        keywords = query.lower().split()
        
        for keyword in keywords[:2]:
            results.append(
                DocResult(
                    library_id=f"/search/{keyword}",
                    library_name=keyword.title(),
                    snippet=f"关于 '{keyword}' 的本地缓存信息。在线查询请确保 MCP 服务可用。",
                    url=None
                )
            )
        
        return results[:num_results]
    
    async def get_context(self, topic: str) -> ContextResult:
        """获取上下文信息
        
        Args:
            topic: 主题
            
        Returns:
            ContextResult: 上下文结果
        """
        # 尝试解析库名
        library_id = await self.resolve_library_id(topic)
        
        # 查询相关文档
        docs = await self.query_docs(topic, library_id, num_results=3)
        
        content = "\n\n".join([
            f"- {doc.snippet}"
            for doc in docs
        ])
        
        sources = [
            doc.url for doc in docs
            if doc.url
        ]
        
        return ContextResult(
            topic=topic,
            content=content or f"关于 '{topic}' 的上下文信息（离线模式）",
            sources=sources
        )
