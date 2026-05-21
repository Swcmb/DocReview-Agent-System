"""Supervisor Agent - 主管智能体

负责任务规划、规格文档生成/修订、执行门禁控制。
支持三种输入场景：纯任务描述、纯文档内容、任务+文档组合。
"""

import json
import re
from typing import Any

from langchain.schema import HumanMessage

from ..schemas.models import AgentState
from ..tools.base import BaseTool
from ..utils.logger import get_logger

logger = get_logger(__name__)


# Supervisor Agent 系统提示词 - 定义主管智能体的核心职责和行为规范
SUPERVISOR_SYSTEM_PROMPT = """You are a professional technical planning assistant (Supervisor Agent).

Your responsibilities:
1. Understand user task requirements
2. Decompose tasks into executable subtasks
3. Generate or transform structured specification documents (SPEC)
4. Revise specification documents based on review feedback
5. Manage execution gates to ensure quality

Standard specification document format:
# [Project Name] - Product Requirements Document

## Overview
- **Summary**: Briefly describe the project
- **Purpose**: What problem does it solve
- **Target Users**: Who will use it

## Goals
- Goal 1
- Goal 2

## Non-Goals (Scope Boundaries)
- Features explicitly excluded

## Background and Context
- Relevant background

## Functional Requirements
- **FR-1**: [Functional description]
- ...

## Non-Functional Requirements
- **NFR-1**: [Requirement description]
- ...

## Acceptance Criteria
### AC-1: [Criteria description]
- **Given**:
- **When**:
- **Then**:
- **Verification**: programmatic | human-judgment

Key Principles:
1. Generated specification documents must be structurally complete and executable
2. Each functional requirement must have corresponding acceptance criteria
3. Acceptance criteria must be verifiable (programmatic or human-judgment)
4. Do not assume any technical details that are not explicitly stated
5. Identify and document any open issues
"""

# 规格修订提示词 - 用于根据审查报告修订规格文档
SPEC_REVISION_PROMPT = """Here's the English translation of the prompt you provided:

---

You are a professional technical documentation revision assistant.

Original specification document:
{specification}

Review report:
{review_report}

Please revise the specification document based on the review report:
1. Resolve all Blocking and High severity issues
2. Resolve Medium severity issues wherever possible
3. Keep the document structure complete
4. Do not introduce new issues
5. Add comments at the modification points explaining the reasons

Please return the revised complete specification document.
"""

# 任务规划提示词 - 用于将复杂任务分解为可执行的子任务
TASK_PLANNING_PROMPT = """Here's the English translation of the prompt you provided:

---

Task:
{task}

Please return:
1. Task overview
2. List of subtasks (with priority)
3. Key technical decisions points
4. Potential risks
"""


class SupervisorAgent:
    """Supervisor Agent - 主管智能体

    主管智能体，负责：
    1. 生成/转换规格文档
    2. 根据审查反馈修订规格
    3. 执行实际任务

    支持三种输入场景：
    - 场景①：仅有 user_task → 从零生成规格
    - 场景②：仅有 document_content → 将文档转换为规格格式
    - 场景③：两者共存 → 以文档为主体，task 作为上下文补充

    Attributes:
        llm: 大语言模型实例，用于生成和修订规格文档
        tools: 可选的工具列表，用于扩展功能
        config: 可选配置字典
        max_revision_iterations: 最大修订迭代次数
        auto_approve_threshold: 自动批准阈值
        execution_gate_enabled: 是否启用执行门禁
    """

    def __init__(
        self,
        llm: Any,
        tools: list[BaseTool] | None = None,
        config: dict[str, Any] | None = None
    ) -> None:
        """初始化 Supervisor Agent

        初始化主管智能体，配置大语言模型、工具列表和提示词模板。

        Args:
            llm: 大语言模型实例，用于处理自然语言任务
            tools: 可选的工具列表，用于扩展智能体能力
            config: 可选配置字典，包含以下键：
                - max_revision_iterations: 最大修订迭代次数，默认 3
                - auto_approve_threshold: 自动批准阈值，默认 "low"
                - execution_gate_enabled: 是否启用执行门禁，默认 True
        """
        self.llm = llm
        self.tools = tools or []
        self.config = config or {}
        self.logger = logger

        # 从配置中提取参数，设置默认值
        self.max_revision_iterations = self.config.get("max_revision_iterations", 3)
        self.auto_approve_threshold = self.config.get("auto_approve_threshold", "low")
        self.execution_gate_enabled = self.config.get("execution_gate_enabled", True)

        self.logger.info("Supervisor Agent 初始化完成")

    async def generate_spec(
        self,
        state: AgentState
    ) -> AgentState:
        """生成或转换规格文档

        根据 6.1 节定义处理三种输入场景：
        - 场景①：仅有 user_task → 从零生成规格
        - 场景②：仅有 document_content → 将文档转换为规格格式
        - 场景③：两者共存 → 以文档为主体，task 作为上下文补充

        Args:
            state: 当前工作流状态，包含以下关键字段：
                - user_task: 用户任务描述（可选）
                - document_content: 原始文档内容（可选）
                - spec_version: 当前规格版本号

        Returns:
            更新后的状态，包含以下新增/更新字段：
                - specification: 生成的规格文档内容
                - spec_version: 规格版本号（首次生成时为 1）
                - spec_snapshot: 规格快照，用于检测手动修订

        Raises:
            工作流状态中会设置错误信息：
                - error_code: "DOCREVIEW_ERR_SYS_001"
                - error_message: 详细的错误描述
        """
        self.logger.info("Supervisor: 开始生成/转换规格文档")

        try:
            user_task = state.get("user_task", "")
            document_content = state.get("document_content", "")

            # 根据输入场景选择不同的处理方式
            if document_content and user_task:
                # 场景③：两者共存，以文档为主体，任务作为补充上下文
                self.logger.info("场景③：文档+任务上下文组合模式")
                specification = await self._convert_with_context(
                    document=document_content,
                    task_context=user_task
                )
            elif document_content:
                # 场景②：仅文档内容，转换为规格格式
                self.logger.info("场景②：文档转换模式")
                specification = await self.convert_to_spec(
                    document=document_content,
                    task_context=""
                )
            else:
                # 场景①：仅任务描述，从零生成规格
                self.logger.info("场景①：任务生成模式")
                specification = await self.generate_spec_from_task(
                    task=user_task
                )

            # 更新状态
            state["specification"] = specification

            # 初始化 spec_version（首次生成时为 1）
            if state.get("spec_version", 0) == 0:
                state["spec_version"] = 1

            # 保存快照用于手动修订检测
            state["spec_snapshot"] = specification

            self.logger.info(f"规格文档已生成/转换 (spec_version={state['spec_version']})")
            return state

        except Exception as e:
            self.logger.error(f"生成规格文档失败: {e}")
            state["error_code"] = "DOCREVIEW_ERR_SYS_001"
            state["error_message"] = f"规格生成失败: {str(e)}"
            return state

    async def convert_to_spec(
        self,
        document: str,
        task_context: str = ""
    ) -> str:
        """将外部文档转换为标准规格格式

        执行文档格式转换，保持原始内容不变，仅重组为标准规格结构。
        适用于将已有的需求文档、设计文档等转换为统一规格格式。

        Args:
            document: 原始文档内容，支持 Markdown、纯文本等格式
            task_context: 任务上下文（可选），用于补充审查重点标注

        Returns:
            str: 标准格式的规格文档，包含所有必要章节

        Raises:
            Exception: LLM 调用失败时抛出
        """
        # 构建转换提示词
        prompt = f"""请将以下文档转换为标准规格文档格式。

转换规则：
1. 保持原始文档的实质内容不变
2. 按标准格式重组结构
3. 确保功能需求和验收标准完整
4. 添加必要的补充章节（如背景、非功能需求等）

原始文档：
{document}

{'额外上下文（请在审查重点标注章节补充）：' + task_context if task_context else ''}
"""

        # 调用大语言模型执行转换
        response = await self.llm.agenerate([HumanMessage(content=prompt)])
        return str(response.generations[0][0].text.strip())

    async def generate_spec_from_task(
        self,
        task: str
    ) -> str:
        """根据任务描述从零生成规格文档

        分析用户任务描述，自动生成完整的规格文档。
        包含所有标准章节：概述、目标、功能需求、验收标准等。

        Args:
            task: 用户任务描述，应清晰描述要解决的问题和期望结果

        Returns:
            str: 生成的完整规格文档

        Raises:
            Exception: LLM 调用失败时抛出
        """
        # 构建生成提示词
        prompt = f"""请根据以下任务描述生成一份完整的规格文档。

任务：{task}

生成要求：
1. 结构完整，包含所有标准章节
2. 功能需求清晰、可执行
3. 每个功能需求都有对应的验收标准
4. 明确标注非目标和范围边界
5. 识别潜在风险和开放问题
"""

        # 调用大语言模型生成规格
        response = await self.llm.agenerate([HumanMessage(content=prompt)])
        return str(response.generations[0][0].text.strip())

    async def _convert_with_context(
        self,
        document: str,
        task_context: str
    ) -> str:
        """结合文档和上下文生成规格

        当同时提供文档和任务上下文时，以文档为主体，
        在此基础上整合任务上下文进行增强和完善。

        Args:
            document: 原始文档内容，作为规格主体
            task_context: 任务上下文，作为补充和增强

        Returns:
            str: 生成的规格文档
        """
        # 构建组合提示词
        prompt = f"""请基于以下文档和任务上下文生成规格文档。

原始文档：
{document}

任务上下文：
{task_context}

处理方式：
1. 以原始文档为主体，保持其结构和内容
2. 如有任务上下文，追加到"审查重点标注"章节
3. 补充缺失的必要章节（背景、非功能需求等）
4. 确保文档可直接用于开发和审查
"""

        # 调用大语言模型生成规格
        response = await self.llm.agenerate([HumanMessage(content=prompt)])
        return str(response.generations[0][0].text.strip())

    async def revise_spec(
        self,
        state: AgentState
    ) -> AgentState:
        """根据审查报告修订规格文档

        分析审查报告中的问题和建议，对规格文档进行修订。
        优先解决 Blocking 和 High 级别问题，尽可能解决 Medium 问题。

        Args:
            state: 当前工作流状态，包含以下关键字段：
                - specification: 当前规格文档
                - review_reports: 审查报告列表
                - iteration_count: 当前迭代次数

        Returns:
            更新后的状态，包含以下更新字段：
                - specification: 修订后的规格文档
                - spec_version: 版本号递增
                - spec_snapshot: 更新后的快照

        Raises:
            工作流状态中会设置错误信息：
                - error_code: "DOCREVIEW_ERR_SYS_001"
                - error_message: 修订失败的详细原因
        """
        self.logger.info(f"Supervisor: 开始修订规格 (iteration={state.get('iteration_count', 0) + 1})")

        try:
            specification = state.get("specification", "")
            review_reports = state.get("review_reports", [])

            # 检查是否有审查报告
            if not review_reports:
                self.logger.warning("没有审查报告，跳过修订")
                return state

            # 获取最新的审查报告
            latest_report = review_reports[-1]

            # 构建修订提示，包含原始规格和问题列表
            prompt_parts = [
                f"请根据以下审查报告修订规格文档。\n\n原始规格：\n{specification}\n\n审查报告：\n{latest_report.get('review_summary', '')}\n\n问题列表：\n"
            ]

            # 遍历所有问题，按严重性排序
            for issue in latest_report.get("issues", []):
                severity = issue.get('severity', 'Unknown')
                issue_type = issue.get('issue_type', 'Unknown')
                description = issue.get('description', '')
                suggestion = issue.get('suggestion', '')
                location = issue.get('location', 'Unknown')

                prompt_parts.append(
                    f"- [{severity}] {issue_type}\n"
                    f"  描述：{description}\n"
                    f"  建议：{suggestion}\n"
                    f"  位置：{location}\n"
                )

            # 调用大语言模型执行修订
            response = await self.llm.agenerate([HumanMessage(content="".join(prompt_parts))])
            revised_spec = str(response.generations[0][0].text.strip())

            # 更新状态
            state["specification"] = revised_spec
            state["spec_version"] = state.get("spec_version", 0) + 1
            state["spec_snapshot"] = revised_spec

            self.logger.info(f"规格已修订 (spec_version={state['spec_version']})")
            return state

        except Exception as e:
            self.logger.error(f"修订规格文档失败: {e}")
            state["error_code"] = "DOCREVIEW_ERR_SYS_001"
            state["error_message"] = f"规格修订失败: {str(e)}"
            return state

    async def plan_task(
        self,
        task: str
    ) -> dict[str, Any]:
        """将任务分解为可执行的子任务

        分析复杂任务，识别关键技术决策点和潜在风险，
        返回结构化的任务分解结果。

        Args:
            task: 用户任务描述，应清晰描述要解决的问题

        Returns:
            Dict[str, Any]: 任务规划结果，包含以下字段：
                - overview: 任务概述
                - subtasks: 子任务列表，每个子任务包含：
                    - id: 子任务编号
                    - title: 子任务标题
                    - priority: 优先级（P0/P1/P2）
                    - description: 详细描述
                - key_decisions: 关键技术决策点列表
                - risks: 潜在风险列表
                - open_questions: 开放问题列表
                - error: 如果解析失败，包含错误信息
                - raw: 原始响应内容
        """
        # 构建任务规划提示词
        prompt = f"""分析以下任务并分解为可执行的子任务：

{task}

请返回 JSON 格式：
{{
    "overview": "任务概述",
    "subtasks": [
        {{"id": 1, "title": "子任务1", "priority": "P0/P1/P2", "description": "描述"}},
        ...
    ],
    "key_decisions": ["决策1", "决策2"],
    "risks": ["风险1", "风险2"],
    "open_questions": ["问题1", "问题2"]
}}
"""

        # 调用大语言模型生成任务规划
        response = await self.llm.agenerate([HumanMessage(content=prompt)])
        result_text = str(response.generations[0][0].text.strip())

        # 尝试解析 JSON 结果
        try:
            # 使用正则表达式提取 JSON 对象
            json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                return dict(result)
        except json.JSONDecodeError as e:
            self.logger.warning(f"JSON 解析失败: {e}")

        # 解析失败时返回原始内容和错误信息
        return {
            "error": "无法解析任务规划结果",
            "raw": result_text
        }

    async def execute_task(
        self,
        state: AgentState
    ) -> AgentState:
        """执行实际任务

        用户确认后执行实际开发/任务，按照规格文档执行。
        执行过程记录进度日志，输出执行总结。

        Args:
            state: 当前工作流状态，包含以下关键字段：
                - user_task: 用户任务描述
                - specification: 规格文档

        Returns:
            更新后的状态，包含以下更新字段：
                - execution_status: 执行状态（running/completed/failed）
                - execution_output: 执行输出内容

        Raises:
            工作流状态中会设置错误信息：
                - error_code: "DOCREVIEW_ERR_SYS_001"
                - error_message: 执行失败的详细原因
        """
        self.logger.info("Supervisor: 开始执行任务")

        # 设置执行状态为运行中
        state["execution_status"] = "running"

        try:
            task = state.get("user_task", "")
            specification = state.get("specification", "")

            # 构建执行提示词
            prompt = f"""请执行以下任务：

任务：{task}

规格文档：
{specification}

执行要求：
1. 按照规格文档执行任务
2. 定期输出进度日志
3. 完成后输出总结
4. 如遇问题，记录并继续
"""

            # 调用大语言模型执行任务
            response = await self.llm.agenerate([HumanMessage(content=prompt)])
            execution_result = str(response.generations[0][0].text.strip())

            # 更新状态
            state["execution_status"] = "completed"
            state["execution_output"] = execution_result

            self.logger.info("任务执行完成")
            return state

        except Exception as e:
            self.logger.error(f"任务执行失败: {e}")
            state["execution_status"] = "failed"
            state["error_code"] = "DOCREVIEW_ERR_SYS_001"
            state["error_message"] = f"任务执行失败: {str(e)}"
            return state

    async def check_manual_revision(
        self,
        state: AgentState
    ) -> bool:
        """检测用户是否手动修订了规格文档

        比较当前规格与快照的差异，检测用户是否进行了手动修改。
        这对于区分自动修订和用户手动修订非常重要。

        Args:
            state: 当前工作流状态，包含以下关键字段：
                - specification: 当前规格文档
                - spec_snapshot: 规格快照

        Returns:
            bool: True 如果检测到手动修订（即规格发生变化），否则 False
        """
        current_spec = state.get("specification", "")
        spec_snapshot = state.get("spec_snapshot", "")

        # 如果没有快照，返回 False
        if not spec_snapshot:
            return False

        # 比较内容是否有实质性变化
        # 使用 strip() 去除首尾空白后再比较
        current_normalized = current_spec.strip()
        snapshot_normalized = spec_snapshot.strip()

        has_changed = current_normalized != snapshot_normalized

        if has_changed:
            self.logger.info("检测到规格文档被手动修订")

        return has_changed

    def check_execution_gate(
        self,
        state: AgentState
    ) -> dict[str, Any]:
        """执行门禁检查

        在任务执行前进行质量检查，确保规格文档满足执行条件。
        检查项包括：规格完整性、审查通过状态、用户批准等。

        Args:
            state: 当前工作流状态，包含以下关键字段：
                - specification: 规格文档
                - review_reports: 审查报告列表
                - user_approved: 用户是否批准

        Returns:
            Dict[str, Any]: 门禁检查结果，包含以下字段：
                - passed: 是否通过门禁
                - blocked: 是否被阻止
                - reasons: 阻止原因列表
                - warnings: 警告信息列表
        """
        # 如果门禁未启用，直接通过
        if not self.execution_gate_enabled:
            return {
                "passed": True,
                "blocked": False,
                "reasons": [],
                "warnings": []
            }

        blocked = False
        reasons = []
        warnings = []

        # 检查 1：规格文档是否存在
        specification = state.get("specification", "")
        if not specification:
            blocked = True
            reasons.append("规格文档为空，无法执行")

        # 检查 2：规格文档是否完整（基本结构检查）
        required_sections = ["概述", "功能需求", "验收标准"]
        for section in required_sections:
            if section not in specification:
                warnings.append(f"规格文档缺少「{section}」章节")

        # 检查 3：是否有审查报告
        review_reports = state.get("review_reports", [])
        if not review_reports:
            warnings.append("尚未进行审查，建议先完成审查流程")
        else:
            # 检查最新审查报告是否有关键问题
            latest_report = review_reports[-1]
            blocking_issues = [
                issue for issue in latest_report.get("issues", [])
                if issue.get("severity") in ["Blocking", "High"]
            ]
            if blocking_issues:
                blocked = True
                reasons.append(f"存在 {len(blocking_issues)} 个阻塞性问题未解决")

        # 检查 4：用户是否批准
        if not state.get("user_approved", False):
            # 根据自动批准阈值决定是否阻止
            if self.auto_approve_threshold == "strict":
                blocked = True
                reasons.append("用户尚未批准，无法执行")
            else:
                warnings.append("用户尚未明确批准，执行需谨慎")

        return {
            "passed": not blocked,
            "blocked": blocked,
            "reasons": reasons,
            "warnings": warnings
        }

    async def get_execution_summary(
        self,
        state: AgentState
    ) -> str:
        """获取执行摘要

        生成当前工作流的执行摘要，用于向用户展示进度和状态。

        Args:
            state: 当前工作流状态

        Returns:
            str: 格式化的执行摘要文本
        """
        summary_parts = [
            "## 执行摘要\n",
            f"**规格版本**: v{state.get('spec_version', 0)}",
            f"**迭代次数**: {state.get('iteration_count', 0)}",
            f"**执行状态**: {state.get('execution_status', 'pending')}",
            f"**审查报告数**: {len(state.get('review_reports', []))}",
        ]

        # 添加门禁检查结果
        gate_result = self.check_execution_gate(state)
        if gate_result["blocked"]:
            summary_parts.append("\n**门禁状态**: ❌ 阻塞")
            summary_parts.append("**阻塞原因**:")
            for reason in gate_result["reasons"]:
                summary_parts.append(f"  - {reason}")
        else:
            summary_parts.append("\n**门禁状态**: ✅ 通过")

        # 添加警告信息
        if gate_result["warnings"]:
            summary_parts.append("\n**警告**:")
            for warning in gate_result["warnings"]:
                summary_parts.append(f"  - {warning}")

        return "\n".join(summary_parts)
