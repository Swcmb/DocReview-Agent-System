"""T-12：§11.2 确定性 atomic inference batch plan。

三条验收主线（§11.2 规则 4）：
- **N=8 / N=9 golden**：8 个同 schema item 合成一批，9 个拆成 8+1；
- **多 question**：``rows = N * question_count``，不是 item 数；
- **oversized item**：单 item 自身超预算即 ``LAYA_ERR_BATCH``，不切碎 question set。

``encoded_chars`` 是**预算估算** ``sum(state_chars) * question_count``，不是事后
tokenizer 测量（§11.2 明示），故本文件全部可离线复现，无需 torch/权重。
"""

from __future__ import annotations

from typing import Any

import pytest

from src.config import LayaConfig
from src.decisions.laya_adapter import (
    AdapterError,
    BatchItem,
    plan_batches,
    state_chars,
)

SCHEMA_A = "sha256:" + "a" * 64
SCHEMA_B = "sha256:" + "b" * 64


def _config(**overrides: Any) -> LayaConfig:
    base: dict[str, Any] = {}
    base.update(overrides)
    return LayaConfig(**base)


def _item(
    index: int,
    *,
    question_count: int = 1,
    state: dict[str, Any] | None = None,
    schema: str = SCHEMA_A,
    primitive: str = "assess",
    model: str | None = None,
) -> BatchItem:
    return BatchItem(
        index=index,
        primitive=primitive,
        question_schema_hash=schema,
        model=model,
        state=state if state is not None else {"prompt": f"state-{index}"},
        questions=[{"type": "noul"} for _ in range(question_count)],
    )


# ===========================================================================
# state_chars：按 Unicode character 计数，不是字节
# ===========================================================================
def test_state_chars_counts_characters_not_bytes():
    """CJK 每字符 3 字节但只算 1 character——这是文本预算不是网络预算。"""
    assert state_chars({"prompt": "中文"}) == len(
        '{"prompt":"中文"}'
    )
    assert len('{"prompt":"中文"}'.encode()) > len('{"prompt":"中文"}')


def test_state_chars_is_canonical_and_order_insensitive():
    """canonical JSON：键序不同但内容相同 → state_chars 相同。"""
    assert state_chars({"a": 1, "b": 2}) == state_chars({"b": 2, "a": 1})


# ===========================================================================
# N=8 / N=9 golden
# ===========================================================================
def test_n8_fits_exactly_one_batch():
    plans = plan_batches([_item(i) for i in range(8)], _config())
    assert len(plans) == 1
    assert plans[0]["item_indexes"] == list(range(8))
    assert plans[0]["rows"] == 8  # N * question_count = 8 * 1
    assert plans[0]["question_count"] == 1


def test_n9_splits_into_eight_plus_one():
    """N=9 超 rows=8 → 拆成 8+1，question set 仍完整。"""
    plans = plan_batches([_item(i) for i in range(9)], _config())
    assert [p["item_indexes"] for p in plans] == [[0, 1, 2, 3, 4, 5, 6, 7], [8]]
    assert [p["rows"] for p in plans] == [8, 1]
    assert [p["batch_id"] for p in plans] == ["batch-000", "batch-001"]


def test_batch_ids_are_deterministic_across_runs():
    first = plan_batches([_item(i) for i in range(9)], _config())
    second = plan_batches([_item(i) for i in range(9)], _config())
    assert first == second


# ===========================================================================
# 多 question：rows = N * question_count
# ===========================================================================
def test_rows_use_question_count_not_item_count():
    """3 个 item × 2 questions = 6 rows（不是 3）。"""
    plans = plan_batches([_item(i, question_count=2) for i in range(3)], _config())
    assert len(plans) == 1
    assert plans[0]["rows"] == 6
    assert plans[0]["question_count"] == 2


def test_multi_question_split_respects_rows():
    """4 items × 2 questions = 8 rows 恰好一批；第 5 个必然另起一批。"""
    plans = plan_batches([_item(i, question_count=2) for i in range(5)], _config())
    assert [p["item_indexes"] for p in plans] == [[0, 1, 2, 3], [4]]
    assert [p["rows"] for p in plans] == [8, 2]


def test_question_set_is_never_split():
    """atomic：宁可多开一批，也不把一个 item 的 question set 拆开。"""
    plans = plan_batches([_item(i, question_count=5) for i in range(2)], _config())
    # 5 questions/item → 2 items = 10 rows > 8，故必须拆成两批
    assert len(plans) == 2
    for plan in plans:
        assert len(plan["item_indexes"]) == 1
        assert plan["question_count"] == 5


# ===========================================================================
# encoded_chars = sum(state_chars) * question_count
# ===========================================================================
def test_encoded_chars_formula():
    items = [_item(0), _item(1)]
    plans = plan_batches(items, _config())
    expected = sum(state_chars(i["state"]) for i in items) * 1
    assert plans[0]["state_chars"] == sum(state_chars(i["state"]) for i in items)
    assert plans[0]["encoded_chars"] == expected


def test_encoded_chars_scales_with_question_count():
    items = [_item(0, question_count=3)]
    plans = plan_batches(items, _config())
    assert plans[0]["encoded_chars"] == state_chars(items[0]["state"]) * 3


# ===========================================================================
# oversized item → LAYA_ERR_BATCH（规则 3）
# ===========================================================================
def test_oversized_question_count_fails():
    """单 item 的 question_count 已超 rows 预算 → 立即失败，不切碎。"""
    with pytest.raises(AdapterError) as excinfo:
        plan_batches([_item(0, question_count=9)], _config())
    assert excinfo.value.code == "LAYA_ERR_BATCH"
    assert "max_batch_rows" in excinfo.value.detail


def test_oversized_encoded_chars_fails():
    """单 item 的 encoded_chars 超限 → LAYA_ERR_BATCH。"""
    huge = {"prompt": "x" * 20000}
    with pytest.raises(AdapterError) as excinfo:
        plan_batches([_item(0, state=huge, question_count=2)], _config())
    assert excinfo.value.code == "LAYA_ERR_BATCH"
    assert "encoded_chars" in excinfo.value.detail


def test_encoded_chars_budget_splits_across_batches():
    """未超单 item 预算但整批超限时，按批拆分而非整体失败。"""
    big = {"prompt": "x" * 6000}  # 约 6014 chars，×2 questions ≈ 12028
    plans = plan_batches(
        [_item(0, state=big, question_count=2), _item(1, state=big, question_count=2)],
        _config(),
    )
    assert len(plans) == 2  # 2 items × 2 q = 4 rows，但 encoded 会超 16384
    for plan in plans:
        assert plan["encoded_chars"] <= 16384
        assert plan["rows"] <= 8


def test_empty_batch_is_noop():
    assert plan_batches([], _config()) == []


# ===========================================================================
# 分组：(model, question_schema_hash, primitive)
# ===========================================================================
def test_different_schema_splits_into_separate_batches():
    plans = plan_batches(
        [_item(0, schema=SCHEMA_A), _item(1, schema=SCHEMA_B)],
        _config(),
    )
    assert len(plans) == 2
    assert [p["item_indexes"] for p in plans] == [[0], [1]]


def test_different_primitive_splits_into_separate_batches():
    plans = plan_batches(
        [_item(0, primitive="assess"), _item(1, primitive="verify_issues")],
        _config(),
    )
    assert len(plans) == 2


def test_different_model_splits_into_separate_batches():
    plans = plan_batches(
        [_item(0, model="english"), _item(1, model="multilingual")],
        _config(),
    )
    assert len(plans) == 2


def test_same_group_shares_one_batch_and_keeps_input_order():
    plans = plan_batches([_item(i) for i in range(5)], _config())
    assert len(plans) == 1
    assert plans[0]["item_indexes"] == [0, 1, 2, 3, 4]


# ===========================================================================
# full specification 不得出现在 item state（规则 4 末句）
# ===========================================================================
def test_batch_plan_carries_no_state_or_question_content():
    """batch plan 只记预算账与 index，不得夹带 state 原文。"""
    plans = plan_batches([_item(0, state={"prompt": "SECRET-CONTENT"})], _config())
    rendered = repr(plans[0])
    assert "SECRET-CONTENT" not in rendered
    assert set(plans[0]) == {
        "batch_id",
        "item_indexes",
        "question_count",
        "rows",
        "state_chars",
        "encoded_chars",
    }
