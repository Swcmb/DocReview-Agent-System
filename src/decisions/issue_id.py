"""Issue ID 分配与必要字段校验（规格 §3.3）。

从 `DocReviewAgent._compile_markdown_report()` 抽出的独立纯函数。分配顺序被
规格钉死（§3.3）：

1. 收集全部 issue，保留原始顺序与原始字段；
2. **先**独立 ``assign_issue_ids()``；
3. ``verify_issues`` / ``verify_resolutions`` 只消费已分配 ID 的快照；
4. structured conclusion 只读结构化字段；
5. ``_compile_markdown_report()`` 只渲染，**不再**有任何业务副作用。

因此 ID 序号按**输入顺序**推进，而不是按 severity 排序后的顺序——排序只是
展示行为，把它混进 ID 分配会让同一个 issue 集合因展示顺序不同而拿到不同 ID，
这正是 §3.3 要求「不改变输入列表顺序」要防的漂移。

``validate_issues()`` 单独暴露而不是内联在 ``assign_issue_ids()`` 里，是为了
让「非法 severity」「非空重复 ID」两条拒绝分支可被直接触达（§15.1 L-03）：
这两条在合法调用路径上不会自然出现（正常流程的 issue 由各审查步骤构造，severity
来自受控枚举），只能手工构造 issue dict 才能验证。写成「先跑
``assign_issue_ids()`` 再断言抛错」是假测试，永远绿。
"""

from __future__ import annotations

from collections.abc import Iterable, MutableSequence

from src.schemas.models import VALID_SEVERITIES, IssueStatus, generate_issue_id

__all__ = [
    "IssueIdError",
    "validate_issues",
    "assign_issue_ids",
]


class IssueIdError(ValueError):
    """Issue 必要字段非法。

    继承 ``ValueError``：调用方只需捕获一类，且不会与 Pydantic/其他
    ``ValueError`` 子类混淆——本异常只在 issue 契约校验处抛出。
    """


def _require_text(issue: IssueStatus, field: str, where: str) -> str:
    """取出必填文本字段，空/空白视为非法。"""
    value = issue.get(field)
    if not isinstance(value, str) or not value.strip():
        raise IssueIdError(f"{where}: 字段 {field!r} 不得为空")
    return value


def validate_issues(issues: Iterable[IssueStatus]) -> None:
    """校验 issue 必要字段，不修改任何输入。

    只拒绝规格点名的四类问题：非法 severity、空 description、空 issue_type、
    非空重复 issue_id。**空/缺失 issue_id 是合法的「未分配」**，由
    ``assign_issue_ids()`` 原位补齐，不在此处报错。

    被拒绝的输入一律不做任何修复——静默修好一个非法 severity 会让后续统计
    悄悄失真，比直接失败危险得多。

    Raises:
        IssueIdError: 任一必要字段非法时。
    """
    seen_ids: set[str] = set()

    for index, issue in enumerate(issues):
        where = f"issues[{index}]"

        severity = issue.get("severity")
        if severity not in VALID_SEVERITIES:
            raise IssueIdError(
                f"{where}: 非法 severity {severity!r}，合法取值 {list(VALID_SEVERITIES)}"
            )

        _require_text(issue, "description", where)
        _require_text(issue, "issue_type", where)

        # 缺失/空串是合法的「未分配」，跳过重复检查。
        issue_id = issue.get("issue_id")
        if isinstance(issue_id, str) and issue_id.strip():
            if issue_id in seen_ids:
                raise IssueIdError(f"{where}: 重复的 issue_id {issue_id!r}")
            seen_ids.add(issue_id)


def assign_issue_ids(
    issues: MutableSequence[IssueStatus],
    iteration: int,
) -> MutableSequence[IssueStatus]:
    """原位补齐 issue ID，返回同一个列表对象。

    保持输入列表顺序与各 issue 原始字段不变；仅把空/缺失的 ``issue_id``
    覆盖为新生成的 ID。ID 格式沿用 ``generate_issue_id()``，按
    Blocking/High/Medium/Low 各自计数、轮内从 1 递增。

    Args:
        issues: 待分配 ID 的 issue 列表，会被就地修改。
        iteration: 当前审查轮次，从 1 开始。

    Returns:
        与入参同一个列表对象（便于链式使用，但调用方不应依赖此返回）。

    Raises:
        IssueIdError: 必要字段非法或存在非空重复 ID。此时不修改任何 issue。
    """
    # 先整体校验再逐个赋值：保证「要么全部分配，要么一个都不改」，
    # 避免写到第 k 个 issue 才抛错，留下半分配状态。
    validate_issues(issues)

    counters: dict[str, int] = dict.fromkeys(VALID_SEVERITIES, 0)

    for issue in issues:
        existing = issue.get("issue_id")
        if isinstance(existing, str) and existing.strip():
            # 已有合法 ID：保留，不重新编号。
            continue

        severity = issue["severity"]
        counters[severity] += 1
        issue["issue_id"] = generate_issue_id(severity, iteration, counters[severity])

    return issues
