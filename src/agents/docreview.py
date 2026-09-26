"""DocReview Sub-Agent - 文档审查智能体

执行严格的六步审查流程，输出结构化审查报告和 Pydantic 审查结论。

六步审查流程：
1. 核心闭环提取 - 识别主业务流程、入口点、出口点、断点
2. 一致性检查 - 验证文档内部逻辑、术语、数据一致性
3. 需求原子化 - 分解需求为可独立验证的原子单元
4. 技术可行性 - 评估技术方案可行性和依赖关系
5. 风险检测 - 识别技术、业务、依赖、时间等风险
6. 可执行性审查 - 从开发者视角评估可执行性
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

try:
    from langchain.chat_models import BaseChatModel
except ImportError:
    try:
        from langchain_core.language_models import BaseChatModel
    except ImportError:
        BaseChatModel = Any
from langchain.schema import HumanMessage

from ..decisions.issue_id import assign_issue_ids
from ..decisions.types import DecisionContext
from ..mcp.sequential_thinking import SequentialThinkingClient
from ..schemas.models import (
    SEVERITY_BLOCKING,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SEVERITY_ORDER,
    AgentState,
    IssueStatus,
    ReviewConclusion,
    ReviewReport,
)
from ..tools.base import BaseTool
from ..utils.llm import CostTracker, invoke_with_cost, resolve_cost_model

logger = logging.getLogger(__name__)


@dataclass
class CoreLoopAnalysis:
    """核心闭环分析结果

    存储从规格文档中提取的核心业务流程分析信息。
    """
    flows: list[str] = field(default_factory=list)
    breaks: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    exit_points: list[str] = field(default_factory=list)


@dataclass
class AtomicRequirement:
    """原子化需求

    表示已分解为最小可验证单元的功能需求。
    """
    id: str
    description: str
    priority: str
    acceptance_criteria: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


@dataclass
class TechContext:
    """技术上下文

    通过 Context7 MCP 获取的技术栈相关信息。
    """
    library_name: str
    relevant_docs: list[str] = field(default_factory=list)
    best_practices: list[str] = field(default_factory=list)
    common_pitfalls: list[str] = field(default_factory=list)


ISSUE_TYPES = {
    "CORE_LOOP_BREAK": "CoreProcessBreak",
    "CONSISTENCY": "ConsistencyCheck",
    "REQUIREMENT_INCOMPLETE": "RequirementCompleteness",
    "FEASIBILITY": "TechnicalFeasibility",
    "RISK": "RiskDetection",
    "EXECUTABILITY": "ExecutabilityReview",
}

# severity 词表与展示顺序统一由 `src.schemas.models` 提供，避免两处定义漂移。

# 结构化输出格式规范 — 嵌入所有 LLM 调用中，确保解析器可工作
STRUCTURED_OUTPUT_FORMAT = """
输出格式要求（严格遵循）：
- 问题: [ISSUE] type=<CoreProcessBreak|ConsistencyCheck|RequirementCompleteness|TechnicalFeasibility|RiskDetection|ExecutabilityReview> severity=<Blocking|High|Medium|Low> description=<描述> location=<位置> suggestion=<建议>
- 需求: [FR-N] <描述> priority=<P0|P1|P2>
- 依赖: [DEP] <源> depends_on <目标>
- 验收: [AC-N] covers=<FR-ID列表> criteria=<标准>
- 流程: [FLOW] <流程描述>
- 缺失: [GAP] <缺失描述>

每条信息独占一行。如无对应信息，不输出该类型行。
"""

# 内联审查提示词 — 六步审查方法论
REVIEW_SYSTEM_PROMPT = """You are DocReview, a professional document review agent.

## 六步审查方法论

对给定的规格文档依次执行以下六个审查步骤：

### Step 1: 核心闭环提取 (CoreProcessBreak)
识别文档中的主业务流程，检查入口点、出口点和流程断点。

### Step 2: 一致性检查 (ConsistencyCheck)
检查文档内部逻辑一致性、术语使用一致性、数据引用一致性。

### Step 3: 需求原子化 (RequirementCompleteness)
将需求分解为可独立验证的原子单元，检查功能需求是否都有对应的验收标准。

### Step 4: 技术可行性 (TechnicalFeasibility)
评估技术方案的可行性、依赖关系和技术风险。

### Step 5: 风险检测 (RiskDetection)
识别技术风险、业务风险、依赖风险、时间风险。

### Step 6: 可执行性审查 (ExecutabilityReview)
从开发者视角评估：需求是否可实现、验收标准是否可测试、文档是否足以指导开发。

## 严重级别分类指南
- **Blocking**: 阻塞项目推进，必须修复（如核心流程断点、关键需求缺失）
- **High**: 严重影响质量，强烈建议修复（如技术可行性问题、重大风险）
- **Medium**: 影响质量但有变通方案（如格式不一致、次要需求不完整）
- **Low**: 改进建议，不影响核心功能（如措辞优化、格式美化）

""" + STRUCTURED_OUTPUT_FORMAT


class DocReviewAgent:
    """DocReview Sub-Agent

    核心审查智能体，执行六步审查流程。
    提示词已内联为 REVIEW_SYSTEM_PROMPT 常量，结构化输出格式见 STRUCTURED_OUTPUT_FORMAT。

    Attributes:
        llm: 语言模型实例
        sequential_thinking: Sequential Thinking MCP 客户端（可选）
        tools: 可用工具列表
    """

    def __init__(
        self,
        llm: BaseChatModel,
        sequential_thinking: SequentialThinkingClient | None = None,
        context7: Any = None,
        tools: list[BaseTool] | None = None,
        decision_engine: Any = None,
    ) -> None:
        """初始化 DocReview Agent

        Args:
            llm: 语言模型实例
            sequential_thinking: Sequential Thinking MCP 客户端（可选）
            context7: 保留参数以兼容，实际不再使用
            tools: 可用工具列表
            decision_engine: 决策层引擎（`LayaDecisionEngine` 或 `NullDecisionEngine`）。

                **默认 `None` 即「决策层不存在」**，`review()` 会完全跳过五原语且不写
                任何 `laya_*` 键——这是 §15 F5「禁用时业务快照与 LAYA__ENABLED=false
                完全相同」的实现基础。刻意不默认注入 `NullDecisionEngine`：那会让
                「禁用」与「启用但恒 uncertain」两种情形产生同样的空结果，
                从而无法区分「决策层没跑」与「决策层跑了但没结论」。
        """
        self.llm = llm
        self.sequential_thinking = sequential_thinking
        self.tools = tools or []
        self.decision_engine = decision_engine
        self.logger = logger

    async def review(self, state: AgentState) -> AgentState:
        """执行完整的六步审查流程

        主要入口方法，执行完整的多步审查并更新状态。

        Args:
            state: 当前工作流状态

        Returns:
            更新后的状态，包含 review_reports 和 review_conclusion_data
        """
        spec = state.get("specification", "")
        iteration = state.get("iteration_count", 0) + 1

        self.logger.info(f"DocReview: 开始第 {iteration} 轮审查")

        # §11.3：本轮自建**局部** tracker。刻意不放 self 上——Agent 实例在
        # workflow 生命周期内复用，挂实例字段会让两次并发审查互相污染成本。
        local_tracker = CostTracker()

        try:
            core_loop = await self._extract_core_loop(spec, tracker=local_tracker)

            consistency_issues = await self._check_consistency(
                spec, core_loop, tracker=local_tracker
            )

            tech_context = await self._enrich_context(spec)

            atomize_issues, atomic_reqs = await self._atomize_requirements(
                spec, core_loop, tracker=local_tracker
            )

            feasibility_issues, dep_graph = await self._deduce_feasibility(
                spec, atomic_reqs, tech_context, tracker=local_tracker
            )

            risk_issues = await self._detect_risks(
                spec, dep_graph,
                consistency_issues + atomize_issues + feasibility_issues,
                tracker=local_tracker,
            )

            exec_issues = await self._review_executability(
                spec,
                consistency_issues + atomize_issues + feasibility_issues + risk_issues,
                tracker=local_tracker,
            )

            all_issues = (
                consistency_issues
                + atomize_issues
                + feasibility_issues
                + risk_issues
                + exec_issues
            )

            # §3.3 步骤 2：ID 必须在任何审计/结论/报告之前独立分配完毕。
            # 顺序很重要——verify_issues/verify_resolutions 只消费已分配 ID 的
            # 快照，Markdown 编译器也不再分配。
            assign_issue_ids(all_issues, iteration)

            # 决策层五原语：只写 laya_findings / laya_trace，不改业务字段（§8.1）。
            # 放在 ID 分配之后——verify_issues/verify_resolutions 按 issue_id 关联快照。
            await self._run_decision_layer(state, spec, all_issues, iteration)

            structured_conclusion = self._compile_structured_conclusion(all_issues, spec)

            review_report: ReviewReport = {
                "iteration": iteration,
                "timestamp": self._get_timestamp(),
                "review_conclusion": structured_conclusion.review_conclusion,
                "review_summary": self._generate_summary(structured_conclusion),
                "issues": all_issues,
                "highlights": self._extract_highlights(spec, all_issues),
                "open_questions": self._extract_open_questions(all_issues),
                "next_steps": self._generate_next_steps(structured_conclusion),
            }

            state["review_reports"].append(review_report)
            state["review_conclusion_data"] = structured_conclusion.model_dump(by_alias=False)
            state["review_conclusion"] = structured_conclusion.review_conclusion
            state["iteration_count"] = iteration

            self.logger.info(
                f"审查完成: conclusion={structured_conclusion.review_conclusion}, "
                f"issues={len(all_issues)}"
            )

            return state

        except Exception as e:
            self.logger.error(f"审查过程出错: {e}")
            state["error_code"] = "DOCREVIEW_ERR_SYS_001"
            state["error_message"] = f"审查失败: {str(e)}"
            # 失败轮次同样计入迭代：保证 iteration_count 每轮都前进，
            # 否则 route_after_evaluate 的轮次上限分支永不成立，循环无法终止。
            state["iteration_count"] = iteration
            return state
        finally:
            # 中途中断的轮次同样已经花掉了钱：崩在第 3 步时前两步的 token 是
            # 真实支出。不累计等于给无限重试发免单，预算闸门会形同虚设。
            # 放在 finally 而非 try 尾部，正是为了覆盖 except 路径。
            if local_tracker.request_count:
                state["total_llm_cost"] = (
                    state.get("total_llm_cost", 0.0) + local_tracker.total_cost
                )

    # ── 决策层节点与原语分流（T-17b / §13.1）─────────────────────────
    # screen/assess 作用于**文档**本身，不依赖任何一轮的 issue 快照，故各占一个
    # 图节点；verify_issues/verify_resolutions/judge_convergence 要读上一轮与本轮
    # 的 issue 快照，只能在 review() 拿到 all_issues 之后调用，故留在 review() 内。
    # 这样每个原语只在**唯一**一处被调用——否则同一 guard 会在 trace 里留下两条
    # 记录，审计方无法区分「跑过一次」和「跑了两次」。

    @staticmethod
    def _decision_context(state: AgentState, iteration: int) -> DecisionContext:
        """构造决策层上下文。

        iteration 语义随调用点而变：文档级原语发生在审查轮次之前，恒为 0；
        issue 级原语传本轮轮次。
        """
        return DecisionContext(
            thread_id=state.get("thread_id", "") or "",
            iteration=iteration,
            spec_version=state.get("spec_version", 0),
        )

    @staticmethod
    def _append_decision_output(
        state: AgentState,
        findings: list[dict[str, Any]],
        trace: list[dict[str, Any]],
    ) -> None:
        """跨轮累积 findings / trace。finalize 把这两个键整体写进 history 顶层。"""
        if findings:
            state["laya_findings"] = list(state.get("laya_findings", [])) + findings
        if trace:
            state["laya_trace"] = list(state.get("laya_trace", [])) + trace

    async def screen_spec(self, state: AgentState) -> AgentState:
        """screen 节点：文档级 guard。**只产 warning finding，不拒绝、不改文**（§8.1）。

        F5 契约：`decision_engine is None`（未启用 / 已降级）时**首行即返回**，
        不写任何 `laya_*` 键——「决策层没跑」与「决策层跑了但恒 uncertain」必须可区分，
        前者的业务快照要与 `LAYA__ENABLED=false` 逐字段相同。
        """
        engine = self.decision_engine
        if engine is None:
            return state

        try:
            spec = state.get("specification", "") or ""
            # 文档级原语发生在审查轮次之前，iteration 恒为 0。
            result = await engine.screen_document(
                spec, context=self._decision_context(state, 0)
            )
            self._append_decision_output(
                state,
                list(result.get("findings", [])),
                list(result.get("trace", [])),
            )
        except Exception as e:  # noqa: BLE001 - 增强层不得拖垮业务审查
            self.logger.warning(f"screen 原语失败（不影响审查结果）: {e}")
        return state

    async def assess_spec(self, state: AgentState) -> AgentState:
        """assess 节点：文档级完备度标注。`route_action` 恒 `no_action`（§8.1）。"""
        engine = self.decision_engine
        if engine is None:
            return state

        try:
            spec = state.get("specification", "") or ""
            # section_index 传 None：决策层允许无索引运行（§5 投影纪律），
            # 而让 agent 层依赖 SectionIndex 会把两套分块语义耦合在一起。
            result = await engine.assess_document(
                spec, None, context=self._decision_context(state, 0)
            )
            self._append_decision_output(
                state,
                [dict(w) for w in (result.get("warnings") or [])],
                list(result.get("trace", [])),
            )
        except Exception as e:  # noqa: BLE001 - 增强层不得拖垮业务审查
            self.logger.warning(f"assess 原语失败（不影响审查结果）: {e}")
        return state

    async def _run_decision_layer(
        self,
        state: AgentState,
        spec: str,
        current_issues: list[IssueStatus],
        iteration: int,
    ) -> None:
        """调用 issue 级三原语，把 findings / trace 写入 state。**不改任何业务字段**。

        §8.1 把这三个原语全部限定为标注或 audit-only：`verify_issues` 不删 issue、
        不改 severity；`verify_resolutions` 的 `audit_only` 恒为 True；
        `judge_convergence` 的 `suggested_loop_cap` 恒为 None。故本方法只往
        `laya_findings` / `laya_trace` 写，**绝不**回写 `all_issues` /
        `review_conclusion` / `issue_tracker`。

        F5 契约与 `screen_spec` / `assess_spec` 同源：`decision_engine is None`
        时首行即返回。决策层的任何异常都在此吞掉：它是增强层，挂了不能拖垮业务审查。
        """
        engine = self.decision_engine
        if engine is None:
            return

        try:
            context = self._decision_context(state, iteration)
            section_index = None

            reports = state.get("review_reports", [])
            previous_issues: list[IssueStatus] = (
                list(reports[-1].get("issues", [])) if reports else []
            )
            tracker = state.get("issue_tracker", {})

            counts = {
                "iteration": iteration,
                "previous_issues": len(previous_issues),
                "current_issues": len(current_issues),
            }

            findings: list[dict[str, Any]] = []
            trace: list[dict[str, Any]] = []

            # 1) verify_issues：只加标注，转成 LayaFinding 形状供摘要统一消费。
            verified = await engine.verify_issues(
                current_issues, spec, section_index, context=context
            )
            for item in verified:
                trace.extend(item.get("trace", []))
                findings.append(self._verified_to_finding(item))

            # 2) verify_resolutions：只审计 observed_status，audit_only 恒 True。
            resolutions = await engine.verify_resolutions(
                previous_issues, spec, section_index, context=context
            )
            for item in resolutions:
                trace.extend(item.get("trace", []))

            # 3) judge_convergence：仅两个 noul 可建议 cap。
            convergence = await engine.judge_convergence(
                previous_issues, current_issues, counts, tracker, context=context
            )
            trace.extend(convergence.get("trace", []))

            self._append_decision_output(state, findings, trace)

        except Exception as e:  # noqa: BLE001 - 增强层不得拖垮业务审查
            self.logger.warning(f"决策层调用失败（不影响审查结果）: {e}")

    @staticmethod
    def _verified_to_finding(item: dict[str, Any]) -> dict[str, Any]:
        """把 `VerifiedIssue` 收敛成 `LayaFinding` 形状。

        severity_review 是自由字符串，落到 `FindingSeverity`（info/warning/high）
        之外的值一律降为 `info`——决策层是标注层，不允许它抬高告警等级。
        """
        severity = item.get("severity_review")
        if severity not in ("info", "warning", "high"):
            severity = "info"
        note = item.get("laya_note") or f"grounded={item.get('grounded')}"
        return {
            "finding_id": f"verify-{item.get('issue_id', 'unknown')}",
            "kind": "verify_issue",
            "severity": severity,
            "message": str(note),
            "source": "laya",
            "decision_ids": [str(item.get("issue_id", ""))],
        }

    async def _think_step(
        self,
        step_name: str,
        context: str,
        num_thoughts: int = 2,
        tracker: CostTracker | None = None,
    ) -> str:
        """通过 Sequential Thinking MCP 进行多步推理

        使用 MCP 进行结构化推理，当 MCP 不可用时降级为纯 LLM 推理。

        Args:
            step_name: 步骤名称
            context: 上下文内容
            num_thoughts: 思维迭代次数
            tracker: 局部成本追踪器（§11.3）；仅 LLM 降级路径产生成本，
                MCP 路径不计入美元成本（Laya usage 走 DecisionAudit.usage）

        Returns:
            最终推理结果
        """
        if not self.sequential_thinking or self.sequential_thinking.is_degraded:
            return await self._llm_think(step_name, context, tracker)

        try:
            thoughts = []
            for i in range(num_thoughts):
                result = await self.sequential_thinking.think(
                    thought=f"[{step_name}] {context[:500]}..." if i == 0 else context,
                    thought_number=i + 1,
                    total_thoughts=num_thoughts,
                    next_thought_needed=i < num_thoughts - 1,
                )
                thoughts.append(result.step.thought)

            return "\n".join(thoughts)

        except Exception as e:
            self.logger.warning(f"Sequential Thinking 调用失败，降级为纯 LLM: {e}")
            return await self._llm_think(step_name, context, tracker)

    async def _llm_think(
        self,
        step_name: str,
        context: str,
        tracker: CostTracker | None = None,
    ) -> str:
        """纯 LLM 推理（降级模式）

        Args:
            step_name: 步骤名称
            context: 上下文内容
            tracker: 局部成本追踪器（§11.3），由 review() 自建并逐层传入

        Returns:
            LLM 生成的推理结果
        """
        prompt = f"""{REVIEW_SYSTEM_PROMPT}

请对以下内容进行{step_name}分析：

{context}

{STRUCTURED_OUTPUT_FORMAT}"""
        response, _ = await invoke_with_cost(
            lambda: self.llm.agenerate([HumanMessage(content=prompt)]),
            tracker=tracker if tracker is not None else CostTracker(),
            model=resolve_cost_model(),
        )
        return response.generations[0][0].text.strip()

    async def _enrich_context(self, spec: str) -> TechContext | None:
        """从规格文档提取技术上下文（纯文本分析，不依赖外部服务）

        Args:
            spec: 规格文档内容

        Returns:
            技术上下文对象，未找到技术栈时返回 None
        """
        tech_stack = self._extract_tech_stack(spec)
        if not tech_stack:
            return None

        return TechContext(
            library_name=tech_stack,
            relevant_docs=[],
            best_practices=[],
            common_pitfalls=[],
        )

    def _extract_tech_stack(self, spec: str) -> str:
        """从规格文档提取技术栈

        Args:
            spec: 规格文档内容

        Returns:
            技术栈名称，未找到时返回空字符串
        """
        patterns = [
            r"技术栈[：:]\s*(.+)",
            r"技术选型[：:]\s*(.+)",
            r"技术要求[：:]\s*(.+)",
            r"\b(Python|Node\.js|React|Vue|Go|Rust|Java|TypeScript)\b",
        ]

        for pattern in patterns:
            match = re.search(pattern, spec, re.IGNORECASE)
            if match:
                return match.group(1).strip() if len(match.groups()) > 0 else match.group(0)
        return ""


    async def _extract_core_loop(
        self,
        spec: str,
        tracker: CostTracker | None = None,
    ) -> CoreLoopAnalysis:
        """步骤 1：核心闭环提取

        识别规格文档中的主业务流程、入口点、出口点和潜在断点。

        Args:
            spec: 规格文档内容

        Returns:
            核心闭环分析结果
        """
        self.logger.debug("步骤 1：核心闭环提取")

        thinking_result = await self._think_step(
            "core_loop_extraction",
            f"分析以下规格文档的核心业务流程和闭环完整性：\n\n{spec}",
            tracker=tracker,
        )

        flows = self._extract_list_from_text(thinking_result, ["流程", "process", "flow"])
        breaks = self._extract_list_from_text(thinking_result, ["缺失", "break", "gap"])

        return CoreLoopAnalysis(
            flows=flows[:5],
            breaks=breaks[:5],
            entry_points=["用户发起请求"],
            exit_points=["任务完成"],
        )

    async def _check_consistency(
        self,
        spec: str,
        core_loop: CoreLoopAnalysis,
        tracker: CostTracker | None = None,
    ) -> list[IssueStatus]:
        """步骤 2：一致性检查

        检查文档内部逻辑、术语、数据的一致性。

        Args:
            spec: 规格文档内容
            core_loop: 核心闭环分析结果

        Returns:
            发现的一致性问题列表
        """
        self.logger.debug("步骤 2：一致性检查")

        thinking_result = await self._think_step(
            "consistency_check",
            f"检查以下规格的一致性问题：\n\n{spec}\n\n核心闭环：{core_loop.flows}",
            tracker=tracker,
        )

        issues = self._parse_issues_from_text(
            thinking_result,
            expected_types=[ISSUE_TYPES["CONSISTENCY"]],
            default_severity=SEVERITY_MEDIUM,
        )

        return issues

    async def _atomize_requirements(
        self,
        spec: str,
        core_loop: CoreLoopAnalysis,
        tracker: CostTracker | None = None,
    ) -> tuple[list[IssueStatus], list[AtomicRequirement]]:
        """步骤 3：需求原子化

        将需求分解为可独立验证的原子单元，并识别完整性问题。

        Args:
            spec: 规格文档内容
            core_loop: 核心闭环分析结果

        Returns:
            (完整性问题列表, 原子化需求列表)
        """
        self.logger.debug("步骤 3：需求原子化")

        thinking_result = await self._think_step(
            "requirement_atomization",
            f"分析以下规格的需求完整性和原子化程度：\n\n{spec}\n\n核心闭环：{core_loop.flows}",
            tracker=tracker,
        )

        issues = self._parse_issues_from_text(
            thinking_result,
            expected_types=[ISSUE_TYPES["REQUIREMENT_INCOMPLETE"]],
            default_severity=SEVERITY_MEDIUM,
        )

        atomic_reqs = self._parse_atomic_requirements(thinking_result)

        return issues, atomic_reqs

    async def _deduce_feasibility(
        self,
        spec: str,
        atomic_reqs: list[AtomicRequirement],
        context: TechContext | None,
        tracker: CostTracker | None = None,
    ) -> tuple[list[IssueStatus], dict[str, Any]]:
        """步骤 4：技术可行性推导

        评估技术方案的可行性和依赖关系。

        Args:
            spec: 规格文档内容
            atomic_reqs: 原子化需求列表
            context: 技术上下文

        Returns:
            (可行性问题列表, 依赖关系图)
        """
        self.logger.debug("步骤 4：技术可行性推导")

        context_str = f"\n技术上下文：{context}" if context else ""

        thinking_result = await self._think_step(
            "feasibility_deduction",
            f"分析以下规格的技术可行性和依赖关系：\n\n{spec}{context_str}",
            tracker=tracker,
        )

        issues = self._parse_issues_from_text(
            thinking_result,
            expected_types=[ISSUE_TYPES["FEASIBILITY"]],
            default_severity=SEVERITY_HIGH,
        )

        dep_graph = self._parse_dependency_graph(thinking_result)

        return issues, dep_graph

    async def _detect_risks(
        self,
        spec: str,
        dep_graph: dict[str, Any],
        prev_issues: list[IssueStatus],
        tracker: CostTracker | None = None,
    ) -> list[IssueStatus]:
        """步骤 5：风险检测

        识别技术、业务、依赖关系等方面的潜在风险。

        Args:
            spec: 规格文档内容
            dep_graph: 依赖关系图
            prev_issues: 之前发现的问题

        Returns:
            发现的风险列表
        """
        self.logger.debug("步骤 5：风险检测")

        thinking_result = await self._think_step(
            "risk_detection",
            f"识别以下规格的潜在风险：\n\n{spec}\n\n依赖关系：{dep_graph}\n\n已知问题：{prev_issues}",
            tracker=tracker,
        )

        issues = self._parse_issues_from_text(
            thinking_result,
            expected_types=[ISSUE_TYPES["RISK"]],
            default_severity=SEVERITY_MEDIUM,
        )

        return issues

    async def _review_executability(
        self,
        spec: str,
        all_issues: list[IssueStatus],
        tracker: CostTracker | None = None,
    ) -> list[IssueStatus]:
        """步骤 6：可执行性审查

        从开发者视角评估文档的可执行性。

        Args:
            spec: 规格文档内容
            all_issues: 所有已知问题

        Returns:
            可执行性问题列表
        """
        self.logger.debug("步骤 6：可执行性审查")

        thinking_result = await self._think_step(
            "executability_review",
            f"从开发者视角审查以下规格的可执行性：\n\n{spec}\n\n已知问题：{all_issues}",
            tracker=tracker,
        )

        issues = self._parse_issues_from_text(
            thinking_result,
            expected_types=[ISSUE_TYPES["EXECUTABILITY"]],
            default_severity=SEVERITY_MEDIUM,
        )

        return issues

    def _compile_markdown_report(
        self,
        issues: list[IssueStatus],
        iteration: int,
    ) -> str:
        """生成 Markdown 格式的审查报告

        §3.3 步骤 6：本方法**只渲染**。它不分配 issue_id、不修改 issues、
        不修改 tracker、不写 state——ID 已由 `assign_issue_ids()` 在流程上游
        分配完毕。排序仅决定展示顺序，不再参与 ID 编号。

        Args:
            issues: 已分配 ID 的问题列表
            iteration: 当前迭代轮次（仅用于报告头部信息）

        Returns:
            Markdown 格式的审查报告
        """
        sorted_issues = sorted(
            issues,
            key=lambda x: SEVERITY_ORDER.get(x["severity"], 99),
        )

        report_lines = [
            "## DocReview Review Report",
            "**Review Conclusion**: [待评估]",
            f"**Review Summary**: 本轮审查发现 {len(issues)} 个问题",
            "",
            "### List of Issues Found",
            "",
        ]

        for issue in sorted_issues:
            report_lines.extend([
                f"- **{issue['issue_id']}**",
                f"  - **Severity**: {issue['severity']}",
                f"  - **Issue Type**: {issue['issue_type']}",
                f"  - **Issue Description**: {issue['description']}",
                f"  - **Modification Suggestion**: {issue['suggestion']}",
                f"  - **Relevant Location**: {issue['location']}",
                "",
            ])

        return "\n".join(report_lines)

    def _compile_structured_conclusion(
        self,
        issues: list[IssueStatus],
        spec: str,
    ) -> ReviewConclusion:
        """生成结构化审查结论

        判定规则（严格优先级）：
        1. 存在 Blocking 问题 ≥ 1 → Fail
        2. AC 覆盖率不完整（P0 FR 未全部被 AC 覆盖）→ Fail
        3. 无 Blocking 但存在 High 问题 ≥ 1 → Conditional Pass
        4. 仅有 Medium/Low 问题或零问题 → Pass

        Args:
            issues: 问题列表
            spec: 规格文档内容

        Returns:
            结构化审查结论
        """
        blocking_count = sum(1 for i in issues if i["severity"] == SEVERITY_BLOCKING)
        high_count = sum(1 for i in issues if i["severity"] == SEVERITY_HIGH)
        medium_count = sum(1 for i in issues if i["severity"] == SEVERITY_MEDIUM)
        low_count = sum(1 for i in issues if i["severity"] == SEVERITY_LOW)

        ac_coverage_complete = self._check_ac_coverage(spec, issues)

        if blocking_count > 0 or not ac_coverage_complete:
            conclusion = "Fail"
        elif high_count > 0:
            conclusion = "Conditional Pass"
        else:
            conclusion = "Pass"

        return ReviewConclusion(
            review_conclusion=conclusion,
            blocking_count=blocking_count,
            high_count=high_count,
            medium_count=medium_count,
            low_count=low_count,
            ac_coverage_complete=ac_coverage_complete,
        )

    def _check_ac_coverage(self, spec: str, issues: list[IssueStatus]) -> bool:
        """检查 AC 覆盖率（支持 6 种格式变体）

        验证 P0 功能需求是否被验收标准覆盖。

        支持的 P0 标记格式：
        - **FR-N**(P0)    - FR-N (P0)    - FR-N: P0
        - FR-N - P0       - FR-N [P0]    - **FR-N**: 优先级 P0

        Args:
            spec: 规格文档内容
            issues: 问题列表

        Returns:
            覆盖率是否完整（所有 P0 FR 均有对应 AC）
        """
        # 提取所有 P0 功能需求 ID
        p0_patterns = [
            r"\*\*FR-(\d+)\*\*.*?\(P0\)",
            r"FR-(\d+)\s*\(P0\)",
            r"FR-(\d+)\s*[:：]\s*P0",
            r"FR-(\d+)\s*-\s*P0",
            r"FR-(\d+)\s*\[P0\]",
            r"\*\*FR-(\d+)\*\*.*?优先级.*?P0",
        ]
        p0_frs: set[str] = set()
        for pattern in p0_patterns:
            p0_frs.update(re.findall(pattern, spec, re.IGNORECASE | re.DOTALL))

        # 若无 P0 需求，检查是否有任何 FR
        if not p0_frs:
            any_fr = re.findall(r"FR-(\d+)", spec)
            return bool(any_fr)  # 有 FR 但无 P0 标记，默认通过

        # 提取所有 AC 覆盖的 FR 列表
        ac_frs: set[str] = set()
        # [AC-N] covers=FR-1,FR-2 格式
        for m in re.findall(r"\[AC-\d+\]\s*covers?=(.+?)(?:\s|$)", spec, re.IGNORECASE):
            ac_frs.update(re.findall(r"FR-(\d+)", m))
        # **AC-N** 格式（传统）
        ac_matches = re.findall(r"\*\*AC-(\d+)\*\*", spec)

        # 有 AC 覆盖的 FR 或有 AC 编号即视为有覆盖
        covered = ac_frs or ac_matches
        if not covered:
            return False

        return p0_frs.issubset(ac_frs) if ac_frs else bool(ac_matches)

    def _parse_issues_from_text(
        self,
        text: str,
        expected_types: list[str],
        default_severity: str,
    ) -> list[IssueStatus]:
        """从文本中解析问题列表（双路径策略 + 质量门控）

        优先解析 [ISSUE] 结构化格式，降级到宽松模式匹配。

        Args:
            text: LLM 生成的文本
            expected_types: 预期的问题类型列表
            default_severity: 默认严重级别

        Returns:
            解析出的问题列表（最多 20 条，超出截断并记录警告）
        """
        issues = self._parse_structured_issues(text)
        if not issues:
            issues = self._parse_loose_issues(text, expected_types, default_severity)

        # 质量门控：单次超过 20 条视为误报
        max_issues = 20
        if len(issues) > max_issues:
            self.logger.warning(f"解析到 {len(issues)} 条 issue，截断至 {max_issues} 条（疑似误报）")
            issues = issues[:max_issues]

        return issues

    def _parse_structured_issues(self, text: str) -> list[IssueStatus]:
        """解析 [ISSUE] 结构化格式"""
        pattern = r"\[ISSUE\]\s*type=(\S+)\s*severity=(\S+)\s*description=(.+?)\s*location=(.+?)\s*suggestion=(.+?)(?:\n|$)"
        matches = re.findall(pattern, text, re.IGNORECASE)
        issues = []
        for m in matches:
            issues.append(IssueStatus(
                issue_id="",
                severity=m[1] if m[1] in (SEVERITY_BLOCKING, SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW) else SEVERITY_MEDIUM,
                issue_type=m[0],
                description=m[2].strip()[:200],
                suggestion=m[4].strip(),
                location=m[3].strip(),
                status="open",
            ))
        return issues

    def _parse_loose_issues(
        self,
        text: str,
        expected_types: list[str],
        default_severity: str,
    ) -> list[IssueStatus]:
        """降级：宽松模式匹配"""
        issues = []
        patterns = [
            r"(问题|issue)[\s:：]+(.+?)(?=\n\n|\n$|$)",
            r"(风险|risk)[\s:：]+(.+?)(?=\n\n|\n$|$)",
            r"(缺失|gap|missing)[\s:：]+(.+?)(?=\n\n|\n$|$)",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, re.DOTALL)
            for match in matches:
                issues.append(IssueStatus(
                    issue_id="",
                    severity=default_severity,
                    issue_type=expected_types[0] if expected_types else ISSUE_TYPES["CONSISTENCY"],
                    description=match[1].strip()[:200],
                    suggestion="请根据问题描述进行修订",
                    location="规格文档",
                    status="open",
                ))
        return issues

    def _parse_atomic_requirements(self, text: str) -> list[AtomicRequirement]:
        """从文本中解析原子化需求（[FR-N] 格式 + 宽松匹配）

        Args:
            text: LLM 生成的文本

        Returns:
            原子化需求列表
        """
        reqs = []
        # 结构化格式: [FR-N] description priority=P0
        structured = re.findall(r"\[FR-(\d+)\]\s*(.+?)(?:\s+priority=(P[012]))?(?:\n|$)", text, re.IGNORECASE)
        for m in structured:
            reqs.append(AtomicRequirement(
                id=f"FR-{m[0]}",
                description=m[1].strip(),
                priority=m[2] if m[2] else "P1",
            ))
        # 降级: **FR-N** 格式
        if not reqs:
            loose = re.findall(r"\*\*FR-(\d+)\*\*[：:]*\s*(.+?)(?:\n|$)", text)
            for m in loose:
                reqs.append(AtomicRequirement(
                    id=f"FR-{m[0]}",
                    description=m[1].strip(),
                    priority="P1",
                ))
        return reqs

    def _parse_dependency_graph(self, text: str) -> dict[str, Any]:
        """从文本中解析依赖关系图（[DEP] 格式 + 宽松匹配）

        Args:
            text: LLM 生成的文本

        Returns:
            依赖关系图 {"nodes": [...], "edges": [...]}
        """
        nodes = set()
        edges = []
        # 结构化格式: [DEP] source depends_on target
        structured = re.findall(r"\[DEP\]\s*(\S+)\s+depends_on\s+(\S+)", text, re.IGNORECASE)
        for src, dst in structured:
            nodes.add(src)
            nodes.add(dst)
            edges.append({"from": src, "to": dst})
        # 降级: "依赖"/"depends on" 关键词
        if not edges:
            loose = re.findall(r"(?:依赖|depends?\s+on|requires?)\s*[：:]*\s*(\S+)", text, re.IGNORECASE)
            for dep in loose:
                nodes.add(dep)
                edges.append({"from": "unknown", "to": dep})
        return {"nodes": list(nodes), "edges": edges}

    def _extract_list_from_text(self, text: str, keywords: list[str]) -> list[str]:
        """从文本中提取包含关键词的行

        Args:
            text: 输入文本
            keywords: 关键词列表

        Returns:
            匹配的行列表
        """
        lines = text.split("\n")
        result = []
        for line in lines:
            if any(kw.lower() in line.lower() for kw in keywords):
                cleaned = line.strip()
                if cleaned:
                    result.append(cleaned)
        return result

    def _extract_highlights(
        self,
        spec: str,
        issues: list[IssueStatus],
    ) -> list[str]:
        """提取文档亮点

        识别文档中的优点和良好实践。

        Args:
            spec: 规格文档内容
            issues: 问题列表

        Returns:
            亮点列表
        """
        highlights = []

        if "# " in spec and "## " in spec:
            highlights.append("文档结构清晰，章节层次分明")

        if "验收标准" in spec or "Acceptance Criteria" in spec:
            highlights.append("包含完整的验收标准")

        if "**FR-" in spec and "**AC-" in spec:
            highlights.append("具有可追溯的需求和验收标准编号")

        if "背景" in spec or "Background" in spec:
            highlights.append("包含背景说明")

        return highlights

    def _extract_open_questions(self, issues: list[IssueStatus]) -> list[str]:
        """提取开放问题

        从问题列表中识别需要进一步澄清的问题。

        Args:
            issues: 问题列表

        Returns:
            开放问题列表
        """
        open_qs = []
        for issue in issues:
            if "?" in issue["description"] or "待定" in issue["description"]:
                open_qs.append(issue["description"][:100])
        return open_qs

    def _generate_summary(self, conclusion: ReviewConclusion) -> str:
        """生成审查摘要

        Args:
            conclusion: 审查结论

        Returns:
            摘要字符串
        """
        counts = (
            f"{conclusion.blocking_count}B/"
            f"{conclusion.high_count}H/"
            f"{conclusion.medium_count}M/"
            f"{conclusion.low_count}L"
        )
        total = conclusion.blocking_count + conclusion.high_count + conclusion.medium_count + conclusion.low_count
        return f"审查发现 {total} 个问题 ({counts})"

    def _generate_next_steps(self, conclusion: ReviewConclusion) -> str:
        """生成下一步建议

        根据审查结论提供后续行动建议。

        Args:
            conclusion: 审查结论

        Returns:
            下一步建议
        """
        if conclusion.review_conclusion == "Fail":
            return "请根据审查问题修订规格文档"
        elif conclusion.review_conclusion == "Conditional Pass":
            return "请确认是否接受有条件通过"
        else:
            return "规格文档审查通过，可进入执行阶段"

    def _get_timestamp(self) -> str:
        """获取当前时间戳

        Returns:
            ISO 格式的时间戳字符串
        """
        return datetime.now(UTC).isoformat()
