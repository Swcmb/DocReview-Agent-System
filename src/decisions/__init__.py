"""Laya 决策层 / Laya Decision Layer

**导入边界（规格 T-03 硬要求）**::

    & $python -c "import src.decisions,sys; assert 'torch' not in sys.modules"

导入本包**绝不能**加载 ``torch``、``transformers`` 或 ``laya``。原因有三：

1. ``torch`` 导入约需数秒，而决策层在未启用时也要能被 ``import``（配置、审计、
   Null 实现都需要它）；
2. 未启用 Laya 的部署根本不应被迫安装/加载深度学习栈；
3. 加载 Laya 权重是有副作用的动作（可能触发下载），绝不能由 ``import`` 隐式发生。

因此本模块只暴露惰性访问器：真正需要 Laya 时由调用方显式调用
:func:`load_laya_router`，而不是在 import 期触发。
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = ["load_laya_router", "LAYA_IMPORT_ERROR"]

#: Laya 相关降级码（§1185）。导入/初始化失败统一归为 LAYA_ERR_IMPORT。
LAYA_IMPORT_ERROR = "LAYA_ERR_IMPORT"


def load_laya_router(*args: Any, **kwargs: Any) -> Any:
    """显式加载 Laya ``Router``。

    这是本包**唯一**触碰 ``laya`` 的入口，因此 ``import src.decisions`` 保持
    torch-free。用 ``importlib`` 而非字面 ``import laya``：字面导入会被静态分析
    当成硬依赖，而 Laya 是可选的、且装在另一个环境里；动态导入同时让失败以
    ``ImportError`` 形式在此抛出，由调用方转为 ``LAYA_ERR_IMPORT`` 降级。

    Args:
        *args: 透传给 ``laya.Router`` 的位置参数。
        **kwargs: 透传给 ``laya.Router`` 的关键字参数。

    Returns:
        laya.Router 实例。

    Raises:
        ImportError: Laya 未安装或导入失败（调用方应转为 audit-only 降级）。
    """
    router_cls = importlib.import_module("laya").Router  # 延迟到调用时加载
    return router_cls(*args, **kwargs)
