"""LangGraph 工作流 - 审查工作流

定义完整的多智能体审查流程，包括：
- initialize: 初始化状态
- load_document: 加载文档
- generate_spec: 生成规格
- docreview: 执行审查
- evaluate_result: 评估结果
- revise_spec: 修订规格
- user_approval: 用户确认
- execute: 执行任务
- finalize: 清理和保存

用法:
    workflow = build_workflow(supervisor, docreview_agent)
    result = await workflow.ainvoke(initial_state)
"""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
from typing import Any, Dict, Literal, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from ..agents.docreview import DocReviewAgent
from ..agents.supervisor import SupervisorAgent
from ..config import AppConfig
from ..mcp.context7 import Context7Client
from ..mcp.sequential_thinking import SequentialThinkingClient
from ..schemas.models import AgentState
from ..tools.reading import ReadingTool
from ..tools.terminal import TerminalTool
from ..tools.web_search import WebSearchTool

logger = logging.getLogger(__name__)

DATA_DIR = "data"
CHECKPOINT_DB = f"{DATA_DIR}/checkpoints.db"
CHECKPOINT_BACKUP = f"{DATA_DIR}/checkpoints.db.bak"


def _ensure_checkpoint_dir() -> None:
    """确保 checkpoint 目录存在"""
    os.makedirs(DATA_DIR, exist_ok=True)


def _backup_and_recreate_checkpoint() -> SqliteSaver:
    """备份损坏的 checkpoint 并重建新的

    当检测到 checkpoint 数据库损坏时，自动备份旧文件并创建新的数据库。

    Returns:
        新的 SqliteSaver 实例
    """
    _ensure_checkpoint_dir()

    if os.path.exists(CHECKPOINT_DB):
        try:
            shutil.copy2(CHECKPOINT_DB, CHECKPOINT_BACKUP)
            logger.info(f"已备份损坏的 checkpoint 到 {CHECKPOINT_BACKUP}")
        except Exception as e:
            logger.warning(f"备份 checkpoint 失败: {e}")

        try:
            os.remove(CHECKPOINT_DB)
            logger.info("已删除损坏的 checkpoint 文件")
        except Exception as e:
            logger.warning(f"删除 checkpoint 失败: {e}")

    return SqliteSaver.from_conn_string(CHECKPOINT_DB)


def _create_checkpointer() -> SqliteSaver:
    """创建 checkpointer，检测损坏并自动恢复

    Returns:
        SqliteSaver 实例
    """
    _ensure_checkpoint_dir()

    if not os.path.exists(CHECKPOINT_DB):
        return SqliteSaver.from_conn_string(CHECKPOINT_DB)

    try:
        checkpointer = SqliteSaver.from_conn_string(CHECKPOINT_DB)
        return checkpointer
    except Exception as e:
        logger.warning(f"Checkpoint 数据库损坏，尝试恢复: {e}")
        return _backup_and_recreate_checkpoint()


async def initialize(state: AgentState) -> AgentState:
    """初始化工作流状态

    - 加载配置
    - 创建必要目录
    - 设置默认值
    - MCP 服务健康检查

    Args:
        state: 当前工作流状态

    Returns:
        更新后的状态
    """
    config = AppConfig()
    logger.info("初始化工作流状态")

    state["max_iterations"] = state.get("max_iterations", config.agent_behavior.max_review_iterations)
    state["stagnation_count"] = 0
    state["stagnation_threshold"] = config.agent_behavior.stagnation_threshold
    state["iteration_count"] = state.get("iteration_count", 0)
    state["review_reports"] = state.get("review_reports", [])
    state["review_conclusion"] = "pending"
    state["review_conclusion_data"] = None
    state["user_approved"] = state.get("user_approved", False)
    state["awaiting_approval"] = False
    state["approval_timed_out"] = False
    state["execution_status"] = "pending"
    state["error_code"] = None
    state["error_message"] = None
    state["mcp_degraded"] = False
    state["issue_tracker"] = state.get("issue_tracker") or {
        "all_issues": [],
        "fixed_count": 0,
        "partially_fixed_count": 0,
        "unfixed_count": 0,
        "new_in_current_round": []
    }
    state["spec_snapshot"] = ""
    state["total_llm_cost"] = state.get("total_llm_cost", 0.0)

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs("reviews", exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    try:
        proc = await asyncio.create_subprocess_exec(
            "npx", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
        logger.info("Node.js 可用，MCP 服务可用")
    except (FileNotFoundError, asyncio.TimeoutError):
        logger.warning("Node.js 未安装或不可用，MCP 服务将被禁用")
        state["mcp_degraded"] = True

    return state


def route_after_initialize(state: AgentState) -> Literal["load_document", "generate_spec"]:
    """initialize 后的条件路由

    - 若有 document_path 则加载文档
    - 否则直接生成规格

    Args:
        state: 当前工作流状态

    Returns:
        下一节点名称
    """
    if state.get("document_path"):
        return "load_document"
    return "generate_spec"


async def load_document(state: AgentState) -> AgentState:
    """加载文档内容

    使用 ReadingTool 读取指定路径的文档

    Args:
        state: 当前工作流状态

    Returns:
        更新后的状态，包含 document_content
    """
    reading_tool = ReadingTool()
    path = state.get("document_path", "")

    if not path:
        state["error_code"] = "DOCREVIEW_ERR_DOC_001"
        state["error_message"] = "文档路径为空"
        logger.error("文档路径为空")
        return state

    result = reading_tool.read_file(path)

    if result.success:
        state["document_content"] = result.data.get("content", "")
        logger.info(f"文档已加载: {path} ({len(state['document_content'])} chars)")
    else:
        state["error_code"] = "DOCREVIEW_ERR_DOC_001"
        state["error_message"] = f"文档加载失败: {result.error}"
        logger.error(f"文档加载失败: {result.error}")

    return state


def user_approval(state: AgentState) -> AgentState:
    """用户确认节点（中断点）

    LangGraph 将在此节点中断，等待用户输入

    Args:
        state: 当前工作流状态

    Returns:
        当前状态
    """
    logger.info("等待用户确认")
    return state


async def evaluate_result(state: AgentState) -> AgentState:
    """评估审查结果

    - 读取 review_conclusion_data 判定 Pass/Fail
    - 检测停滞
    - 保存规格快照
    - 执行历史压缩

    Args:
        state: 当前工作流状态

    Returns:
        更新后的状态
    """
    data = state.get("review_conclusion_data")
    conclusion = "Fail"
    if data:
        conclusion = data.get("review_conclusion", "Fail")

    state["review_conclusion"] = conclusion

    if _is_stagnant(state):
        state["stagnation_count"] = state.get("stagnation_count", 0) + 1
    else:
        state["stagnation_count"] = 0

    state["spec_snapshot"] = state.get("specification", "")

    _prune_review_history(state)

    config = AppConfig()
    max_cost = config.agent_behavior.max_cost_per_task
    if max_cost > 0 and state.get("total_llm_cost", 0) > max_cost:
        state["error_code"] = "DOCREVIEW_ERR_LLM_008"
        state["error_message"] = f"LLM API 成本超预算: ${state['total_llm_cost']:.4f}"

    logger.info(
        f"审查评估: conclusion={conclusion}, "
        f"stagnation={state['stagnation_count']}"
    )

    return state


def route_after_evaluate(state: AgentState) -> Literal["user_approval", "revise_spec", "finalize"]:
    """evaluate_result 后的条件路由

    Args:
        state: 当前工作流状态

    Returns:
        下一节点名称
    """
    conclusion = state.get("review_conclusion", "Fail")
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", 10)
    stagnation_count = state.get("stagnation_count", 0)
    stagnation_threshold = state.get("stagnation_threshold", 2)

    if iteration_count >= max_iterations:
        logger.info("达到最大迭代次数，强制终止")
        return "finalize"

    if stagnation_count >= stagnation_threshold:
        logger.info("检测到停滞，强制终止")
        return "finalize"

    if conclusion in ("Pass", "Conditional Pass"):
        state["awaiting_approval"] = True
        return "user_approval"

    return "revise_spec"


def user_approval(state: AgentState) -> AgentState:
    """用户确认节点（中断点）

    LangGraph 将在此节点中断，等待用户输入

    Args:
        state: 当前工作流状态

    Returns:
        当前状态
    """
    logger.info("等待用户确认")
    return state


def route_after_approval(state: AgentState) -> Literal["execute", "revise_spec", "finalize"]:
    """user_approval 后的条件路由

    Args:
        state: 当前工作流状态

    Returns:
        下一节点名称
    """
    if state.get("approval_timed_out"):
        state["error_code"] = "DOCREVIEW_ERR_LOOP_003"
        return "finalize"

    if state.get("user_approved"):
        return "execute"

    conclusion = state.get("review_conclusion", "")

    if conclusion == "Conditional Pass":
        return "revise_spec"

    return "finalize"


async def execute(state: AgentState) -> AgentState:
    """执行实际任务

    调用 SupervisorAgent.execute_task

    Args:
        state: 当前工作流状态

    Returns:
        更新后的状态
    """
    logger.info("执行任务")
    state["execution_status"] = "running"
    return state


async def finalize(state: AgentState) -> AgentState:
    """保存结果并清理资源

    - 保存审查历史
    - 输出摘要
    - 清理 MCP 进程

    Args:
        state: 当前工作流状态

    Returns:
        更新后的状态
    """
    logger.info("执行清理和保存")

    if state.get("review_reports"):
        _save_review_history(state)

    state["execution_status"] = "completed"

    _print_summary(state)

    return state


def _is_stagnant(state: AgentState) -> bool:
    """检测审查问题列表是否停滞（连续两轮无变化）

    通过比较最近两轮的问题 ID 集合来判断是否停滞。

    Args:
        state: 当前工作流状态

    Returns:
        是否停滞
    """
    reports = state.get("review_reports", [])
    if len(reports) < 2:
        return False

    this_issues = {i["issue_id"] for i in reports[-1].get("issues", [])}
    prev_issues = {i["issue_id"] for i in reports[-2].get("issues", [])}

    return this_issues == prev_issues


def _prune_review_history(state: AgentState) -> None:
    """Token 累积管理：对 3 轮前的审查报告执行摘要压缩

    保留策略：
    - 最近 2 轮：完整保留
    - 第 3 轮及更早：替换为单行摘要

    Args:
        state: 当前工作流状态
    """
    reports = state.get("review_reports", [])
    if len(reports) <= 2:
        return

    for i in range(len(reports) - 2):
        r = reports[i]
        blk = sum(1 for j in r.get("issues", []) if j.get("severity") == "Blocking")
        hi = sum(1 for j in r.get("issues", []) if j.get("severity") == "High")
        md = sum(1 for j in r.get("issues", []) if j.get("severity") == "Medium")
        lo = sum(1 for j in r.get("issues", []) if j.get("severity") == "Low")

        r["issues"] = []
        r["review_summary"] = f"{r.get('review_conclusion', 'Unknown')} | {blk}B/{hi}H/{md}M/{lo}L"

    logger.debug("审查历史已压缩")


def _save_review_history(state: AgentState) -> None:
    """序列化审查历史到磁盘

    Args:
        state: 当前工作流状态
    """
    try:
        thread_id = f"review-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        output = {
            "thread_id": thread_id,
            "spec_version": state.get("spec_version", 1),
            "review_conclusion": state.get("review_conclusion", "unknown"),
            "total_llm_cost": state.get("total_llm_cost", 0),
            "reports": state.get("review_reports", [])
        }

        os.makedirs("reviews", exist_ok=True)
        output_path = f"reviews/history-{thread_id}.json"

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        logger.info(f"审查历史已保存: {output_path}")

    except Exception as e:
        logger.error(f"保存审查历史失败: {e}")


def _print_summary(state: AgentState) -> None:
    """输出审查摘要到 stdout

    Args:
        state: 当前工作流状态
    """
    conclusion = state.get("review_conclusion", "unknown")
    iteration = state.get("iteration_count", 0)

    total_issues = 0
    for report in state.get("review_reports", []):
        total_issues += len(report.get("issues", []))

    summary = f"""
========================================
DocReview 审查完成摘要
========================================
审查结论: {conclusion}
迭代轮次: {iteration}
发现问题: {total_issues}
LLM 成本: ${state.get('total_llm_cost', 0):.4f}
========================================
"""
    print(summary)


def build_workflow(
    supervisor: SupervisorAgent,
    docreview_agent: DocReviewAgent
) -> StateGraph:
    """构建审查工作流图

    Args:
        supervisor: SupervisorAgent 实例
        docreview_agent: DocReviewAgent 实例

    Returns:
        编译后的 StateGraph
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("initialize", initialize)
    workflow.add_node("load_document", load_document)
    workflow.add_node("generate_spec", supervisor.generate_spec)
    workflow.add_node("docreview", docreview_agent.review)
    workflow.add_node("evaluate_result", evaluate_result)
    workflow.add_node("revise_spec", supervisor.revise_spec)
    workflow.add_node("user_approval", user_approval)
    workflow.add_node("execute", supervisor.execute_task)
    workflow.add_node("finalize", finalize)

    workflow.set_entry_point("initialize")

    workflow.add_conditional_edges(
        "initialize",
        route_after_initialize,
        {
            "load_document": "load_document",
            "generate_spec": "generate_spec"
        }
    )

    workflow.add_edge("load_document", "generate_spec")
    workflow.add_edge("generate_spec", "docreview")
    workflow.add_edge("docreview", "evaluate_result")
    workflow.add_edge("revise_spec", "docreview")
    workflow.add_edge("execute", "finalize")

    workflow.add_conditional_edges(
        "evaluate_result",
        route_after_evaluate,
        {
            "user_approval": "user_approval",
            "revise_spec": "revise_spec",
            "finalize": "finalize"
        }
    )

    workflow.add_conditional_edges(
        "user_approval",
        route_after_approval,
        {
            "execute": "execute",
            "revise_spec": "revise_spec",
            "finalize": "finalize"
        }
    )

    workflow.add_edge("finalize", END)

    checkpointer = _create_checkpointer()

    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=["user_approval"]
    )


async def create_workflow_runtime(
    config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """创建工作流运行时环境

    初始化所有必要的组件并返回工作流实例

    Args:
        config: 可选的配置字典

    Returns:
        包含 workflow、agents、tools 等的字典
    """
    app_config = AppConfig()

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        logger.error("请安装 langchain-openai: pip install langchain-openai")
        raise

    llm = ChatOpenAI(
        model=app_config.llm.model,
        api_key=app_config.llm.api_key,
        base_url=app_config.llm.base_url or None,
        temperature=app_config.llm.temperature,
        request_timeout=app_config.llm.request_timeout
    )

    seq_thinking = SequentialThinkingClient(
        timeout=app_config.mcp.call_timeout
    )
    context7 = Context7Client(
        timeout=app_config.mcp.call_timeout
    )

    reading_tool = ReadingTool(workspace_dir=str(app_config.system.workspace_dir))
    terminal_tool = TerminalTool()
    web_search_tool = WebSearchTool()

    supervisor = SupervisorAgent(
        llm=llm,
        tools=[reading_tool, terminal_tool, web_search_tool]
    )

    docreview_agent = DocReviewAgent(
        llm=llm,
        sequential_thinking=seq_thinking,
        context7=context7,
        tools=[reading_tool, web_search_tool]
    )

    workflow = build_workflow(supervisor, docreview_agent)

    return {
        "workflow": workflow,
        "supervisor": supervisor,
        "docreview_agent": docreview_agent,
        "llm": llm,
        "seq_thinking": seq_thinking,
        "context7": context7,
        "config": app_config
    }


async def run_review_workflow(
    initial_state: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None
) -> AgentState:
    """运行审查工作流的便捷函数

    Args:
        initial_state: 初始状态
        config: 可选配置

    Returns:
        最终状态
    """
    runtime = await create_workflow_runtime(config)

    if initial_state is None:
        from ..state.agent_state import create_initial_state
        initial_state = create_initial_state()

    final_state = await runtime["workflow"].ainvoke(initial_state)

    return final_state


async def run_review_workflow_with_interrupts(
    initial_state: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """运行审查工作流，支持用户中断点

    在 user_approval 节点会暂停，等待用户确认后继续。
    适合需要用户在审查通过后手动确认的场景。

    Args:
        initial_state: 初始状态
        config: 可选配置

    Returns:
        包含 state 和 runtime 的字典
    """
    runtime = await create_workflow_runtime(config)

    if initial_state is None:
        from ..state.agent_state import create_initial_state
        initial_state = create_initial_state()

    current_state = initial_state

    async for event in runtime["workflow"].astream(initial_state):
        current_state = event
        if runtime["workflow"].is_interrupted(current_state):
            logger.info("工作流在 user_approval 节点中断，等待用户确认")
            break

    return {
        "state": current_state,
        "runtime": runtime,
        "is_interrupted": runtime["workflow"].is_interrupted(current_state)
    }
