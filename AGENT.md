# AGENT.md — DocReview-Agent-System 代理工作约定

本文件是**代理行为约定**，与 `CLAUDE.md`（项目架构说明）互补。
`CLAUDE.md` 回答「项目是什么、怎么跑」；本文件回答「代理该怎么干活」。

规格文档：`claude-context/specs/2026-09-25-laya-decision-layer-integration-spec.md`（v1.9）

---

## 1. 自主执行模式（用户长期要求）

**用户要求原文：**

> 接下来不要向我提问，采用你推荐的即可，进入全自动模式，完成剩余任务。
> 全部完成后保存任务状态，然后关机。注意安全。

### 1.1 生效范围

进入自主模式后，代理**不得**：

- 就实现方案、命名、测试写法、任务拆分向用户提问；
- 在两个都可行的方案之间停下来等裁决——**选一个、说明理由、继续**；
- 因「不确定用户是否想要」而中断任务。

代理**必须**：

- 自主决策并一路推进到任务清单全部完成为止；
- 每个关键决策在 commit message 或代码注释里写清**为什么选它**，
  让事后复盘不必反推；
- 遇到规格与现状冲突时，按「就低不就高」处理：优先**不破坏既有契约**，
  其次满足新需求，必要时缩小改动范围并显式记录未完成项。

### 1.2 例外（仍需停下）

以下情况**必须**先停下来问，不得自行决断：

- 不可逆且影响系统全局的操作（删库、删分支、force push、数据库迁移）；
- 需要花用户真实的钱、动生产环境、动用户账号凭据；
- 规格自相矛盾且两种解释会导致**行为不兼容**（选错就是错的，不只是风格问题）；
- 授权凭据缺失（API key、token），且无法在本地以任何方式取得。

**关机**属于用户已显式授权的终局操作，见 §1.3。

### 1.3 关机协议

用户已明确授权「全部完成后关机」。执行前**必须**全部满足：

1. 任务清单所有条目 `completed`，无 `pending` / `in_progress`；
2. 工作已提交，`git status` 除未跟踪的 `claude-context/` 外干净；
3. 验收命令已跑完并留有结果（全量 pytest、逐文件 ruff 对比基线）；
4. 任务状态已落盘（见 §1.4）。

关机命令：

```powershell
shutdown /s /t 60 /c "T-18/T-19/T-20 完成，任务状态已保存，60 秒后关机"
```

留 60 秒缓冲是刻意的：万一关机前发现状态没存完，还能中断
（`shutdown /a`）抢救。不要用 `/t 0`。

### 1.4 任务状态落盘

关机前把进度写进 `claude-context/STATUS.md`（**不提交**，见 §2），
内容包括：已完成任务与 commit 号、验收数字、已知遗留项、下一步入口。
这样下次开机读一个文件就知道上次停在哪。

---

## 2. 仓库卫生

- `claude-context/` 是规格与状态目录，**永远不提交**（无例外）。
  它在 `.gitignore` 之外靠「不 `git add`」保持未跟踪，别依赖 `.gitignore`
  ——T-18 期间它确实以 `??` 状态出现在 `git status` 里，这是正确状态。
- 提交前用 `git diff --cached --stat` 复核暂存区，只应有本次任务的意图文件。
- **不擅自清理既有死代码**。发现无用导入/函数，写进 commit message 或
  单独告知，不在顺手重构里删掉。
- 每个任务一个独立 commit，提交前必须跑验收。

---

## 3. 验收纪律

### 3.1 失败基线

既有失败基线**恰为 4 项**，全在 `tests/test_tools/test_terminal.py`
（Windows terminal 环境相关）：

- `test_execute_command_with_working_directory`
- `test_execute_piped_command`
- `test_command_whitelist_allowed`
- `test_command_duration_recorded`

任何任务结束时：**失败数不得超过 4，且不得出现该文件之外的新失败**。
禁止把这些失败改成 `xfail` / `skip` —— 基线的价值在于它真实存在。

### 3.2 命令必须在项目根跑

```powershell
cd D:\DocReviewer\DocReview-Agent-System
```

在父目录 `D:\DocReviewer` 跑会让 `testpaths` 失效并误收 `laya-github` 的测试，
产生假性 INTERNALERROR。

### 3.3 逐文件 ruff 对比基线

本仓库**带存量 lint 债**（`docreview.py` 单文件 170+ 条）。因此验收标准不是
「ruff 零错误」，而是**逐文件与 HEAD 基线对比不得引入新规则计数**：

```powershell
# 1) 记录当前
$cur = <ruff 输出按 file|code 分组>
# 2) git stash 掉本次改动，记录 HEAD 基线
# 3) git stash pop，比较差值
```

新增文件（如新测试）没有基线，要求**自身干净**。

> 踩过的坑：改完先跑一遍 ruff 再看总数，会把「存量」误当成「自己引入的」。
> 必须做 HEAD 对比，且差值要按 `file|rule` 聚合到规则级别——
> 按行数比会被行号漂移干扰。

### 3.4 依赖同一解释器

所有 Python 命令使用 `D:\ProgramFiles\anaconda3\envs\default\python.exe`，
不临时换环境。

---

## 4. 已踩过的坑（别再犯）

| 坑 | 现象 | 教训 |
|:---|:---|:---|
| `AgentState` 用 `NotRequired[...]` | LangGraph 建图时 pydantic `create_model` 抛 `PydanticForbiddenQualifier`，`build_workflow()` 直接不可用 | TypedDict 交给 pydantic 的上下文**拒绝** `NotRequired`；「键可缺失」由缺键表达，不需要限定符 |
| 读不存在的 state 键 | 原语静默收到空串，恒 `uncertain`，**表现完全正常**，运行结果发现不了 | 写新代码时逐字核对 state 字段名；补一条断言把契约钉死 |
| `git commit -F -` | PowerShell 无法喂 stdin，报 `empty commit message` | 用 `-F <文件路径>` |
| Typer 三态开关 | `Optional[bool]` 默认 `None` 表示「未表态」，判据必须是 `is not None` 而非真值 | 否则 `--no-laya` 会被当成没传 |
| Typer 参数形态 | `--x/--no-x` 正向在 `opts`、反向在 `secondary_opts` | 断言互斥性别只查 `opts` |
| 同秒 thread_id 撞车 | 纯秒级时间戳让同秒启动的两次审查共用 ID，checkpoint 与 history 互相覆盖且外部不可见 | ID 必须带随机后缀 |

---

## 5. 工作流

```
initialize → load_document(可选) → generate_spec → screen → assess → docreview
                                                                        ↓
finalize ← execute ← user_approval ← revise_spec ← (如需循环)
```

`screen` / `assess` 是决策层文档级原语节点（T-17b）。五个原语**各只在唯一一处**
被调用：screen/assess 在图节点，verify_issues/verify_resolutions/judge_convergence
在 `review()` 内。重复调用会让 trace 出现两条记录，审计方无法区分「跑过一次」
与「跑了两次」。

### 决策层开关

优先级 **CLI > `LAYA__ENABLED` > 默认 false**。配置只从环境读，嵌套用双下划线
（`LAYA__ENABLED`）；扁平 `LAYA_ENABLED` 只告警不生效。

未启用时决策层**不存在**（`decision_engine is None`），不写任何 `laya_*` 键——
「决策层没跑」与「决策层跑了但恒 uncertain」必须可区分。
