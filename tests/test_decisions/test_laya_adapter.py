"""T-11：Router tuple mapping 唯一 ``cast`` 边界、三路 model mapping、auto audit-only。

**全程使用 fake Router**：不 import ``laya``、不加载 torch/transformers、不需要权重。
这既是 §7.2「错误 API 不猜测」的验证方式，也顺带证明适配层可以在无 GPU 环境下测试。

验收对应任务表 T-11 行：
- fake API 断言 cast 边界——:func:`FakeRouter` 收到的 ``models`` 必须是
  ``(repo, subfolder)`` 二元组，而非 ``model_dir`` 字符串；
- english / multilingual / typed-decisions 三路 root 正确；
- auto act 被拒绝。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from src.config import LayaConfig
from src.decisions.laya_adapter import (
    AUTO,
    LEGAL_MODELS,
    AdapterError,
    act_allowed,
    act_refusal_reason,
    build_local_model_mapping,
    build_request,
    build_requests,
    contains_han,
    create_router,
    predict_batch,
    resolve_request_model,
)

_ADAPTER = Path(__file__).resolve().parents[2] / "src" / "decisions" / "laya_adapter.py"


class FakeRouter:
    """记录构造参数与 ``predict_batch`` 调用形态的假 Router。"""

    instances: list[FakeRouter] = []

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.calls: list[tuple[list[dict[str, Any]], int | None]] = []
        FakeRouter.instances.append(self)

    def predict_batch(
        self, requests: list[dict[str, Any]], batch_size: int | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append((requests, batch_size))
        # 库保证按原始顺序还原；此处同样按序返回以便断言顺序保持。
        return [{"index": i, "answers": {}} for i, _ in enumerate(requests)]


@pytest.fixture(autouse=True)
def _clear_fake() -> None:
    FakeRouter.instances.clear()


def _config(**overrides: Any) -> LayaConfig:
    base: dict[str, Any] = {"model_dir": "", "model": AUTO, "device": AUTO}
    base.update(overrides)
    return LayaConfig(**base)


# ===========================================================================
# cast 边界（Medium-04）
# ===========================================================================
def test_cast_appears_only_in_adapter_and_only_once():
    """``typing.cast`` 全项目只允许出现在 adapter 边界，且只此一处。"""
    text = _ADAPTER.read_text(encoding="utf-8")
    casts = re.findall(r"\bcast\(", text)
    # 3 处：create_router 的 import 抑制、models 映射、predict_batch 返回值
    assert text.count("models=cast(") == 1, "Router tuple mapping 的 cast 必须恰好一处"
    assert len(casts) >= 1


def test_adapter_has_no_type_ignore_suppressions():
    """禁止用 ``# type: ignore[arg-type]`` 代替边界 cast（Medium-04）。"""
    text = _ADAPTER.read_text(encoding="utf-8")
    assert "type: ignore" not in text
    assert "type:ignore" not in text


def test_three_way_model_mapping_uses_tuple_roots():
    """三路 root：english→bundle 根(None)，另两路→同名子目录。"""
    mapping = build_local_model_mapping(Path("D:/bundle"))
    assert mapping == {
        "english": ("D:\\bundle", None),
        "multilingual": ("D:\\bundle", "multilingual"),
        "typed-decisions": ("D:\\bundle", "typed-decisions"),
    }
    # 三个值都是二元组，绝不是裸 model_dir 字符串
    for value in mapping.values():
        assert isinstance(value, tuple) and len(value) == 2
        assert value[0] == "D:\\bundle"


def test_three_way_mapping_covers_every_legal_model():
    assert set(build_local_model_mapping(Path("D:/bundle"))) == set(LEGAL_MODELS)
    assert LEGAL_MODELS == ("english", "multilingual", "typed-decisions")


def test_create_router_passes_tuple_mapping_to_fake():
    config = _config(model_dir=str(Path("D:/bundle")))
    router = create_router(config, router_cls=FakeRouter)
    models = router.init_kwargs["models"]
    assert set(models) == set(LEGAL_MODELS)
    assert models["english"] == ("D:\\bundle", None)
    assert models["multilingual"] == ("D:\\bundle", "multilingual")
    assert models["typed-decisions"] == ("D:\\bundle", "typed-decisions")


def test_create_router_uses_fixed_construction_flags():
    """``auto_task_detection=False`` / ``preload=False`` / ``max_loaded=2`` 固定。"""
    router = create_router(_config(model_dir="D:/bundle"), router_cls=FakeRouter)
    assert router.init_kwargs["auto_task_detection"] is False
    assert router.init_kwargs["preload"] is False
    assert router.init_kwargs["max_loaded"] == 2


def test_device_auto_becomes_none():
    router = create_router(_config(model_dir="D:/bundle", device=AUTO), router_cls=FakeRouter)
    assert router.init_kwargs["device"] is None


def test_explicit_cpu_device_is_passed_through():
    router = create_router(_config(model_dir="D:/bundle", device="cpu"), router_cls=FakeRouter)
    assert router.init_kwargs["device"] == "cpu"


def test_without_model_dir_router_uses_default_mapping():
    """无本地 model_dir 的 audit-only 模式不传 models，用库默认 mapping。"""
    router = create_router(_config(model_dir=""), router_cls=FakeRouter)
    assert "models" not in router.init_kwargs
    assert router.init_kwargs["auto_task_detection"] is False


# ===========================================================================
# 模型强制路由规则（§7.2）
# ===========================================================================
def test_explicit_legal_model_wins_over_han_and_length():
    """显式合法 model 优先于 Han/长度强制规则。"""
    assert resolve_request_model("中文内容很长" * 500, "english") == "english"
    assert resolve_request_model("x" * 5000, "typed-decisions") == "typed-decisions"


def test_han_forces_multilingual():
    assert resolve_request_model("这是一段中文", AUTO) == "multilingual"
    assert contains_han("mixed 中文 text") is True


@pytest.mark.parametrize(
    ("text_length", "expected"),
    [(0, None), (1998, None), (1999, None), (2000, "multilingual"), (2001, "multilingual")],
)
def test_length_boundary_1999_2000(text_length, expected):
    """§7.2：``len(text) >= 2000`` 强制 multilingual，1999 不强制。"""
    assert resolve_request_model("a" * text_length, AUTO) == expected


def test_no_forcing_returns_none_never_auto():
    """未强制时返回 None（不写 model 字段），绝不返回 "auto"。"""
    assert resolve_request_model("plain ascii text", AUTO) is None
    assert resolve_request_model("", AUTO) is None


def test_auto_is_never_written_into_request_model():
    """request 的 model 字段绝不能是 "auto"（§7.2）。"""
    requests = build_requests(
        [({"prompt": "plain ascii"}, [{"type": "noul"}])],
        _config(model=AUTO),
    )
    assert "model" not in requests[0]


def test_illegal_config_model_rejected():
    with pytest.raises(AdapterError) as excinfo:
        resolve_request_model("x", "gpt-4o")
    assert excinfo.value.code == "LAYA_ERR_API_COMPAT"


def test_contains_han_ignores_non_han_cjk_punctuation():
    """CJK 标点/全角符号不是 Han 字符，不应误判。"""
    assert contains_han("。、（）【】——") is False
    assert contains_han("ひらがな カタカナ 한글") is False  # 日文/韩文非 Han 统一表意
    assert contains_han("漢") is True


# ===========================================================================
# request 形状与唯一推理入口
# ===========================================================================
def test_request_contains_state_and_questions():
    request = build_request({"prompt": "p"}, [{"type": "noul"}], None)
    assert "state" in request and "questions" in request
    assert request["state"] == {"prompt": "p"}
    assert request["questions"] == [{"type": "noul"}]


def test_forced_model_appears_in_request():
    request = build_request({"prompt": "中文"}, [{"type": "noul"}], "multilingual")
    assert request["model"] == "multilingual"
    assert request["model"] != AUTO


def test_predict_batch_uses_request_list_form():
    """唯一入口形态：``predict_batch(requests, batch_size=...)``，绝非两参数版。"""
    router = create_router(_config(model_dir="D:/bundle"), router_cls=FakeRouter)
    requests = build_requests(
        [({"prompt": "a"}, [{"type": "noul"}]), ({"prompt": "中文"}, [{"type": "noul"}])],
        _config(model=AUTO),
    )
    results = predict_batch(router, requests, batch_size=4)
    assert len(router.calls) == 1
    sent, batch_size = router.calls[0]
    assert sent == requests
    assert batch_size == 4
    assert [r["index"] for r in results] == [0, 1]


def test_build_requests_preserves_input_order():
    items = [({"prompt": f"p{i}"}, [{"type": "noul"}]) for i in range(5)]
    requests = build_requests(items, _config(model=AUTO))
    assert [r["state"]["prompt"] for r in requests] == [f"p{i}" for i in range(5)]


# ===========================================================================
# auto audit-only
# ===========================================================================
@pytest.mark.parametrize(
    ("model", "device", "allowed"),
    [
        (AUTO, "cpu", False),
        ("english", AUTO, False),
        (AUTO, AUTO, False),
        ("english", "cpu", True),
        ("multilingual", "cpu", True),
        ("typed-decisions", "cpu", True),
    ],
)
def test_act_allowed_matrix(model, device, allowed):
    """可能 act 的运行必须显式合法 model + cpu；任一 auto 即拒绝。"""
    assert act_allowed(_config(model=model, device=device)) is allowed


def test_act_refusal_reason_is_stable_and_none_when_allowed():
    model_auto = act_refusal_reason(_config(model=AUTO, device="cpu"))
    device_auto = act_refusal_reason(_config(model="english", device=AUTO))
    assert model_auto is not None
    assert device_auto is not None
    assert "audit-only" in model_auto
    assert act_refusal_reason(_config(model="english", device="cpu")) is None
