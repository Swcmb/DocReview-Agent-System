"""Sequential Thinking MCP 客户端

Sequential Thinking MCP Server: npx @modelcontextprotocol/server-sequential-thinking
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from .base import BaseMCPClient, MCPError, MCPTimeoutError

logger = logging.getLogger(__name__)

@dataclass
class ThinkingStep:
    """思维步骤"""
    thought_number: int
    thought: str
    next_thought_needed: bool
    total_thoughts: Optional[int] = None
    timestamp: Optional[str] = None

@dataclass
class ThinkingResult:
    """思维结果"""
    step: ThinkingStep
    chain_length: int

class SequentialThinkingClient(BaseMCPClient):
    """Sequential Thinking MCP 客户端
    
    通过 npx 启动本地 MCP Server，与 DocReview 系统通过标准 MCP JSON-RPC 协议通信。
    """
    
    SERVER_COMMAND = ["npx", "-y", "@modelcontextprotocol/server-sequential-thinking"]
    
    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 3,
        default_thoughts: int = 3
    ):
        super().__init__(timeout, max_retries)
        self.default_thoughts = default_thoughts
        self._stdin_writer: Optional[asyncio.StreamWriter] = None
        self._stdout_reader: Optional[asyncio.StreamReader] = None
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._response_queue: asyncio.Queue = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None
    
    async def start(self) -> bool:
        """启动 Sequential Thinking MCP 服务"""
        try:
            # 检查 npx 是否可用
            proc = await asyncio.create_subprocess_exec(
                "npx", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            
            # 启动 MCP Server 进程
            process = await asyncio.create_subprocess_exec(
                *self.SERVER_COMMAND,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            self.processes["sequential_thinking"] = process
            
            # 设置异步 I/O
            self._stdout_reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(self._stdout_reader)
            await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, process.stdout)
            
            self._stdin_writer = asyncio.StreamWriter(process.stdin, protocol, None, asyncio.get_event_loop())
            
            # 启动响应读取任务
            self._reader_task = asyncio.create_task(self._read_responses())
            
            self.logger.info("Sequential Thinking MCP 服务已启动")
            return True
            
        except FileNotFoundError:
            self.logger.warning("npx 未找到，Sequential Thinking 将降级为纯 LLM 模式")
            self._degraded = True
            return False
        except Exception as e:
            self.logger.error(f"启动 Sequential Thinking MCP 服务失败: {e}")
            self._degraded = True
            return False
    
    async def stop(self) -> None:
        """停止 MCP 服务"""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        
        await self._cleanup_all()
        self.logger.info("Sequential Thinking MCP 服务已停止")
    
    async def health_check(self) -> bool:
        """健康检查"""
        if self._degraded:
            return False
        
        try:
            # 发送 ping 请求
            result = await self._execute_with_retry(self._ping)
            return result is not None
        except Exception:
            return False
    
    async def _ping(self) -> Dict[str, Any]:
        """发送 ping 请求"""
        return await self._send_request("ping", {})
    
    async def think(
        self,
        thought: str,
        thought_number: int,
        total_thoughts: int,
        next_thought_needed: bool = True
    ) -> ThinkingResult:
        """提交一个思维步骤
        
        Args:
            thought: 当前思维内容
            thought_number: 当前思维编号
            total_thoughts: 预计总思维数
            next_thought_needed: 是否需要继续思考
            
        Returns:
            ThinkingResult: 思维结果
        """
        if self._degraded:
            raise MCPError("MCP 服务处于降级模式", "DOCREVIEW_ERR_MCP_001")
        
        params = {
            "thought": thought,
            "thoughtNumber": thought_number,
            "totalThoughts": total_thoughts,
            "nextThoughtNeeded": next_thought_needed
        }
        
        response = await self._execute_with_retry(self._send_request, "thought", params)
        
        return ThinkingResult(
            step=ThinkingStep(
                thought_number=thought_number,
                thought=thought,
                next_thought_needed=next_thought_needed,
                total_thoughts=total_thoughts
            ),
            chain_length=response.get("chainLength", 1)
        )
    
    async def get_chain(self) -> List[ThinkingStep]:
        """获取完整的思维链"""
        if self._degraded:
            return []
        
        try:
            response = await self._execute_with_retry(self._send_request, "getChain", {})
            chain = response.get("chain", [])
            return [
                ThinkingStep(
                    thought_number=s.get("thoughtNumber", i + 1),
                    thought=s.get("thought", ""),
                    next_thought_needed=s.get("nextThoughtNeeded", False),
                    total_thoughts=s.get("totalThoughts"),
                    timestamp=s.get("timestamp")
                )
                for i, s in enumerate(chain)
            ]
        except Exception:
            return []
    
    async def revise_thought(
        self,
        thought_number: int,
        new_thought: str
    ) -> ThinkingResult:
        """修订之前的思维步骤
        
        Args:
            thought_number: 要修订的思维编号
            new_thought: 新的思维内容
        """
        if self._degraded:
            raise MCPError("MCP 服务处于降级模式", "DOCREVIEW_ERR_MCP_001")
        
        params = {
            "thoughtNumber": thought_number,
            "newThought": new_thought
        }
        
        response = await self._execute_with_retry(self._send_request, "reviseThought", params)
        
        return ThinkingResult(
            step=ThinkingStep(
                thought_number=thought_number,
                thought=new_thought,
                next_thought_needed=True,
                total_thoughts=response.get("totalThoughts")
            ),
            chain_length=response.get("chainLength", 1)
        )
    
    async def _send_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """发送 JSON-RPC 请求"""
        request_id = f"{method}_{id(params)}"
        
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {}
        }
        
        future = asyncio.Future()
        self._pending_requests[request_id] = future
        
        try:
            request_json = json.dumps(request) + "\n"
            self._stdin_writer.write(request_json.encode())
            await self._stdin_writer.drain()
            
            response = await asyncio.wait_for(future, timeout=self.timeout)
            return response.get("result", {})
            
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise MCPTimeoutError(f"MCP 请求超时: {method}")
        except Exception as e:
            self._pending_requests.pop(request_id, None)
            raise MCPError(f"MCP 请求失败: {str(e)}")
    
    async def _read_responses(self) -> None:
        """异步读取 MCP 响应"""
        while True:
            try:
                if self._stdout_reader is None:
                    break
                    
                line = await self._stdout_reader.readline()
                if not line:
                    break
                
                try:
                    response = json.loads(line.decode())
                    request_id = response.get("id")
                    if request_id in self._pending_requests:
                        future = self._pending_requests.pop(request_id)
                        if not future.done():
                            future.set_result(response)
                except json.JSONDecodeError:
                    self.logger.warning(f"无法解析 MCP 响应: {line}")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"读取 MCP 响应失败: {e}")
