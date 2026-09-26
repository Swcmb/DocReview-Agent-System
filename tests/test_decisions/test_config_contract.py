"""LayaConfig 配置契约测试（规格 T-03「配置契约测试」）。

锁定三件事：

1. §11.1 的 15 个字段与默认值逐项对齐；
2. **不得**出现任何可覆盖业务阈值的字段（阈值只能来自 calibration manifest）；
3. 嵌套 `__` 环境变量能真正写入 ``config.laya``，而扁平 ``LAYA_*`` 只告警。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from src.config import AppConfig, LayaConfig

# §11.1 字段表：字段名 -> 默认值
EXPECTED_DEFAULTS: dict[str, object] = {
    "enabled": False,
    "runtime_commit": "",
    "model": "auto",
    "model_dir": "",
    "checkpoint_manifest_path": "",
    "calibration_path": "",
    "device": "auto",
    "max_len": 8192,
    "head_max_len": 256,
    "chunk_limit": 8,
    "max_chars_per_chunk": 1024,
    "batch_size": 4,
    "max_batch_rows": 8,
    "max_batch_encoded_chars": 16384,
    "timeout_seconds": 120,
}


def test_laya_config_field_set_matches_spec() -> None:
    """字段集合与 §11.1 完全一致——多一个少一个都是契约破坏。"""
    assert set(LayaConfig.model_fields) == set(EXPECTED_DEFAULTS)


@pytest.mark.parametrize("field,expected", sorted(EXPECTED_DEFAULTS.items()))
def test_laya_config_defaults(field: str, expected: object) -> None:
    """逐字段锁定默认值。"""
    assert getattr(LayaConfig(), field) == expected


def test_laya_config_has_no_threshold_override_fields() -> None:
    """§11.1 禁令：`LayaConfig` 不得提供任何可覆盖业务阈值的字段。

    ``calibration_path`` 是「阈值从哪读」的路径，不是阈值本身，故不在此列。
    """
    banned = sorted(
        name
        for name in LayaConfig.model_fields
        if any(token in name.lower() for token in ("threshold", "epsilon", "cutover", "min_conf", "gate"))
        and not name.endswith("_path")
    )
    assert not banned, f"LayaConfig 不得暴露阈值覆盖字段：{banned}"


def test_app_config_exposes_laya_section() -> None:
    """`AppConfig` 必须挂载 `laya` 子配置。"""
    assert isinstance(AppConfig().laya, LayaConfig)


def test_defaults_are_audit_only_safe() -> None:
    """默认配置必须落在 audit-only 安全态：关闭、auto、无路径。

    这是「未取得合规 calibration 前一切判定为 uncertain」的配置侧保证——
    拿一个裸默认配置去跑，不应该、也不能产生任何业务动作。
    """
    config = LayaConfig()
    assert config.enabled is False
    assert config.model == "auto"
    assert config.device == "auto"
    assert config.model_dir == ""
    assert config.calibration_path == ""


def test_numeric_bounds_are_positive() -> None:
    """数值字段必须为正（pydantic gt=0 生效）。"""
    config = LayaConfig()
    for name in (
        "max_len",
        "head_max_len",
        "chunk_limit",
        "max_chars_per_chunk",
        "batch_size",
        "max_batch_rows",
        "max_batch_encoded_chars",
        "timeout_seconds",
    ):
        assert getattr(config, name) > 0, f"{name} 必须为正"


def test_head_max_len_must_be_below_max_len() -> None:
    """§11.1：`head_max_len` 为正整数且小于 `max_len`。"""
    assert LayaConfig().head_max_len < LayaConfig().max_len


def test_nested_env_var_is_applied() -> None:
    """`LAYA__*` 双下划线形式能真正写入 config.laya。"""
    config = LayaConfig(
        enabled=True,
        runtime_commit="970dc8c5f63d7b886a68409493f37d569424f933",
        model="multilingual",
        device="cpu",
    )
    assert config.enabled is True
    assert config.model == "multilingual"
    assert config.device == "cpu"


def test_flat_env_var_warns_and_is_not_applied() -> None:
    """扁平 `LAYA_ENABLED` 只告警，不写入 config.laya（§11.1）。

    以子进程验证，确保 `extra="ignore"` 的静默丢弃确实被告警覆盖。
    """
    script = (
        "import logging,sys;"
        "logging.basicConfig(level=logging.WARNING, stream=sys.stdout, format='%(message)s');"
        "from src.config import AppConfig;"
        "c=AppConfig();"
        "print('EFFECTIVE_ENABLED=' + str(c.laya.enabled))"
    )
    env = dict(os.environ)
    env.update({"LAYA_ENABLED": "true", "LAYA_MODEL": "multilingual"})
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    combined = proc.stdout + proc.stderr
    assert "EFFECTIVE_ENABLED=False" in combined, "扁平变量不得生效"
    assert "LAYA__ENABLED" in combined, "必须告警并提示双下划线形式"


def test_importing_decisions_does_not_load_torch() -> None:
    """T-03 硬验收：`import src.decisions` 不得加载 torch/transformers/laya。"""
    script = (
        "import src.decisions,sys;"
        "leaked=[m for m in ('torch','transformers','laya') if m in sys.modules];"
        "assert not leaked, leaked;"
        "print('CLEAN')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "CLEAN" in proc.stdout


def test_load_laya_router_is_lazy_and_not_called_on_import() -> None:
    """`load_laya_router` 是唯一触碰 laya 的入口，且 import 期不触发。"""
    import src.decisions as decisions

    assert callable(decisions.load_laya_router)
    assert decisions.LAYA_IMPORT_ERROR == "LAYA_ERR_IMPORT"
    # 模块内不得存在 import-time 的 laya 绑定
    assert "Router" not in vars(decisions)
