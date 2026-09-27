---
name: docreview
description: 基于 LangGraph 的多智能体文档评审系统，带可审计决策层，并以 MCP Server 暴露 HTTP/stdio 两种模式。当用户要评审 PRD、技术方案、需求文档等结构化文档，或要生成规格说明、配置 MCP Server、排查评审流程、开启 Laya 真实权重决策时使用。触发词：文档审查、文档评审、代码审查、技术文档评审、PRD 审查、需求文档审查、文档分析、生成规格、MCP Server、文档评审系统、智能文档审查、Laya 决策层。
---

# DocReview Agent System

对技术文档执行六步结构化评审，并在评审链的每个判定点留下可审计证据。

## 先决条件

```bash
pip install -e ".[dev]"     # Python 3.11+
cp .env.example .env        # 至少填 LLM_API_KEY
```

决策层依赖已随仓库 vendor 在 `laya/`（Apache-2.0），**无需**单独克隆上游。

## 决策：先选运行形态

| 场景 | 用什么 |
|---|---|
| 一次性评审本地文档 | CLI `docreview review` |
| 让 AI 客户端自主调用 | MCP **stdio**（标准协议，Claude Desktop / Continue） |
| 服务化、多客户端并发 | MCP **HTTP** |
| 改本项目代码 | 先读 `README.md` 与 `claude-context/STATUS.md` |

## CLI

```bash
docreview review --doc-path ./docs/prd.md --task "评审这份 PRD"
docreview review --doc-path ./docs/prd.md --laya      # 开启 Laya 真实权重
docreview review --doc-path ./docs/prd.md --no-laya   # 强制关闭（优先级最高）
docreview generate-spec --task "设计用户认证系统" --spec-output ./specs/auth.md
docreview status
docreview resume --thread-id review-20260520-140010 --approve
```

`--laya/--no-laya` 是三态开关：**命令行 > `LAYA__ENABLED` > 默认关闭**。不传即不覆盖配置。

退出码：`0` 成功 / `1` 评审失败 / `2` 系统异常 / `3` 用户中断 / `4` 参数非法。

## MCP 两种模式的方法名不同（易踩）

| 能力 | stdio（标准） | HTTP（**非标准**，冻结契约） |
|---|---|---|
| 握手 | `initialize` | 无 |
| 列工具 | `tools/list` | `list_tools` |
| 调工具 | `tools/call` | `invoke` |
| 工具 schema 键 | `inputSchema` | `parameters` |

**用标准 MCP 客户端连 HTTP 会失败**（它发 `tools/call`，HTTP 侧只认 `invoke`）。
这是规格 F8 冻结的既有分歧，不是缺陷；改动需先改规格并重生成快照。

```bash
python mcp_stdio_start.py                                    # stdio
python mcp_server_start.py --host 127.0.0.1 --port 8000      # HTTP
```

三个工具：`review_document`（`doc_path`/`task`/`max_iterations`）、
`generate_spec`（`task` 必填/`document_content`）、`health_check`（无参）。

客户端配置：

```json
{
  "mcpServers": {
    "docreview": {
      "command": "python",
      "args": ["<仓库绝对路径>/mcp_stdio_start.py"],
      "env": { "LLM_API_KEY": "sk-...", "LOG_LEVEL": "WARNING" }
    }
  }
}
```

## 决策层

五个原语：`screen_document`、`assess_document`、`verify_issues`、
`verify_resolutions`、`judge_convergence`。默认走 `NullDecisionEngine`
（判定恒为 `uncertain`，不加载任何 ML 栈）；`--laya` 才切到真实权重。

`import src.decisions` **不会**加载 torch/laya——这条惰性导入边界有测试守着，
不要在模块顶层引入 laya。

开启 Laya 需 5 个环境变量：`LAYA__MODEL`（`english`/`multilingual`/`typed-decisions`）、
`LAYA__DEVICE`、`LAYA__MODEL_DIR`、`LAYA__CHECKPOINT_MANIFEST_PATH`、`LAYA__CALIBRATION_PATH`。

> ⚠️ 嵌套配置**必须**用双下划线：`LAYA__ENABLED` 能被解析，`LAYA_ENABLED` 不能。

校准包必须由人工标注语料拟合（fit ≥100 / validation ≥50）。仓库内
`tests/test_decisions/fixtures/calibration/` 是**测试夹具**，不能当生产校准语料用。

## 质量闸门

```bash
python -m pytest                    # 777 passed, 4 frozen failed, 9 deselected
python -m ruff check .              # All checks passed
python -m mypy src/                 # 75 errors in 14 files（历史债务）
```

4 项 Windows terminal 失败是**既有冻结基线**，不要用 skip/xfail 掩盖。
带 `laya_integration` 标记的 9 项真实权重测试默认 deselected。

## 排障

| 现象 | 原因与处理 |
|---|---|
| CLI 报缺密钥 | `.env` 未填或变量名用了单下划线 |
| 决策层不生效 | 默认关闭，需显式 `--laya`；且权重与校准包须齐备 |
| 改 MCP 后快照测试失败 | 契约已冻结，勿改字段；确需变更先改规格再跑 `python -m tests.test_mcp_server._regen_snapshots` |
| HTTP 模式 `tools/call` 报未知方法 | 见上文方法名差异，改用 `invoke` 或改走 stdio |
| 内存闸门恒不触发 | 必须做负向验证（阈值调到必然超标，确认退出码非零） |

日志在 `logs/`，checkpoint 在 `data/checkpoints.db`（支持损坏检测与恢复）。
