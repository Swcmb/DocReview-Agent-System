# DocReview Agent System

> 用多智能体评审技术文档，并把每个判断留下可审计的证据链。
> 通过 CLI 或 MCP 对外服务，内置一层**可开关的决策层**。

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 它解决什么问题

评审一份 PRD 时，真正耗时的不是"让 LLM 读一遍"，而是三件事：

1. **判断要能复查**——"这里有风险"凭什么是风险？依据在哪？
2. **过程要能中止**——AI 提了个改动方案就自动执行？不该。
3. **结论要能复现**——同一份文档两次评审结论不一致，等于没评审。

本系统对这三点给出具体机制：决策层把模糊判断拆成**五个原语**，每次判定输出结构化结论 +
audit 记录 + 来源指纹；执行前有 LangGraph 中断点强制人工确认；跨轮次比对问题指纹识别
"原地打转"。

**决策层默认关闭。** 不开也能跑完整流程——它退化为"不下判断"而非"崩溃"。

## 快速开始

```bash
git clone https://github.com/Swcmb/DocReview-Agent-System.git
cd DocReview-Agent-System

pip install -e ".[dev]"        # 含 pytest / ruff / mypy
cp .env.example .env           # 填入 LLM_API_KEY
```

```bash
# 评审一份文档
docreview review --doc-path ./docs/prd.md --task "评审这份 PRD"

# 只生成规格，不评审
docreview generate-spec --task "设计一个用户认证系统" --spec-output ./specs/auth.md
```

> **不需要**额外克隆 laya。决策层依赖已随仓库 vendor 在 `laya/`（Apache-2.0，
> 来源见 [`third_party/laya/NOTICE.md`](third_party/laya/NOTICE.md)）。

## 核心特性

| 特性 | 说明 |
|---|---|
| **六步文档审查** | 按固定流程评审，产出带严重度与稳定 ID 的结构化问题（如 `BK-3-2`） |
| **可审计决策层** | 五个原语，每次判定输出 audit、来源指纹、置信度；无权重环境可完整测试 |
| **执行门控** | 执行已批准任务前必须人工确认（LangGraph `interrupt_before`） |
| **停滞检测** | 跨轮次问题指纹比对，命中阈值强制终止 |
| **成本控制** | 累计 `total_llm_cost` 超 `LLM_MAX_COST_PER_TASK` 即中止 |
| **三种 MCP 接入** | stdio（推荐）、HTTP + JSON-RPC、Streamable HTTP |
| **契约有测试守着** | 26 份快照逐字节锁定对外 schema，改契约须先改规格 |
| **Skill 接入** | 随仓库分发 Agent Skill，AI 客户端可自动发现 |

## 架构

```
   CLI / MCP Server
          │
          ▼
┌─────────────────────────────────────┐
│        LangGraph 工作流              │
│                                     │
│  initialize → load_document（可选）   │
│      ↓                              │
│  generate_spec ⇄ revise_spec        │
│      ↓                              │
│  docreview（六步审查）                │
│      ↓                              │
│  evaluate_result                     │
│      ↓                              │
│  user_approval  ◀── 人工中断点       │
│      ↓                              │
│  execute → finalize                 │
└──────────────────┬──────────────────┘
                   │ 每个节点都问决策层
                   ▼
┌─────────────────────────────────────┐
│      决策层 src/decisions            │
│                                     │
│  screen_document      文档风险筛查   │
│  assess_document      文档级裁决     │
│  verify_issues         单问题核实     │
│  verify_resolutions    修复核实       │
│  judge_convergence     收敛判定       │
└──────────────────┬──────────────────┘
                   │ 引擎可替换
        ┌──────────┴──────────┐
        ▼                     ▼
 NullDecisionEngine      LayaDecisionEngine
 （默认，零依赖）         （--laya，真实权重）
```

**决策层是惰性的**：`import src.decisions` 不加载 `torch` / `laya` / `transformers`。
只有显式开启 Laya 且真正需要判定时才惰性载入权重。这条边界有测试守着。

## 安装

**前置要求**

- Python 3.11+
- 一个 OpenAI 兼容的 LLM 接口（OpenAI / Anthropic / 通义千问 / 本地 vLLM 均可）
- 可选：Laya 真实权重（仅 `--laya` 模式需要）

## CLI

```bash
# 评审
docreview review --doc-path ./docs/prd.md
docreview review --doc-path ./docs/prd.md --task "评审这份 PRD"
docreview review --doc-path ./docs/prd.md --max-iterations 5

# 决策层开关（三态：命令行 > 环境变量 > 默认关闭）
docreview review --doc-path ./docs/prd.md --laya
docreview review --doc-path ./docs/prd.md --no-laya

# 生成规格
docreview generate-spec --task "设计一个用户认证系统" --spec-output ./specs/auth.md

# 状态 / 恢复中断
docreview status
docreview resume --thread-id review-20260520-140010 --approve
```

### 退出码

| 码 | 常量 | 含义 |
|---|---|---|
| 0 | `EXIT_SUCCESS` | 成功 |
| 1 | `EXIT_REVIEW_FAILED` | 评审未通过 / 失败 |
| 2 | `EXIT_SYSTEM_ERROR` | 系统级异常 |
| 3 | `EXIT_USER_ABORT` | 用户中断 |
| 4 | `EXIT_INVALID_ARGS` | 参数非法 |

> ⚠️ **2 号码有歧义**：Typer/Click 的**用法错误**（缺必填参数、未知子命令）也固定返回 `2`，
> 与 `EXIT_SYSTEM_ERROR` 撞号。脚本判断"是参数问题还是系统异常"不能只看退出码，
> 需看 stderr 是否含 `Usage:`。实测：`docreview resume`（缺 `--thread-id`）→ `2`（Click）；
> `docreview review`（缺 `--doc-path` 与 `--task`）→ `4`（项目自身校验）。

## 接入 MCP

三种接入方式，服务的是同一组工具：`health_check`、`review_document`、`generate_spec`。

### stdio 模式（推荐给 Claude Desktop 等客户端）

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

stdio 是**唯一完全符合 MCP 规范**的接入方式，无会话、无状态，兼容性最好。

### HTTP 模式

```bash
python mcp_server_start.py --host 127.0.0.1 --port 8000
```

#### REST

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/tools
```

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 健康检查 |
| `GET` | `/tools` | 工具列表 |
| `POST` | `/review` | 评审文档 |
| `POST` | `/generate-spec` | 生成规格 |
| `POST` | `/invoke` | 调用工具（JSON-RPC 体） |
| `POST` | `/` | JSON-RPC 2.0 端点 |

#### JSON-RPC（`POST /`）

同时接受**两套方法名**，标准与旧版并存：

| 方法 | 返回形态 | 说明 |
|---|---|---|
| `initialize` | 握手信息 | MCP 标准 |
| `tools/list` | `inputSchema` | MCP 标准 |
| `tools/call` | `content` + `metadata` | MCP 标准 |
| `list_tools` | `parameters` | 旧方法，保留兼容 |
| `invoke` | Pydantic 模型 | 旧方法，保留兼容 |

```bash
# 标准调用（不需要会话）
curl -X POST http://127.0.0.1:8000/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"health_check","arguments":{}}}'
```

同名工具经标准方法与 stdio 返回**逐字段一致**的响应（有测试锁定）。

> ⚠️ **PowerShell 用户注意**：PowerShell 会吃掉 `curl -d '{...}'` 里的双引号，导致
> 服务端收到 `json_invalid`（400）。改用 `curl.exe --data-raw` 并把 JSON 放进
> 变量，或直接用 Python / `Invoke-RestMethod` 发请求。README 中示例按 bash 书写。


#### Streamable HTTP（`/mcp`，规范 2025-03-26）

```bash
# 1) 握手，拿会话 ID
curl -i -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2024-11-05","capabilities":{},
                 "clientInfo":{"name":"my-client","version":"1.0"}}}'
# 响应头含：Mcp-Session-Id: <id>

# 2) 后续请求必须带上该头
curl -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Mcp-Session-Id: <id>" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

# 3) 终结会话
curl -X DELETE http://127.0.0.1:8000/mcp -H "Mcp-Session-Id: <id>"
```

| 方法/路径 | 作用 |
|---|---|
| `POST /mcp` | 客户端 → 服务端；`initialize` 回传 `Mcp-Session-Id` |
| `GET /mcp` | 服务端 → 客户端 SSE 流 |
| `DELETE /mcp` | 终结会话（204） |

- 非 `initialize` 请求**必须**带 `Mcp-Session-Id`，缺失 `400`、失效 `404`。
- 传输层错误用 RFC 9457 `application/problem+json`，**不与 JSON-RPC `error` 信封混用**——
  混用会让客户端把传输错误误判为协议响应。
- **会话只存在进程内存**：服务重启后全部失效，客户端须重新 `initialize`。
  这是刻意取舍——本服务跨请求无状态，落盘只会引入无谓的持久化与清理负担。
- `GET /mcp` 只发保活注释：本服务没有服务端主动消息（进度、日志推送）。

### 契约测试

对外 schema 由 26 份快照**逐字节**锁定（`tests/test_mcp_server/snapshots/`）。
改契约的正确流程是**先改规格、再重生成基线**，而不是让实现去迁就测试：

```bash
python -m tests.test_mcp_server._regen_snapshots
```

## 决策层

### 五个原语

| 原语 | 职责 | 产出 |
|---|---|---|
| `screen_document` | 文档级风险筛查 | `ScreenResult`（是否值得深审、findings） |
| `assess_document` | 文档级裁决 | 路由判定与置信度 |
| `verify_issues` | 单个问题是否成立 | 成立与否、观测状态 |
| `verify_resolutions` | 修复是否真的解决 | 成立与否 |
| `judge_convergence` | 是否收敛 | 收敛结论与路由 |

每个原语返回结构化结果，并附 audit 记录、来源指纹与 `answer_confidence`。

### 两种引擎

| 引擎 | 何时使用 | 依赖 |
|---|---|---|
| `NullDecisionEngine` | **默认**。不加载任何 ML 栈，判定恒为 `uncertain` | 无 |
| `LayaDecisionEngine` | 显式 `--laya` 且权重齐备 | `laya` + `torch`（已 vendor） |

没装 torch 的环境也能跑完整测试与评审——决策层退化为"不下判断"而非"崩溃"。

### 开启 Laya

```bash
LAYA__ENABLED=true
LAYA__MODEL=multilingual          # english | multilingual | typed-decisions
LAYA__DEVICE=cpu
LAYA__MODEL_DIR=/path/to/laya-huggingface
LAYA__CHECKPOINT_MANIFEST_PATH=/path/to/manifest.json
LAYA__CALIBRATION_PATH=/path/to/calibration.json
```

生成 checkpoint manifest：

```bash
python scripts/build_laya_checkpoint_manifest.py \
  --model-dir /path/to/laya-huggingface --model multilingual \
  --output ./artifacts/checkpoint_manifest.json
```

> ⚠️ `LAYA__CALIBRATION_PATH` 指向的校准包必须由**人工标注语料**拟合而来
> （fit ≥100 / validation ≥50）。仓库内 `tests/test_decisions/fixtures/calibration/`
> 是**测试夹具**，不是合规校准语料，不要拿它冒充。

## 配置

通过 `.env` 配置，模板见 [`.env.example`](.env.example)。

```bash
# ── LLM ──
LLM_PROVIDER=openai
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o
LLM_BASE_URL=https://api.openai.com/v1
LLM_TEMPERATURE=0.3
LLM_REQUEST_TIMEOUT=120
LLM_MAX_COST_PER_TASK=5.0

# ── 智能体行为 ──
MAX_REVIEW_ITERATIONS=10
STAGNATION_THRESHOLD=3
USER_APPROVAL_TIMEOUT=3600
WORKSPACE_DIR=./workspace

# ── MCP 客户端（Sequential Thinking / Context7）──
MCP_SEQUENTIAL_THINKING_ENABLED=true
MCP_CALL_TIMEOUT=30

# ── 决策层（可选，默认全关）──
LAYA__ENABLED=false
LAYA__MODEL=multilingual
LAYA__DEVICE=cpu
LAYA__MAX_LEN=8192
LAYA__TIMEOUT_SECONDS=30

# ── 日志 ──
LOG_LEVEL=INFO
LOG_FILE=./logs/docreview.log
```

> ⚠️ **嵌套配置必须用双下划线**：`LAYA__ENABLED` 才会被解析成 `config.laya.enabled`，
> 写成 `LAYA_ENABLED` 不会生效。
>
> MCP Server 的 host/port 是**命令行参数**（`--host` / `--port`），不是环境变量。

## 项目结构

```
DocReview-Agent-System/
├── main.py                       # CLI 入口（typer）
├── mcp_server_start.py           # MCP HTTP 入口
├── mcp_stdio_start.py            # MCP stdio 入口
├── src/
│   ├── agents/                   # Supervisor / DocReview 智能体
│   ├── decisions/                # 决策层（五原语 + 两引擎 + 校准）
│   │   └── issue_id.py           #   问题 ID 分配的纯函数
│   ├── mcp/                      # MCP 客户端（Sequential Thinking / Context7）
│   ├── mcp_server/               # MCP 服务端
│   │   ├── core.py               #   两种传输的唯一真相源
│   │   ├── server.py             #   HTTP 传输（薄）
│   │   ├── stdio_server.py       #   stdio 传输（薄）
│   │   └── streamable.py         #   Streamable HTTP 传输
│   ├── schemas/                  # 数据模型（AgentState / IssueStatus / ...）
│   ├── state/                    # 状态与历史（SectionIndex / 问题指纹）
│   ├── tools/                    # 工具层（reading / terminal / web_search）
│   ├── utils/                    # LLM 客户端、日志、prompt 加载
│   └── workflows/                # LangGraph 工作流定义
├── laya/                         # vendored 决策引擎（Apache-2.0）
├── third_party/laya/             # 上游 LICENSE 与 NOTICE
├── skills/docreview/SKILL.md     # Agent Skill 定义
├── scripts/                      # manifest / 校准 / 评估 / 内存测量
├── tests/                        # pytest 套件（26 个测试文件）
├── docs/  examples/  specs/  prompts/
└── pyproject.toml
```

## 质量闸门

```bash
python -m compileall -q src tests   # exit 0
python -m pytest                    # 797 passed, 4 failed, 9 deselected
python -m ruff check .              # All checks passed!
python -m mypy src/                 # 75 errors in 14 files
```

| 闸门 | 现状 | 说明 |
|---|---|---|
| compileall | **0** | 通过 |
| ruff | **0** | 全绿；vendored 的 `laya/` 已排除在闸门外 |
| pytest | 797 passed / **4 failed** | 4 项为 Windows terminal 既有冻结失败，见「已知限制」 |
| mypy `src/` | **75 errors** | 历史债务，集中在 14 个早期模块；本轮改动零新增 |
| MCP 契约 | 35 passed | 17 契约 + 18 Streamable |
| 导入边界 | 通过 | `import src.decisions` 不加载 torch/laya |
| Laya 硬闸门 | **未通过** | 需人工标注语料，见「已知限制」 |

带 `laya_integration` 标记的 9 项真实权重测试默认排除，需显式选择：

```bash
python -m pytest -m laya_integration
```

## 已知限制

诚实列出当前**未通过**或**未收口**的部分：

1. **4 项 Windows terminal 测试失败**（既有基线，已登记，**不可**用 skip/xfail 掩盖）：
   `test_execute_command_with_working_directory`、`test_execute_piped_command`、
   `test_command_whitelist_allowed`、`test_command_duration_recorded`。

2. **Laya 真实权重硬闸门未通过**：`tests/test_decisions/test_laya_integration.py` 的
   9 项测试需要环境变量齐备，其中 `LAYA__CALIBRATION_PATH` 指向的校准包必须由
   **人工标注语料**拟合（fit ≥100 / validation ≥50）。这是人工前置依赖，不在代码
   实现范围内。测试按设计直接失败而非跳过。

3. **mypy 未达绝对零**：75 项历史错误集中在 14 个早期模块（多为缺类型标注）。
   绝对清零需单独立项。

4. **Streamable HTTP 会话不持久**：仅存于进程内存，重启即失效（见上文取舍说明）。

5. **`_compile_markdown_report()` 无生产调用方**：定义在 `src/agents/docreview.py:783`，
   目前仅被测试引用（10 处）。issue_id 分配已抽为纯函数
   `src/decisions/issue_id.py::assign_issue_ids()`，该方法退化为纯渲染后失去调用点，
   去留待定。

6. **`calibration.py` 的 `confidence` 允许为 `None`**：契约层面合法，但下游存在
   `float()` 运算，理论可达 `TypeError`。当前保留 `type: ignore` 以维持既有行为，
   可达性待排查。

## 开发

```bash
pip install -e ".[dev]"

pytest                             # 全部
pytest tests/test_decisions/       # 按目录
pytest -k "workflow"               # 按名
pytest --cov=src/                  # 覆盖率
pytest -m laya_integration         # 真实权重（需权重与校准包）

ruff check .                       # lint
ruff check . --fix                 # 自动修
mypy src/                          # 类型

# 内存闸门（需先启动 Docker Desktop）
docker compose --profile rss run --rm docreview-rss
python scripts/measure_peak_rss.py --limit-mb 1   # 负向验证，期望 exit 1
```

> 内存 / 阈值类闸门**务必做负向验证**（把阈值调到必然超标，确认退出码非零）。
> 否则无法区分"真的达标"与"闸门恒真"。

### 改动纪律

- **契约冻结**：MCP 对外的任何字段增删都要先改规格，再重生成快照，
  并在同一次提交里说明原因。若只是重构导致快照失配，正确处置是**回退实现**。
- **不要用测试夹具冒充真实数据**（尤其是校准语料）。
- **保持 torch-free 边界**：新增 import 时别让 `src.decisions` 拉进 ML 栈。
- 提交前跑齐上面四个闸门；4 项 terminal 失败属既有基线，不要顺手"修"成 skip。

## 许可证

本项目采用 **MIT** 许可证。

仓库内 vendor 了第三方组件 **laya**（Apache-2.0，upstream
[NandhaKishorM/laya](https://github.com/NandhaKishorM/laya)），其许可证全文与来源说明见
[`third_party/laya/LICENSE`](third_party/laya/LICENSE) 与
[`third_party/laya/NOTICE.md`](third_party/laya/NOTICE.md)。
