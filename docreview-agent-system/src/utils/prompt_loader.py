"""提示词加载工具模块 / Prompt Loader Utility Module

提供提示词文件的加载和管理功能，支持模板变量替换和缓存。
"""

import os
from pathlib import Path
from typing import Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


class PromptLoaderError(Exception):
    """提示词加载异常 / Prompt Loader Error"""
    pass


class PromptFileNotFoundError(PromptLoaderError):
    """提示词文件未找到异常 / Prompt File Not Found Error"""
    pass


class PromptLoader:
    """提示词加载器 / Prompt Loader

    负责从文件系统加载提示词模板，支持变量替换和缓存。
    """

    DEFAULT_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

    def __init__(self, prompts_dir: Optional[Path] = None) -> None:
        """初始化提示词加载器 / Initialize Prompt Loader

        Args:
            prompts_dir: 提示词文件目录，默认为项目 prompts 目录
        """
        self.prompts_dir = prompts_dir or self.DEFAULT_PROMPTS_DIR
        self._cache: dict[str, str] = {}

    def load(self, filename: str, use_cache: bool = True) -> str:
        """加载提示词文件 / Load Prompt File

        Args:
            filename: 提示词文件名
            use_cache: 是否使用缓存

        Returns:
            str: 提示词内容

        Raises:
            PromptFileNotFoundError: 文件不存在时抛出
        """
        if use_cache and filename in self._cache:
            logger.debug(f"从缓存加载提示词: {filename}")
            return self._cache[filename]

        file_path = self.prompts_dir / filename

        try:
            content = file_path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            logger.error(f"提示词文件未找到: {file_path}")
            raise PromptFileNotFoundError(
                f"提示词文件不存在: {filename}，路径: {file_path}"
            ) from e
        except OSError as e:
            logger.error(f"读取提示词文件失败: {file_path}, 错误: {e}")
            raise PromptLoaderError(f"读取提示词文件失败: {e}") from e

        if use_cache:
            self._cache[filename] = content
            logger.debug(f"缓存提示词文件: {filename}")

        logger.info(f"成功加载提示词文件: {filename}")
        return content

    def load_with_variables(
        self,
        filename: str,
        variables: dict[str, Any],
        use_cache: bool = True
    ) -> str:
        """加载提示词文件并替换变量 / Load Prompt File with Variable Substitution

        Args:
            filename: 提示词文件名
            variables: 变量字典
            use_cache: 是否使用缓存

        Returns:
            str: 替换变量后的提示词内容
        """
        template = self.load(filename, use_cache=use_cache)

        try:
            result = template.format(**variables)
            logger.debug(f"成功替换 {len(variables)} 个变量: {filename}")
            return result
        except KeyError as e:
            logger.error(f"变量替换失败，缺少变量: {e}")
            raise PromptLoaderError(f"提示词模板缺少变量: {e}") from e

    def clear_cache(self) -> None:
        """清除提示词缓存 / Clear Prompt Cache"""
        self._cache.clear()
        logger.info("已清除提示词缓存")

    def reload(self, filename: str) -> str:
        """重新加载提示词文件（绕过缓存）/ Reload Prompt File (Bypass Cache)

        Args:
            filename: 提示词文件名

        Returns:
            str: 提示词内容
        """
        if filename in self._cache:
            del self._cache[filename]
        return self.load(filename, use_cache=True)


_prompt_loader: Optional[PromptLoader] = None


def get_prompt_loader() -> PromptLoader:
    """获取提示词加载器单例 / Get Prompt Loader Singleton

    Returns:
        PromptLoader: 提示词加载器实例
    """
    global _prompt_loader
    if _prompt_loader is None:
        prompts_dir = os.environ.get("PROMPTS_DIR")
        _prompt_loader = PromptLoader(
            prompts_dir=Path(prompts_dir) if prompts_dir else None
        )
    return _prompt_loader


def load_prompt(filename: str, use_cache: bool = True) -> str:
    """快捷函数：加载提示词文件 / Convenience Function: Load Prompt File

    Args:
        filename: 提示词文件名
        use_cache: 是否使用缓存

    Returns:
        str: 提示词内容
    """
    return get_prompt_loader().load(filename, use_cache=use_cache)


def load_prompt_with_variables(
    filename: str,
    variables: dict[str, Any],
    use_cache: bool = True
) -> str:
    """快捷函数：加载提示词文件并替换变量 / Convenience Function: Load Prompt with Variables

    Args:
        filename: 提示词文件名
        variables: 变量字典
        use_cache: 是否使用缓存

    Returns:
        str: 替换变量后的提示词内容
    """
    return get_prompt_loader().load_with_variables(
        filename,
        variables,
        use_cache=use_cache
    )
