"""重生成 T-00b 的 MCP 对外契约快照（一次性基线工具）。

用法（项目根执行）：
    & $python -m tests.test_mcp_server._regen_snapshots

生成物落在 `tests/test_mcp_server/snapshots/*.json`。

**使用纪律**：日常开发不应运行本脚本。只有当 MCP 对外契约的变更已被规格
显式批准时，才可重新生成基线，并必须在同一次提交中说明变更原因。
若只是重构导致快照失配，正确处置是回退实现，而不是重生成基线。

契约采集逻辑复用 `test_contract.collect_snapshots()`，保证「基线怎么生成」
与「测试怎么比对」永远同源。
"""

import asyncio
import json

import pytest

from tests.test_mcp_server import test_contract as tc
from tests.test_mcp_server.test_contract import SNAP_DIR, collect_snapshots


async def main() -> None:
    # 直接实例化 MonkeyPatch（无需 pytest 运行即可用），从而复用
    # `test_contract._install_stubs` 单一替身定义——避免此处再复制一份
    # runtime 字典而与测试侧漂移（历史上正是这类重复导致基线与测试不一致）。
    mp = pytest.MonkeyPatch()
    try:
        tc._install_stubs(mp)
        snapshots = await collect_snapshots(mp)
    finally:
        mp.undo()

    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in sorted(snapshots.items()):
        path = SNAP_DIR / f"{name}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path.name}")

    print(f"\n{len(snapshots)} snapshots -> {SNAP_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
