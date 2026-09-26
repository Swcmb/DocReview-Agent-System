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
import logging
import os
import shutil
import subprocess
from typing import Any, Dict, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from ..agents.docreview import DocReviewAgent
from ..agents.supervisor import SupervisorAgent
from ..config import AppConfig
from ..decisions.factory import (
    await_thread,
    configure_provider,
    get_decision_engine,
)
from ..mcp.context7 import Context7Client
from ..mcp.sequential_thinking import SequentialThinkingClient
from ..schemas.models import AgentState
from ..state.history_store import new_thread_id, save_review_history
from ..state.issue_fingerprint import fingerprint_set
from .review_routing import (
    has_unresolved_blocking as has_unresolved_blocking,
)
from .review_routing import (
    BUDGET_ERROR_CODE,
    route_after_approval,
    route_after_evaluate,
    route_after_initialize,
)
from .review_routing import (
    route_after_generate_spec as route_after_generate_spec,
    route_after_load_document as route_after_load_document,
    route_after_revise_spec as route_after_revise_spec,
)
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

    # §12.2：thread_id 在入口**只生成一次**并写回 state，后续 finalize 复用。
    # 旧实现每次落盘都按秒重算 ID，同一秒内两次落盘会互相覆盖。
    # 用 new_thread_id() 而非裸时间戳：CLI 入口与本节点都会用到它，必须是同一
    # 套生成规则；随机后缀进一步避免同秒并发启动的两次审查撞同一个 ID。
    if not state.get("thread_id"):
        state["thread_id"] = new_thread_id()

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
    total_cost = state.get("total_llm_cost", 0.0) or 0.0
    if max_cost > 0 and total_cost > max_cost:
        # §11.3：超预算必须同时置 execution_status="failed" 并写 error/message，
        # 再由 route_after_evaluate 走 finalize——该错误优先于 max/停滞/结论。
        # 只写 error_code 而不动 execution_status，会让 CLI 摘要仍把这次审查
        # 报成「已完成」，掩盖真正的失败原因。
        state["error_code"] = BUDGET_ERROR_CODE
        state["error_message"] = f"LLM API 成本超预算: ${total_cost:.4f} > ${max_cost:.2f}"
        state["execution_status"] = "failed"

    logger.info(
        f"审查评估: conclusion={conclusion}, "
        f"stagnation={state['stagnation_count']}"
    )

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

    通过比较最近两轮问题的内容身份集合来判断是否停滞。指纹由
    `src/state/issue_fingerprint.py` 唯一定义（§3.3／§10.4）——本模块
    不再内联计算，避免两套语义漂移。

    Args:
        state: 当前工作流状态

    Returns:
        是否停滞
    """
    reports = state.get("review_reports", [])
    if len(reports) < 2:
        return False

    this_issues = fingerprint_set(reports[-1].get("issues", []))
    prev_issues = fingerprint_set(reports[-2].get("issues", []))

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
    """序列化审查历史到磁盘（T-16：委托给 `history_store`）。

    落盘协议由 `src/state/history_store.py` 承担：thread_id 只生成一次、
    `specification_snapshots` 去重、per-thread ``O_EXCL`` 锁、原子替换。

    这里刻意**吞掉**落盘异常：history 写失败不应让一次已完成的审查整体崩掉。
    库函数 `save_review_history()` 本身按 §12.2 显式抛出（锁超时／legacy 碰撞），
    需要感知失败的调用方与测试直接调它。
    """
    try:
        output_path = save_review_history(state)
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
    # 决策层文档级原语（§13.1）。两者都在审查开始前对 spec 施加标注，故串在
    # generate_spec 与 docreview 之间；engine 为 None 时各自首行返回、零差异。
    workflow.add_node("screen", docreview_agent.screen_spec)
    workflow.add_node("assess", docreview_agent.assess_spec)
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
    workflow.add_edge("generate_spec", "screen")
    workflow.add_edge("screen", "assess")
    workflow.add_edge("assess", "docreview")
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


#: Laya 源 checkout 路径（规格 §20 固定的探测目标）。权重不入库，editable 安装的源
#: 才是运行时身份的依据。
LAYA_SOURCE_ROOT = r"D:\DocReviewer\laya-github"


def _probe_laya_source() -> tuple[str, bool, str]:
    """探测 Laya 源身份，返回 ``(runtime_commit, source_tree_clean, runtime_source_digest)``。

    §8.3：三者任一不可信，engine 只能 audit/degraded，**不得 act**。故本函数
    fail-closed——git 不可用、路径不存在、HEAD 非 40 位 SHA、tracked ``laya/``
    有未提交改动，一律返回 ``("", False, "")``，绝不猜测或放宽阈值。

    只探测 tracked 的包路径 ``laya``：untracked 的 ``.claude`` 等与运行时无关，
    把它们计入会让一个干净的 checkout 永久显示为 dirty。
    """
    try:
        commit = subprocess.run(
            ["git", "-C", LAYA_SOURCE_ROOT, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        if len(commit) != 40:
            return "", False, ""
        dirty = subprocess.run(
            ["git", "-C", LAYA_SOURCE_ROOT, "status", "--porcelain", "--", "laya"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "", False, ""
    # digest 直接取 HEAD：§8.3 的 runtime_source_digest 标识「跑的是哪份源码」，
    # 而 commit 已是该内容的唯一标识，另算一份 hash 只会引入第二处真相。
    return commit, (not dirty), commit


def _load_guard_questions() -> dict[str, Any]:
    """惰性取 laya preset 的 guard questions；取不到返回空表（fail-closed）。

    guard 原语的 question **不在** §5.2 的冻结契约内（其 noul 无 ``criteria`` 键），
    只能来自 laya 自己的 preset。取不到时退化为空表 → screen 只产 uncertain。
    """
    try:
        presets = __import__("laya.presets", fromlist=["guard_questions"])
        return dict(presets.guard_questions())
    except Exception:  # noqa: BLE001 - 取不到就退化为空表，不冒泡
        return {}


def _build_decision_provider(app_config: AppConfig) -> Any:
    """构造决策层 provider 工厂；**未启用时返回 None**（§15 F5）。

    返回 None 而非一个 ``NullDecisionEngine`` 是刻意的：未启用时决策层根本不该
    存在，工厂据此给出 degraded handle（``engine=None``），
    ``DocReviewAgent._run_decision_layer`` 首行即返回，业务快照与禁用态零差异。
    若注入 Null 实例，「决策层没跑」与「决策层跑了但恒 uncertain」就变得不可区分。
    """
    laya_config = getattr(app_config, "laya", None)
    if laya_config is None or not getattr(laya_config, "enabled", False):
        return None

    def _factory(*, timeout_seconds: int = 60) -> Any:
        # 重量级模块放工厂内导入：未启用时它们根本不被加载（T-03 导入边界）。
        from ..decisions.laya_adapter import create_router, predict_batch
        from ..decisions.laya_decisions import LayaDecisionEngine, RuntimeProvenance

        commit, clean, digest = _probe_laya_source()
        router = create_router(laya_config)

        def _predict(batch: Any) -> Any:
            return predict_batch(router, batch, laya_config.batch_size)

        return LayaDecisionEngine(
            laya_config,
            provenance=RuntimeProvenance(
                laya_runtime_commit=commit,
                source_tree_clean=clean,
                runtime_source_digest=digest,
                requested_device=str(getattr(laya_config, "device", "cpu")),
                actual_device=str(getattr(laya_config, "device", "cpu")),
            ),
            predict=_predict,
            guard_questions=_load_guard_questions(),
            # §0.2：本轮无人工校准语料 → 空阈值表 → 五原语恒 uncertain。
            # 这是刻意的：宁可不产出业务效果，也不产出未校准的结论。
            thresholds={},
            # §8.3：spec 内容哈希缺失即禁止 act。engine 构造期无从得知被评审的
            # 规格内容，故留空；本轮接线只做 audit。
            spec_content_sha256=None,
        )

    return _factory


def resolve_runtime_config(config: Any = None) -> AppConfig:
    """把 ``create_workflow_runtime`` 的 ``config`` 参数解析成 :class:`AppConfig`。

    §12.3 要求的兼容契约：

    - ``None``：按现状构造 ``AppConfig()``（从环境读）。**刻意不改成
      ``get_config()`` 单例**——那会改变「同进程内多次调用读到不同配置」这一
      既有行为，超出本任务范围。
    - ``AppConfig``：使用其**深拷贝**。CLI 的 ``--laya/--no-laya`` 正是靠这条
      路径把开关送进决策层；不拷贝的话调用方的对象会被我们改掉，而它在别处
      还要继续使用。
    - ``dict``：旧调用方传的是 LangGraph 的 ``configurable`` dict，从来没有被
      本函数消费过。为保持向后兼容，这里**继续忽略**它，而不是把它误当配置。
    """
    if isinstance(config, AppConfig):
        return config.model_copy(deep=True)
    return AppConfig()


async def create_workflow_runtime(
    config: Any | None = None
) -> Dict[str, Any]:
    """创建工作流运行时环境

    初始化所有必要的组件并返回工作流实例

    Args:
        config: 可选的 :class:`AppConfig`；传 ``dict`` 会被忽略（旧调用方兼容）

    Returns:
        包含 workflow、agents、tools 等的字典
    """
    app_config = resolve_runtime_config(config)

    # ── 决策层接线（T-17b）────────────────────────────────────────────
    # 未启用时 `_build_decision_provider` 返回 None，`configure_provider(None)`
    # 显式清空 provider：factory 是进程级单例，不清会让上一次启用留下的 engine
    # 泄漏到本次「禁用」的运行里，直接违反 F5 的零差异契约。
    configure_provider(_build_decision_provider(app_config))
    # engine 构造会加载权重，必须经 to_thread，否则阻塞事件循环。
    decision_handle = await await_thread(
        get_decision_engine,
        timeout_seconds=getattr(app_config.laya, "timeout_seconds", 60),
    )
    decision_engine = decision_handle.engine
    if decision_handle.is_degraded:
        logger.info(
            f"决策层不可用（{decision_handle.degraded_reason}）：审查按纯 LLM 路径进行"
        )

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
        tools=[reading_tool, web_search_tool],
        decision_engine=decision_engine
    )

    workflow = build_workflow(supervisor, docreview_agent)

    return {
        "workflow": workflow,
        "supervisor": supervisor,
        "docreview_agent": docreview_agent,
        "llm": llm,
        "seq_thinking": seq_thinking,
        "context7": context7,
        "decision_engine": decision_engine,
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
