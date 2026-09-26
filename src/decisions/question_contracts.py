"""五个非 guard 业务原语的冻结 question 契约与 compact state 模板（规格 §5.2／§6.1）。

本模块是「传给 Laya 的权威源」。**不得**凭直觉改动字段位置——上表的形态受
Laya 0.3.20 两处独立校验约束（§5.2 表）：

1. ``labels`` 只允许出现在 ``noul`` 上（`laya/agent.py:529-531`）——所以
   5 个 ``choice``/``score`` 题一律不带 ``labels``；
2. ``noul`` 的 ``criteria`` 键必须恰为 ``true``/``false``（`laya/agent.py:521-528`）。

v1.7 起的形态：``labels`` 一律不写。6 个 ``noul`` 把待判命题放进 ``criteria``
（由 Laya 回落到 ``_DEFAULT_NOUL_LABELS``），因为 ``criteria`` 是投影后**唯一**
承载命题的通道；若命题只活在 ``labels`` 里，一旦被误当作「必填且与 labels 等价」
而填充或 true/false 对调，命题即被改写**且不抛异常**（`_resolve_noul_labels` 只
要求「distinct non-empty string」，`common.py:38-45`）。

**投影纪律**：`to_laya_questions()` 只输出 Laya 支持的 ``type`` /
``instructions`` / ``criteria`` 三个键，``labels`` 永不进入 Laya。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any, Final, cast

__all__ = [
    "FROZEN_BUSINESS_QUESTIONS",
    "PRIMITIVE_ORDER",
    "QUESTION_ORDER",
    "STATE_TEMPLATES",
    "BUSINESS_QUESTION_IDS",
    "to_laya_questions",
    "schema_descriptor",
    "canonical_json_bytes",
    "sha256_canonical",
    "build_screen_state",
    "build_assess_state",
    "build_verify_issues_state",
    "build_review_executability_state",
    "build_verify_resolutions_state",
    "build_judge_convergence_state",
]


# ---------------------------------------------------------------------------
# §5.2 冻结 question 对象（11 个，5 个非 guard 原语）
# ---------------------------------------------------------------------------
FROZEN_BUSINESS_QUESTIONS: Final[dict[str, dict[str, dict[str, object]]]] = {
    "assess": {
        "document_type": {
            "type": "choice",
            "instructions": "Classify the main purpose of `document_context` and the selected `evidence_chunk`.",
            "criteria": {
                "prd": "A product requirements document describing users, needs, scope, and expected outcomes.",
                "technical_plan": "A technical solution describing architecture, components, interfaces, and technical decisions.",
                "implementation_plan": "An ordered plan describing implementation tasks, sequencing, dependencies, and verification.",
                "acceptance_checklist": "A checklist of verifiable acceptance conditions and test evidence.",
                "non_technical": "The document does not fit one of the technical or product document types above.",
            },
        },
        "completeness": {
            "type": "score",
            "instructions": "Rate how complete and executable the selected `evidence_chunk` is in the compact `document_context`, from the least to the most complete.",
            "criteria": [
                "almost_no_substantive_information",
                "only_titles_and_overview",
                "requirements_without_acceptance_criteria",
                "structured_but_missing_important_details",
                "complete_and_executable",
            ],
        },
        "has_acceptance_criteria": {
            "type": "noul",
            "instructions": "Does the selected `evidence_chunk` contain at least one concrete, testable acceptance criterion?",
            "criteria": {
                "false": "No concrete testable acceptance criterion is present.",
                "true": "A concrete testable acceptance criterion is present.",
            },
        },
        "has_vague_terms": {
            "type": "noul",
            "instructions": "Does the selected `evidence_chunk` use an important requirement or constraint that is materially vague, unmeasurable, or undefined?",
            "criteria": {
                "false": "Important requirements and constraints are sufficiently defined.",
                "true": "At least one important requirement or constraint is materially vague.",
            },
        },
    },
    "verify_issues": {
        "grounded": {
            "type": "noul",
            "instructions": "Is `issue` supported by explicit evidence in the selected `evidence_chunk`?",
            "criteria": {
                "false": "The specification does not explicitly support the issue.",
                "true": "The specification explicitly supports the issue.",
            },
        },
        "severity_review": {
            "type": "choice",
            "instructions": "Which severity best matches the documented impact of `issue`?",
            "criteria": {
                "blocking": "The issue prevents a required workflow or makes the deliverable unusable.",
                "high": "The issue creates a substantial correctness, safety, or delivery risk.",
                "medium": "The issue is material but has a bounded workaround or limited impact.",
                "low": "The issue is minor, editorial, or has little effect on execution.",
            },
        },
    },
    "review_executability": {
        "style_only": {
            "type": "noul",
            "instructions": "Is `issue` only a wording, formatting, or stylistic concern rather than a substantive specification problem?",
            "criteria": {
                "false": "The issue is substantive and affects meaning, correctness, or execution.",
                "true": "The issue is only wording, formatting, or stylistic.",
            },
        },
    },
    "verify_resolutions": {
        "resolution_status": {
            "type": "choice",
            "instructions": "Given `previous_issue` and the selected `evidence_chunk`, classify the resolution state of that previous issue.",
            "criteria": {
                "fixed": "The previously reported problem is no longer present in the current specification.",
                "partially_fixed": "The previous problem is reduced but a material part remains.",
                "unfixed": "The previous problem remains materially unchanged.",
                "outdated": "The previous issue no longer applies because the relevant requirement or context changed.",
            },
        },
    },
    "judge_convergence": {
        "blocking_high_resolved": {
            "type": "noul",
            "instructions": "Are all Blocking and High issues from `previous_issues` resolved or no longer applicable in `current_issues`?",
            "criteria": {
                "false": "At least one current or immediately previous Blocking/High issue remains unresolved.",
                "true": "No current or immediately previous unresolved Blocking/High issue remains.",
            },
        },
        "converged": {
            "type": "noul",
            "instructions": "Would another review round likely discover a substantive new problem, rather than only wording or formatting issues?",
            "criteria": {
                "false": "Another round is unlikely to discover a substantive new problem.",
                "true": "Another round is likely to discover a substantive new problem.",
            },
        },
        "improvement": {
            "type": "score",
            "instructions": "Rate the substantive improvement from the previous specification to the current specification.",
            "criteria": [
                "no_improvement",
                "minor_improvement",
                "substantial_improvement",
                "complete_resolution",
            ],
        },
    },
}

#: 原语顺序即 `schema_descriptor` 的 question 顺序来源（§5.2：顺序是语义的一部分）。
PRIMITIVE_ORDER: Final[tuple[str, ...]] = (
    "assess",
    "verify_issues",
    "review_executability",
    "verify_resolutions",
    "judge_convergence",
)

#: 全部 11 个 question ID，按原语与原语内顺序展平。
QUESTION_ORDER: Final[tuple[str, ...]] = tuple(
    qid for primitive in PRIMITIVE_ORDER for qid in FROZEN_BUSINESS_QUESTIONS[primitive]
)

BUSINESS_QUESTION_IDS: Final[frozenset[str]] = frozenset(QUESTION_ORDER)


# ---------------------------------------------------------------------------
# §5.2 投影纪律
# ---------------------------------------------------------------------------
#: Laya 支持的 question 键。投影只允许这三个（§5.2）。
_LAYA_ALLOWED_KEYS: Final[frozenset[str]] = frozenset({"type", "instructions", "criteria"})


def to_laya_questions(questions: Mapping[str, Mapping[str, object]]) -> list[dict[str, object]]:
    """把冻结 question 投影为 Laya 接受的形态（§5.2 投影纪律）。

    只输出 ``type`` / ``instructions`` / ``criteria``；``labels`` 永不进入 Laya。
    这条投影线是 B-01/B-02 的唯一拦截点：``labels`` 若随 choice/score 抵达
    ``laya/agent.py:529-531`` 会直接 ``ValueError``。

    返回**有序 list**且不带 ``id``——Laya 按位置回答案，调用方必须依赖
    `QUESTION_ORDER` 把答案映射回 question ID。这也是「顺序是语义的一部分」
    的原因：换序即换语义。

    Args:
        questions: question ID -> 冻结 question 定义，须按 `QUESTION_ORDER` 顺序。

    Returns:
        投影后的 question 列表，键集合恒为 ``LAYA_ALLOWED_KEYS`` 的子集。
    """
    projected: list[dict[str, object]] = []
    for question in questions.values():
        item: dict[str, object] = {
            "type": question["type"],
            "instructions": question["instructions"],
        }
        criteria = question.get("criteria")
        if criteria is not None:
            item["criteria"] = criteria
        projected.append(item)
    return projected


# ---------------------------------------------------------------------------
# §5.2 order-aware schema descriptor
# ---------------------------------------------------------------------------
def canonical_json_bytes(value: object) -> bytes:
    """§5.2 的统一 canonical 序列化：sort_keys + 紧凑分隔符 + 保留非 ASCII。

    ``sort_keys=True`` 只排序 dict 的键；**list 顺序仍是语义的一部分**。
    """
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_canonical(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def schema_descriptor(ordered_questions: Mapping[str, Mapping[str, object]]) -> list[dict[str, object]]:
    """order-aware schema descriptor（§5.2）。

    键序固定，值全部参与哈希：

    - question ID 按**显式列表顺序**保留；
    - ``choice`` 的 criteria 转 ``[[key, value], ...]``，保留声明顺序；
    - ``score`` 的 criteria 保留有序数组；
    - ``noul`` 的 ``criteria`` 按 ``("true", "false")`` 固定键序转
      ``[[key, value], ...]``，使其值（待判命题）参与哈希。**这是 v1.8 依
      Blocker-1 新增的分支**：v1.7 缺它会让 ``noul`` 落入 else 分支、命题载荷
      零覆盖——改写任一 noul 的 true/false 命题，``_check_question`` 仍通过
      （键仍是 {true,false}）、golden hash 仍全绿。
    - 仅无序的 ``labels`` metadata 使用**排序后**的键值数组。

    ``guard`` 的 3 个 noul 只有 ``type`` + ``instructions``、**无 ``criteria`` 键**
    （`laya/presets.py:82-119`），故本分支不触及 guard。

    本函数里的 ``assert isinstance(...)`` 是**类型收窄**（让 mypy 看清
    ``object`` 其实是 dict），**不是**运行时校验：``-O`` 会把它剥掉。真正的
    形状防线在 `tests/test_decisions/test_question_contracts.py`（criteria 键集
    与题型匹配）与 T-08 的 golden hash／`_check_question` 锁。即便 assert 被剥，
    形状错了也会在后续索引处抛 TypeError，不会静默算出错误 hash。
    """
    descriptor: list[dict[str, object]] = []
    for qid in ordered_questions:
        question = ordered_questions[qid]
        item: dict[str, object] = {
            "id": qid,
            "type": question["type"],
            "instructions": question["instructions"],
        }
        if question["type"] == "choice":
            criteria = question["criteria"]
            assert isinstance(criteria, dict)
            item["criteria"] = [[key, criteria[key]] for key in criteria]
        elif question["type"] == "score":
            item["criteria"] = list(cast("Iterable[Any]", question["criteria"]))
        elif question["type"] == "noul" and "criteria" in question:
            # 键序按 true/false 固定；值参与哈希 —— 命题被改写或对调必改 hash
            criteria = question["criteria"]
            assert isinstance(criteria, dict)
            item["criteria"] = [
                [key, criteria[key]] for key in ("true", "false") if key in criteria
            ]
        if "labels" in question:
            raw_labels = question["labels"]
            if isinstance(raw_labels, dict):
                item["labels"] = [[key, raw_labels[key]] for key in sorted(raw_labels)]
            else:
                item["labels"] = list(cast("Iterable[Any]", raw_labels))
        descriptor.append(item)
    return descriptor


# ---------------------------------------------------------------------------
# §6.1 state 模板
# ---------------------------------------------------------------------------
#: 大写符号是类型变量，不是待补文本；实际值由当前 state 填充。
STATE_TEMPLATES: Final[dict[str, dict[str, str]]] = {
    "screen": {"prompt": "SELECTED_SECTION_CHUNK"},
    "assess": {
        "evidence_chunk": "SELECTED_EVIDENCE_CHUNK",
        "section_locator": "COMPACT_SECTION_LOCATOR",
        "document_context": "COMPACT_DOCUMENT_CONTEXT",
    },
    "verify_issues": {
        "issue": "ASSIGNED_ISSUE",
        "evidence_chunk": "SELECTED_EVIDENCE_CHUNK",
        "section_locator": "COMPACT_SECTION_LOCATOR",
        "location_resolution": "LOCATION_RESOLUTION",
        "compact_context": "COMPACT_ISSUE_CONTEXT",
    },
    "review_executability": {
        "issue": "CURRENT_ISSUE_SNAPSHOT",
        "evidence_chunk": "SELECTED_EVIDENCE_CHUNK",
        "compact_context": "COMPACT_ISSUE_CONTEXT",
    },
    "verify_resolutions": {
        "previous_issue": "PREVIOUS_ISSUE_SNAPSHOT",
        "evidence_chunk": "SELECTED_EVIDENCE_CHUNK",
        "section_locator": "COMPACT_SECTION_LOCATOR",
        "compact_context": "COMPACT_RESOLUTION_CONTEXT",
    },
    "judge_convergence": {
        "previous_issues": "PREVIOUS_ISSUE_SNAPSHOTS",
        "current_issues": "CURRENT_ISSUE_SNAPSHOTS",
        "counts": "DETERMINISTIC_SEVERITY_COUNTS",
        "tracker": "CANONICAL_ISSUE_TRACKER",
    },
}

#: 任何 state 模板都**不得**出现的键——放 full specification 会同时超出
#: evidence 预算并让 assess/verify 退化成「拿全文提问」（§6.1）。
FORBIDDEN_STATE_KEYS: Final[frozenset[str]] = frozenset(
    {"specification", "full_specification", "document", "full_document", "document_content"}
)

#: 紧凑上下文的字符上限（§6.1 要求 compact；具体预算见 §11.1 的
#: ``max_chars_per_chunk`` 与 batch plan，这里只做模板级的兜底）。
MAX_COMPACT_CONTEXT_CHARS: Final = 1024


def _truncate(value: str, limit: int = MAX_COMPACT_CONTEXT_CHARS) -> str:
    """把紧凑上下文字段截到预算内。

    截断是**兜底**而非常态：正常路径由 batch plan 保证预算。留在这里是因为
    一旦有人误把整份规格塞进 compact_context，没有截断就会直接突破 evidence
    预算，而这种越界在 trace 里看不出来。
    """
    if len(value) <= limit:
        return value
    return value[:limit]


def _guard_keys(state: Mapping[str, object], template: str) -> None:
    """断言 state 的键集合与模板一致，且不含 full specification 类键。"""
    expected = set(STATE_TEMPLATES[template])
    actual = set(state)
    if actual != expected:
        raise ValueError(
            f"{template} state 键与模板不符：多 {actual - expected} / 少 {expected - actual}"
        )
    leaked = actual & FORBIDDEN_STATE_KEYS
    if leaked:
        raise ValueError(f"{template} state 不得包含 full specification 字段：{sorted(leaked)}")


def build_screen_state(chunk: str) -> dict[str, object]:
    """§6.1：``screen`` 的 ``prompt`` 必须包装为 ``{"prompt": chunk}``。

    这是为匹配 ``guard_questions()`` 的字段语义——guard 期待名为 ``prompt``
    的字段，直接塞裸 chunk 会被当成未知字段。
    """
    state: dict[str, object] = {"prompt": chunk}
    _guard_keys(state, "screen")
    return state


def build_assess_state(
    evidence_chunk: str, section_locator: str, document_context: str
) -> dict[str, object]:
    """assess：只含 selected evidence chunk + compact locator/context。

    **不得**放 full specification + chunk（§6.1）——那会让 assess 退化成
    「拿全文提问」，既超预算又让分块失去意义。
    """
    state: dict[str, object] = {
        "evidence_chunk": evidence_chunk,
        "section_locator": _truncate(section_locator),
        "document_context": _truncate(document_context),
    }
    _guard_keys(state, "assess")
    return state


def build_verify_issues_state(
    issue: Mapping[str, object],
    evidence_chunk: str,
    section_locator: str,
    location_resolution: Mapping[str, object],
    compact_context: str,
) -> dict[str, object]:
    """verify_issues：``location_resolution`` 由 SectionIndex 计算。

    Laya 不回答「location 是否有效」（§6.4），所以该字段是**算好的结论**，
    不是待 Laya 判断的输入。
    """
    state: dict[str, object] = {
        "issue": dict(issue),
        "evidence_chunk": evidence_chunk,
        "section_locator": _truncate(section_locator),
        "location_resolution": dict(location_resolution),
        "compact_context": _truncate(compact_context),
    }
    _guard_keys(state, "verify_issues")
    return state


def build_review_executability_state(
    issue: Mapping[str, object], evidence_chunk: str, compact_context: str
) -> dict[str, object]:
    """review_executability：与 verify_issues 共用同一 issue/evidence 快照。

    它是 verify_issues 的内部子原语，只产生 ``style_only`` 标注，故模板里
    没有 ``section_locator`` / ``location_resolution``——那些与「是否只是文字
    表面问题」无关。
    """
    state: dict[str, object] = {
        "issue": dict(issue),
        "evidence_chunk": evidence_chunk,
        "compact_context": _truncate(compact_context),
    }
    _guard_keys(state, "review_executability")
    return state


def build_verify_resolutions_state(
    previous_issue: Mapping[str, object],
    evidence_chunk: str,
    section_locator: str,
    compact_context: str,
) -> dict[str, object]:
    """verify_resolutions：``previous_issue`` 只取紧邻上一轮的快照。"""
    state: dict[str, object] = {
        "previous_issue": dict(previous_issue),
        "evidence_chunk": evidence_chunk,
        "section_locator": _truncate(section_locator),
        "compact_context": _truncate(compact_context),
    }
    _guard_keys(state, "verify_resolutions")
    return state


def build_judge_convergence_state(
    previous_issues: list[Mapping[str, object]],
    current_issues: list[Mapping[str, object]],
    counts: Mapping[str, int],
    tracker: Mapping[str, Any],
) -> dict[str, object]:
    """judge_convergence：``previous_issues`` 只取紧邻上一轮。

    **不扫描陈旧的全量历史**（§6.1）——把全部历史塞进去会让 convergence 判断
    被早已修复的 issue 污染。
    """
    state: dict[str, object] = {
        "previous_issues": [dict(item) for item in previous_issues],
        "current_issues": [dict(item) for item in current_issues],
        "counts": dict(counts),
        "tracker": dict(tracker),
    }
    _guard_keys(state, "judge_convergence")
    return state
