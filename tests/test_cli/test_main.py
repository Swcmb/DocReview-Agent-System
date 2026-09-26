"""CLI 入口特征化测试 / Characterization Tests for the CLI Entry (T-00b)

冻结 `main.py` 的对外行为（规格 F8／§0.5）。决策层接入不得改变：

- 命令集合与参数面（`review` / `generate-spec` / `status` / `resume`）
- **退出码语义**（客户端与 CI 依赖，见下方 EXIT_* 常量）
- 参数缺失与缺少 API Key 时的失败退出码

所有断言写在测试体内（而非快照文件），以免被"重生成基线"顺手改掉。
"""

import asyncio
import json
import re
from typing import Any, cast

import pytest
from typer.main import get_command
from typer.testing import CliRunner

import main as cli

runner = CliRunner()


def _commands() -> Any:
    """`get_command` 静态标注为 `Command`，但根命令实际是 `Group`。

    集中在此收窄类型，避免每处断言都写 cast。
    """
    return cast("Any", get_command(cli.app)).commands

# ── 被冻结的退出码语义 ──
EXIT_SUCCESS = 0
EXIT_REVIEW_FAILED = 1
EXIT_SYSTEM_ERROR = 2
EXIT_USER_ABORT = 3
EXIT_INVALID_ARGS = 4


class _FakeWorkflow:
    """最小 `workflow` 替身：只实现 `ainvoke`。"""

    def __init__(self, result: dict) -> None:
        self._result = result
        self.seen_config: Any = None
        # T-18：`review` 传给 `create_workflow_runtime` 的 AppConfig 副本。
        self.seen_runtime_config: Any = None

    async def ainvoke(self, state, config=None):
        self.seen_config = config
        return self._result


def _stub_runtime(monkeypatch: pytest.MonkeyPatch, result: dict) -> _FakeWorkflow:
    """接管 `create_workflow_runtime`，返回可断言的 workflow 替身。

    T-18 起 `review` 会传入一份 `AppConfig`（承载 `--laya/--no-laya` 的解析结果），
    故替身必须能接受该位置参数，并把它记录下来供优先级断言使用。
    """
    workflow = _FakeWorkflow(result)

    async def _fake_create_runtime(config: Any = None):
        workflow.seen_runtime_config = config
        return {"workflow": workflow}

    monkeypatch.setattr(cli, "create_workflow_runtime", _fake_create_runtime)
    return workflow


def asyncio_run(coro: Any) -> Any:
    """同步驱动一个协程。

    本文件是同步测试（`CliRunner` 也是同步的），但要直接测 `initialize` 这类
    async 节点函数。没有封装的话每个用例都得写一遍 `asyncio.run` + 事件循环
    清理，读起来噪音大于信息。
    """
    return asyncio.run(coro)


# ─────────────────── 退出码常量本身 ───────────────────


def test_exit_codes_are_frozen():
    """不变量：五个退出码的数值不得变动。"""
    assert cli.EXIT_SUCCESS == EXIT_SUCCESS
    assert cli.EXIT_REVIEW_FAILED == EXIT_REVIEW_FAILED
    assert cli.EXIT_SYSTEM_ERROR == EXIT_SYSTEM_ERROR
    assert cli.EXIT_USER_ABORT == EXIT_USER_ABORT
    assert cli.EXIT_INVALID_ARGS == EXIT_INVALID_ARGS


# ─────────────────── 命令面 ───────────────────


def test_command_surface_is_frozen():
    """不变量：顶层命令集合固定为 4 个。"""
    assert sorted(_commands()) == ["generate-spec", "resume", "review", "status"]


def test_review_options_are_frozen():
    """不变量：`review` 的参数名与默认值固定。"""
    review = _commands()["review"]
    params = {p.name: p for p in review.params}

    assert set(params) == {
        "doc_path",
        "task",
        "max_iterations",
        "output_dir",
        "spec_output",
        "no_mcp",
        "model",
        "laya",
    }
    assert params["max_iterations"].default == 10
    assert params["output_dir"].default == "./reviews/"
    assert params["doc_path"].default is None
    assert params["no_mcp"].default is False
    # T-18：三态开关。默认 None 表示「CLI 未表态」，必须与 False 区分——
    # 否则 `--no-laya` 会被当成没传，从而回落到 LAYA__ENABLED。
    assert params["laya"].default is None


def test_generate_spec_options_are_frozen():
    """不变量：`generate-spec` 的 task 为必填位置参数，输出默认路径固定。"""
    cmd = _commands()["generate-spec"]
    params = {p.name: p for p in cmd.params}

    assert set(params) == {"task", "spec_output", "verbose"}
    assert params["spec_output"].default == "./docs/specification.md"
    assert params["task"].required is True


def test_status_and_resume_options_are_frozen():
    """不变量：`status` / `resume` 的参数面固定。"""
    commands = _commands()

    status_params = {p.name for p in commands["status"].params}
    assert status_params == {"thread_id"}

    resume_params = {p.name: p for p in commands["resume"].params}
    assert set(resume_params) == {"thread_id", "approve"}
    assert resume_params["thread_id"].required is True
    assert resume_params["approve"].default is None


# ─────────────────── review：参数校验 ───────────────────


def test_review_without_doc_path_and_task_exits_invalid_args(monkeypatch):
    """不变量：`review` 必须至少给出 `--doc-path` 或 `--task`，否则退出 4。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review"])
    assert result.exit_code == EXIT_INVALID_ARGS


def test_review_without_api_key_exits_invalid_args(monkeypatch):
    """不变量：缺少 LLM API Key 时退出 4（而非 2），并给出 `.env` 提示。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: False)
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审"])
    assert result.exit_code == EXIT_INVALID_ARGS
    assert "LLM_API_KEY" in result.output


# ─────────────────── review：结论 → 退出码映射 ───────────────────


@pytest.mark.parametrize(
    ("conclusion", "expected"),
    [
        ("Pass", EXIT_SUCCESS),
        ("Conditional Pass", EXIT_SUCCESS),
        ("Fail", EXIT_REVIEW_FAILED),
    ],
)
def test_review_maps_conclusion_to_exit_code(monkeypatch, conclusion, expected):
    """不变量（规格 §17.1）：`Pass` / `Conditional Pass` → 0，其余 → 1。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    _stub_runtime(
        monkeypatch,
        {"review_conclusion": conclusion, "iteration_count": 1, "total_llm_cost": 0.01},
    )
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审"])
    assert result.exit_code == expected


def test_review_defaults_to_failed_exit_code(monkeypatch):
    """不变量：工作流未给出 `review_conclusion` 时按 `unknown` → 退出 1。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    _stub_runtime(monkeypatch, {})
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审"])
    assert result.exit_code == EXIT_REVIEW_FAILED


def test_review_writes_spec_output_when_provided(monkeypatch, tmp_path):
    """不变量：`--spec-output` 存在且结果含 specification 时落盘。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    _stub_runtime(
        monkeypatch,
        {"review_conclusion": "Pass", "iteration_count": 1, "total_llm_cost": 0.0,
         "specification": "# 生成的规格"},
    )
    target = tmp_path / "out" / "spec.md"
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli.app, ["review", "--task", "评审", "--spec-output", str(target)]
        )
    assert result.exit_code == EXIT_SUCCESS
    assert target.read_text(encoding="utf-8") == "# 生成的规格"


def test_review_builds_thread_id_and_passes_it(monkeypatch):
    """不变量：`review` 以 `review-YYYYMMDD-HHMMSS` 生成 thread_id 并交给工作流。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    workflow = _stub_runtime(
        monkeypatch, {"review_conclusion": "Pass", "iteration_count": 1, "total_llm_cost": 0.0}
    )
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审"])

    assert result.exit_code == EXIT_SUCCESS
    thread_id = workflow.seen_config["configurable"]["thread_id"]
    assert thread_id.startswith("review-")
    # T-18：秒级时间戳后追加 6 位随机十六进制，否则同秒内两次启动会撞同一个
    # ID，checkpoint 与 history 互相覆盖且外部看不出发生过覆盖。
    assert re.fullmatch(r"review-\d{8}-\d{6}-[0-9a-f]{6}", thread_id)


def test_review_propagates_system_error_exit_code(monkeypatch):
    """不变量：工作流抛异常 → 退出 2（EXIT_SYSTEM_ERROR）。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)

    async def _boom():
        raise RuntimeError("模拟运行时故障")

    monkeypatch.setattr(cli, "create_workflow_runtime", _boom)
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审"])
    assert result.exit_code == EXIT_SYSTEM_ERROR


def test_review_creates_output_dir(monkeypatch, tmp_path):
    """不变量：`--output-dir` 不存在时会被创建。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    _stub_runtime(
        monkeypatch, {"review_conclusion": "Pass", "iteration_count": 1, "total_llm_cost": 0.0}
    )
    target = tmp_path / "nested" / "reviews"
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli.app, ["review", "--task", "评审", "--output-dir", str(target)]
        )
    assert result.exit_code == EXIT_SUCCESS
    assert target.is_dir()


# ─────────────────── generate-spec ───────────────────


def test_generate_spec_requires_api_key(monkeypatch):
    """不变量：缺少 API Key → 退出 4。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: False)
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["generate-spec", "设计认证"])
    assert result.exit_code == EXIT_INVALID_ARGS


def test_generate_spec_requires_task_argument():
    """不变量：缺 `task` 位置参数由 typer 直接拒绝（退出码非 0）。"""
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["generate-spec"])
    assert result.exit_code != EXIT_SUCCESS


def test_generate_spec_writes_file(monkeypatch, tmp_path):
    """不变量：成功时把 specification 写入 `--spec-output`。

    `generate_spec` 命令先构造 `ChatOpenAI` 再构造 `SupervisorAgent`
    （`main.py:186-197`），故两者都必须打桩：只替换 Supervisor 仍会在
    ChatOpenAI 阶段因缺少 api_key 抛 OpenAIError。
    """
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    # 函数内为局部 import，故打在包属性上
    monkeypatch.setattr("langchain_openai.ChatOpenAI", lambda **kwargs: object())

    class _StubSupervisor:
        async def generate_spec(self, state):
            return {"specification": "# 规格正文", "spec_version": 1}

    monkeypatch.setattr(
        "src.agents.supervisor.SupervisorAgent", lambda llm=None: _StubSupervisor()
    )
    target = tmp_path / "spec.md"
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli.app, ["generate-spec", "设计认证", "--spec-output", str(target)]
        )
    assert result.exit_code == EXIT_SUCCESS
    assert target.read_text(encoding="utf-8") == "# 规格正文"


# ─────────────────── status ───────────────────


def test_status_without_reviews_dir_succeeds(monkeypatch):
    """不变量：`reviews/` 不存在时提示无历史并以 0 退出（非错误）。"""
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == EXIT_SUCCESS
    assert "暂无审查历史" in result.output


def test_status_lists_history_files(monkeypatch):
    """不变量：`status` 读取 `reviews/history-*.json` 并按 spec_version 等列渲染。"""
    with runner.isolated_filesystem():
        from pathlib import Path

        reviews = Path("reviews")
        reviews.mkdir()
        (reviews / "history-t1.json").write_text(
            json.dumps(
                {
                    "thread_id": "t1",
                    "spec_version": 2,
                    "review_conclusion": "Pass",
                    "total_llm_cost": 0.5,
                    "reports": [],
                }
            ),
            encoding="utf-8",
        )
        result = runner.invoke(cli.app, ["status"])

    assert result.exit_code == EXIT_SUCCESS
    assert "t1" in result.output


def test_status_by_thread_id_shows_detail(monkeypatch):
    """不变量：`--thread-id` 命中时展示单条详情。"""
    with runner.isolated_filesystem():
        from pathlib import Path

        reviews = Path("reviews")
        reviews.mkdir()
        (reviews / "history-t9.json").write_text(
            json.dumps(
                {
                    "thread_id": "t9",
                    "spec_version": 3,
                    "review_conclusion": "Fail",
                    "total_llm_cost": 1.25,
                    "reports": [{"iteration": 1}],
                }
            ),
            encoding="utf-8",
        )
        result = runner.invoke(cli.app, ["status", "--thread-id", "t9"])

    assert result.exit_code == EXIT_SUCCESS
    assert "t9" in result.output


def test_status_missing_thread_id_is_not_an_error(monkeypatch):
    """不变量：`--thread-id` 未命中时提示并以 0 退出。"""
    with runner.isolated_filesystem():
        from pathlib import Path

        reviews = Path("reviews")
        reviews.mkdir()
        # 必须至少存在一个 history 文件，否则 status 会在检查 thread_id 之前提前返回
        (reviews / "history-other.json").write_text(
            json.dumps(
                {
                    "thread_id": "other",
                    "spec_version": 1,
                    "review_conclusion": "Pass",
                    "total_llm_cost": 0.0,
                    "reports": [],
                }
            ),
            encoding="utf-8",
        )
        result = runner.invoke(cli.app, ["status", "--thread-id", "nope"])

    assert result.exit_code == EXIT_SUCCESS
    assert "未找到线程" in result.output


def test_status_empty_dir_ignores_thread_id(monkeypatch):
    """不变量（**已知的既有早退行为**）：`reviews/` 为空时直接提示无历史并返回，
    即使传了 `--thread-id` 也不会走到「未找到线程」分支（`main.py:246-248`）。"""
    with runner.isolated_filesystem():
        from pathlib import Path

        Path("reviews").mkdir()
        result = runner.invoke(cli.app, ["status", "--thread-id", "nope"])

    assert result.exit_code == EXIT_SUCCESS
    assert "暂无审查历史" in result.output
    assert "未找到线程" not in result.output


# ─────────────────── resume ───────────────────


def test_resume_requires_api_key(monkeypatch):
    """不变量：`resume` 缺少 API Key → 退出 4。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: False)
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["resume", "--thread-id", "t1"])
    assert result.exit_code == EXIT_INVALID_ARGS


def test_resume_requires_thread_id():
    """不变量：`--thread-id` 为必填选项。"""
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["resume"])
    assert result.exit_code != EXIT_SUCCESS


def test_resume_passes_approve_flag(monkeypatch):
    """不变量：`--approve` 映射为 `user_approved=True` 并按 thread_id 恢复。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    workflow = _stub_runtime(monkeypatch, {"review_conclusion": "Pass"})

    seen: dict[str, Any] = {}

    class _RecordingWorkflow(_FakeWorkflow):
        async def ainvoke(self, state, config=None):
            seen["user_approved"] = state["user_approved"]
            return await super().ainvoke(state, config)

    workflow.__class__ = _RecordingWorkflow
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["resume", "--thread-id", "t1", "--approve"])

    assert result.exit_code == EXIT_SUCCESS
    assert seen["user_approved"] is True
    assert workflow.seen_config["configurable"]["thread_id"] == "t1"


def test_resume_reject_flag(monkeypatch):
    """不变量：`--reject` 映射为 `user_approved=False`。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    workflow = _stub_runtime(monkeypatch, {"review_conclusion": "Pass"})

    seen: dict[str, Any] = {}

    class _RecordingWorkflow(_FakeWorkflow):
        async def ainvoke(self, state, config=None):
            seen["user_approved"] = state["user_approved"]
            return await super().ainvoke(state, config)

    workflow.__class__ = _RecordingWorkflow
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["resume", "--thread-id", "t1", "--reject"])

    assert result.exit_code == EXIT_SUCCESS
    assert seen["user_approved"] is False


def test_resume_leaves_approval_untouched_when_flag_absent(monkeypatch):
    """不变量：不传 `--approve/--reject` 时不写 `user_approved`，保持 `create_initial_state()` 的 False。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    workflow = _stub_runtime(monkeypatch, {"review_conclusion": "Pass"})

    seen: dict[str, Any] = {}

    class _RecordingWorkflow(_FakeWorkflow):
        async def ainvoke(self, state, config=None):
            seen["user_approved"] = state["user_approved"]
            return await super().ainvoke(state, config)

    workflow.__class__ = _RecordingWorkflow
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["resume", "--thread-id", "t1"])

    assert result.exit_code == EXIT_SUCCESS
    assert seen["user_approved"] is False


# ─────────────────── 全局回调 ───────────────────


def test_verbose_flag_creates_log_file():
    """不变量：回调的 `--verbose` 会初始化 `logs/app.log`。"""
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["--verbose", "status"])
        from pathlib import Path

        assert (Path("logs") / "app.log").exists()
    assert result.exit_code == EXIT_SUCCESS


def test_config_path_is_echoed():
    """不变量：`--config` 仅回显提示，不改变行为。"""
    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["--config", "custom.yaml", "status"])
    assert "custom.yaml" in result.output


# ─────────────────── T-18：Laya 开关优先级 ───────────────────


def test_laya_defaults_to_false_when_env_also_absent(monkeypatch):
    """§12.3：CLI 与 LAYA__ENABLED 都未表态时，默认 false。"""
    monkeypatch.delenv("LAYA__ENABLED", raising=False)
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    workflow = _stub_runtime(monkeypatch, {"review_conclusion": "Pass"})

    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审"])

    assert result.exit_code == EXIT_SUCCESS
    assert workflow.seen_runtime_config.laya.enabled is False


def test_laya_reads_env_when_cli_absent(monkeypatch):
    """§12.3：CLI 未表态 → 回落到 ``LAYA__ENABLED``。"""
    monkeypatch.setenv("LAYA__ENABLED", "true")
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    workflow = _stub_runtime(monkeypatch, {"review_conclusion": "Pass"})

    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审"])

    assert result.exit_code == EXIT_SUCCESS
    assert workflow.seen_runtime_config.laya.enabled is True


def test_cli_laya_overrides_env(monkeypatch):
    """§12.3：CLI > 环境。``--no-laya`` 必须能压住 ``LAYA__ENABLED=true``。"""
    monkeypatch.setenv("LAYA__ENABLED", "true")
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    workflow = _stub_runtime(monkeypatch, {"review_conclusion": "Pass"})

    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审", "--no-laya"])

    assert result.exit_code == EXIT_SUCCESS
    assert workflow.seen_runtime_config.laya.enabled is False


def test_cli_laya_overrides_env_false(monkeypatch):
    """反向覆盖：``LAYA__ENABLED=false`` 时 ``--laya`` 仍能开启。"""
    monkeypatch.setenv("LAYA__ENABLED", "false")
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    workflow = _stub_runtime(monkeypatch, {"review_conclusion": "Pass"})

    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审", "--laya"])

    assert result.exit_code == EXIT_SUCCESS
    assert workflow.seen_runtime_config.laya.enabled is True


def test_laya_switch_is_mutually_exclusive():
    """Typer 三态开关：正反形态分列 opts / secondary_opts，且共享一个 name。"""
    review = _commands()["review"]
    laya_param = {p.name: p for p in review.params}["laya"]
    # Typer 把 `--x/--no-x` 的正向形态放 opts、反向放 secondary_opts；
    # 两者共用同一个参数对象，所以「互斥」由 Typer 保证，不需要我们再断言
    # 运行时冲突——这里只锁住「确实存在这一对形态」这一对外契约。
    assert laya_param.opts == ["--laya"]
    assert laya_param.secondary_opts == ["--no-laya"]


# ─────────────────── T-18：thread_id 同源 ───────────────────


def test_thread_id_is_shared_by_state_and_config(monkeypatch):
    """§12.2：同一次审查的 state 与 checkpoint 必须用**同一个** thread_id。

    旧实现里 CLI 和 workflow 的 initialize 节点各生成一个，两边可能不同——
    checkpoint 用 CLI 的、history 用 workflow 的，事后无法对齐同一次审查。
    """
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    seen: dict = {}

    class _Capturing(_FakeWorkflow):
        async def ainvoke(self, state, config=None):
            seen["state"] = dict(state)
            seen["config"] = config
            return {"review_conclusion": "Pass"}

    workflow = _Capturing({"review_conclusion": "Pass"})

    async def _fake_create_runtime(config: Any = None):
        return {"workflow": workflow}

    monkeypatch.setattr(cli, "create_workflow_runtime", _fake_create_runtime)

    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审"])

    assert result.exit_code == EXIT_SUCCESS
    assert seen["state"]["thread_id"] == seen["config"]["configurable"]["thread_id"]


def test_thread_id_is_not_regenerated_by_initialize(monkeypatch):
    """入口写好的 thread_id 必须被 initialize 原样保留（不重算）。"""
    from src.schemas.models import AgentState
    from src.state.agent_state import create_initial_state
    from src.workflows.review_workflow import initialize

    state: AgentState = create_initial_state()
    state["thread_id"] = "review-20260926-101010-abc123"

    asyncio_run(initialize(state))

    assert state["thread_id"] == "review-20260926-101010-abc123"


def test_initialize_generates_random_suffixed_thread_id(monkeypatch):
    """未预设时，initialize 用 new_thread_id() 生成带随机后缀的 ID。"""
    from src.state.agent_state import create_initial_state
    from src.workflows.review_workflow import initialize

    monkeypatch.setattr("src.workflows.review_workflow.DATA_DIR", "data")
    state = create_initial_state()

    asyncio_run(initialize(state))

    assert re.fullmatch(r"review-\d{8}-\d{6}-[0-9a-f]{6}", state["thread_id"])


def test_two_thread_ids_in_same_second_differ(monkeypatch):
    """同秒内连续生成的两个 ID 必须不同（随机后缀的存在理由）。"""
    from src.state.history_store import new_thread_id

    ids = {new_thread_id() for _ in range(20)}
    assert len(ids) == 20


# ─────────────────── T-18：结构化摘要 ───────────────────


def test_summary_reads_structured_laya_findings(monkeypatch):
    """§12.3：摘要读结构化 findings，不解析 Markdown。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    _stub_runtime(
        monkeypatch,
        {
            "review_conclusion": "Pass",
            "laya_findings": [
                {
                    "finding_id": "screen-1",
                    "kind": "screen",
                    "severity": "warning",
                    "message": "缺少错误码定义",
                    "source": "laya",
                    "decision_ids": [],
                }
            ],
        },
    )

    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审"])

    assert result.exit_code == EXIT_SUCCESS
    assert "缺少错误码定义" in result.output
    assert "screen" in result.output


def test_summary_absent_findings_is_not_an_error(monkeypatch):
    """决策层没跑（无 laya_findings 键）时摘要降级说明，且退出码不受影响。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    _stub_runtime(monkeypatch, {"review_conclusion": "Pass"})

    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审"])

    assert result.exit_code == EXIT_SUCCESS
    assert "决策层" in result.output


def test_summary_ignores_report_markdown(monkeypatch):
    """回归守卫：摘要不得从 report_markdown 里刨内容。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    _stub_runtime(
        monkeypatch,
        {"review_conclusion": "Pass", "report_markdown": "MARKDOWN_CANARY_未被结构化收集"},
    )

    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审"])

    assert "MARKDOWN_CANARY" not in result.output


# ─────────────────── T-18：落盘失败必须非零退出 ───────────────────


def test_error_code_in_result_exits_nonzero(monkeypatch):
    """审查报错时即便结论是 Pass 也必须非零退出。"""
    monkeypatch.setattr(cli, "_check_api_key", lambda: True)
    _stub_runtime(
        monkeypatch,
        {
            "review_conclusion": "Pass",
            "error_code": "DOCREVIEW_ERR_LLM_008",
            "error_message": "预算超限",
        },
    )

    with runner.isolated_filesystem():
        result = runner.invoke(cli.app, ["review", "--task", "评审"])

    assert result.exit_code == EXIT_SYSTEM_ERROR
    assert "DOCREVIEW_ERR_LLM_008" in result.output
