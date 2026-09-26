# Laya 决策层运行时环境（T-02）

本文件记录 Laya 决策层集成所依赖的**固定解释器、editable 安装、版本基线与源码身份**。
规格：`claude-context/specs/2026-09-25-laya-decision-layer-integration-spec.md` v1.9（T-02）。

## 1. 固定解释器

所有命令必须使用同一个解释器，不得新建虚拟环境（§7.1）：

```powershell
$python = "D:\ProgramFiles\anaconda3\envs\default\python.exe"
```

| 项 | 固定值 |
|---|---|
| 解释器 | `D:\ProgramFiles\anaconda3\envs\default\python.exe` |
| Python | `3.13.9` |

## 2. 安装 Laya（editable，`--no-deps`）

```powershell
uv pip install --python $python --no-deps -e D:\DocReviewer\laya-github
```

`--no-deps` 是硬要求：Laya 声明的依赖范围会把 `transformers` 拉离当前已验证的 5.x，
进而破坏既有测试环境。**torch 必须保持 `2.14.0+cpu` 不变。**

## 3. 版本基线

安装前后实测（`importlib.metadata`）：

| 包 | 安装前 | 安装后 | 判定 |
|---|---|---|---|
| `laya` | 未安装 | `0.3.20` | 与源码基线一致 ✅ |
| `torch` | `2.14.0+cpu` | `2.14.0+cpu` | 未变 ✅ |
| `transformers` | `5.6.2` | `5.6.2` | 未变 ✅ |
| `tokenizers` | `0.22.2` | `0.22.2` | 未变 ✅ |
| `safetensors` | `0.7.0` | `0.7.0` | 未变 ✅ |
| `huggingface-hub` | `1.20.1` | `1.20.1` | 未变 ✅ |

> **`transformers.__version__` 实际值 = `5.6.2`（规格要求记录）。**
> T-02b 真实权重冒烟若因 transformers 5.x 不兼容而失败，规格要求**先在 `default`
> 环境降级到经验证的 4.x 并记录前后版本**，**严禁**为迁就 laya 新建虚拟环境。
> 本行即为降级决策所需的「前」版本证据。

## 4. 四类断言（`scripts/probe_laya_runtime.py`）

```powershell
& $python scripts/probe_laya_runtime.py            # 人读
& $python scripts/probe_laya_runtime.py --json     # 机器可读
```

任一断言失败即非零退出，**不得**以「预期降级」为由放行：

| 组 | 断言 | 判据 |
|---|---|---|
| `interpreter` | `executable` / `python` | 解释器路径与 3.13.9 均为固定值 |
| `laya` | `version` / `importable` | `0.3.20` 且可导入 |
| `torch` | `unchanged` | `2.14.0+cpu` |
| `source` | `head` | HEAD == `970dc8c5f63d7b886a68409493f37d569424f933` |
| `source` | `tracked-clean` | `git status --porcelain -- laya` 为空 |
| `source` | `runtime-source-digest` | 按 §7 算法重算并记录（可复现） |

当前实测结果：**ALL PASS**（8/8）。

### 源码身份算法（§7，spec L1077）

- 只检查 **tracked package path**：`git status --porcelain -- laya`。
  无关的 untracked（如 `laya-github/.claude/`）**不影响**判定。
- `tracked_tree_digest` = 对 `git ls-tree -r --full-tree HEAD -- laya` 按原始顺序、
  UTF-8、每行 `tree_oid<TAB>path` 加末尾 LF 的 SHA-256（当前 21 个条目）。
- `runtime_source_digest` = canonical JSON `{"head":HEAD,"tree_digest":...}` 的 SHA-256。

## 5. checkpoint manifest（`scripts/build_laya_checkpoint_manifest.py`）

只读扫描选定 checkpoint root，生成/验证行为文件摘要，**绝不写入权重仓库**。

```powershell
# 生成（§19.2）
& $python scripts/build_laya_checkpoint_manifest.py `
    --model-dir D:\DocReviewer\laya-huggingface `
    --model multilingual `
    --output D:\controlled\laya_checkpoint_manifest.json

# 验证（重算并逐字段比对）
& $python scripts/build_laya_checkpoint_manifest.py `
    --model-dir D:\DocReviewer\laya-huggingface `
    --model multilingual `
    --output D:\controlled\laya_checkpoint_manifest.json --verify
```

- `--model-dir` 传 **bundle 根**；按 §7.3 解析实际 checkpoint 目录：
  `english`→bundle 根、`multilingual`→`bundle/multilingual`、
  `typed-decisions`→`bundle/typed-decisions`。
- `files` 按 `path` UTF-8 字典序排列；每条 `path` 相对该 root，禁止 sibling/上级路径。
- canonical JSON = schema 字段顺序 + `separators=(",", ":")` + `ensure_ascii=False`。
- `checkpoint_manifest_sha256` = 去掉该字段后的 canonical JSON 摘要。
- 文件摘要按 `(path, size, mtime_ns)` 缓存（`--digest-cache`），任一变化即重算。
- 输出路径若落在权重 bundle 内**直接拒绝**（实测退出码 2，仓库未被污染）。

当前 `multilingual` 实测：

```
checkpoint_manifest_sha256 = sha256:bcfe05afe818705a46dd27de5d8a334544813d2784109c9ccd08c4c3bc19a598
```

该值与规格 §9.1／§9.2／§9.3 示例中的 `checkpoint_manifest_sha256` **逐位一致**，
且 5 个行为文件摘要与 §7.3 示例**全部一致**（权重 643835514 B、
`rl_agent_config.json` 497 B、`tokenizer.json` 34363188 B 等）。

## 6. 已发现的规格不一致（`runtime_source_digest`）
**现象**：按 §7（spec L1077）算法重算得到的
`runtime_source_digest = sha256:df86aa6f4621577cfcfd320d3237b8462c0593f0e3a99c2351bac234ff095ede`，
与规格 §9.1／§9.2／§9.3 示例 JSON 中写的
`sha256:c80e800b84be2035cc75724af8d31273f61522558d25921b101cd2bcf98211f7` **不一致**。

**已排除实现错误**：

1. §7.3 的 5 个文件摘要、`checkpoint_manifest_sha256`（`bcfe05af…`）与规格示例
   **逐位一致** —— 说明 canonical JSON 序列化方式与摘要算法本身正确。
2. 我方实现连续两次运行结果相同（可复现，非不确定性）。
3. 对 L1077 文字的 8 种可能读法（`oid`/`mode`/`path`/原始行、TAB/空格、有无末尾 LF、
   是否排序）逐一试算，**无一种**产出 `c80e800b…`。
4. 唯一能复现 `45917e1f…` 的变体是「误用 git mode 字段」，系我方初版 bug，已修正，
   与规格意图无关。

**结论**：§7 L1077 的**规范性算法**清晰且可复现，我方按其实现；§9.x 的
`c80e800b…` 属示例常量，疑为按与 L1077 不同的 `tree_digest` 算法或过期 tree 状态
计算所得。**不影响 T-02 验收**（T-02 要求的是「可复现」，我方结果可复现），
但若 T-03+ 的校准 fixture 直接硬编码该常量，需按此说明校正期望值。

运行时以本文件 §4 实测值为准；`runtime_source_digest` 参与 calibration 绑定，
**必须**由实际 source tree 重算，不得采信任何硬编码示例值。

## 7. T-02b 硬闸门：实际在 `laya` conda 环境执行（与 §7.1 的偏离，已获用户批准）

### 7.1 实测结果

T-02b（真实权重 + `noul`/`choice`/`score` 三题型均返回有限 `answer_confidence`）
在 **`laya` conda 环境**通过：**9 passed**。三题型实测（真实权重，严格离线）：

| primitive | `answer_confidence` | 载荷 |
|---|---|---|
| `noul` | 0.6322 | `noul=0.3678` |
| `choice` | 0.9948 | `choice='refund'`（命中正确） |
| `score` | 0.7614 | `score=2.7142`（0–3 档内） |

### 7.2 为什么不在 `default` 环境跑

`default` 环境的 `torchvision 0.22.1` 与 `torch 2.14.0+cpu` **ABI 不匹配**，
导致 `import torchvision` 直接失败：

```
RuntimeError: operator torchvision::nms does not exist
```

`transformers.image_utils` 用 `if is_torchvision_available():` 守卫导入 torchvision；
由于 torchvision「已安装」（哪怕已损坏），守卫为真 → 触发失败 →
`transformers.loss` → 整个 Laya 导入链崩溃。这是 `default` 环境的**既有潜伏缺陷**
（现有 233 项测试无一 import torchvision，故此前未暴露），**非本次改动引入**。

规格 T-02b 授权的 remedy 是「在 `default` 环境降级 `transformers` 到 4.x」，
但本故障是 torch/torchvision **ABI 不匹配**，不是 transformers API 差异，
降级 transformers 无法修复。

### 7.3 两个环境的差异

| | `default`（规格原指定） | `laya`（T-02b 实际使用） |
|---|---|---|
| Python | 3.13.9 | 3.12.14 |
| torch | `2.14.0+cpu` | `2.14.0+cu130` |
| torchvision | `0.22.1`（**已损坏**） | `0.29.0+cu130` ✅ 配对 |
| transformers | 5.6.2 | 5.17.0 |
| laya | 0.3.20 | 0.3.20 |

`laya` 环境是**已存在的**环境，非为本次新建。

### 7.4 对 T-03+ 的架构影响（重要）

规格 §7.1 要求「统一解释器」、§328 要求「**进程内运行**」。既然 Laya 真实推理
**只能在 `laya` 环境发生**，则：

- **T-03+ 的决策层不能假设在 `default` 进程内 import Laya**——`default` 里
  `import laya` 会在加载权重时崩溃。
- 决策层需要显式处理「Laya 在独立环境」这一事实：要么以子进程调用 `laya` 环境，
  要么整个 DocReview 运行时改用 `laya` 环境。
- **在取得合规 calibration 之前，所有 Laya 判定恒为 `uncertain`**（§0.2／§2150），
  端到端行为与「未接入 Laya」一致——这是规格的正确行为，不是缺陷。

### 7.5 未校准温度告警（须留意）

冒烟时 Laya 输出：

```
RuntimeWarning: this checkpoint ships invalid temperatures ... choice:11+=0.1005... -> 0.5
Treat confidence from the affected entries as uncalibrated.
```

`choice` 题型的部分置信度来自**未校准**条目。T-02b 的判据只看「是否有限」，
故闸门通过；但按 §11.1，阈值只能来自 calibration manifest、且 `LayaConfig`
不得覆盖阈值，故 `choice` 的业务判定在合规 calibration 到位前必须保持 `uncertain`。

### 7.6 运行方式

```powershell
$lpython = "D:\ProgramFiles\anaconda3\envs\laya\python.exe"
$env:LAYA__MODEL = "multilingual"
$env:LAYA__DEVICE = "cpu"
$env:LAYA__MODEL_DIR = "D:\DocReviewer\laya-huggingface"
$env:LAYA__CHECKPOINT_MANIFEST_PATH = "D:\DocReviewer\config\laya-checkpoint-manifest.json"
$env:LAYA__CALIBRATION_PATH = "D:\DocReviewer\config\laya-calibration.json"
& $lpython -m pytest tests/test_decisions/test_laya_integration.py -m laya_integration -q --noconftest
```

`--noconftest` 用于跳过项目 `tests/conftest.py`（它 import `src.utils.llm`，
与本闸门无关，且 `laya` 环境无 DocReview 依赖）。闸门用例自包含所需 fixture。

`pyproject.toml` 的 `addopts` 已设 `-m "not laya_integration"`，使全量套件
**默认不收集**闸门（避免 `default` 环境误报 9 个 error）；显式
`-m laya_integration` 仍可触发，且**缺前置时大声失败**（不 skip），
符合 T-02b「不允许记录后继续」的要求。
