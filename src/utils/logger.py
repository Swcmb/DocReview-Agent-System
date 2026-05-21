"""日志工具模块 / Logging Utility Module

提供结构化日志配置和管理功能，支持多级别日志记录和控制台/文件双输出。
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from rich.logging import RichHandler

from src.config import get_config


class StructuredLogger:
    """结构化日志记录器 / Structured Logger

    提供统一的日志接口，支持富文本输出和日志文件轮转。
    """

    _loggers: dict[str, logging.Logger] = {}

    def __init__(self, name: str) -> None:
        """初始化日志记录器 / Initialize Logger

        Args:
            name: 日志记录器名称
        """
        self.name = name
        self._logger: Optional[logging.Logger] = None

    @property
    def logger(self) -> logging.Logger:
        """获取或创建日志记录器实例 / Get or Create Logger Instance

        Returns:
            logging.Logger: 日志记录器实例
        """
        if self._logger is not None:
            return self._logger

        if self.name in self._loggers:
            self._logger = self._loggers[self.name]
            return self._logger

        config = get_config()

        self._logger = logging.getLogger(self.name)
        self._logger.setLevel(getattr(logging, config.system.log_level))

        if not self._logger.handlers:
            self._setup_handlers()

        self._loggers[self.name] = self._logger
        return self._logger

    def _setup_handlers(self) -> None:
        """设置日志处理器 / Setup Log Handlers

        配置控制台输出（使用 Rich）和文件输出（支持轮转）。
        """
        config = get_config()
        rich_handler = RichHandler(
            rich_tracebacks=True,
            tracebacks_show_locals=True,
            show_time=True,
            show_path=False
        )
        rich_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(message)s",
            datefmt="[%Y-%m-%d %H:%M:%S]"
        )
        rich_handler.setFormatter(formatter)
        self._logger.addHandler(rich_handler)

        log_file = config.system.log_file
        if log_file.parent != Path("."):
            log_file.parent.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=config.system.log_max_bytes,
            backupCount=config.system.log_backup_count,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_formatter)
        self._logger.addHandler(file_handler)

    def debug(self, message: str, **kwargs) -> None:
        """记录调试级别日志 / Log Debug Level Message"""
        self.logger.debug(message, **kwargs)

    def info(self, message: str, **kwargs) -> None:
        """记录信息级别日志 / Log Info Level Message"""
        self.logger.info(message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        """记录警告级别日志 / Log Warning Level Message"""
        self.logger.warning(message, **kwargs)

    def error(self, message: str, **kwargs) -> None:
        """记录错误级别日志 / Log Error Level Message"""
        self.logger.error(message, **kwargs)

    def critical(self, message: str, **kwargs) -> None:
        """记录严重级别日志 / Log Critical Level Message"""
        self.logger.critical(message, **kwargs)

    def exception(self, message: str, **kwargs) -> None:
        """记录异常信息（自动包含堆栈跟踪）/ Log Exception with Stack Trace"""
        self.logger.exception(message, **kwargs)


def get_logger(name: str) -> StructuredLogger:
    """获取结构化日志记录器 / Get Structured Logger

    Args:
        name: 日志记录器名称

    Returns:
        StructuredLogger: 结构化日志记录器实例
    """
    return StructuredLogger(name)


def configure_root_logger() -> None:
    """配置根日志记录器 / Configure Root Logger

    配置应用程序根日志记录器，确保所有模块日志正确传播。
    """
    config = get_config()
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.system.log_level))

    if not root_logger.handlers:
        handler = RichHandler(
            rich_tracebacks=True,
            tracebacks_show_locals=True
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger.addHandler(handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
