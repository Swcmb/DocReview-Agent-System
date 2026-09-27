# DocReview Agent System

> 基于 LangGraph 的多智能体文档评审系统，带一套**可审计的决策层**，并以 MCP Server 对外提供服务。

## 概述

DocReview Agent System 用 AI 智能体对技术文档做结构化评审：读文档 → 生成/修订规格 → 六步审查 →
评估结果 → 人工确认后执行 → 出报告。

它的特别之处不在"用 LLM 评文档"，而在于**每个判断都留下可审计的证据链**。系统内置的决策层把
"这篇文档有没有风险"这类模糊判断，拆成五个原语，每个原语产出结构化结论、audit 记录与来源指纹；
没有 LLM 也能跑（Null 引擎），有 LLM 则走 Laya 真实权重。**决策层默认关闭**，需显式开启。

## 特性

- **六步文档审查**：`docreview` 智能体按固定流程评审，产出带严重度与稳定 ID 的结构化问题
- **可审计决策层**：五个原语（screen / assess / verify / convergence + issue 流转），
  每次判定输出 audit、来源指纹与置信度；无权重环境可完整测试
- **真实权重可选**：vendored 的 [laya](https://github.com/NandhaKishorM/laya) 提供
  `noul` / `choice` / `score` 三种题型判定，CPU 可跑
- **执行门控**：执行已批准任务前必须人工确认（LangGraph 中断点）
- **停滞检测**：跨轮次问题指纹比对，识别"原地打转"
- **两种 MCP 模式**：HTTP（REST + JSON-RPC）与 stdio，覆盖 Claude Desktop、Continue 等客户端
- **Skill 接入**：随仓库分发一份 Agent Skill，供 AI 客户端自动发现

## 架构

```
                      ┌──────────────────────────────┐
   CLI / MCP Server   │        LangGraph 工作流       │
   ─────────────────▶│                              │
                      │  initialize                  │
                      │      ↓                       │
                      │  load_document（可选）        │
                      │      ↓                       │
                      │  generate_spec ⇄ revise_spec │
                      │      ↓                       │
                      │  docreview（六步审查）        │
                      │      ↓                       │
                      │  evaluate_result             │
                      │      ↓                       │
                      │  user_approval  ◀── 中断点    │
                      │      ↓                       │
                      │  execute → finalize          │
                      └──────────────┬───────────────┘
                                     │ 每个节点都问决策层
                                     ▼
                      ┌──────────────────────────────┐
                      │       决策层 src/decisions    │
                      │                              │
                      │  screen_document             │  文档级风险筛查
                      │  assess_document             │  文档级裁决
                      │  verify_issues               │  单问题核实
                      │  verify_resolutions          │  修复核实
                      │  judge_convergence           │  收敛判定
                      └──────────────┬───────────────┘
                                     │ 引擎可替换
                        ┌────────────┴────────────┐
                        ▼                         ▼
                NullDecisionEngine        LayaDecisionEngine
                （默认，无依赖）         （--laya 开启，真实权重）
```

**决策层是惰性的**：`import src.decisions` 不会加载 `torch` / `laya` / `transformers`。
只有显式开启 Laya 且真正需要判定时，才通过 `__import__` 惰性载入权重。这条边界有测试守着。

## 安装

### 前置要求

- Python 3.11+
- LLM API 密钥（OpenAI / Anthropic / 通义千问等，OpenAI 兼容接口即可）
- 可选：Laya 真实权重（仅 `--laya` 模式需要）

### 安装步骤

```bash
git clone <repository-url>
cd DocReview-Agent-System

pip install -e ".[dev]"      # 含 pytest / ruff / mypy

cp .env.example .env         # 填入 LLM_API_KEY 等
```

> **不需要**再单独克隆 laya。决策层依赖已随仓库 vendor 在 `laya/`（Apache-2.0，
> 见 [`third_party/laya/NOTICE.md`](third_party/laya/NOTICE.md)）。

## 快速开始

### CLI

```bash
# 评审已有文档
docreview review --doc-path ./docs/prd.md

# 附带任务描述
docreview review --doc-path ./docs/prd.md --task "评审这份 PRD"

# 开启 Laya 真实权重决策（默认关闭）
docreview review --doc-path ./docs/prd.md --laya
docreview review --doc-path ./docs/prd.md --no-laya     # 显式关闭，优先级最高

# 由任务描述直接生成规格
docreview generate-spec --task "设计一个用户认证系统" --spec-output ./specs/auth.md

# 查看状态 / 恢复中断
docreview status
docreview resume --thread-id review-20260520-140010 --approve
```

`--laya/--no-laya` 是**三态开关**：命令行 > `LAYA__ENABLED` 环境变量 > 默认关闭。
不传即不覆盖配置文件。退出码：

| 码 | 常量 | 含义 |
|---|---|---|
| 0 | `EXIT_SUCCESS` | 成功 |
| 1 | `EXIT_REVIEW_FAILED` | 评审未通过 / 失败 |
| 2 | `EXIT_SYSTEM_ERROR` | 系统级异常 |
| 3 | `EXIT_USER_ABORT` | 用户中断 |
| 4 | `EXIT_INVALID_ARGS` | 参数非法 |

### MCP Server（HTTP 模式）

```bash
python mcp_server_start.py --host 127.0.0.1 --port 8000

curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/tools
```

同时暴露 REST（`/health`、`/tools`）与 JSON-RPC（`/mcp`）。

### MCP Server（stdio 模式）

给 Claude Desktop 等客户端用，配置示例：

```json
{
  "mcpServers": {
    "docreview": {
      "command": "python",
      "args": ["D:/path/to/DocReview-Agent-System/mcp_stdio_start.py"],
      "env": { "LOG_LEVEL": "WARNING" }
    }
  }
}
```

两种模式暴露**同一组工具**：`health_check`、`review_document`、`generate_spec`、`list_tools`。

## 决策层

### 五个原语

| 原语 | 职责 | 产出 |
|---|---|---|
| `screen_document` | 文档级风险筛查 | `ScreenResult`（是否值得深审、findings） |
| `assess_document` | 文档级裁决 | 路由判定与置信度 |
| `verify_issues` | 单个问题的核实 | 是否成立、观测状态 |
| `verify_resolutions` | 修复的核实 | 是否真的解决 |
| `judge_convergence` | 是否收敛 | 收敛结论与路由 |

每个原语都返回结构化结果，并附 audit 记录、来源指纹与 `answer_confidence`。

### 两种引擎

| 引擎 | 何时使用 | 依赖 |
|---|---|---|
| `NullDecisionEngine` | **默认**。不加载任何 ML 栈，判定恒为 `uncertain` | 无 |
| `LayaDecisionEngine` | 显式 `--laya` 且权重齐备 | `laya` + `torch`（vendored） |

这是刻意的设计：没装 torch 的环境也能跑完整测试与评审，决策层退化为"不下判断"
而非"崩溃"。

### 开启 Laya 所需的环境变量

```bash
LAYA__MODEL=multilingual            # english | multilingual | typed-decisions
LAYA__DEVICE=cpu
LAYA__MODEL_DIR=/path/to/laya-huggingface
LAYA__CHECKPOINT_MANIFEST_PATH=/path/to/manifest.json
LAYA__CALIBRATION_PATH=/path/to/calibration.json
```

生成 manifest：

```bash
python scripts/build_laya_checkpoint_manifest.py \
  --model-dir /path/to/laya-huggingface --model multilingual \
  --output ./artifacts/checkpoint_manifest.json
```

> **注意**：`LAYA__CALIBRATION_PATH` 指向的校准包必须由人工标注语料拟合而来
> （fit ≥100 / validation ≥50）。仓库内的 `tests/test_decisions/fixtures/calibration/`
> 是**测试夹具**，不是合规校准语料。相关硬闸门见下文「质量闸门」。

## MCP Server API

### 工具

| 工具 | 说明 | 主要入参 |
|---|---|---|
| `health_check` | 健康检查 | — |
| `review_document` | 评审文档 | `doc_path` / `task` / `max_iterations` |
| `generate_spec` | 生成规格说明 | `task` / `document_content` |

工具枚举走 JSON-RPC 方法 `tools/list`（HTTP 模式下另有 `list_tools` 方法与 `GET /tools`），
它本身不是一个可调用工具。

HTTP 模式额外提供 `GET /health`、`GET /tools`（REST）与 `POST /mcp`（JSON-RPC 2.0）。

### JSON-RPC 调用示例

```bash
curl -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"health_check","arguments":{}}}'
```

## 项目结构

```
DocReview-Agent-System/
├── main.py                  # CLI 入口（typer）
├── mcp_server_start.py      # MCP HTTP 入口
├── mcp_stdio_start.py       # MCP stdio 入口
├── src/
│   ├── agents/              # Supervisor / DocReview 智能体
│   ├── decisions/           # 决策层（五原语 + 两种引擎 + 校准）
│   ├── mcp/                 # MCP 客户端（Sequential Thinking / Context7）
│   ├── mcp_server/          # MCP 服务端（server.py / stdio_server.py）
│   ├── schemas/             # 数据模型（AgentState / IssueStatus / ...）
│   ├── state/               # 状态与历史（SectionIndex / issue 指纹）
│   ├── tools/               # 工具层（reading / terminal / web_search）
│   ├── workflows/           # LangGraph 工作流定义
│   └── utils/               # LLM 客户端、日志、prompt 加载
├── laya/                    # vendored 第三方决策引擎（Apache-2.0）
├── third_party/laya/        # 上游 LICENSE 与 NOTICE
├── scripts/                 # manifest / 校准 / 评估 / 内存测量
├── Skill/SKILL.md           # Agent Skill 定义
├── tests/                   # pytest 套件
└── claude-context/          # 规格与任务状态（不纳入版本控制）
```

## 配置

通过 `.env` 配置（模板见 `.env.example`）。嵌套配置用 `__` 分隔。

```bash
# LLM
LLM_PROVIDER=openai
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o
LLM_BASE_URL=https://api.openai.com/v1
LLM_TEMPERATURE=0.3
LLM_REQUEST_TIMEOUT=120
LLM_MAX_COST_PER_TASK=5.0

# 智能体行为
MAX_REVIEW_ITERATIONS=10
STAGNATION_THRESHOLD=3
USER_APPROVAL_TIMEOUT=3600
WORKSPACE_DIR=./workspace

# 决策层（可选）
LAYA__ENABLED=false
LAYA__MODEL=multilingual          # english | multilingual | typed-decisions
LAYA__DEVICE=cpu
LAYA__MODEL_DIR=/path/to/laya-huggingface
LAYA__MAX_LEN=8192
LAYA__TIMEOUT_SECONDS=30

# 系统
LOG_LEVEL=INFO
LOG_FILE=./logs/docreview.log
```

MCP 服务的 host/port 是**命令行参数**（`--host` / `--port`），不是环境变量。

> ⚠️ 嵌套配置必须用双下划线。`LAYA_ENABLED` 不会被解析成 `config.laya.enabled`。

## 质量闸门

```bash
python -m compileall -q src tests   # exit 0
python -m pytest                    # 777 passed, 4 failed, 9 deselected
python -m ruff check .              # All checks passed
python -m mypy src/                 # 77 errors in 14 files
```

| 闸门 | 现状 | 说明 |
|---|---|---|
| pytest | 777 passed / **4 failed** | 4 项为 Windows terminal 既有冻结失败，见「已知限制」 |
| ruff | **0** | 全绿；vendored 的 `laya/` 已排除在闸门外 |
| mypy `src/` | **77 errors** | 决策层等本次新增文件已 0 error；余下为历史债务 |
| 导入边界 | 通过 | `import src.decisions` 不加载 torch/laya |
| Laya 硬闸门 | **未通过** | 需人工标注语料，见「已知限制」 |

带 `laya_integration` 标记的 9 项真实权重测试默认被排除，需显式选择：

```bash
python -m pytest -m laya_integration
```

## 已知限制

诚实列出当前**未通过**或**未收口**的部分：

1. **4 项 Windows terminal 测试失败**（既有基线，已登记，不可用 skip/xfail 掩盖）：
   `test_execute_command_with_working_directory`、`test_execute_piped_command`、
   `test_command_whitelist_allowed`、`test_command_duration_recorded`。

2. **Laya 真实权重硬闸门未通过**：`tests/test_decisions/test_laya_integration.py` 的 9 项测试
   需要 5 个环境变量齐备，其中 `LAYA__CALIBRATION_PATH` 指向的校准包必须由**人工标注语料**
   拟合（fit ≥100 / validation ≥50）。这是人工前置依赖，不在代码实现范围内。
   测试按设计直接失败而非跳过，**不要用测试夹具冒充校准语料**。

3. **mypy 未达绝对零**：77 项历史错误集中在 14 个早期模块（多为缺类型标注）。
   本次改动零新增（基线 81），但绝对清零需单独立项。

4. **`_compile_markdown_report()` 无生产调用方**：定义在 `src/agents/docreview.py:783`，
   目前仅被测试引用（`tests/test_agents/test_docreview.py`、`tests/test_decisions/test_issue_id.py`）。
   issue_id 分配已抽为纯函数 `src/decisions/issue_id.py::assign_issue_ids()`，
   该方法退化为纯渲染后即失去调用点，去留待定。

5. **`calibration.py` 的 `confidence` 允许为 `None`**：契约层面合法，但下游存在 `float()`
   运算，理论可达 `TypeError`。当前保留一处 `type: ignore` 以维持既有行为，可达性待排查。

## 开发

```bash
pip install -e ".[dev]"

pytest                      # 全部
pytest tests/test_decisions/  # 目录
pytest -k "workflow"        # 按名
pytest --cov=src/           # 覆盖率

ruff check .                # lint
ruff check . --fix          # 自动修
mypy src/                   # 类型

# 内存闸门（需先启动 Docker Desktop）
docker compose --profile rss run --rm docreview-rss
python scripts/measure_peak_rss.py --limit-mb 1   # 负向验证，期望 exit 1
```

> 内存/阈值类闸门**务必做负向验证**（把阈值调到必然超标，确认退出码非零）。
> 否则无法区分"真的达标"与"闸门恒真"。

## 许可证

本项目采用 **MIT** 许可证。

仓库内 vendor 了第三方组件 **laya**（Apache-2.0），其许可证全文与来源说明见
[`third_party/laya/LICENSE`](third_party/laya/LICENSE) 与
[`third_party/laya/NOTICE.md`](third_party/laya/NOTICE.md)。
