# DocReview Agent System - .trae 目录规范

> **版本**: 1.0  
> **日期**: 2026-05-21  
> **目的**: 定义 DocReview Agent System 项目的文档命名规范和目录组织结构

---

## 📁 目录结构总览

```
.trae/                              # DocReview Agent System 规范目录
├── README.md                       # 本文件：命名规范和目录说明
├── prompts/                        # AI 智能体运行时提示词
│   └── docreview-agent-system/    # DocReview 子智能体相关提示词
│       ├── agent-review-prompt.md          # 运行时审查指令
│       └── agent-invocation-rules.md       # 调用触发条件
├── specs/                         # 技术规格与设计文档
│   ├── docreview-agent/            # DocReview Agent 规格（内部）
│   │   ├── spec.md
│   │   ├── tasks.md
│   │   └── checklist.md
│   └── docreview-agent-system/    # DocReview Agent System 规格（外部）
│       ├── system-specification.md          # 完整系统设计规格
│       └── spac-architecture-reference.md  # SPAC 方法论参考
├── docs/                          # 组件使用文档
│   └── modules/
│       └── mcp-client-usage-guide.md       # MCP 模块使用指南
└── reports/                       # 项目状态报告
    └── mcp-completion-summary.md           # MCP 模块完成总结
```

---

## 📋 目录用途说明

### 1. `.trae/prompts/` - AI 智能体提示词

**用途**: 存放面向 AI 智能体的运行时指令和规则定义。

**命名规则**:
- 文件名必须包含 `agent`、`prompt`、`runtime` 或 `rules` 关键词
- 使用语义化的名称描述功能
- 遵循 `kebab-case` 命名规范

**子目录**:
- `docreview-agent-system/`: DocReview 子智能体相关提示词

**示例**:
```
.trae/prompts/docreview-agent-system/
├── agent-review-prompt.md      # AI 运行时审查指令
└── agent-invocation-rules.md   # 调用触发条件
```

---

### 2. `.trae/specs/` - 技术规格与设计文档

**用途**: 存放面向人类开发者的设计规格、架构文档和技术参考。

**命名规则**:
- 文件名必须包含 `specification`、`design`、`requirements`、`architecture` 或 `reference` 关键词
- 区分内部（`docreview-agent/`）和外部（`docreview-agent-system/`）规格
- 遵循 `kebab-case` 命名规范

**子目录**:
- `docreview-agent/`: DocReview Agent 内部规格
- `docreview-agent-system/`: DocReview Agent System 外部规格

**示例**:
```
.trae/specs/docreview-agent-system/
├── system-specification.md          # 完整系统设计规格
└── spac-architecture-reference.md  # SPAC 方法论参考
```

---

### 3. `.trae/docs/` - 组件使用文档

**用途**: 存放组件使用指南、API 文档和集成手册。

**命名规则**:
- 文件名必须包含 `guide`、`usage`、`manual`、`api` 或 `reference` 关键词
- 按组件/模块分类组织
- 遵循 `kebab-case` 命名规范

**子目录**:
- `modules/`: 各模块的使用文档

**示例**:
```
.trae/docs/modules/
└── mcp-client-usage-guide.md   # MCP 客户端使用指南
```

---

### 4. `.trae/reports/` - 项目状态报告

**用途**: 存放项目进度报告、完成总结和质量评估报告。

**命名规则**:
- 文件名必须包含 `summary`、`report`、`status`、`completion` 或 `progress` 关键词
- 按项目阶段或模块分类
- 遵循 `kebab-case` 命名规范

**示例**:
```
.trae/reports/
└── mcp-completion-summary.md   # MCP 模块完成总结
```

---

## 📝 文件命名映射表

| 旧文件名 | 新文件名 | 新路径 | 分类 |
|---------|---------|--------|------|
| `PROMPT.md` | `agent-review-prompt.md` | `.trae/prompts/docreview-agent-system/` | 系统提示词 |
| `WHENTOCALL.md` | `agent-invocation-rules.md` | `.trae/prompts/docreview-agent-system/` | 行为规范 |
| `SPAC_prompt.md` | `spac-architecture-reference.md` | `.trae/specs/docreview-agent-system/` | 参考文档 |
| `specification.md` | `system-specification.md` | `.trae/specs/docreview-agent-system/` | 技术规格 |
| `README_MCP.md` | `mcp-client-usage-guide.md` | `.trae/docs/modules/` | 组件手册 |
| `MCP_COMPLETION_SUMMARY.md` | `mcp-completion-summary.md` | `.trae/reports/` | 项目报告 |

---

## 🔄 版本控制要求

**所有提示词和规格文件必须纳入 Git 版本控制**，每次修改必须：

1. 使用 **conventional commit** 格式提交
2. 在提交信息中说明变更原因
3. 保持提交粒度适中（避免超长提交）

**推荐 commit 类型**:
- `docs:` - 文档更新
- `fix:` - 修复文档错误
- `feat:` - 新增功能文档
- `refactor:` - 重构文档结构

**示例**:
```bash
git commit -m "docs: update agent-review-prompt with new review criteria"
git commit -m "fix: correct invocation rules for edge cases"
git commit -m "docs: reorganize specs directory structure"
```

---

## 🚫 禁止事项

1. **禁止保留重复文件**: 同一文档只能存在于一个位置，删除旧位置的文件
2. **禁止随意命名**: 文件名必须包含规定的关键词，否则不予版本控制
3. **禁止混用目录**: AI 提示词和技术规格必须分别存放在各自目录

---

## ✅ 检查清单

创建或移动文档时，请确认：

- [ ] 文件名包含规定的关键词
- [ ] 文件存放在正确的目录
- [ ] 已删除旧位置的文件（保持唯一来源）
- [ ] 已更新本文档的映射表（如适用）

---

## 📋 变更记录

### 2026-05-21 - 目录结构迁移与代码修复

**变更类型**: 目录重构 + 代码修复

**变更内容**:

| 项目 | 变更说明 |
|------|---------|
| 目录结构 | 将项目文档从 `prompts/` 和 `doc/` 目录迁移到 `.trae/` 统一规范目录 |
| 文件重命名 | 按规范重命名了所有 6 个文档文件（参见映射表） |
| 代码修复 | 更新了 `src/agents/docreview.py` 和 `src/utils/prompt_loader.py` 中的路径引用 |
| 向后兼容 | 为 PromptLoader 添加了旧文件名自动映射功能 |

**修复的文件**:

1. **[src/agents/docreview.py](file:///d:/DocReview-Agent-System/src/agents/docreview.py#L116-L117)**:
   - 更新 `PROMPT_PATH` 为 `.trae/prompts/docreview-agent-system/agent-review-prompt.md`
   - 更新 `WHENTOCALL_PATH` 为 `.trae/prompts/docreview-agent-system/agent-invocation-rules.md`

2. **[src/utils/prompt_loader.py](file:///d:/DocReview-Agent-System/src/utils/prompt_loader.py#L34-L46)**:
   - 更新 `DEFAULT_PROMPTS_DIR` 为 `.trae/prompts/`
   - 添加 `FILENAME_MAPPING` 字典实现旧文件名到新路径的自动映射
   - 新增 `_resolve_path()` 和 `exists()` 方法
   - 增强了错误信息和调试日志

**验证结果**:

| 验证项 | 状态 | 说明 |
|--------|------|------|
| 文件路径存在性 | ✅ 通过 | 所有迁移后的文档文件位置正确 |
| PromptLoader 旧文件名加载 | ✅ 通过 | 支持 `PROMPT.md` 等旧文件名自动解析到新路径 |
| PromptLoader 新路径加载 | ✅ 通过 | 直接使用新路径也能正常工作 |
| DocReviewAgent 路径常量 | ✅ 通过 | 路径常量指向正确且文件可读取 |

**测试文件**:
- `tests/test_path_fix.py`: 路径修复验证测试（通过）
- `tests/test_prompt_load.py`: 完整功能测试（需 langchain 依赖）

---

## 📞 联系方式

如有疑问，请联系项目维护者。

**最后更新**: 2026-05-21