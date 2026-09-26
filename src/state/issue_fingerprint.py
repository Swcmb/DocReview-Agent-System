"""issue 内容指纹——全项目**唯一**实现（规格 §3.3／§10.4，T-15）。

为什么需要它：``issue_id`` 内嵌轮次号（``BK-3-2``），跨轮必然不同，因此
**不能**用作「同一个问题」的身份。指纹必须只由**内容**决定。

稳定字段投影（§3.3 步骤 4／§10.4）——只有这三个字段参与身份：

===========================  ==========================================
字段                          理由
===========================  ==========================================
``severity``                  严重级别是问题性质的一部分
``issue_type``                问题类别
``description``（规范化后）    问题的实际内容
===========================  ==========================================

**排除** ``issue_id``（含轮次号）、``suggestion``／``location``（可随轮次
微调而不改变问题本身）、``status``（会随修复流转）、以及一切时间戳。

``description`` 规范化：去空白、去标点、转小写。这样同义改写（``缺少 AC.``
与 ``缺少AC``）仍被判定为同一问题，避免停滞检测被措辞抖动绕过。

本模块是唯一实现，供**停滞检测**、``new_in_current_round``、**tracker** 共用
（§13.2）。``review_workflow.py`` 不得再内联计算指纹——两处实现一旦漂移，
停滞判断就会在两套语义之间摇摆。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..decisions.question_contracts import sha256_canonical

__all__ = [
    "FINGERPRINT_FIELDS",
    "normalize_description",
    "issue_projection",
    "issue_fingerprint",
    "fingerprint_set",
]

#: 参与身份的稳定字段投影，顺序即 canonical JSON 的键序。
FINGERPRINT_FIELDS: tuple[str, ...] = ("severity", "issue_type", "description")

#: 规范化时剔除：空白 + 非字母数字（含下划线）。
_NOISE = re.compile(r"[\s\W_]+", flags=re.UNICODE)


def normalize_description(description: object) -> str:
    """把 description 规范化为可比形式：去空白/标点、转小写。

    非字符串输入（``None``、缺失、非文本）一律退化为空串，而不是抛错——
    指纹用于**比较**，坏数据应当表现为「与空描述不可区分」，而不是让整个
    停滞检测崩掉。
    """
    if not isinstance(description, str):
        return ""
    return _NOISE.sub("", description).lower()


def issue_projection(issue: Mapping[str, Any]) -> dict[str, str]:
    """构造 issue 的稳定字段投影。

    只含 :data:`FINGERPRINT_FIELDS`，值全部规范化为 ``str``，因此
    canonical JSON 序列化是稳定的。
    """
    return {
        "severity": str(issue.get("severity", "") or ""),
        "issue_type": str(issue.get("issue_type", "") or ""),
        "description": normalize_description(issue.get("description")),
    }


def issue_fingerprint(issue: Mapping[str, Any]) -> str:
    """issue 的内容身份，形如 ``sha256:<64 hex>``。

    相同内容恒得相同指纹，**与轮次、issue_id、措辞标点无关**。

    Args:
        issue: 单条审查问题；只读取 :data:`FINGERPRINT_FIELDS` 三个字段。

    Returns:
        ``sha256:`` 前缀的 canonical 摘要。
    """
    return sha256_canonical(issue_projection(issue))


def fingerprint_set(issues: Any) -> set[str]:
    """一组 issue 的指纹集合，供集合差/相等比较使用。

    Args:
        issues: 任意可迭代的 issue 映射序列。

    Returns:
        指纹集合；重复内容自然折叠为一个元素。
    """
    return {issue_fingerprint(issue) for issue in issues}
