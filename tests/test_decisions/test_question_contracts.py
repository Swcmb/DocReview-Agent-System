"""冻结 question 契约、投影纪律、compact state 模板与 golden hash 锁。

- **T-07**：§5.2 投影纪律、§6.1 state 模板 schema、assess/verify 无 full specification。
- **T-08**：v1.8 六个 golden hash、顺序交换、noul 对调变异（Blocker-1）、
  投影后过 Laya `_check_question`（B-01/B-02 回归锁）、guard hash 稳定性、
  规格内五处逐位一致的机械校验。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
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
    schema_descriptor,
    sha256_canonical,
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


# ===========================================================================
# T-08：v1.8 golden hash、B-01/B-02 回归锁、Blocker-1 变异锁
# ===========================================================================

# 规格 §5.2 的六个 v1.8 golden hash。**实现不得运行时生成、替换或覆盖它们**；
# 若 question 文本、顺序、labels 或 guard source 变化，必须先更新并重新审核。
GOLDEN_HASHES: dict[str, str] = {
    "assess": "sha256:35f49cb2e835c5af7fadd6ccd60ffe034daebd842dfc0be9d5cfc1a097836767",
    "verify_issues": "sha256:85442fcf535f388c020150ee989f89b43c7b69c640aa31eba88526e26828789a",
    "review_executability": "sha256:483dce24c69b4dbf3cd8890826faec543dd9c69cbac1b193c1e6de01ed7c7a0f",
    "verify_resolutions": "sha256:14bfb5791242a54530c37fbfdd037d9247d47edb8ce3551b3d83c79ca151819d",
    "judge_convergence": "sha256:ddd1b655c76785ec1b93812641cfc69e243ae5dbac7980b5cb5594c8782a210e",
    "guard": "sha256:8fdc1e131c1173fb9aa26f40e3c9431d4d7ad77cd812c89d2de0fe5e4940ebc4",
}

#: 规格中每个 digest 的实测出现次数。§5.2 要求「6 个值必须在 §5.2 表、§5.3
#: pin、§9.1、§9.2、§9.3 五处逐位一致，以 grep 验证」。这里记的是本规格当前
#: 的真实计数，不是「至少 N 次」——精确计数才能在任一处被单独改动时报警。
SPEC_HASH_OCCURRENCES: dict[str, int] = {
    "assess": 3,
    "verify_issues": 6,
    "review_executability": 3,
    "verify_resolutions": 3,
    "judge_convergence": 3,
    "guard": 4,
}

_SPEC_PATH = (
    Path(__file__).resolve().parents[2]
    / "claude-context"
    / "specs"
    / "2026-09-25-laya-decision-layer-integration-spec.md"
)


def _flat_business_questions() -> dict[str, dict[str, object]]:
    """按 QUESTION_ORDER 展平 11 题——descriptor 的规范输入顺序。"""
    flat: dict[str, dict[str, object]] = {}
    for primitive in PRIMITIVE_ORDER:
        for qid, question in FROZEN_BUSINESS_QUESTIONS[primitive].items():
            flat[qid] = question
    return flat


def _guard_questions() -> dict[str, dict[str, object]]:
    """真实 guard 预设（§5.3：不得复制一份可变副本，故此处 import 而非复刻）。"""
    laya = pytest.importorskip(
        "laya.presets",
        reason="需要本地 laya 仓库（§5.3 要求 screen 调 laya.presets.guard_questions）",
    )
    return dict(laya.guard_questions())


# --- golden hash -----------------------------------------------------------
@pytest.mark.parametrize("primitive", PRIMITIVE_ORDER)
def test_business_schema_golden_hash(primitive):
    assert sha256_canonical(schema_descriptor(FROZEN_BUSINESS_QUESTIONS[primitive])) == GOLDEN_HASHES[primitive]


def test_guard_schema_golden_hash():
    """§5.2/§5.3：guard 三轮逐位不变。其 3 个 noul 无 ``criteria`` 键，
    故 v1.8 新增的 noul 分支不触及 guard——这正是「算式口径未变」的交叉验证。"""
    assert sha256_canonical(schema_descriptor(_guard_questions())) == GOLDEN_HASHES["guard"]


def test_guard_question_ids_and_order_pinned():
    questions = _guard_questions()
    assert list(questions) == ["jailbreak", "prompt_injection", "sensitive_data", "harm_severity", "topic"]


def test_guard_noul_questions_have_no_criteria_key():
    """Laya 官方 guard 的 3 个 noul 只有 type + instructions（presets.py:82-119）。
    若哪天它们长出 criteria，本分支就会开始影响 guard hash——必须显式盯住。"""
    for qid, question in _guard_questions().items():
        if question["type"] == "noul":
            assert "criteria" not in question, qid
            assert "labels" not in question, qid


# --- 顺序 / 变异锁（Blocker-1）---------------------------------------------
def test_question_order_swap_changes_hash():
    """顺序是语义的一部分：交换两个 question 必须改 hash。"""
    questions = _flat_business_questions()
    qids = list(questions)
    baseline = sha256_canonical(schema_descriptor(questions))
    swapped = {qid: questions[qid] for qid in [qids[1], qids[0], *qids[2:]]}
    assert sha256_canonical(schema_descriptor(swapped)) != baseline


def test_choice_criteria_order_swap_changes_hash():
    questions = copy.deepcopy(FROZEN_BUSINESS_QUESTIONS["assess"])
    baseline = sha256_canonical(schema_descriptor(questions))
    criteria = _criteria(questions["document_type"])
    keys = list(criteria)
    questions["document_type"]["criteria"] = {k: criteria[k] for k in [keys[1], keys[0], *keys[2:]]}
    assert sha256_canonical(schema_descriptor(questions)) != baseline


def test_score_criteria_order_swap_changes_hash():
    questions = copy.deepcopy(FROZEN_BUSINESS_QUESTIONS["assess"])
    baseline = sha256_canonical(schema_descriptor(questions))
    levels = _criteria_list(questions["completeness"])
    questions["completeness"]["criteria"] = [levels[1], levels[0], *levels[2:]]
    assert sha256_canonical(schema_descriptor(questions)) != baseline


def test_noul_true_false_swap_changes_hash():
    """Blocker-1 的核心锁：对调 noul 的 true/false 命题**必须**改 hash。

    v1.7 缺 noul 分支时这一改动对 hash 零影响——命题不进哈希输入，
    ``_check_question`` 也照样通过（键仍是 {true,false}），故本测试是
    「v1.6 的 B-02 修正载荷确实被覆盖」的唯一证据。
    """
    questions = copy.deepcopy(FROZEN_BUSINESS_QUESTIONS["review_executability"])
    baseline = sha256_canonical(schema_descriptor(questions))
    criteria = _criteria(questions["style_only"])
    questions["style_only"]["criteria"] = {"true": criteria["false"], "false": criteria["true"]}
    assert sha256_canonical(schema_descriptor(questions)) != baseline


def test_noul_proposition_rewrite_changes_hash():
    """改写任一 noul 命题文本也必须改 hash（不只是对调）。"""
    questions = copy.deepcopy(FROZEN_BUSINESS_QUESTIONS["assess"])
    baseline = sha256_canonical(schema_descriptor(questions))
    _criteria(questions["has_vague_terms"])["true"] = "REWRITTEN PROPOSITION"
    assert sha256_canonical(schema_descriptor(questions)) != baseline


def test_every_noul_proposition_is_hash_covered():
    """逐一确认 6 个 noul 的命题都进了哈希输入，而非只有抽到的那个。"""
    for primitive in PRIMITIVE_ORDER:
        for qid, question in FROZEN_BUSINESS_QUESTIONS[primitive].items():
            if question["type"] != "noul":
                continue
            mutated = copy.deepcopy(FROZEN_BUSINESS_QUESTIONS[primitive])
            _criteria(mutated[qid])["true"] = "SENTINEL"
            baseline = sha256_canonical(schema_descriptor(FROZEN_BUSINESS_QUESTIONS[primitive]))
            assert sha256_canonical(schema_descriptor(mutated)) != baseline, f"{primitive}.{qid}"


def test_verify_resolutions_hash_unchanged_by_noul_branch():
    """§5.2 交叉验证：verify_resolutions 唯一题型是 choice，补 noul 分支后
    逐位不变。故它的 hash 在 v1.7→v1.8 没有变化。"""
    types = {q["type"] for q in FROZEN_BUSINESS_QUESTIONS["verify_resolutions"].values()}
    assert types == {"choice"}
    assert GOLDEN_HASHES["verify_resolutions"] == (
        "sha256:14bfb5791242a54530c37fbfdd037d9247d47edb8ce3551b3d83c79ca151819d"
    )


# --- 投影后的 Laya 校验（B-01/B-02 回归锁）--------------------------------
def _laya_check_question(qid: str, qdef: Mapping[str, object]) -> None:
    """忠实复刻 ``laya/agent.py:478-535`` 的 ``_check_question``（stub，不 import torch）。

    逐条镜像上游分支，包括 ``noul`` 的 ``criteria`` 键集约束与 ``labels`` 的
    题型约束。**上游若改这里，本 stub 必须同步更新**，否则本文件就不再是
    B-01/B-02 的真实回归锁。
    """
    t = qdef.get("type")
    if t not in {"choice", "score", "noul"}:
        raise ValueError(f"question {qid!r}: unknown type {t!r}")
    if "instructions" not in qdef:
        raise ValueError(f"question {qid!r}: no 'instructions'")
    crit = qdef.get("criteria")
    if t == "choice":
        if not isinstance(crit, dict | list):
            raise ValueError(f"question {qid!r}: choice takes 'criteria' as dict or list")
        if not crit:
            raise ValueError(f"question {qid!r}: choice needs at least one criterion")
    elif t == "score":
        if not isinstance(crit, list):
            raise ValueError(f"question {qid!r}: score takes 'criteria' as a list")
        if not crit:
            raise ValueError(f"question {qid!r}: score needs at least one level")
    elif crit is not None and not isinstance(crit, dict):
        raise ValueError(f"question {qid!r}: noul takes 'criteria' as a dict or omits it")
    elif isinstance(crit, dict):
        keys = {str(k).lower() for k in crit}
        if not keys <= {"true", "false"}:
            raise ValueError(f"question {qid!r}: noul 'criteria' keyed only 'true'/'false', got {sorted(keys)}")
    if "labels" in qdef:
        if t != "noul":
            raise ValueError(f"question {qid!r}: 'labels' is only supported for noul questions")
        labels = qdef["labels"]
        if not isinstance(labels, dict) or set(labels) != {"false", "true"}:
            raise ValueError(f"question {qid!r}: noul labels must map exactly 'false'/'true'")
        false_label, true_label = labels["false"], labels["true"]
        if not isinstance(false_label, str) or not isinstance(true_label, str):
            raise ValueError(f"question {qid!r}: noul labels must be strings")
        stripped_false, stripped_true = false_label.strip(), true_label.strip()
        if not stripped_false or not stripped_true or stripped_false == stripped_true:
            raise ValueError(f"question {qid!r}: noul labels must be distinct non-empty strings")


def test_all_eleven_projected_questions_pass_laya_validation():
    """§16.2：11 个 question 的投影结果跑一遍 Laya 校验，断言零 ValueError。

    这是 v1.6 缺的正是这条测试——缺它则 B-01/B-02 都可能带着进实施。
    """
    flat = _flat_business_questions()
    projected = to_laya_questions(flat)
    assert len(projected) == 11
    for qid, item in zip(flat, projected, strict=True):
        _laya_check_question(qid, item)


def test_projection_is_what_prevents_labels_valueerror():
    """反事实锁：若投影被绕过、labels 直达 Laya，choice/score 题必 ValueError。

    证明这条投影线是**承重**的，而不是可有可无的整理动作。
    """
    for qid in ("document_type", "severity_review", "improvement"):
        primitive = next(p for p in PRIMITIVE_ORDER if qid in FROZEN_BUSINESS_QUESTIONS[p])
        leaking = dict(FROZEN_BUSINESS_QUESTIONS[primitive][qid])
        leaking["labels"] = {"a": "x", "b": "y"}
        with pytest.raises(ValueError, match="only supported for noul"):
            _laya_check_question(qid, leaking)


def test_projected_guard_questions_pass_laya_validation():
    projected = to_laya_questions(_guard_questions())
    assert len(projected) == 5
    for (qid, _), item in zip(_guard_questions().items(), projected, strict=True):
        _laya_check_question(qid, item)


def test_noul_labels_still_supported_for_future_short_option_words():
    """schema_descriptor 保留 labels 分支以覆盖未来合法的 noul 短选项词。
    它在 v1.7/v1.8 冻结对象上不可触发，但分支必须仍然工作。"""
    questions = copy.deepcopy(FROZEN_BUSINESS_QUESTIONS["review_executability"])
    questions["style_only"]["labels"] = {"false": "no", "true": "yes"}
    _laya_check_question("style_only", to_laya_questions(questions)[0])
    # labels 是无序 metadata：交换键序不改变 hash（故上表用 sorted()）。
    reordered = copy.deepcopy(questions)
    reordered["style_only"]["labels"] = {"true": "yes", "false": "no"}
    assert sha256_canonical(schema_descriptor(questions)) == sha256_canonical(schema_descriptor(reordered))


# --- 规格内五处逐位一致的机械校验（§5.2）---------------------------------
@pytest.mark.parametrize("schema_name", sorted(GOLDEN_HASHES))
def test_golden_hash_appears_in_spec_exact_times(schema_name):
    """§5.2：6 个值必须在 §5.2 表、§5.3 pin、§9.1、§9.2、§9.3 五处逐位一致，
    以 grep 验证；任一处不一致即 fail closed。"""
    if not _SPEC_PATH.is_file():
        pytest.skip(f"规格文件不在工作区（{_SPEC_PATH}）；该锁只在带规格的检出中生效")
    text = _SPEC_PATH.read_text(encoding="utf-8")
    digest = GOLDEN_HASHES[schema_name]
    occurrences = text.count(digest)
    assert occurrences == SPEC_HASH_OCCURRENCES[schema_name], (
        f"{schema_name} 的 golden hash 在规格中出现 {occurrences} 次，"
        f"期望 {SPEC_HASH_OCCURRENCES[schema_name]} 次——规格与常量已不一致"
    )


@pytest.mark.parametrize("schema_name", sorted(GOLDEN_HASHES))
def test_no_stale_v17_digest_presented_as_current(schema_name):
    """v1.7 的作废 digest 不得在规格中以「当前值」身份出现。

    作废值只允许出现在删除线（~~...~~）里；这里只守住「常量与当前值一致」，
    作废行的历史保留由 §5.2 表格本身负责。
    """
    if not _SPEC_PATH.is_file():
        pytest.skip(f"规格文件不在工作区（{_SPEC_PATH}）")
    text = _SPEC_PATH.read_text(encoding="utf-8")
    assert GOLDEN_HASHES[schema_name] in text
