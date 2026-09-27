DocReview-Agent-System
Copyright (c) 2026 Swcmb

本产品包含第三方组件 laya，其原始版权与许可声明见下。

--------------------------------------------------------------------------------
第三方组件：laya
--------------------------------------------------------------------------------

上游项目：https://github.com/NandhaKishorM/laya
上游提交：970dc8c5f63d7b886a68409493f37d569424f933
上游日期：2026-09-24
许可证  ：Apache License 2.0（完整文本见本目录 LICENSE 文件）

vendored 位置：仓库根目录 laya/
包含内容    ：laya 包全部 21 个 Python 源文件（289.8 KB），未作任何修改，
              仅排除 __pycache__ 与 *.pyc

vendoring 原因
--------------
1. 消除幽灵依赖：laya 原先既不在 pyproject.toml 声明，也不随本仓库分发，
   仅依赖相邻克隆仓库的 editable 安装。单独 clone 本仓库将无法运行决策层。
2. 保证可复现：决策层（src/decisions）经惰性 __import__ 使用 laya.Router 与
   laya.presets.guard_questions()，包名必须保持为 laya——若改名，上游内部的
   绝对自引用导入（router.py / structured.py 中的 `import laya`）会解析到别处，
   造成两份实现并存的 split-brain。

保留上游包名带来的约束
----------------------
laya 包内 router.py、structured.py、cli.py 存在绝对自引用导入 `import laya`。
因此 laya/ 必须位于可被解析为顶层包 `laya` 的位置（仓库根目录），不可重命名、
不可下沉为子包。

同步上游
--------
本目录为上游快照，不接受本地修改。需要升级时：

    git clone https://github.com/NandhaKishorM/laya <tmp>
    git -C <tmp> checkout <目标提交>
    # 用 <tmp>/laya 整体替换仓库根 laya/，并同步更新本文件的提交号与日期
    # 随后重跑闸门：pytest / ruff check . / mypy src/
