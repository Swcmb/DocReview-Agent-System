"""测试用例：验证提示词文件加载功能（文件移动后修复验证）
"""

import sys
from pathlib import Path

# 将项目根目录添加到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import logging
# 设置基本日志，不依赖项目复杂的logger配置
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


def test_docreview_agent_prompt_load():
    """测试 DocReviewAgent 能否正确加载提示词文件"""
    print("=" * 60)
    print("测试1: DocReviewAgent 提示词加载测试")
    print("=" * 60)
    
    try:
        from src.agents.docreview import DocReviewAgent
        from langchain_openai import ChatOpenAI

        # 创建一个 mock 的 LLM（不需要真正调用）
        llm = ChatOpenAI(
            base_url="http://mock.openai.com",
            api_key="mock_key",
            model="gpt-4o"
        )

        # 初始化 DocReviewAgent
        print(f"\n初始化 DocReviewAgent...")
        agent = DocReviewAgent(llm=llm)
        
        # 检查提示词是否加载成功
        print(f"\n检查 review_prompt 长度: {len(agent.review_prompt)} 字符")
        print(f"检查 whentocall_prompt 长度: {len(agent.whentocall_prompt)} 字符")

        if len(agent.review_prompt) > 0:
            print("✅ review_prompt 加载成功！")
            print(f"   前100字符: {repr(agent.review_prompt[:100])}")
        else:
            print("❌ review_prompt 为空！")
        
        if len(agent.whentocall_prompt) > 0:
            print("✅ whentocall_prompt 加载成功！")
            print(f"   前100字符: {repr(agent.whentocall_prompt[:100])}")
        else:
            print("❌ whentocall_prompt 为空！")
        
        print("\n✅ DocReviewAgent 提示词加载测试通过！")
        return True
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_prompt_loader():
    """测试 PromptLoader 工具类"""
    print("\n" + "=" * 60)
    print("测试2: PromptLoader 工具类测试")
    print("=" * 60)
    
    try:
        from src.utils.prompt_loader import (
            PromptLoader,
            PromptFileNotFoundError
        )

        loader = PromptLoader()

        # 测试新路径加载（新规范）
        print("\n--- 测试新路径（新规范）")
        new_prompt = loader.load("docreview-agent-system/agent-review-prompt.md")
        print(f"✅ 新路径加载成功，长度: {len(new_prompt)}")

        # 测试旧文件名自动映射（向后兼容）
        print("\n--- 测试旧文件名自动映射（向后兼容）")
        old_prompt = loader.load("PROMPT.md")
        print(f"✅ 旧文件名 'PROMPT.md' 映射成功，长度: {len(old_prompt)}")
        assert old_prompt == new_prompt
        print("   新旧路径内容一致")

        old_whentocall = loader.load("WHENTOCALL.md")
        print(f"✅ 旧文件名 'WHENTOCALL.md' 映射成功")

        # 测试 exists 方法
        print("\n--- 测试 exists 方法")
        print(f"PROMPT.md 存在: {loader.exists('PROMPT.md')}")
        print(f"agent-review-prompt.md (新路径) 存在: {loader.exists('docreview-agent-system/agent-review-prompt.md')}")
        print(f"不存在的文件: {loader.exists('non_existent_file_xyz.md')}")

        print("\n✅ PromptLoader 工具类测试通过！")
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_files_exist():
    """验证所有移动后的文件是否在正确位置"""
    print("\n" + "=" * 60)
    print("测试3: 验证文件位置")
    print("=" * 60)
    
    files_to_check = [
        ".trae/prompts/docreview-agent-system/agent-review-prompt.md",
        ".trae/prompts/docreview-agent-system/agent-invocation-rules.md",
        ".trae/specs/docreview-agent-system/spac-architecture-reference.md",
        ".trae/specs/docreview-agent-system/system-specification.md",
        ".trae/docs/modules/mcp-client-usage-guide.md",
        ".trae/reports/mcp-completion-summary.md",
        ".trae/README.md"
    ]
    
    all_exist = True
    for file_path in files_to_check:
        full_path = project_root / file_path
        exists = full_path.exists()
        status = "✅" if exists else "❌"
        print(f"{status} {file_path}")
        if not exists:
            all_exist = False
    
    return all_exist


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("开始执行提示词加载功能验证测试")
    print("项目根目录:", project_root)
    print("工作目录:", Path.cwd())
    print("=" * 60)
    
    # 运行测试
    results = []
    results.append(("测试1: DocReviewAgent 提示词加载", test_docreview_agent_prompt_load()))
    results.append(("测试2: PromptLoader 工具类", test_prompt_loader()))
    results.append(("测试3: 验证文件位置", test_files_exist()))
    
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{name}: {status}")
    print("=" * 60)
    all_passed = all(passed for _, passed in results)
    
    if all_passed:
        print("✅ 所有测试通过！路径修复成功！")
        sys.exit(0)
    else:
        print("❌ 部分测试失败，请检查")
        sys.exit(1)
