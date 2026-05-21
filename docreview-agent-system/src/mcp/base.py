"""MCP 基类和公共功能"""
import asyncio
import logging
import subprocess
import signal
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Union
from datetime import datetime

logger = logging.getLogger(__name__)

# 支持两种进程类型：subprocess.Popen 和 asyncio.subprocess.Process
ProcessType = Union[subprocess.Popen, asyncio.subprocess.Process]

@dataclass
class MCPProcess:
    """MCP 进程信息"""
    process: ProcessType
    start_time: datetime
    command: List[str]
    cwd: Optional[str] = None

class MCPError(Exception):
    """MCP 相关错误基类"""
    def __init__(self, message: str, error_code: str = "DOCREVIEW_ERR_MCP_001"):
        self.message = message
        self.error_code = error_code
        super().__init__(message)

class MCPTimeoutError(MCPError):
    """MCP 调用超时"""
    def __init__(self, message: str = "MCP 调用超时"):
        super().__init__(message, "DOCREVIEW_ERR_MCP_001")

class MCPConnectionError(MCPError):
    """MCP 连接错误"""
    def __init__(self, message: str = "MCP 服务连接失败"):
        super().__init__(message, "DOCREVIEW_ERR_MCP_001")

class MCPResponseError(MCPError):
    """MCP 响应格式错误"""
    def __init__(self, message: str = "MCP 响应格式异常"):
        super().__init__(message, "DOCREVIEW_ERR_MCP_003")

class BaseMCPClient(ABC):
    """MCP 客户端基类"""
    
    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 3,
        retry_delays: List[int] = None
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delays = retry_delays or [1, 2, 4]
        self.processes: Dict[str, MCPProcess] = {}
        self.logger = logging.getLogger(self.__class__.__name__)
        self._degraded = False
    
    @property
    def is_degraded(self) -> bool:
        """MCP 服务是否处于降级模式"""
        return self._degraded
    
    @abstractmethod
    async def start(self) -> bool:
        """启动 MCP 服务"""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """停止 MCP 服务"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        pass
    
    async def _send_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """发送 JSON-RPC 请求（由子类实现具体协议）"""
        raise NotImplementedError
    
    async def _execute_with_retry(
        self,
        func,
        *args,
        **kwargs
    ) -> Any:
        """带重试的执行"""
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                last_error = MCPTimeoutError(f"MCP 调用超时（尝试 {attempt + 1}/{self.max_retries}）")
                self.logger.warning(f"MCP 调用超时: {last_error}")
            except MCPError as e:
                last_error = e
                self.logger.warning(f"MCP 调用失败: {e}")
            
            if attempt < self.max_retries - 1:
                delay = self.retry_delays[min(attempt, len(self.retry_delays) - 1)]
                self.logger.info(f"等待 {delay}s 后重试...")
                await asyncio.sleep(delay)
        
        self._degraded = True
        raise last_error
    
    async def _terminate_process(self, name: str, timeout: int = 5) -> None:
        """安全终止进程"""
        if name not in self.processes:
            return
        
        proc_info = self.processes[name]
        proc = proc_info.process
        
        try:
            # 判断进程类型
            if isinstance(proc, asyncio.subprocess.Process):
                # asyncio 子进程
                if proc.returncode is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=timeout)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
            else:
                # subprocess.Popen
                if proc.poll() is None:  # 进程仍在运行
                    proc.terminate()
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
            
            del self.processes[name]
            self.logger.info(f"MCP 进程 {name} 已终止")
        except Exception as e:
            self.logger.error(f"终止 MCP 进程 {name} 失败: {e}")
            # 即使失败也尝试从字典中移除
            if name in self.processes:
                del self.processes[name]
    
    async def _cleanup_all(self) -> None:
        """清理所有 MCP 进程"""
        for name in list(self.processes.keys()):
            try:
                await self._terminate_process(name, timeout=5)
            except Exception as e:
                self.logger.error(f"清理 MCP 进程 {name} 失败: {e}")
