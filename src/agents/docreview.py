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
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from langchain.chat_models import BaseChatModel
except ImportError:
    try:
        from langchain_core.language_models import BaseChatModel
    except ImportError:
        BaseChatModel = Any
from langchain.schema import HumanMessage

from ..mcp.context7 import Context7Client
from ..mcp.sequential_thinking import SequentialThinkingClient
from ..schemas.models import (
    AgentState,
    IssueStatus,
    ReviewConclusion,
    ReviewReport,
    generate_issue_id,
)
from ..tools.base import BaseTool

logger = logging.getLogger(__name__)


@dataclass
class CoreLoopAnalysis:
    """核心闭环分析结果
    
    存储从规格文档中提取的核心业务流程分析信息。
    """
    flows: List[str] = field(default_factory=list)
    breaks: List[str] = field(default_factory=list)
    entry_points: List[str] = field(default_factory=list)
    exit_points: List[str] = field(default_factory=list)


@dataclass
class AtomicRequirement:
    """原子化需求
    
    表示已分解为最小可验证单元的功能需求。
    """
    id: str
    description: str
    priority: str
    acceptance_criteria: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)


@dataclass
class TechContext:
    """技术上下文
    
    通过 Context7 MCP 获取的技术栈相关信息。
    """
    library_name: str
    relevant_docs: List[str] = field(default_factory=list)
    best_practices: List[str] = field(default_factory=list)
    common_pitfalls: List[str] = field(default_factory=list)


ISSUE_TYPES = {
    "CORE_LOOP_BREAK": "CoreProcessBreak",
    "CONSISTENCY": "ConsistencyCheck",
    "REQUIREMENT_INCOMPLETE": "RequirementCompleteness",
    "FEASIBILITY": "TechnicalFeasibility",
    "RISK": "RiskDetection",
    "EXECUTABILITY": "ExecutabilityReview",
}

SEVERITY_BLOCKING = "Blocking"
SEVERITY_HIGH = "High"
SEVERITY_MEDIUM = "Medium"
SEVERITY_LOW = "Low"


class DocReviewAgent:
    """DocReview Sub-Agent
    
    核心审查智能体，执行六步审查流程：
    1. 核心闭环提取
    2. 一致性检查
    3. 需求原子化
    4. 技术可行性
    5. 风险检测
    6. 可执行性审查
    
    Attributes:
        llm: 语言模型实例
        sequential_thinking: Sequential Thinking MCP 客户端（可选）
        context7: Context7 MCP 客户端（可选）
        tools: 可用工具列表
        review_prompt: 审查提示词
        whentocall_prompt: 调用条件提示词
    """
    
    PROMPT_PATH = ".trae/prompts/docreview-agent-system/agent-review-prompt.md"
    WHENTOCALL_PATH = ".trae/prompts/docreview-agent-system/agent-invocation-rules.md"
    
    def __init__(
        self,
        llm: BaseChatModel,
        sequential_thinking: Optional[SequentialThinkingClient] = None,
        context7: Optional[Context7Client] = None,
        tools: Optional[List[BaseTool]] = None,
    ) -> None:
        """初始化 DocReview Agent
        
        Args:
            llm: 语言模型实例
            sequential_thinking: Sequential Thinking MCP 客户端
            context7: Context7 MCP 客户端
            tools: 可用工具列表
        """
        self.llm = llm
        self.sequential_thinking = sequential_thinking
        self.context7 = context7
        self.tools = tools or []
        self.logger = logger
        
        self.review_prompt = self._load_prompt_safe(self.PROMPT_PATH)
        self.whentocall_prompt = self._load_prompt_safe(self.WHENTOCALL_PATH)
    
    def _load_prompt_safe(self, path: str) -> str:
        """安全加载提示词文件
        
        Args:
            path: 提示词文件路径
            
        Returns:
            提示词内容，文件不存在时返回空字符串
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            self.logger.warning(f"提示词文件未找到: {path}")
            return ""
    
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
        
        try:
            core_loop = await self._extract_core_loop(spec)
            
            consistency_issues = await self._check_consistency(spec, core_loop)
            
            tech_context = await self._enrich_context(spec)
            
            atomize_issues, atomic_reqs = await self._atomize_requirements(spec, core_loop)
            
            feasibility_issues, dep_graph = await self._deduce_feasibility(
                spec, atomic_reqs, tech_context
            )
            
            risk_issues = await self._detect_risks(
                spec, dep_graph,
                consistency_issues + atomize_issues + feasibility_issues,
            )
            
            exec_issues = await self._review_executability(
                spec,
                consistency_issues + atomize_issues + feasibility_issues + risk_issues,
            )
            
            all_issues = (
                consistency_issues
                + atomize_issues
                + feasibility_issues
                + risk_issues
                + exec_issues
            )
            
            markdown_report = self._compile_markdown_report(all_issues, iteration)
            
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
            return state
    
    async def _think_step(
        self,
        step_name: str,
        context: str,
        num_thoughts: int = 2,
    ) -> str:
        """通过 Sequential Thinking MCP 进行多步推理
        
        使用 MCP 进行结构化推理，当 MCP 不可用时降级为纯 LLM 推理。
        
        Args:
            step_name: 步骤名称
            context: 上下文内容
            num_thoughts: 思维迭代次数
            
        Returns:
            最终推理结果
        """
        if not self.sequential_thinking or self.sequential_thinking.is_degraded:
            return await self._llm_think(step_name, context)
        
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
            return await self._llm_think(step_name, context)
    
    async def _llm_think(self, step_name: str, context: str) -> str:
        """纯 LLM 推理（降级模式）
        
        Args:
            step_name: 步骤名称
            context: 上下文内容
            
        Returns:
            LLM 生成的推理结果
        """
        prompt = f"""请对以下内容进行{step_name}分析：

{context}

请提供结构化的分析结果。"""
        response = await self.llm.agenerate([HumanMessage(content=prompt)])
        return response.generations[0][0].text.strip()
    
    async def _enrich_context(self, spec: str) -> Optional[TechContext]:
        """通过 Context7 MCP 获取技术上下文
        
        从规格文档中提取技术栈信息，查询相关文档和最佳实践。
        
        Args:
            spec: 规格文档内容
            
        Returns:
            技术上下文对象，获取失败时返回 None
        """
        if not self.context7 or self.context7.is_degraded:
            return None
        
        try:
            tech_stack = self._extract_tech_stack(spec)
            
            if not tech_stack:
                return None
            
            library_id = await self.context7.resolve_library_id(tech_stack)
            
            docs = await self.context7.query_docs("best practices", library_id)
            
            return TechContext(
                library_name=tech_stack,
                relevant_docs=[d.snippet for d in docs],
                best_practices=self._extract_best_practices(docs),
                common_pitfalls=[],
            )
            
        except Exception as e:
            self.logger.warning(f"Context7 调用失败: {e}")
            return None
    
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
    
    def _extract_best_practices(self, docs: List[Any]) -> List[str]:
        """从文档结果中提取最佳实践
        
        Args:
            docs: 文档结果列表
            
        Returns:
            最佳实践列表
        """
        practices = []
        for doc in docs:
            if hasattr(doc, "snippet") and doc.snippet:
                practices.append(doc.snippet[:100])
        return practices[:5]
    
    async def _extract_core_loop(self, spec: str) -> CoreLoopAnalysis:
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
    ) -> List[IssueStatus]:
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
    ) -> Tuple[List[IssueStatus], List[AtomicRequirement]]:
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
        atomic_reqs: List[AtomicRequirement],
        context: Optional[TechContext],
    ) -> Tuple[List[IssueStatus], Dict[str, Any]]:
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
        dep_graph: Dict[str, Any],
        prev_issues: List[IssueStatus],
    ) -> List[IssueStatus]:
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
        all_issues: List[IssueStatus],
    ) -> List[IssueStatus]:
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
        )
        
        issues = self._parse_issues_from_text(
            thinking_result,
            expected_types=[ISSUE_TYPES["EXECUTABILITY"]],
            default_severity=SEVERITY_MEDIUM,
        )
        
        return issues
    
    def _compile_markdown_report(
        self,
        issues: List[IssueStatus],
        iteration: int,
    ) -> str:
        """生成 Markdown 格式的审查报告
        
        按严重程度排序并分配 issue_id。
        
        Args:
            issues: 问题列表
            iteration: 当前迭代轮次
            
        Returns:
            Markdown 格式的审查报告
        """
        severity_order = {
            SEVERITY_BLOCKING: 0,
            SEVERITY_HIGH: 1,
            SEVERITY_MEDIUM: 2,
            SEVERITY_LOW: 3,
        }
        
        sorted_issues = sorted(
            issues,
            key=lambda x: severity_order.get(x["severity"], 99),
        )
        
        severity_counters = {SEVERITY_BLOCKING: 0, SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 0, SEVERITY_LOW: 0}
        
        for issue in sorted_issues:
            if "issue_id" not in issue or not issue["issue_id"]:
                severity = issue["severity"]
                severity_counters[severity] = severity_counters.get(severity, 0) + 1
                issue["issue_id"] = generate_issue_id(
                    severity,
                    iteration,
                    severity_counters[severity],
                )
        
        report_lines = [
            "## DocReview Review Report",
            f"**Review Conclusion**: [待评估]",
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
        issues: List[IssueStatus],
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
    
    def _check_ac_coverage(self, spec: str, issues: List[IssueStatus]) -> bool:
        """检查 AC 覆盖率
        
        验证 P0 功能需求是否被验收标准覆盖。
        
        Args:
            spec: 规格文档内容
            issues: 问题列表
            
        Returns:
            覆盖率是否完整
        """
        p0_frs = re.findall(r"\*\*FR-(\d+)\*\*.*?\(P0\)", spec, re.DOTALL)
        
        if not p0_frs:
            fr_matches = re.findall(r"\*\*FR-(\d+)\*\*", spec)
            if fr_matches:
                return True
        
        ac_matches = re.findall(r"\*\*AC-(\d+)\*\*", spec)
        
        return len(ac_matches) > 0 if p0_frs else True
    
    def _parse_issues_from_text(
        self,
        text: str,
        expected_types: List[str],
        default_severity: str,
    ) -> List[IssueStatus]:
        """从文本中解析问题列表
        
        使用模式匹配从 LLM 输出中提取问题信息。
        
        Args:
            text: LLM 生成的文本
            expected_types: 预期的问题类型列表
            default_severity: 默认严重级别
            
        Returns:
            解析出的问题列表
        """
        issues = []
        
        patterns = [
            r"(问题|issue)[\s:：]+(.+?)(?=\n\n|\n$|$)",
            r"(风险|risk)[\s:：]+(.+?)(?=\n\n|\n$|$)",
            r"(缺失|gap|missing)[\s:：]+(.+?)(?=\n\n|\n$|$)",
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, text, re.DOTALL)
            for match in matches:
                issues.append(
                    IssueStatus(
                        issue_id="",
                        severity=default_severity,
                        issue_type=expected_types[0] if expected_types else ISSUE_TYPES["CONSISTENCY"],
                        description=match[1].strip()[:200],
                        suggestion="请根据问题描述进行修订",
                        location="规格文档",
                        status="open",
                    )
                )
        
        return issues
    
    def _parse_atomic_requirements(self, text: str) -> List[AtomicRequirement]:
        """从文本中解析原子化需求
        
        Args:
            text: LLM 生成的文本
            
        Returns:
            原子化需求列表
        """
        return []
    
    def _parse_dependency_graph(self, text: str) -> Dict[str, Any]:
        """从文本中解析依赖关系图
        
        Args:
            text: LLM 生成的文本
            
        Returns:
            依赖关系图
        """
        return {"nodes": [], "edges": []}
    
    def _extract_list_from_text(self, text: str, keywords: List[str]) -> List[str]:
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
        issues: List[IssueStatus],
    ) -> List[str]:
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
    
    def _extract_open_questions(self, issues: List[IssueStatus]) -> List[str]:
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
        return datetime.now().isoformat()
