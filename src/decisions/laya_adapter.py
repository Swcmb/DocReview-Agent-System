"""§7.2 真实 Laya API 适配层。

本模块是**唯一**允许出现 ``typing.cast`` 的地方（Medium-04）：Laya 的
``Router.__init__`` 把 ``models`` 标注为 ``Optional[Dict[str, str]]``，但实际接受并
要求 ``(repo, subfolder)`` 二元组（``laya/router.py:212-235``，再由
``Router.load(name)`` 传给 ``Agent(repo, subfolder=sub)``）。这个类型标注与真实契约
不符，只能在边界上一次性 ``cast`` 掉——把它散落到 Agent/workflow 会让每一处都
带上一个骗人的类型断言。

三条纪律：

1. **导入边界**：模块顶层**不** import ``laya``。``Router`` 只在
   :func:`create_router` 内部惰性导入，故 ``import src.decisions`` 不会加载
   torch/transformers（§8.2）。测试通过 ``router_cls`` 注入 fake，无需真实权重。
2. **单一推理入口**：唯一调用点是 :func:`predict_batch`，形态恒为
   ``Router.predict_batch(requests, batch_size=...)``。**不得**出现
   ``Router.predict_batch(states, questions)`` 形态——库要求每个 request 同时含
   ``state`` 与 ``questions``（``laya/router.py:619-624``），两参数形态根本不存在。
3. **auto 只能 audit-only**：``model="auto"`` 或 ``device="auto"`` 的运行不得产生
   业务效果（§7.2 末条）。:func:`act_allowed` 是这条纪律的唯一判据，被拒绝时
   仍写 audit，但业务结果为 uncertain。

T-11 只负责路由/模型/设备这一层；batch 预算校验属 T-12，五个原语与 factory
生命周期属 T-13。
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, TypedDict, cast

from src.decisions.question_contracts import canonical_json_bytes

__all__ = [
    "LEGAL_MODELS",
    "AUTO",
    "HAN_MULTILINGUAL_LEN",
    "AdapterError",
    "contains_han",
    "resolve_request_model",
    "build_local_model_mapping",
    "create_router",
    "build_request",
    "build_requests",
    "predict_batch",
    "act_allowed",
    "act_refusal_reason",
    # ---- §11.2 atomic batch（T-12）----
    "BatchItem",
    "BatchPlan",
    "state_chars",
    "plan_batches",
]

#: §7.2 唯一允许出现在 request ``model`` 字段里的逻辑模型名。
LEGAL_MODELS: Final[tuple[str, ...]] = ("english", "multilingual", "typed-decisions")

#: ``auto`` 只交给 Router 自行判定，**绝不**写入 request 的 ``model`` 字段。
AUTO: Final = "auto"

#: 文本长度达到该值即强制 ``multilingual``（§7.2）。1999 不强制、2000 强制。
HAN_MULTILINGUAL_LEN: Final = 2000


class AdapterError(Exception):
    """适配层拒绝了一个请求。``code`` 是稳定的 ``LAYA_ERR_*``。"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def contains_han(text: str) -> bool:
    """文本是否含任意 Unicode Han 字符。

    以 ``unicodedata.name()`` 为权威（§7.2），而不是硬编码码位区间：Han 的码位
    分散且随 Unicode 版本增补，写死区间会在新字符加入时静默漏判。CJK 统一表意
    文字与兼容表意文字的官方名都以 ``CJK`` 开头。
    """
    for char in text:
        if not char.isalpha():
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:  # 无名字符不可能是 Han
            continue
        if "CJK" in name and "IDEOGRAPH" in name:
            return True
    return False


def resolve_request_model(text: str, configured_model: str) -> str | None:
    """按 §7.2 决定 request 是否带显式 ``model``。

    优先级：**显式合法 model 优先**；否则含任意 Han 字符或
    ``len(text) >= 2000`` 时强制 ``multilingual``；其余返回 ``None``——即
    「不写 ``model`` 字段，交给 Router 判定」。

    绝不返回 ``"auto"``：把 ``auto`` 传给 Router 会被当成一个真实模型名去
    ``Router.load("auto")``，那是错的。
    """
    if configured_model in LEGAL_MODELS:
        return configured_model
    if configured_model != AUTO:
        raise AdapterError(
            "LAYA_ERR_API_COMPAT",
            f"config.model 必须是 {AUTO!r} 或 {LEGAL_MODELS} 之一，收到 {configured_model!r}",
        )
    if contains_han(text) or len(text) >= HAN_MULTILINGUAL_LEN:
        return "multilingual"
    return None


def build_local_model_mapping(model_dir: Path) -> dict[str, Any]:
    """本地权重 bundle → Laya 的三路 tuple mapping（§7.2）。

    ``english`` 指向 bundle 根（subfolder 为 ``None``），另两路指向同名子目录。
    刻意**不**把 ``model_dir`` 当成一个普通模型名塞进去——Laya 期望的是
    ``(repo, subfolder)`` 二元组。
    """
    root = str(model_dir)
    return {
        "english": (root, None),
        "multilingual": (root, "multilingual"),
        "typed-decisions": (root, "typed-decisions"),
    }


def _prepare_runtime_env() -> None:
    """懒加载**前**设置 transformers 运行时环境变量（§7.2）。

    ``USE_TF=0`` 不是可选项：transformers 探测到 TF 后，其 abseil 运行时会让
    模型构建死锁。用 ``setdefault`` 尊重外部显式设置，且只在本函数内发生——
    不得污染 ``src.decisions`` 的顶层 import。
    """
    os.environ.setdefault("USE_TF", "0")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def create_router(config: Any, router_cls: type | None = None) -> Any:
    """构造 Router——本模块**唯一**的 ``typing.cast`` 边界。

    ``router_cls`` 仅供测试注入 fake；缺省时惰性导入真实 ``laya.Router``，
    故模块顶层不加载 torch/transformers（§8.2 导入边界）。

    固定参数：``auto_task_detection=False``（不猜任务类型）、``preload=False``
    （懒加载）、``max_loaded=2``。``device="auto"`` 转成 ``Router(device=None)``，
    让库自行选择——但这只影响**审计**用途，见 :func:`act_allowed`。
    """
    _prepare_runtime_env()
    if router_cls is None:
        try:
            router_module = cast(Any, __import__("laya.router", fromlist=["Router"]))
        except ImportError as exc:
            raise AdapterError("LAYA_ERR_IMPORT", f"无法导入 laya.router：{exc}") from exc
        router_cls = cast(type, router_module.Router)

    model_dir = str(getattr(config, "model_dir", "") or "")
    if not model_dir:
        # 无本地权重的 audit-only 模式才允许用 Laya 默认 mapping（§7.2）。
        return router_cls(
            device=None if config.device == AUTO else config.device,
            auto_task_detection=False,
            preload=False,
            max_loaded=2,
        )

    # 刻意标注为精确的 tuple mapping 类型（而非函数签名的 dict[str, Any]）：
    # 下面的 cast 才是真实的一次「收紧类型 → 放宽给第三方 Router」的边界转换。
    local_models: dict[str, tuple[str, str | None]] = build_local_model_mapping(Path(model_dir))
    return router_cls(
        # ↓↓↓ 全项目唯一的 typing.cast（Medium-04）。理由见模块 docstring。
        # 上面的 local_models 已标注为精确 tuple mapping，故此处 cast 是真实的
        # 「收紧类型 → 放宽给第三方 Router」边界转换，而非空操作。
        models=cast(dict[str, Any], local_models),
        device=None if config.device == AUTO else config.device,
        auto_task_detection=False,
        preload=False,
        max_loaded=2,
    )


def build_request(
    state: Mapping[str, Any],
    questions: Sequence[Mapping[str, Any]],
    model: str | None,
) -> dict[str, Any]:
    """组装单个 request——``state`` 与 ``questions`` 缺一即库会抛错。

    ``model`` 为 ``None`` 时**不写**该键（交给 Router 判定）；强制时只写合法模型名。
    绝不能写 ``model="auto"``。
    """
    request: dict[str, Any] = {"state": dict(state), "questions": [dict(q) for q in questions]}
    if model is not None:
        request["model"] = model
    return request


def build_requests(
    items: Sequence[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]],
    config: Any,
) -> list[dict[str, Any]]:
    """按输入顺序组装整批 request，并保持顺序可还原（库按原始顺序写回结果）。"""
    requests: list[dict[str, Any]] = []
    for state, questions in items:
        text = state.get("prompt") if isinstance(state, Mapping) else None
        model = resolve_request_model(text if isinstance(text, str) else "", str(config.model))
        requests.append(build_request(state, questions, model))
    return requests


def predict_batch(
    router: Any, requests: Sequence[Mapping[str, Any]], batch_size: int
) -> list[dict[str, Any]]:
    """**唯一**推理入口（§7.2）。

    形态恒为 ``Router.predict_batch(requests, batch_size=...)``。库内部已完成
    route → 按 model 分组 → 按 order-aware schema 二次分组 → 每组只 load 一次
    checkpoint → 按原始顺序还原（``router.py:704-808``），故此处**不**自行分组。
    """
    return cast(
        list[dict[str, Any]],
        router.predict_batch(list(requests), batch_size=batch_size),
    )


def act_allowed(config: Any) -> bool:
    """是否允许产生业务效果（act）。

    §7.2：``model=auto`` 或 ``device=auto`` 只能 audit-only。可能是 act 的运行必须
    显式合法 model 且 ``device="cpu"``。
    """
    return str(getattr(config, "model", AUTO)) in LEGAL_MODELS and (
        str(getattr(config, "device", AUTO)) == "cpu"
    )


def act_refusal_reason(config: Any) -> str | None:
    """不可 act 时的稳定原因串（可写入 audit）；可 act 时返回 ``None``。"""
    model = str(getattr(config, "model", AUTO))
    device = str(getattr(config, "device", AUTO))
    if model not in LEGAL_MODELS:
        return f"model={model!r} 是 auto/非法值，只能 audit-only"
    if device != "cpu":
        return f"device={device!r} 非 cpu，只能 audit-only"
    return None


# ===========================================================================
# §11.2 确定性 atomic inference batch plan（T-12）
# ===========================================================================
class BatchItem(TypedDict):
    """一个 atomic inference item：已分块 state + 同一 primitive 的完整 question set。"""

    index: int
    primitive: str
    question_schema_hash: str
    model: str | None
    state: dict[str, Any]
    questions: list[dict[str, Any]]


class BatchPlan(TypedDict):
    """一批的预算账（§11.2 规则 2）。离线重算必须复现相同边界。"""

    batch_id: str
    item_indexes: list[int]
    question_count: int
    rows: int
    state_chars: int
    encoded_chars: int


def state_chars(state: Mapping[str, Any]) -> int:
    """canonical compact state JSON 的 Unicode character 数（§11.2）。

    按**字符**而非字节计数：state 是文本预算，不是网络字节预算。
    """
    return len(canonical_json_bytes(dict(state)).decode("utf-8"))


def _item_budget(item: BatchItem) -> tuple[int, int, int]:
    """返回 ``(question_count, state_chars, encoded_chars)``。"""
    question_count = len(item["questions"])
    chars = state_chars(item["state"])
    return question_count, chars, chars * question_count


def plan_batches(items: Sequence[BatchItem], config: Any) -> list[BatchPlan]:
    """按 §11.2 生成确定性 batch plan。

    预算（两条必须**同时**满足）::

        rows         = N * question_count        <= max_batch_rows
        encoded_chars = sum(state_chars) * qcount <= max_batch_encoded_chars

    ``encoded_chars`` 是**预算估算**，不是事后 tokenizer 测量（§11.2 明示），
    故离线可复现。单个 item 自身超任一预算即抛 ``LAYA_ERR_BATCH``——不切碎
    question set、不重试、不进入无限循环（规则 3）。
    """
    max_rows = int(getattr(config, "max_batch_rows", 8))
    max_encoded = int(getattr(config, "max_batch_encoded_chars", 16384))

    for item in items:
        question_count, chars, encoded = _item_budget(item)
        if question_count > max_rows:
            raise AdapterError(
                "LAYA_ERR_BATCH",
                f"item {item['index']} 的 question_count {question_count} > max_batch_rows {max_rows}",
            )
        if encoded > max_encoded:
            raise AdapterError(
                "LAYA_ERR_BATCH",
                f"item {item['index']} 的 encoded_chars {encoded} > "
                f"max_batch_encoded_chars {max_encoded}（state_chars={chars}）",
            )

    # 按 (model, question_schema_hash, primitive) 分组，组内保持输入顺序（规则 1、2）。
    groups: dict[tuple[str, str, str], list[BatchItem]] = {}
    for item in items:
        key = (item["model"] or AUTO, item["question_schema_hash"], item["primitive"])
        groups.setdefault(key, []).append(item)

    plans: list[BatchPlan] = []
    for key in groups:
        group = groups[key]
        # 同一 schema 即同一 question set，故组内 question_count 恒定；
        # §11.2 的 sum(state_chars) * question_count 正是按这个恒定值算的。
        question_count = len(group[0]["questions"])
        current: list[BatchItem] = []
        sum_chars = 0

        for item in group:
            item_chars = state_chars(item["state"])
            candidate_rows = (len(current) + 1) * question_count
            candidate_encoded = (sum_chars + item_chars) * question_count
            if current and (
                candidate_rows > max_rows or candidate_encoded > max_encoded
            ):
                plans.append(_make_plan(len(plans), current, question_count, sum_chars))
                current, sum_chars = [], 0
            current.append(item)
            sum_chars += item_chars
        if current:
            plans.append(_make_plan(len(plans), current, question_count, sum_chars))
    return plans


def _make_plan(
    batch_ordinal: int,
    batch_items: Sequence[BatchItem],
    question_count: int,
    sum_chars: int,
) -> BatchPlan:
    """按 §11.2 记 ``rows = N * question_count``、``encoded_chars = sum(state_chars) * question_count``。"""
    return BatchPlan(
        batch_id=f"batch-{batch_ordinal:03d}",
        item_indexes=[item["index"] for item in batch_items],
        question_count=question_count,
        rows=len(batch_items) * question_count,
        state_chars=sum_chars,
        encoded_chars=sum_chars * question_count,
    )
