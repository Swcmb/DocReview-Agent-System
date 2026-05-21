"""数据模型模块 / Data Models Module

定义系统使用的数据结构和类型。
包含 LangGraph 状态定义、Pydantic 验证模型和审查相关的数据结构。
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class IssueStatus(TypedDict):
    """问题状态定义

    追踪单个审查问题的状态信息。
    issue_id 生成规则: {severity_short}-{round}-{seq}，如 BK-3-2 = Blocking/第3轮/第2个
    """
    issue_id: str
    severity: str
    issue_type: str
    description: str
    suggestion: str
    location: str
    status: str


class IssueTracker(TypedDict):
    """跨迭代轮次的问题追踪器

    维护所有审查轮次中发现的问题及其解决状态。
    """
    all_issues: list[IssueStatus]
    fixed_count: int
    partially_fixed_count: int
    unfixed_count: int
    new_in_current_round: list[str]


class ReviewConclusion(BaseModel):
    """结构化审查结论

    供 evaluate_result 节点直接读取的审查结论模型。
    包含各严重级别问题计数和验收标准覆盖率信息。
    """
    review_conclusion: Literal["Pass", "Conditional Pass", "Fail"] = Field(
        alias="conclusion",
        description="审查结论"
    )
    blocking_count: int = Field(ge=0, description="阻塞性问题数量")
    high_count: int = Field(ge=0, description="高优先级问题数量")
    medium_count: int = Field(ge=0, description="中优先级问题数量")
    low_count: int = Field(ge=0, description="低优先级问题数量")
    ac_coverage_complete: bool = Field(description="验收标准覆盖率是否完整")

    class Config:
        populate_by_name = True


class ReviewReport(TypedDict):
    """审查报告结构

    记录每次审查迭代的完整报告信息。
    """
    iteration: int
    timestamp: str
    review_conclusion: str
    review_summary: str
    issues: list[IssueStatus]
    highlights: list[str]
    open_questions: list[str]
    next_steps: str


class AgentState(TypedDict):
    """LangGraph 工作流状态定义

    在整个文档审查工作流中传递的全局状态。
    包含用户输入、审查循环、执行门禁、问题追踪等所有状态信息。
    """
    user_task: str
    document_path: str | None
    document_content: str
    specification: str
    spec_version: int
    review_reports: list[ReviewReport]
    review_conclusion_data: dict | None
    iteration_count: int
    review_conclusion: str
    max_iterations: int
    stagnation_count: int
    stagnation_threshold: int
    user_approved: bool
    awaiting_approval: bool
    approval_timed_out: bool
    execution_status: str
    execution_output: str
    mcp_degraded: bool
    issue_tracker: IssueTracker
    spec_snapshot: str
    error_code: str | None
    error_message: str | None
    total_llm_cost: float
    messages: Annotated[list, add_messages]


def generate_issue_id(severity: str, round_num: int, seq: int) -> str:
    """生成问题唯一标识符

    根据严重级别、审查轮次和序号生成标准格式的问题 ID。

    Args:
        severity: 问题严重级别，取值范围: Blocking/High/Medium/Low
        round_num: 当前审查轮次，从1开始计数
        seq: 本轮内该严重级别的序号，从1开始计数

    Returns:
        问题 ID 字符串，格式: {severity_short}-{round}-{seq}
        例如: "BK-3-2" 表示 Blocking 级别第3轮第2个问题

    Examples:
        >>> generate_issue_id("Blocking", 1, 1)
        'BK-1-1'
        >>> generate_issue_id("High", 2, 3)
        'HI-2-3'
    """
    severity_map = {
        "Blocking": "BK",
        "High": "HI",
        "Medium": "MD",
        "Low": "LO"
    }
    short = severity_map.get(severity, "UK")
    return f"{short}-{round_num}-{seq}"


def check_termination_conditions(
    iteration_count: int,
    max_iterations: int,
    stagnation_count: int,
    stagnation_threshold: int,
    review_conclusion: str
) -> tuple[bool, str]:
    """检查是否满足工作流终止条件

    评估当前状态是否应该终止审查循环。
    检测以下终止条件：达到最大迭代次数、检测到停滞、审查通过。

    Args:
        iteration_count: 当前已完成的迭代次数
        max_iterations: 配置的最大迭代次数限制
        stagnation_count: 当前连续停滞轮次计数
        stagnation_threshold: 停滞检测阈值，超过此值则判定为停滞
        review_conclusion: 当前审查结论，取值: Pass/Conditional Pass/Fail

    Returns:
        tuple[bool, str]: (should_terminate, reason)
            - should_terminate: 是否应该终止工作流
            - reason: 终止原因代码，空字符串表示不终止
              可能值: "max_iterations_reached", "stagnation_detected", "review_passed", ""

    Examples:
        >>> check_termination_conditions(10, 10, 0, 2, "Fail")
        (True, 'max_iterations_reached')
        >>> check_termination_conditions(3, 10, 3, 2, "Fail")
        (True, 'stagnation_detected')
        >>> check_termination_conditions(2, 10, 0, 2, "Pass")
        (True, 'review_passed')
        >>> check_termination_conditions(1, 10, 0, 2, "Fail")
        (False, '')
    """
    if iteration_count >= max_iterations:
        return True, "max_iterations_reached"
    if stagnation_count >= stagnation_threshold:
        return True, "stagnation_detected"
    if review_conclusion in ("Pass", "Conditional Pass"):
        return True, "review_passed"
    return False, ""


def calculate_ac_coverage(
    spec_requirements: list[dict],
    acceptance_criteria: list[dict]
) -> bool:
    """计算验收标准覆盖率

    检查所有 P0 优先级的功能需求是否被验收标准覆盖。
    覆盖率完整当且仅当每个 P0_FR 都有对应的验收标准。

    Args:
        spec_requirements: 规格文档中的功能需求列表
            每个元素应包含 'id' 和 'priority' 字段
            例如: [{"id": "FR-001", "priority": "P0", ...}, ...]
        acceptance_criteria: 验收标准列表
            每个元素应包含 'covered_frs' 字段，列出其覆盖的功能需求 ID
            例如: [{"id": "AC-001", "covered_frs": ["FR-001", "FR-002"], ...}, ...]

    Returns:
        bool: P0_FRs ⊆ Covered_FRs，即所有 P0 需求是否都被覆盖
              如果没有 P0 需求，默认返回 True

    Algorithm:
        1. 提取所有 P0 优先级的功能需求 ID 集合 P0_FRs
        2. 合并所有 AC 的 covered_frs 得到已覆盖需求集合 Covered_FRs
        3. 返回 P0_FRs 是否为 Covered_FRs 的子集

    Examples:
        >>> spec = [{"id": "FR-001", "priority": "P0"}, {"id": "FR-002", "priority": "P1"}]
        >>> ac = [{"covered_frs": ["FR-001"]}]
        >>> calculate_ac_coverage(spec, ac)
        True

        >>> spec = [{"id": "FR-001", "priority": "P0"}, {"id": "FR-002", "priority": "P0"}]
        >>> ac = [{"covered_frs": ["FR-001"]}]
        >>> calculate_ac_coverage(spec, ac)
        False
    """
    p0_frs = {fr["id"] for fr in spec_requirements if fr.get("priority") == "P0"}
    covered_frs = set()
    for ac in acceptance_criteria:
        covered_frs.update(ac.get("covered_frs", []))
    return p0_frs.issubset(covered_frs) if p0_frs else True


class ReviewStatus(StrEnum):
    """审查状态枚举 / Review Status Enum"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class ReviewIssueSeverity(StrEnum):
    """审查问题严重性枚举 / Review Issue Severity Enum"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ReviewIssueCategory(StrEnum):
    """审查问题类别枚举 / Review Issue Category Enum"""
    CONSISTENCY = "consistency"
    OMISSION = "omission"
    RISK = "risk"
    AMBIGUITY = "ambiguity"
    EXECUTABILITY = "executability"
    CLARITY = "clarity"
    COMPLETENESS = "completeness"


class DocumentInfo(BaseModel):
    """文档信息模型 / Document Info Model"""
    document_id: str = Field(description="文档唯一标识符")
    document_type: str = Field(description="文档类型（如 PRD、技术方案等）")
    title: str = Field(description="文档标题")
    file_path: str | None = Field(default=None, description="文档文件路径")
    content: str | None = Field(default=None, description="文档内容")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    metadata: dict[str, Any] = Field(default_factory=dict, description="额外元数据")


class ReviewIssue(BaseModel):
    """审查问题模型 / Review Issue Model"""
    issue_id: str = Field(description="问题唯一标识符")
    category: ReviewIssueCategory = Field(description="问题类别")
    severity: ReviewIssueSeverity = Field(description="问题严重性")
    title: str = Field(description="问题标题")
    description: str = Field(description="问题描述")
    location: str | None = Field(default=None, description="问题位置（如章节、行号）")
    suggestion: str | None = Field(default=None, description="修改建议")
    is_resolved: bool = Field(default=False, description="是否已解决")
    resolved_comment: str | None = Field(default=None, description="解决说明")


class ReviewFinding(BaseModel):
    """审查发现模型 / Review Finding Model"""
    finding_id: str = Field(description="发现唯一标识符")
    title: str = Field(description="发现标题")
    content: str = Field(description="发现内容")
    issues: list[ReviewIssue] = Field(default_factory=list, description="相关问题列表")
    confidence: float = Field(ge=0.0, le=1.0, description="置信度")
    requires_attention: bool = Field(default=True, description="是否需要关注")


class ReviewReportModel(BaseModel):
    """审查报告模型 / Review Report Model"""
    report_id: str = Field(description="报告唯一标识符")
    document_info: DocumentInfo = Field(description="文档信息")
    status: ReviewStatus = Field(default=ReviewStatus.PENDING, description="审查状态")
    findings: list[ReviewFinding] = Field(default_factory=list, description="审查发现列表")
    summary: str | None = Field(default=None, description="审查摘要")
    recommendations: list[str] = Field(default_factory=list, description="建议列表")
    iteration: int = Field(default=0, description="当前迭代次数")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")
    metadata: dict[str, Any] = Field(default_factory=dict, description="额外元数据")


class AgentAction(BaseModel):
    """代理动作模型 / Agent Action Model"""
    action_type: str = Field(description="动作类型")
    action_name: str = Field(description="动作名称")
    parameters: dict[str, Any] = Field(default_factory=dict, description="动作参数")
    result: Any | None = Field(default=None, description="动作结果")
    error: str | None = Field(default=None, description="错误信息")
    timestamp: datetime = Field(default_factory=datetime.now, description="时间戳")
    duration_ms: int | None = Field(default=None, description="执行时长（毫秒）")


class AgentResponse(BaseModel):
    """代理响应模型 / Agent Response Model"""
    message: str = Field(description="响应消息")
    actions: list[AgentAction] = Field(default_factory=list, description="执行的动作列表")
    next_agent: str | None = Field(default=None, description="下一个代理")
    should_continue: bool = Field(default=True, description="是否继续")
    metadata: dict[str, Any] = Field(default_factory=dict, description="额外元数据")


class UserFeedback(BaseModel):
    """用户反馈模型 / User Feedback Model"""
    feedback_id: str = Field(description="反馈唯一标识符")
    feedback_type: str = Field(description="反馈类型（approve/reject/revise）")
    comment: str | None = Field(default=None, description="反馈评论")
    requested_changes: list[str] | None = Field(default=None, description="请求的修改")
    timestamp: datetime = Field(default_factory=datetime.now, description="时间戳")

