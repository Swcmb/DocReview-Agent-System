"""终端工具模块 / Terminal Tool Module

提供终端命令执行能力的工具，支持超时、重试、白名单/黑名单安全检查。
"""

import re
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from src.tools.base import BaseTool, ToolResult


@dataclass
class CommandResult:
    """命令执行结果 / Command Execution Result"""
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    timed_out: bool


class TerminalTool(BaseTool):
    """终端工具类 / Terminal Tool Class

    提供安全的命令执行能力，支持超时、重试、危险命令拦截。
    """

    # 危险命令黑名单模式
    DANGEROUS_PATTERNS = [
        r"rm\s+-rf\s+/",           # 递归删除根目录
        r"rm\s+-rf\s+\*",          # 递归删除所有文件
        r":\(\)\{",                 # Fork 炸弹
        r"dd\s+if=.*of=/dev/",     # 直接写入设备
        r"mkfs",                    # 格式化
        r">\s*/etc/passwd",        # 覆写系统文件
    ]

    # 安全命令白名单（如果启用）
    SAFE_COMMANDS = {
        "ls", "cat", "pwd", "echo", "cd", "mkdir", "cp", "mv", "head", "tail",
        "grep", "find", "wc", "sort", "uniq", "diff", "tree", "stat", "sleep"
    }

    def __init__(
        self,
        default_timeout: int = 300,
        enable_whitelist: bool = True,
        cwd: Optional[str] = None
    ):
        """初始化终端工具 / Initialize Terminal Tool

        Args:
            default_timeout: 默认超时时间（秒）
            enable_whitelist: 是否启用命令白名单
            cwd: 工作目录
        """
        super().__init__(
            name="terminal",
            description="执行终端命令"
        )
        self.default_timeout = default_timeout
        self.enable_whitelist = enable_whitelist
        self.cwd = cwd
        self.max_retries = 3
        self.retry_delays = [1, 2, 4]  # 指数退避

    def execute(
        self,
        command: str,
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
        retry: bool = True,
        **kwargs
    ) -> ToolResult:
        """执行终端命令 / Execute Terminal Command

        Args:
            command: 要执行的命令
            timeout: 超时时间（秒）
            cwd: 工作目录
            retry: 是否启用重试

        Returns:
            ToolResult: 包含命令执行结果
        """
        timeout = timeout or self.default_timeout
        work_dir = cwd or self.cwd

        # 安全检查
        is_safe, error = self._check_command_safety(command)
        if not is_safe:
            self.logger.warning(f"命令被拦截: {error}")
            return self._create_error_result(
                error,
                error_code="DOCREVIEW_ERR_TOOL_003"
            )

        # 重试逻辑
        retries = self.max_retries if retry else 0
        last_error = None

        for attempt in range(retries + 1):
            if attempt > 0:
                delay = self.retry_delays[min(attempt - 1, len(self.retry_delays) - 1)]
                self.logger.info(f"重试命令 (尝试 {attempt + 1}/{retries + 1}), 等待 {delay}s")
                time.sleep(delay)

            result = self._execute_command(command, timeout, work_dir)

            if result.exit_code == 0:
                return self._create_success_result(
                    data={
                        "command": command,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "exit_code": result.exit_code,
                        "duration_ms": result.duration_ms,
                        "timed_out": result.timed_out
                    }
                )

            last_error = result.stderr or f"命令执行失败，退出码: {result.exit_code}"

            if result.timed_out:
                return self._create_error_result(
                    f"命令执行超时 ({timeout}s): {command}",
                    error_code="DOCREVIEW_ERR_TOOL_004"
                )

        return self._create_error_result(
            f"命令执行失败: {last_error}",
            error_code="DOCREVIEW_ERR_TOOL_001"
        )

    def _execute_command(
        self,
        command: str,
        timeout: int,
        cwd: Optional[str]
    ) -> CommandResult:
        """实际执行命令 / Execute Command Internally

        Args:
            command: 命令
            timeout: 超时时间
            cwd: 工作目录

        Returns:
            CommandResult: 命令执行结果
        """
        start_time = time.time()
        timed_out = False

        try:
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                text=True
            )

            try:
                stdout, stderr = process.communicate(timeout=timeout)
                exit_code = process.returncode
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                exit_code = -1
                timed_out = True

        except Exception as e:
            stdout, stderr = "", str(e)
            exit_code = -1

        duration_ms = (time.time() - start_time) * 1000

        return CommandResult(
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out
        )

    def _check_command_safety(self, command: str) -> tuple[bool, Optional[str]]:
        """检查命令安全性 / Check Command Safety

        检查命令是否在黑名单或白名单中。

        Args:
            command: 待检查的命令

        Returns:
            tuple[bool, Optional[str]]: (是否安全, 错误信息)
        """
        # 检查危险模式
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"危险命令被拦截: {command}"

        # 如果启用白名单检查
        if self.enable_whitelist:
            first_word = command.strip().split()[0] if command.strip() else ""
            if first_word and first_word not in self.SAFE_COMMANDS:
                # 允许某些常用命令前缀
                if not any(command.strip().startswith(cmd) for cmd in ["python", "node", "npm", "pip", "git", "docker"]):
                    return False, f"命令不在白名单中: {first_word}"

        return True, None
