"""网络搜索工具模块 / Web Search Tool Module

提供网页搜索、URL获取、API验证、依赖检测等联网功能的工具。
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

from src.tools.base import BaseTool, ToolResult


@dataclass
class SearchResult:
    """搜索结果 / Search Result"""
    title: str
    url: str
    snippet: str


@dataclass
class ApiValidationResult:
    """API 验证结果 / API Validation Result"""
    api_name: str
    available: bool
    response_time_ms: float
    status_code: Optional[int]
    error: Optional[str]


class WebSearchTool(BaseTool):
    """联网搜索工具类 / Web Search Tool Class

    提供网页搜索、URL内容获取、API验证、依赖检测等功能。
    """

    def __init__(self, timeout: int = 30):
        """初始化联网搜索工具 / Initialize Web Search Tool

        Args:
            timeout: 请求超时时间（秒）
        """
        super().__init__(
            name="web_search",
            description="网页搜索、URL内容获取、API验证、依赖检测"
        )
        self.timeout = timeout
        self.search_api = None  # 预留搜索 API

    def execute(self, operation: str, **kwargs) -> ToolResult:
        """执行联网搜索操作 / Execute Web Search Operation

        Args:
            operation: 操作类型（search, fetch_url, validate_api, check_dependencies）
            **kwargs: 操作参数

        Returns:
            ToolResult: 执行结果
        """
        try:
            if operation == "search":
                return self.search(kwargs.get("query", ""), kwargs.get("num_results", 5))
            elif operation == "fetch_url":
                return self.fetch_url(kwargs.get("url", ""))
            elif operation == "validate_api":
                return self.validate_api(kwargs.get("api_spec", ""))
            elif operation == "check_dependencies":
                return self.check_dependencies(kwargs.get("project_path", ""))
            else:
                return self._create_error_result(f"未知操作: {operation}")
        except Exception as e:
            self.logger.error(f"执行联网搜索操作失败: {e}")
            return self._create_error_result(str(e))

    def search(self, query: str, num_results: int = 5) -> ToolResult:
        """搜索网页 / Search Web

        Args:
            query: 搜索关键词
            num_results: 返回结果数量

        Returns:
            ToolResult: 包含搜索结果列表
        """
        try:
            # 使用 DuckDuckGo HTML 搜索（无需 API key）
            results = self._duckduckgo_search(query, num_results)
            return self._create_success_result(
                data={
                    "results": [
                        {"title": r.title, "url": r.url, "snippet": r.snippet}
                        for r in results
                    ],
                    "count": len(results),
                    "query": query
                }
            )
        except Exception as e:
            self.logger.error(f"搜索失败: {e}")
            return self._create_error_result(f"搜索失败: {str(e)}")

    def _duckduckgo_search(self, query: str, num_results: int) -> List[SearchResult]:
        """使用 DuckDuckGo HTML 搜索（无需 API key）/ DuckDuckGo HTML Search

        Args:
            query: 搜索查询
            num_results: 结果数量

        Returns:
            List[SearchResult]: 搜索结果列表
        """
        encoded_query = urllib.parse.quote(query)
        url = f"https://duckduckgo.com/html/?q={encoded_query}"

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )

        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            html = response.read().decode("utf-8")

        # 解析搜索结果
        results = []
        pattern = r'<a class="result__a" href="([^"]+)"[^>]*>([^<]+)</a>'
        snippet_pattern = r'<a class="result__a"[^>]*>[^<]+</a>\s*<p class="result__snippet">([^<]+)</p>'

        matches = re.findall(pattern, html)
        snippet_matches = re.findall(snippet_pattern, html)
        snippets_dict = {m[0]: m[1] for m in snippet_matches}

        for i, (url, title) in enumerate(matches[:num_results]):
            snippet = snippets_dict.get(url, "")
            results.append(SearchResult(title=title, url=url, snippet=snippet))

        return results

    def fetch_url(self, url: str) -> ToolResult:
        """获取 URL 内容并转换为 Markdown / Fetch URL and Convert to Markdown

        Args:
            url: 目标 URL

        Returns:
            ToolResult: 包含 Markdown 格式的内容
        """
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"}
            )

            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                html = response.read()

                if "text/html" in content_type:
                    text = self._html_to_markdown(html.decode("utf-8", errors="replace"))
                else:
                    text = html.decode("utf-8", errors="replace")

            return self._create_success_result(
                data={
                    "content": text,
                    "url": url,
                    "content_type": content_type
                }
            )
        except Exception as e:
            return self._create_error_result(f"获取 URL 内容失败: {str(e)}")

    def _html_to_markdown(self, html: str) -> str:
        """简单的 HTML 到 Markdown 转换 / Simple HTML to Markdown Conversion

        Args:
            html: HTML 内容

        Returns:
            str: Markdown 格式文本
        """
        text = html

        # 移除 script 和 style 标签
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)

        # 转换标题
        text = re.sub(r'<h1[^>]*>(.*?)</h1>', r'# \1\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'## \1\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<h3[^>]*>(.*?)</h3>', r'### \1\n', text, flags=re.IGNORECASE)

        # 转换段落
        text = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', text, flags=re.DOTALL | re.IGNORECASE)

        # 转换链接
        text = re.sub(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.IGNORECASE)

        # 移除剩余标签
        text = re.sub(r'<[^>]+>', '', text)

        # 清理空白
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()

    def validate_api(self, api_spec: str) -> ToolResult:
        """验证第三方 API 可用性 / Validate Third-party API Availability

        Args:
            api_spec: API 规格（URL 或 OpenAPI 规范）

        Returns:
            ToolResult: 包含验证结果
        """
        try:
            # 解析 API 规格
            if api_spec.startswith("http"):
                url = api_spec
            else:
                return self._create_error_result("无效的 API 规格格式")

            start_time = time.time()
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    status_code = response.status
                    response_time_ms = (time.time() - start_time) * 1000

                    result = ApiValidationResult(
                        api_name=url,
                        available=True,
                        response_time_ms=response_time_ms,
                        status_code=status_code,
                        error=None
                    )
            except urllib.error.HTTPError as e:
                result = ApiValidationResult(
                    api_name=url,
                    available=e.code < 500,
                    response_time_ms=(time.time() - start_time) * 1000,
                    status_code=e.code,
                    error=str(e)
                )

            return self._create_success_result(
                data={
                    "api_name": result.api_name,
                    "available": result.available,
                    "response_time_ms": result.response_time_ms,
                    "status_code": result.status_code,
                    "error": result.error
                }
            )
        except Exception as e:
            return self._create_error_result(f"API 验证失败: {str(e)}")

    def check_dependencies(self, project_path: str) -> ToolResult:
        """检测项目依赖的过时版本 / Check Outdated Dependencies

        Args:
            project_path: 项目路径

        Returns:
            ToolResult: 包含过时依赖列表
        """
        try:
            import os
            outdated = []

            # 检查 pyproject.toml / requirements.txt
            pyproject_path = os.path.join(project_path, "pyproject.toml")
            if os.path.exists(pyproject_path):
                with open(pyproject_path) as f:
                    content = f.read()
                    # 简单解析依赖（实际应使用 pip-licenses 或 pip-audit）
                    deps = re.findall(r'"([^"]+)"', content)
                    outdated.append({
                        "file": "pyproject.toml",
                        "note": "请使用 pip check 或 pip-audit 检查更新"
                    })

            # 检查 package.json
            package_json_path = os.path.join(project_path, "package.json")
            if os.path.exists(package_json_path):
                with open(package_json_path) as f:
                    data = json.load(f)
                    deps = data.get("dependencies", {})
                    outdated.append({
                        "file": "package.json",
                        "dependencies": list(deps.keys()),
                        "note": "请使用 npm outdated 检查更新"
                    })

            return self._create_success_result(
                data={
                    "outdated": outdated,
                    "count": len(outdated)
                }
            )
        except Exception as e:
            return self._create_error_result(f"检测依赖失败: {str(e)}")
