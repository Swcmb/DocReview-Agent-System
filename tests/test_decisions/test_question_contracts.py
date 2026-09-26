"""T-07：冻结 question 契约、投影纪律与 compact state 模板（规格 §5.2／§6.1）。

golden hash、顺序交换、noul 对调变异与 `_check_question` 校验锁属 T-08，
本文件只守 T-07 的验收：投影纪律、模板 schema、assess/verify 无 full specification。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, cast

import pytest

from src.decisions.question_contracts import (
    BUSINESS_QUESTION_IDS,
    FORBIDDEN_STATE_KEYS,
    FROZEN_BUSINESS_QUESTIONS,
    PRIMITIVE_ORDER,
    QUESTION_ORDER,
    STATE_TEMPLATES,
    _guard_keys,
    build_assess_state,
    build_judge_convergence_state,
    build_review_executability_state,
    build_screen_state,
    build_verify_issues_state,
    build_verify_resolutions_state,
    to_laya_questions,
)


# 冻结常量的值类型是 `object`（故意的：它同时容纳 dict criteria 与 list score
# criteria）。下面三个 helper 只做**类型收窄**，不改变任何被测行为。
def _criteria(question: Mapping[str, object]) -> dict[str, object]:
    raw = question["criteria"]
    assert isinstance(raw, dict)
    return raw


def _criteria_list(question: Mapping[str, object]) -> list[object]:
    raw = question["criteria"]
    assert isinstance(raw, list)
    return raw


def _text(state: Mapping[str, object], key: str) -> str:
    value = state[key]
    assert isinstance(value, str)
    return value


def _mapping(state: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = state[key]
    assert isinstance(value, dict)
    return value


# ---------------------------------------------------------------------------
# 冻结对象结构
# ---------------------------------------------------------------------------
def test_five_primitives_exactly():
    assert set(FROZEN_BUSINESS_QUESTIONS) == {
        "assess",
        "verify_issues",
        "review_executability",
        "verify_resolutions",
        "judge_convergence",
    }


def test_eleven_questions_total():
    # §5.2/§16.2 反复引用「全部 11 个 question」；数量漂移即契约变更。
    assert len(QUESTION_ORDER) == 11
    assert len(BUSINESS_QUESTION_IDS) == 11


def test_primitive_order_is_spec_order():
    assert PRIMITIVE_ORDER == (
        "assess",
        "verify_issues",
        "review_executability",
        "verify_resolutions",
        "judge_convergence",
    )


def test_question_ids_unique_across_primitives():
    seen: list[str] = []
    for primitive in PRIMITIVE_ORDER:
        seen.extend(FROZEN_BUSINESS_QUESTIONS[primitive])
    assert len(seen) == len(set(seen))


@pytest.mark.parametrize("primitive", PRIMITIVE_ORDER)
def test_every_question_has_type_and_instructions(primitive):
    for qid, question in FROZEN_BUSINESS_QUESTIONS[primitive].items():
        assert question["type"] in {"choice", "score", "noul"}, qid
        assert isinstance(question["instructions"], str) and question["instructions"], qid
        assert "criteria" in question, qid


def test_known_type_census():
    census: dict[str, int] = {}
    for primitive in PRIMITIVE_ORDER:
        for question in FROZEN_BUSINESS_QUESTIONS[primitive].values():
            t = str(question["type"])
            census[t] = census.get(t, 0) + 1
    # 5 choice/score + 6 noul（§5.2：labels 只能在 noul 上，故这 5 个必须无 labels）
    assert census == {"choice": 3, "score": 2, "noul": 6}


# ---------------------------------------------------------------------------
# 投影纪律（§5.2）——本文件的核心
# ---------------------------------------------------------------------------
def test_frozen_object_contains_no_labels_anywhere():
    """v1.7 形态：``labels`` 一律不写。

    命题改由 ``noul`` 的 ``criteria`` 承载；``labels`` 留着会被误当作
    「必填且与 criteria 等价」而回填，届时改写不抛异常。
    """
    for primitive in PRIMITIVE_ORDER:
        for qid, question in FROZEN_BUSINESS_QUESTIONS[primitive].items():
            assert "labels" not in question, f"{primitive}.{qid} 不应带 labels"


def test_projection_contains_no_labels_and_only_allowed_keys():
    for primitive in PRIMITIVE_ORDER:
        projected = to_laya_questions(FROZEN_BUSINESS_QUESTIONS[primitive])
        for item in projected:
            assert "labels" not in item
            assert set(item) <= {"type", "instructions", "criteria"}
            assert set(item) >= {"type", "instructions"}


def test_projection_count_and_order_preserved():
    for primitive in PRIMITIVE_ORDER:
        questions = FROZEN_BUSINESS_QUESTIONS[primitive]
        projected = to_laya_questions(questions)
        assert len(projected) == len(questions)
        # Laya 按位置回答案，顺序必须与 QUESTION_ORDER 一致。
        assert [p["type"] for p in projected] == [q["type"] for q in questions.values()]
        assert [p["instructions"] for p in projected] == [
            q["instructions"] for q in questions.values()
        ]


def test_projection_of_all_eleven_questions_has_no_labels():
    """§16.2 的零 ValueError 投影锁的前置条件：11 个 question 全部投影。"""
    flat = {qid: FROZEN_BUSINESS_QUESTIONS[p][qid] for p in PRIMITIVE_ORDER for qid in FROZEN_BUSINESS_QUESTIONS[p]}
    assert len(flat) == 11
    projected = to_laya_questions(flat)
    assert len(projected) == 11
    assert all("labels" not in item for item in projected)


def test_projection_does_not_mutate_source():
    before = copy.deepcopy(FROZEN_BUSINESS_QUESTIONS)
    for primitive in PRIMITIVE_ORDER:
        to_laya_questions(FROZEN_BUSINESS_QUESTIONS[primitive])
    assert FROZEN_BUSINESS_QUESTIONS == before


# ---------------------------------------------------------------------------
# noul criteria 硬约束（§5.2 表第 2 行）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("primitive", PRIMITIVE_ORDER)
def test_noul_criteria_keys_are_exactly_true_false(primitive):
    for qid, question in FROZEN_BUSINESS_QUESTIONS[primitive].items():
        if question["type"] != "noul":
            continue
        criteria = question["criteria"]
        assert isinstance(criteria, dict), qid
        assert set(criteria) == {"true", "false"}, qid


@pytest.mark.parametrize("primitive", PRIMITIVE_ORDER)
def test_noul_criteria_values_are_distinct_non_empty(primitive):
    """``_resolve_noul_labels`` 只要求「distinct non-empty string」，所以这里
    更严格：两支都必须有意义，且互不相同——否则渲染出的选项无法自解释。"""
    for qid, question in FROZEN_BUSINESS_QUESTIONS[primitive].items():
        if question["type"] != "noul":
            continue
        criteria = _criteria(question)
        true_text, false_text = criteria["true"], criteria["false"]
        assert isinstance(true_text, str) and true_text.strip(), qid
        assert isinstance(false_text, str) and false_text.strip(), qid
        assert true_text != false_text, qid


def test_choice_criteria_values_are_descriptive():
    for primitive in PRIMITIVE_ORDER:
        for qid, question in FROZEN_BUSINESS_QUESTIONS[primitive].items():
            if question["type"] != "choice":
                continue
            for key, value in _criteria(question).items():
                assert isinstance(value, str) and len(value) > 10, f"{qid}.{key}"


def test_score_criteria_is_ordered_list_of_labels():
    for primitive in PRIMITIVE_ORDER:
        for qid, question in FROZEN_BUSINESS_QUESTIONS[primitive].items():
            if question["type"] != "score":
                continue
            criteria = question["criteria"]
            assert isinstance(criteria, list), qid
            assert len(criteria) >= 2, qid
            assert all(isinstance(item, str) and item for item in criteria), qid


# ---------------------------------------------------------------------------
# §6.1 state 模板
# ---------------------------------------------------------------------------
def test_state_templates_cover_six_methods():
    assert set(STATE_TEMPLATES) == {
        "screen",
        "assess",
        "verify_issues",
        "review_executability",
        "verify_resolutions",
        "judge_convergence",
    }


def test_screen_template_is_wrapped_prompt():
    """``screen.prompt`` 必须包成 ``{"prompt": chunk}`` 以匹配
    ``guard_questions()`` 的字段语义（§6.1）。"""
    assert STATE_TEMPLATES["screen"] == {"prompt": "SELECTED_SECTION_CHUNK"}
    assert build_screen_state("chunk text") == {"prompt": "chunk text"}


def test_no_template_mentions_full_specification():
    for name, template in STATE_TEMPLATES.items():
        for key, placeholder in template.items():
            assert key not in FORBIDDEN_STATE_KEYS, name
            assert "SPECIFICATION" not in placeholder.upper() or name == "verify_resolutions", (
                f"{name}.{key} 的占位符疑似整份规格：{placeholder}"
            )


@pytest.mark.parametrize("name", sorted(STATE_TEMPLATES))
def test_templates_never_expose_full_specification_key(name):
    """assess/verify 只能拿 selected chunk + compact 上下文。

    放 full specification 会让分块失去意义并突破 evidence 预算（§6.1）。
    """
    for key in STATE_TEMPLATES[name]:
        assert key not in FORBIDDEN_STATE_KEYS


def test_assess_state_has_no_full_specification():
    state = build_assess_state(
        evidence_chunk="selected chunk",
        section_locator="L12-L40",
        document_context="compact overview",
    )
    assert set(state) == {"evidence_chunk", "section_locator", "document_context"}
    assert not (set(state) & FORBIDDEN_STATE_KEYS)


def test_verify_issues_state_carries_precomputed_location_resolution():
    """``location_resolution`` 是 SectionIndex 算好的结论，Laya 不回答
    location 是否有效（§6.4），所以必须原样透传。"""
    resolution = {"status": "resolved", "section_id": "S1", "start_line": 10, "end_line": 20}
    state = build_verify_issues_state(
        issue={"issue_id": "I-1", "severity": "high"},
        evidence_chunk="chunk",
        section_locator="L10-L20",
        location_resolution=resolution,
        compact_context="ctx",
    )
    assert state["location_resolution"] == resolution
    assert _mapping(state, "issue")["issue_id"] == "I-1"


def test_review_executability_state_has_no_locator():
    """它与 verify_issues 共用 issue/evidence 快照，但只产生 style_only
    标注，故不含 section_locator / location_resolution。"""
    state = build_review_executability_state(
        issue={"issue_id": "I-1"}, evidence_chunk="chunk", compact_context="ctx"
    )
    assert set(state) == {"issue", "evidence_chunk", "compact_context"}


def test_verify_resolutions_state_takes_previous_issue():
    state = build_verify_resolutions_state(
        previous_issue={"issue_id": "I-0"},
        evidence_chunk="chunk",
        section_locator="L1-L9",
        compact_context="ctx",
    )
    assert set(state) == {"previous_issue", "evidence_chunk", "section_locator", "compact_context"}


def test_judge_convergence_state_has_four_slices():
    state = build_judge_convergence_state(
        previous_issues=[{"issue_id": "I-0"}],
        current_issues=[{"issue_id": "I-1"}],
        counts={"Blocking": 0, "High": 1},
        tracker={"open": 1},
    )
    assert set(state) == {"previous_issues", "current_issues", "counts", "tracker"}
    assert _mapping(state, "counts")["High"] == 1


def test_judge_convergence_previous_issues_is_caller_scoped():
    """``previous_issues`` 只取紧邻上一轮，模块不自行扫描历史（§6.1）。"""
    state = build_judge_convergence_state(
        previous_issues=[{"issue_id": "I-9"}],
        current_issues=[],
        counts={},
        tracker={},
    )
    assert state["previous_issues"] == [{"issue_id": "I-9"}]


# ---------------------------------------------------------------------------
# compact 兜底
# ---------------------------------------------------------------------------
def test_compact_fields_truncated_to_budget():
    huge = "x" * 5000
    state = build_assess_state(
        evidence_chunk="chunk", section_locator=huge, document_context=huge
    )
    assert len(_text(state, "section_locator")) == 1024
    assert len(_text(state, "document_context")) == 1024
    # evidence_chunk 不受 compact 预算约束——它由 batch plan 保证 ≤1024
    assert _text(state, "evidence_chunk") == "chunk"


def test_short_compact_fields_untouched():
    state = build_assess_state(
        evidence_chunk="chunk", section_locator="L1", document_context="d"
    )
    assert state["section_locator"] == "L1"
    assert state["document_context"] == "d"


def test_builders_copy_nested_inputs():
    """快照必须复制：调用方复用/修改原对象时，audit 里的 state 不能被带着变。"""
    issue = {"issue_id": "I-1"}
    resolution = {"status": "resolved"}
    state = build_verify_issues_state(
        issue=issue,
        evidence_chunk="chunk",
        section_locator="L1",
        location_resolution=resolution,
        compact_context="ctx",
    )
    issue["issue_id"] = "MUTATED"
    resolution["status"] = "MUTATED"
    assert _mapping(state, "issue")["issue_id"] == "I-1"
    assert _mapping(state, "location_resolution")["status"] == "resolved"


def test_builder_signature_rejects_unknown_kwarg():
    """第一层防御：Python 签名本身拒绝未知关键字参数。

    必须在函数体执行前就抛 ``TypeError``——所以下面那条 ValueError 分支
    走这条路是**到不了**的。这里用 cast 绕过静态检查，因为「不存在
    ``extra_field`` 形参」正是本测试要钉住的事实。
    """
    build = cast(Any, build_assess_state)
    with pytest.raises(TypeError):
        build(
            evidence_chunk="chunk", section_locator="L1", document_context="d",
            extra_field="leak",
        )


def test_guard_rejects_key_set_drift():
    """第二层防御：键集合与模板不符时由 ``_guard_keys`` 抛 ``ValueError``。

    该分支经由关键字参数**不可达**（签名已先抛 TypeError），但它不是死代码：
    一旦有人把形参改成 ``**extra``、或新增形参却忘了同步模板，这里就是
    唯一的兜底。直接调用守卫来覆盖它，而不是假装 kwargs 能走到。
    """
    with pytest.raises(ValueError, match="键与模板不符"):
        _guard_keys({"prompt": "c", "extra": "leak"}, "screen")


def test_guard_rejects_full_specification_key():
    """模板若被塞进 full specification 类键，守卫必须拦下（§6.1）。"""
    with pytest.raises(ValueError, match="键与模板不符"):
        _guard_keys({"prompt": "c", "specification": "whole doc"}, "screen")
    with pytest.raises(ValueError, match="键与模板不符"):
        _guard_keys(
            {"evidence_chunk": "c", "section_locator": "l", "document_context": "d", "document": "full"},
            "assess",
        )
