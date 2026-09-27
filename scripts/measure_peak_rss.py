"""固定 mock 的峰值内存测量入口（规格 T-19「Docker fixed mock RSS」）。

目的：证明**默认镜像**在跑完整决策层通路时的常驻内存有确定上界。
默认镜像按规格不装 torch、不携权重，所以这条通路用的是
:class:`NullDecisionEngine` —— 即「决策层存在但恒 uncertain」的最坏可用形态。
它测的是 Python 解释器 + langgraph + 决策层数据结构本身的基线占用，
不含 LLM 调用（那需要 API key，且波动远大于本项要测的东西）。

验收阈值 **< 500 MB**（规格 T-19 硬指标）。超标即非零退出。

用法::

    python scripts/measure_peak_rss.py
    python scripts/measure_peak_rss.py --json          # 机器可读
    python scripts/measure_peak_rss.py --limit-mb 400  # 收紧阈值

平台差异（两处都要显式处理，写死换算会得到差 1024 倍的错数）：

* ``resource.getrusage`` 的 ``ru_maxrss``：Linux 为 **KiB**，macOS 为 **字节**。
* Windows 没有 ``resource``；改用 ``psapi.GetProcessMemoryInfo`` 的
  ``PeakWorkingSetSize``（字节）。psapi.dll 随系统提供，无需额外依赖。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# `python scripts/measure_peak_rss.py` 把 sys.path[0] 设为 scripts/，项目根不在路径上，
# 于是 `import src.*` 会落回 editable 安装记录的项目路径——而那条记录可能已过期
# （本机曾指向已移动的 D:\DocReview\Agent-System），表现为 src.decisions 找不到。
# 显式把项目根插到最前面，让本脚本在任何 CWD 下都解析到当前仓库。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

#: 默认内存上限（MB）。规格 T-19 定为 500。
DEFAULT_LIMIT_MB = 500

#: 固定输入：刻意用一份中等规模、含多个标题层级的中文规格。
# 固定输入是「可比较」的前提——每次测量同一份文档，数字才有意义。
_FIXED_SPEC = """# 订单服务规格

## 1. 概述
本服务为电商平台提供订单创建、支付回调与履约调度能力。

## 2. 技术栈
Python 3.11 / FastAPI / PostgreSQL 15 / Redis 7

## 3. 核心流程
### 3.1 订单创建
用户提交购物车后，服务校验库存、锁定库存、生成订单。
### 3.2 支付回调
支付网关回调后，服务幂等更新订单状态并触发履约流程。
### 3.3 履约调度
按仓库与配送区域分单，推送到 WMS。

## 4. 非功能需求
### 4.1 性能
峰值 QPS 2000，P99 延迟低于 200ms。
### 4.2 可用性
核心链路可用性 99.9%。
### 4.3 安全
支付回调需验签，密钥轮换周期 90 天。

## 5. 验收标准
每条核心流程需有自动化测试覆盖，错误码体系完整可查。
""" * 3


def _peak_rss_mb() -> float:
    """取本进程峰值 RSS（MB）。跨三平台。

    读不到就抛异常，**不返回 0**：一个报 0 MB 并 PASS 的测量入口比没有更糟，
    它会把「测量失败」伪装成「内存达标」。
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)

        # 必须显式声明 argtypes/restype：HANDLE 是指针宽度，ctypes 默认按 c_int
        # 返回会把句柄截断，GetProcessMemoryInfo 随即以 ERROR_INVALID_HANDLE(6) 失败。
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        # K32GetProcessMemoryInfo 自 Win7 起由 kernel32 导出；psapi 只是转发壳。
        query = kernel32.K32GetProcessMemoryInfo
        query.restype = wintypes.BOOL
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]

        handle = kernel32.GetCurrentProcess()
        if not query(handle, ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo 失败，无法测量峰值 RSS")
        return counters.PeakWorkingSetSize / (1024.0 * 1024.0)

    import resource

    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux/其余 Unix: ru_maxrss 单位是 KiB → MB 除 1024
    # Darwin: 单位是 bytes → MB 除 1024**2
    # 这两个分支曾写反（darwin 除 1024、Linux 除 1024**2），使容器内真实 ~72MB
    # 被报成 ~0.06MB：500MB 闸门因此永不触发，等于没有闸门。
    divisor = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
    return raw / divisor


async def _run_decision_path() -> dict[str, Any]:
    """用 Null 引擎跑一遍五原语通路，返回产出形状摘要。"""
    from src.decisions.null_decisions import NullDecisionEngine
    from src.decisions.types import DecisionContext
    from src.schemas.models import IssueStatus, IssueTracker

    engine = NullDecisionEngine()
    context = DecisionContext(
        thread_id="review-20260101-000000-rsspr",
        iteration=0,
        spec_version=1,
    )
    # TypedDict 构造：显式声明类型而非裸 dict，让 mypy 校验必填键是否齐全。
    issues: list[IssueStatus] = [
        IssueStatus(
            issue_id="BK-1-1",
            severity="High",
            issue_type="missing_definition",
            description="未定义错误码体系",
            suggestion="补充错误码表并与实现对齐",
            location="§5",
            status="open",
        )
    ]
    tracker: IssueTracker = {
        "all_issues": issues,
        "fixed_count": 0,
        "partially_fixed_count": 0,
        "unfixed_count": 1,
        "new_in_current_round": ["BK-1-1"],
    }

    screen = await engine.screen_document(_FIXED_SPEC, context=context)
    assess = await engine.assess_document(_FIXED_SPEC, None, context=context)
    verified = await engine.verify_issues(issues, _FIXED_SPEC, None, context=context)
    resolutions = await engine.verify_resolutions(issues, _FIXED_SPEC, None, context=context)
    convergence = await engine.judge_convergence(
        issues, issues, {"iteration": 1}, tracker, context=context
    )

    return {
        "screen_status": screen.get("status"),
        "screen_findings": len(screen.get("findings", [])),
        "assess_cap": assess.get("cap"),
        "assess_route": assess.get("route_action"),
        "verified": len(verified),
        "resolutions": len(resolutions),
        "convergence_status": convergence.get("status"),
        "convergence_route": convergence.get("route_action"),
        "spec_chars": len(_FIXED_SPEC),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="测量决策层通路的峰值内存")
    parser.add_argument("--limit-mb", type=int, default=DEFAULT_LIMIT_MB, help="内存上限（MB）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    summary = asyncio.run(_run_decision_path())
    peak_mb = _peak_rss_mb()
    ok = peak_mb < args.limit_mb

    if args.json:
        print(json.dumps({"peak_rss_mb": round(peak_mb, 2), "limit_mb": args.limit_mb,
                          "ok": ok, **summary}, ensure_ascii=False, indent=2))
    else:
        print(f"峰值 RSS : {peak_mb:.1f} MB")
        print(f"上限     : {args.limit_mb} MB")
        print(f"输入规模 : {summary['spec_chars']} 字符")
        print(f"五原语   : screen={summary['screen_status']}/{summary['screen_findings']} findings"
              f", verify={summary['verified']}, convergence={summary['convergence_status']}")
        print(f"结论     : {'PASS' if ok else 'FAIL'}")

    if not ok:
        print(f"超出内存上限 {args.limit_mb} MB", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
