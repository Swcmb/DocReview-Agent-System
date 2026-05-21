"""代理状态管理模块 / Agent State Management Module

定义 LangGraph 工作流使用的状态结构。
包含状态初始化函数和状态操作辅助方法。
"""

from typing import Any

from pydantic import BaseModel, Field

from src.schemas.models import (
    DocumentInfo,
    ReviewFinding,
    ReviewIssue,
    ReviewReport,
    ReviewStatus,
)


def create_initial_state() -> dict[str, Any]:
    """创建 AgentState 的初始状态

    生成用于初始化 LangGraph 工作流的默认状态字典。
    所有字段都设置为初始值，确保工作流从干净的起点开始。

    Returns:
        Dict[str, Any]: 符合 AgentState TypedDict 结构的初始状态字典

    Notes:
        - iteration_count: 从0开始，首次审查后变为1
        - max_iterations: 默认10轮，防止无限循环
        - stagnation_threshold: 默认2，检测连续无改进的轮次
        - messages: 使用 Annotated 类型，需要与 add_messages reducer 配合使用

    Examples:
        >>> state = create_initial_state()
        >>> state["iteration_count"]
        0
        >>> state["review_conclusion"]
        'pending'
        >>> len(state["issue_tracker"]["all_issues"])
        0
    """
    return {
        "user_task": "",
        "document_path": None,
        "document_content": "",
        "specification": "",
        "spec_version": 0,
        "review_reports": [],
        "review_conclusion_data": None,
        "iteration_count": 0,
        "review_conclusion": "pending",
        "max_iterations": 10,
        "stagnation_count": 0,
        "stagnation_threshold": 2,
        "user_approved": False,
        "awaiting_approval": False,
        "approval_timed_out": False,
        "execution_status": "pending",
        "execution_output": "",
        "mcp_degraded": False,
        "issue_tracker": {
            "all_issues": [],
            "fixed_count": 0,
            "partially_fixed_count": 0,
            "unfixed_count": 0,
            "new_in_current_round": []
        },
        "spec_snapshot": "",
        "error_code": None,
        "error_message": None,
        "total_llm_cost": 0.0,
        "messages": []
    }


class AgentStateModel(BaseModel):
    """代理状态模型（旧版 Pydantic 模型，保留向后兼容）

    定义整个文档审查工作流的状态结构，用于 LangGraph 节点间数据传递。
    """

    document_info: DocumentInfo | None = Field(
        default=None,
        description="待审查的文档信息"
    )
    review_report: ReviewReport | None = Field(
        default=None,
        description="审查报告"
    )
    current_iteration: int = Field(
        default=0,
        ge=0,
        description="当前迭代次数"
    )
    consecutive_same_results: int = Field(
        default=0,
        ge=0,
        description="连续相同结果计数（用于检测停滞）"
    )
    previous_finding_hash: str | None = Field(
        default=None,
        description="上一轮发现的内容哈希（用于检测停滞）"
    )
    pending_issues: list[ReviewIssue] = Field(
        default_factory=list,
        description="待处理的问题列表"
    )
    resolved_issues: list[ReviewIssue] = Field(
        default_factory=list,
        description="已解决的问题列表"
    )
    current_agent: str | None = Field(
        default=None,
        description="当前执行的代理名称"
    )
    messages: list[dict[str, Any]] = Field(
        default_factory=list,
        description="消息历史记录"
    )
    should_request_approval: bool = Field(
        default=False,
        description="是否请求用户批准"
    )
    user_approved: bool | None = Field(
        default=None,
        description="用户批准结果"
    )
    status: ReviewStatus = Field(
        default=ReviewStatus.PENDING,
        description="当前状态"
    )
    error_message: str | None = Field(
        default=None,
        description="错误消息"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="额外元数据"
    )

    def add_message(self, role: str, content: str) -> None:
        """添加消息到历史记录 / Add Message to History

        Args:
            role: 消息角色（user/assistant/system）
            content: 消息内容
        """
        self.messages.append({
            "role": role,
            "content": content
        })

    def get_messages_by_role(self, role: str) -> list[dict[str, Any]]:
        """获取指定角色的消息 / Get Messages by Role

        Args:
            role: 消息角色

        Returns:
            list[dict[str, Any]]: 消息列表
        """
        return [msg for msg in self.messages if msg.get("role") == role]

    def increment_iteration(self) -> None:
        """增加迭代计数 / Increment Iteration Counter"""
        self.current_iteration += 1

    def check_stagnation(self, threshold: int) -> bool:
        """检查是否停滞 / Check for Stagnation

        Args:
            threshold: 停滞阈值

        Returns:
            bool: 是否停滞
        """
        return self.consecutive_same_results >= threshold

    def reset_stagnation_counter(self) -> None:
        """重置停滞计数器 / Reset Stagnation Counter"""
        self.consecutive_same_results = 0

    def increment_stagnation_counter(self) -> None:
        """增加停滞计数器 / Increment Stagnation Counter"""
        self.consecutive_same_results += 1

    def add_pending_issue(self, issue: ReviewIssue) -> None:
        """添加待处理问题 / Add Pending Issue

        Args:
            issue: 问题对象
        """
        self.pending_issues.append(issue)

    def resolve_issue(self, issue_id: str, comment: str | None = None) -> bool:
        """解决指定问题 / Resolve Specific Issue

        Args:
            issue_id: 问题 ID
            comment: 解决说明

        Returns:
            bool: 是否成功解决
        """
        for issue in self.pending_issues:
            if issue.issue_id == issue_id:
                issue.is_resolved = True
                if comment:
                    issue.resolved_comment = comment
                self.resolved_issues.append(issue)
                self.pending_issues.remove(issue)
                return True
        return False

    def has_new_findings(self, new_findings: list[ReviewFinding]) -> bool:
        """检查是否有新发现 / Check for New Findings

        Args:
            new_findings: 新的发现列表

        Returns:
            bool: 是否有新发现
        """
        if not self.review_report:
            return True
        return len(new_findings) != len(self.review_report.findings)
