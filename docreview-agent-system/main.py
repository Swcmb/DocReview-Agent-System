#!/usr/bin/env python3
"""DocReview CLI - 文档审查系统命令行入口

用法：
    docreview review --doc-path PATH [--task TEXT] [--max-iterations N]
    docreview generate-spec --task TEXT [--spec-output PATH]
    docreview status [--thread-id ID]
    docreview resume --thread-id ID
"""
import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.config import AppConfig
from src.state.agent_state import create_initial_state
from src.workflows.review_workflow import create_workflow_runtime

app = typer.Typer(
    name="docreview",
    help="DocReview Agent System - 多智能体文档审查工具",
    add_completion=False
)

console = Console()

EXIT_SUCCESS = 0
EXIT_REVIEW_FAILED = 1
EXIT_SYSTEM_ERROR = 2
EXIT_USER_ABORT = 3
EXIT_INVALID_ARGS = 4


def _configure_logging(verbose: bool = False) -> None:
    """配置日志系统

    Args:
        verbose: 是否启用 DEBUG 级别日志
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    log_file = Path("logs/app.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(log_file, encoding="utf-8")
        ]
    )


def _check_api_key() -> bool:
    """检查 LLM API Key 是否已配置

    Returns:
        True 如果 API Key 已配置，否则 False
    """
    config = AppConfig()
    api_key = getattr(config.llm, 'api_key', '') or os.environ.get('LLM_API_KEY', '')
    return bool(api_key)


def _print_env_warning() -> None:
    """打印环境配置警告"""
    console.print("[red]错误：未配置 LLM_API_KEY，请先配置环境变量[/red]")
    console.print("[yellow]提示：复制 .env.example 为 .env 并填入 API 密钥[/yellow]")


@app.callback()
def main_callback(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="启用详细日志"),
    config_path: Optional[str] = typer.Option(None, "--config", help="配置文件路径")
):
    """全局回调 - 配置日志和加载配置"""
    _configure_logging(verbose)

    if config_path:
        console.print(f"[green]配置已加载: {config_path}[/green]")


@app.command()
def review(
    doc_path: Optional[str] = typer.Option(None, "--doc-path", help="待审查文档路径"),
    task: Optional[str] = typer.Option(None, "--task", help="任务描述"),
    max_iterations: int = typer.Option(10, "--max-iterations", help="最大审查迭代次数"),
    output_dir: str = typer.Option("./reviews/", "--output-dir", help="审查报告输出目录"),
    spec_output: Optional[str] = typer.Option(None, "--spec-output", help="规格文档输出路径"),
    no_mcp: bool = typer.Option(False, "--no-mcp", help="禁用 MCP 服务"),
    model: Optional[str] = typer.Option(None, "--model", help="覆盖 LLM 模型")
):
    """审查指定文档或根据任务生成规格并审查"""

    if not doc_path and not task:
        console.print("[red]错误：必须提供 --doc-path 或 --task[/red]")
        raise typer.Exit(code=EXIT_INVALID_ARGS)

    if not _check_api_key():
        _print_env_warning()
        raise typer.Exit(code=EXIT_INVALID_ARGS)

    os.makedirs(output_dir, exist_ok=True)

    async def run_review():
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                state = create_initial_state()
                state["user_task"] = task or ""
                state["document_path"] = doc_path
                state["max_iterations"] = max_iterations

                if no_mcp:
                    state["mcp_degraded"] = True

                progress.add_task("[cyan]初始化工作流...", total=None)

                runtime = await create_workflow_runtime()

                progress.add_task("[cyan]执行审查...", total=None)

                thread_id = f"review-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                config = {"configurable": {"thread_id": thread_id}}

                result = await runtime["workflow"].ainvoke(state, config)

                conclusion = result.get("review_conclusion", "unknown")
                iteration = result.get("iteration_count", 0)
                total_cost = result.get("total_llm_cost", 0.0)

                console.print(f"\n[bold]审查结论:[/bold] {conclusion}")
                console.print(f"[bold]迭代轮次:[/bold] {iteration}")
                console.print(f"[bold]LLM 成本:[/bold] ${total_cost:.4f}")

                if spec_output and result.get("specification"):
                    os.makedirs(os.path.dirname(spec_output) or ".", exist_ok=True)
                    with open(spec_output, "w", encoding="utf-8") as f:
                        f.write(result["specification"])
                    console.print(f"[green]规格文档已保存: {spec_output}[/green]")

                if conclusion in ("Pass", "Conditional Pass"):
                    return EXIT_SUCCESS
                else:
                    return EXIT_REVIEW_FAILED

        except KeyboardInterrupt:
            console.print("\n[yellow]用户中断[/yellow]")
            return EXIT_USER_ABORT
        except Exception as e:
            console.print(f"[red]系统错误: {e}[/red]")
            logging.error(f"审查失败: {e}", exc_info=True)
            return EXIT_SYSTEM_ERROR

    exit_code = asyncio.run(run_review())
    raise typer.Exit(code=exit_code)


@app.command()
def generate_spec(
    task: str = typer.Argument(..., help="任务描述"),
    spec_output: str = typer.Option("./docs/specification.md", "--spec-output", help="规格文档输出路径"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="启用详细日志")
):
    """根据任务描述生成规格文档"""

    console.print(f"[bold]生成规格文档:[/bold] {task}")

    if not _check_api_key():
        _print_env_warning()
        raise typer.Exit(code=EXIT_INVALID_ARGS)

    async def run_generate():
        try:
            from langchain_openai import ChatOpenAI
            from src.agents.supervisor import SupervisorAgent

            config = AppConfig()
            llm = ChatOpenAI(
                model=getattr(config.llm, 'model', 'gpt-4o'),
                api_key=getattr(config.llm, 'api_key', ''),
                base_url=getattr(config.llm, 'base_url', None) or None,
                temperature=getattr(config.llm, 'temperature', 0.7)
            )

            supervisor = SupervisorAgent(llm=llm)
            state = create_initial_state()
            state["user_task"] = task

            result_state = await supervisor.generate_spec(state)
            spec = result_state.get("specification", "")

            if not spec:
                console.print("[red]错误：未能生成规格文档[/red]")
                return EXIT_SYSTEM_ERROR

            os.makedirs(os.path.dirname(spec_output) or ".", exist_ok=True)
            with open(spec_output, "w", encoding="utf-8") as f:
                f.write(spec)

            console.print(f"[green]规格文档已保存: {spec_output}[/green]")
            return EXIT_SUCCESS

        except ImportError as e:
            console.print(f"[red]缺少依赖: {e}[/red]")
            console.print("[yellow]提示：安装 langchain-openai: pip install langchain-openai[/yellow]")
            return EXIT_SYSTEM_ERROR
        except Exception as e:
            console.print(f"[red]生成失败: {e}[/red]")
            logging.error(f"生成规格文档失败: {e}", exc_info=True)
            return EXIT_SYSTEM_ERROR

    exit_code = asyncio.run(run_generate())
    raise typer.Exit(code=exit_code)


@app.command()
def status(
    thread_id: Optional[str] = typer.Option(None, "--thread-id", help="审查线程 ID")
):
    """查看审查历史与状态"""

    import json

    reviews_dir = Path("reviews")
    if not reviews_dir.exists():
        console.print("[yellow]暂无审查历史[/yellow]")
        return

    history_files = sorted(
        [f for f in reviews_dir.iterdir() if f.name.startswith("history-") and f.suffix == ".json"],
        reverse=True
    )

    if not history_files:
        console.print("[yellow]暂无审查历史[/yellow]")
        return

    if thread_id:
        target_file = reviews_dir / f"history-{thread_id}.json"
        if target_file.exists():
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                console.print(f"\n[bold]审查详情:[/bold] {thread_id}")
                console.print(f"[cyan]规格版本:[/cyan] {data.get('spec_version', '-')}")
                console.print(f"[cyan]审查结论:[/cyan] {data.get('review_conclusion', '-')}")
                console.print(f"[cyan]LLM 成本:[/cyan] ${data.get('total_llm_cost', 0):.4f}")
                console.print(f"[cyan]报告数量:[/cyan] {len(data.get('reports', []))}")
            except Exception as e:
                console.print(f"[red]读取失败: {e}[/red]")
        else:
            console.print(f"[yellow]未找到线程: {thread_id}[/yellow]")
        return

    table = Table(title="审查历史")
    table.add_column("线程 ID", style="cyan")
    table.add_column("规格版本", style="green")
    table.add_column("审查结论", style="yellow")
    table.add_column("LLM 成本", style="blue")

    for file_path in history_files[:20]:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                tid = data.get("thread_id", file_path.stem.replace("history-", ""))
                spec_version = data.get("spec_version", "-")
                conclusion = data.get("review_conclusion", "-")
                cost = f"${data.get('total_llm_cost', 0):.4f}"
                table.add_row(tid, str(spec_version), conclusion, cost)
        except Exception as e:
            logging.warning(f"读取历史文件失败: {file_path.name}, {e}")

    console.print(table)


@app.command()
def resume(
    thread_id: str = typer.Option(..., "--thread-id", help="审查线程 ID"),
    approve: Optional[bool] = typer.Option(None, "--approve/--reject", help="批准或拒绝执行")
):
    """恢复中断的审查工作流"""

    console.print(f"[bold]恢复审查线程:[/bold] {thread_id}")

    if not _check_api_key():
        _print_env_warning()
        raise typer.Exit(code=EXIT_INVALID_ARGS)

    async def run_resume():
        try:
            runtime = await create_workflow_runtime()

            state = create_initial_state()

            if approve is not None:
                state["user_approved"] = approve

            config = {"configurable": {"thread_id": thread_id}}

            result_state = await runtime["workflow"].ainvoke(state, config)

            conclusion = result_state.get("review_conclusion", "unknown")
            console.print(f"[green]审查完成: {conclusion}[/green]")
            return EXIT_SUCCESS

        except Exception as e:
            console.print(f"[red]恢复失败: {e}[/red]")
            logging.error(f"恢复审查失败: {e}", exc_info=True)
            return EXIT_SYSTEM_ERROR

    exit_code = asyncio.run(run_resume())
    raise typer.Exit(code=exit_code)


if __name__ == "__main__":
    app()
