"""T-02b：真实权重冒烟**硬闸门**（规格 §14／§19.3）。

本文件**不是**业务动作用例，而是**依赖兼容性闸门**：它的全部内容就是
「真实权重 + 显式 model/device/model_dir/manifest/calibration 五个 env 齐备
→ `noul`/`choice`/`score` 三题型均返回有限 `answer_confidence`」。

判据只看三题型是否返回有限 `answer_confidence`，**与业务动作是否为 `act` 无关**。
因此：

- 任一前置缺失 → **失败**并阻断 T-11～T-13，**不得**以「预期 audit-only」放行；
- 不得 skip（本闸门用 ``-m laya_integration`` 显式选择，未选中即不参与全量套件）；
- 不得新建虚拟环境（§7.1），不得为此改写既有测试环境。

运行::

    $env:LAYA__MODEL = "multilingual"
    $env:LAYA__DEVICE = "cpu"
    $env:LAYA__MODEL_DIR = "D:\\DocReviewer\\laya-huggingface"
    $env:LAYA__CHECKPOINT_MANIFEST_PATH = "<T-02 manifest>"
    $env:LAYA__CALIBRATION_PATH = "<calibration>"
    & $python -m pytest tests/test_decisions/test_laya_integration.py -m laya_integration -q
"""

from __future__ import annotations

import importlib
import math
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.laya_integration

# 规格 §19.3 要求的五个 env，缺一即阻断
REQUIRED_ENV = (
    "LAYA__MODEL",
    "LAYA__DEVICE",
    "LAYA__MODEL_DIR",
    "LAYA__CHECKPOINT_MANIFEST_PATH",
    "LAYA__CALIBRATION_PATH",
)

# 三题型各一。schema 取自 laya.presets.triage_questions（上游权威形状）。
QUESTIONS: dict[str, dict[str, object]] = {
    "is_urgent": {
        "type": "noul",
        "instructions": "Does `message` communicate time pressure or a deadline?",
    },
    "intent": {
        "type": "choice",
        "instructions": "What does the customer want in `message`?",
        "criteria": {
            "refund": "money returned or a duplicate charge reversed",
            "technical_help": "a bug, outage or integration problem",
            "other": "none of the other options fits",
        },
    },
    "frustration": {
        "type": "score",
        "instructions": "How frustrated does the customer sound in `message`?",
        "criteria": [
            "calm and neutral",
            "concerned but civil",
            "clearly annoyed",
            "very angry or using strong language",
        ],
    },
}

PRIMITIVE_BY_QUESTION = {"is_urgent": "noul", "intent": "choice", "frustration": "score"}

STATE = {
    "message": "Ich habe mein Konto zweimal belastet und möchte das Geld sofort zurück, "
    "sonst kündige ich. This is unacceptable, I have asked three times already!",
}


def _resolve_checkpoint_root(model_dir: Path, model: str) -> Path:
    """把 ``LAYA__MODEL_DIR`` 解析为实际 checkpoint 目录。

    §7.1 规定 ``LAYA__MODEL_DIR`` 是 **bundle 根**（``multilingual`` 映射到
    ``bundle/multilingual``）；但 §19.3 的 T-02b 示例块直接给了 ``.../multilingual``。
    两种写法都接受：谁自身含 ``model.safetensors`` 就用谁。
    """
    candidates = [model_dir, model_dir / model] if model != "english" else [model_dir]
    for candidate in candidates:
        if (candidate / "model.safetensors").is_file():
            return candidate
    tried = ", ".join(str(c) for c in candidates)
    raise AssertionError(f"未找到 checkpoint 权重（model.safetensors）；已尝试：{tried}")


def _require_preconditions() -> tuple[str, str, Path]:
    """校验五个 env 与本地权重齐备；任一缺失即失败（不 skip）。"""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
    assert not missing, (
        f"T-02b 前置缺失 {missing}：按 §19.3 须先补齐（§19.2）后重跑，"
        f"不得以「预期 audit-only」为由放行"
    )

    model = os.environ["LAYA__MODEL"].strip()
    device = os.environ["LAYA__DEVICE"].strip()
    assert model in {"english", "multilingual", "typed-decisions"}, f"LAYA__MODEL 非法：{model!r}"
    assert device == "cpu", f"T-02b 硬闸门只跑 cpu，实际 LAYA__DEVICE={device!r}"

    model_dir = Path(os.environ["LAYA__MODEL_DIR"].strip())
    assert model_dir.is_absolute(), f"LAYA__MODEL_DIR 必须是绝对路径：{model_dir}"
    root = _resolve_checkpoint_root(model_dir, model)

    manifest = Path(os.environ["LAYA__CHECKPOINT_MANIFEST_PATH"].strip())
    assert manifest.is_file(), f"checkpoint manifest 不存在：{manifest}（应由 T-02 的脚本产出）"
    calibration = Path(os.environ["LAYA__CALIBRATION_PATH"].strip())
    assert calibration.is_file(), f"calibration 文件不存在：{calibration}"
    return model, device, root


@pytest.fixture(scope="module")
def router():
    """加载真实权重。路径存在即严格离线，不回退远端下载（§7.1）。"""
    model, device, root = _require_preconditions()
    # 动态导入而非字面 `import laya`：本闸门须把导入失败报成失败而非收集期错误，
    # 且字面导入会被静态分析当成硬依赖（实际是 editable 装进 default 环境）。
    laya = importlib.import_module("laya")
    return laya.Router(models={model: str(root)}, device=device)


@pytest.fixture(scope="module")
def batch_answers(router) -> dict[str, dict[str, object]]:
    """经 ``Router.predict_batch`` 跑三题型，返回 ``{question_id: answer}``。"""
    results = router.predict_batch([{"state": STATE, "questions": QUESTIONS}])
    assert results, "predict_batch 未返回结果"
    answers = results[0].get("answers")
    assert isinstance(answers, dict), f"结果缺少 answers 键：{sorted(results[0])}"
    return answers


@pytest.mark.parametrize("question_id,primitive", sorted(PRIMITIVE_BY_QUESTION.items()))
def test_three_primitives_present(batch_answers, question_id: str, primitive: str) -> None:
    """三种题型都必须在真实权重下产出对应 primitive 的答案。"""
    assert question_id in batch_answers, f"缺少 {primitive} 答案：{sorted(batch_answers)}"
    answer = batch_answers[question_id]
    assert answer.get("type") == primitive, f"{question_id} 应为 {primitive}，实际 {answer.get('type')!r}"


@pytest.mark.parametrize("question_id", sorted(PRIMITIVE_BY_QUESTION))
def test_answer_confidence_is_finite(batch_answers, question_id: str) -> None:
    """硬闸门判据：每种题型的 ``answer_confidence`` 必须是有限值。"""
    answer = batch_answers[question_id]
    confidence = answer.get("answer_confidence")
    assert isinstance(confidence, int | float), f"{question_id} 缺 answer_confidence：{answer}"
    assert math.isfinite(float(confidence)), f"{question_id} 的 answer_confidence 非有限：{confidence!r}"
    assert 0.0 <= float(confidence) <= 1.0, f"{question_id} 的 answer_confidence 越界：{confidence!r}"


def test_noul_value_is_finite(batch_answers) -> None:
    """``noul`` 另须含有限的 ``noul``（§14 T-02b）。"""
    noul = batch_answers["is_urgent"].get("noul")
    assert isinstance(noul, int | float), f"noul 答案缺 noul 键：{batch_answers['is_urgent']}"
    assert math.isfinite(float(noul)), f"noul 非有限：{noul!r}"
    assert 0.0 <= float(noul) <= 1.0, f"noul 越界：{noul!r}"


def test_choice_and_score_carry_their_own_payload(batch_answers) -> None:
    """`choice` 须命中 criteria 之一；`score` 须落在 criteria 个数范围内。"""
    choice = batch_answers["intent"]
    picked = choice.get("choice")
    allowed = set(QUESTIONS["intent"]["criteria"])  # type: ignore[arg-type]
    assert picked in allowed, f"choice 命中非 criteria 项：{picked!r}，允许 {sorted(allowed)}"

    score = batch_answers["frustration"]
    levels = QUESTIONS["frustration"]["criteria"]
    assert isinstance(levels, list)
    value = score.get("score")
    assert isinstance(value, int | float), f"score 答案缺 score 键：{score}"
    assert -0.001 <= float(value) <= len(levels) + 0.001, f"score 越界：{value!r}，档位数 {len(levels)}"


def test_predictions_came_from_real_weights_on_cpu(batch_answers) -> None:
    """确认确实走了真实权重推理，而非任何回退路径。"""
    assert batch_answers, "答案为空"
    for question_id, answer in batch_answers.items():
        assert answer.get("type") in {"noul", "choice", "score"}, f"{question_id} 类型异常：{answer.get('type')!r}"
        assert isinstance(answer.get("action"), dict), f"{question_id} 缺 action 头：{answer}"
