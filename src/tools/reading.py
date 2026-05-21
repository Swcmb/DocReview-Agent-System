"""文档读取工具模块 / Document Reading Tool Module

提供文件读取、搜索、目录列表、版本对比等功能的工具。
"""

import difflib
import os
import re
from pathlib import Path
from typing import List, Optional

from src.tools.base import BaseTool, ToolResult


class ReadingTool(BaseTool):
    """阅读工具类 / Reading Tool Class

    提供文件操作功能，包括读取、搜索、列表、版本对比等。
    支持多种编码，限制文件大小，防止路径遍历攻击。
    """

    def __init__(self, workspace_dir: str = "./"):
        """初始化阅读工具 / Initialize Reading Tool

        Args:
            workspace_dir: 工作目录路径，所有文件操作限制在此目录下
        """
        super().__init__(
            name="reading",
            description="读取文件、搜索内容、列出目录、版本对比"
        )
        self.workspace_dir = Path(workspace_dir).resolve()
        self.max_file_size = 10 * 1024 * 1024  # 10MB 限制
        self.allowed_encodings = ["utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"]

    def execute(self, operation: str, **kwargs) -> ToolResult:
        """执行阅读操作 / Execute Reading Operation

        Args:
            operation: 操作类型（read_file, search_text, list_directory, compare_versions）
            **kwargs: 操作参数

        Returns:
            ToolResult: 执行结果
        """
        try:
            if operation == "read_file":
                return self.read_file(kwargs.get("path", ""), kwargs.get("encoding", "utf-8"))
            elif operation == "search_text":
                return self.search_text(kwargs.get("content", ""), kwargs.get("pattern", ""))
            elif operation == "list_directory":
                return self.list_directory(kwargs.get("path", ""), kwargs.get("pattern", "*"))
            elif operation == "compare_versions":
                return self.compare_versions(kwargs.get("v1", ""), kwargs.get("v2", ""))
            else:
                return self._create_error_result(
                    f"未知操作: {operation}",
                    error_code="DOCREVIEW_ERR_TOOL_001"
                )
        except Exception as e:
            self.logger.error(f"执行阅读操作失败: {e}")
            return self._create_error_result(str(e))

    def read_file(self, path: str, encoding: str = "utf-8") -> ToolResult:
        """读取文件内容 / Read File Content

        Args:
            path: 文件路径
            encoding: 文件编码

        Returns:
            ToolResult: 包含文件内容的成功结果，或错误信息
        """
        # 路径安全校验
        if not self._is_path_safe(path):
            return self._create_error_result(
                f"路径安全校验失败: {path}",
                error_code="DOCREVIEW_ERR_TOOL_002"
            )

        full_path = self.workspace_dir / path if not Path(path).is_absolute() else Path(path)

        # 检查文件是否存在
        if not full_path.exists():
            return self._create_error_result(f"文件不存在: {path}")

        # 检查是否为文件
        if not full_path.is_file():
            return self._create_error_result(f"路径不是文件: {path}")

        # 检查文件大小
        file_size = full_path.stat().st_size
        if file_size > self.max_file_size:
            return self._create_error_result(
                f"文件超过大小限制 ({self.max_file_size} bytes): {file_size} bytes"
            )

        # 尝试读取文件
        for enc in [encoding] + [e for e in self.allowed_encodings if e != encoding]:
            try:
                with open(full_path, "r", encoding=enc) as f:
                    content = f.read()
                return self._create_success_result(
                    data={
                        "content": content,
                        "path": str(full_path),
                        "encoding": enc,
                        "size": file_size
                    }
                )
            except UnicodeDecodeError:
                continue
            except Exception as e:
                return self._create_error_result(f"读取文件失败: {str(e)}")

        return self._create_error_result(f"无法使用支持的编码读取文件: {path}")

    def search_text(self, content: str, pattern: str) -> ToolResult:
        """在文本中搜索正则表达式 / Search Text with Regex

        Args:
            content: 要搜索的文本内容
            pattern: 正则表达式模式

        Returns:
            ToolResult: 包含匹配结果的列表
        """
        try:
            regex = re.compile(pattern)
            matches = []
            for i, line in enumerate(content.split("\n"), 1):
                for match in regex.finditer(line):
                    matches.append({
                        "line_number": i,
                        "line_content": line,
                        "match_start": match.start(),
                        "match_end": match.end(),
                        "matched_text": match.group()
                    })
            return self._create_success_result(
                data={"matches": matches, "count": len(matches)}
            )
        except re.error as e:
            return self._create_error_result(f"正则表达式错误: {str(e)}")

    def list_directory(self, path: str = "", pattern: str = "*") -> ToolResult:
        """列出目录内容 / List Directory Contents

        Args:
            path: 目录路径（相对于工作目录）
            pattern: 文件名匹配模式

        Returns:
            ToolResult: 包含文件和目录列表
        """
        target_dir = self.workspace_dir / path if path else self.workspace_dir

        if not self._is_path_safe(str(target_dir)):
            return self._create_error_result("路径安全校验失败")

        if not target_dir.exists():
            return self._create_error_result(f"目录不存在: {path}")

        if not target_dir.is_dir():
            return self._create_error_result(f"路径不是目录: {path}")

        try:
            items = []
            for item in target_dir.glob(pattern):
                items.append({
                    "name": item.name,
                    "path": str(item.relative_to(self.workspace_dir)),
                    "is_file": item.is_file(),
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else None
                })
            return self._create_success_result(
                data={
                    "items": items,
                    "count": len(items),
                    "path": str(target_dir)
                }
            )
        except Exception as e:
            return self._create_error_result(f"列出目录失败: {str(e)}")

    def compare_versions(self, v1: str, v2: str) -> ToolResult:
        """生成两个版本文档的 unified diff / Generate Unified Diff

        Args:
            v1: 版本1内容
            v2: 版本2内容

        Returns:
            ToolResult: 包含 unified diff 格式的差异
        """
        try:
            v1_lines = v1.splitlines(keepends=True)
            v2_lines = v2.splitlines(keepends=True)
            diff = difflib.unified_diff(
                v1_lines, v2_lines,
                fromfile="v1", tofile="v2",
                lineterm=""
            )
            diff_text = "".join(diff)
            return self._create_success_result(
                data={"diff": diff_text, "has_changes": bool(diff_text)}
            )
        except Exception as e:
            return self._create_error_result(f"生成 diff 失败: {str(e)}")

    def _is_path_safe(self, path: str) -> bool:
        """检查路径是否在工作目录范围内 / Check Path Safety

        防止路径遍历攻击，确保文件操作不会超出工作目录。

        Args:
            path: 待检查的路径

        Returns:
            bool: 路径是否安全
        """
        try:
            full_path = self.workspace_dir / path if not Path(path).is_absolute() else Path(path)
            resolved = full_path.resolve()
            return str(resolved).startswith(str(self.workspace_dir))
        except Exception:
            return False
