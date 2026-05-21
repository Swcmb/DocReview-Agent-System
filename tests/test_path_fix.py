"""简化测试：只验证文件路径加载逻辑，不依赖 langchain
"""

import sys
from pathlib import Path

# 将项目根目录添加到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def test_docreview_paths():
    """测试 docreview.py 中的路径常量是否正确"""
    print("=" * 60)
    print("验证 docreview.py 路径常量")
    print("=" * 60)
    
    # 直接读取 docreview.py 中的常量值，不导入整个模块（避免依赖问题）
    docreview_py = project_root / "src" / "agents" / "docreview.py"
    content = docreview_py.read_text(encoding="utf-8")
    
    # 提取 PROMPT_PATH 和 WHENTOCALL_PATH 的值
    prompt_path_line = [l for l in content.split("\n") if "PROMPT_PATH = " in l][0]
    whentocall_path_line = [l for l in content.split("\n") if "WHENTOCALL_PATH = " in l][0]
    
    prompt_path = prompt_path_line.split("=", 1)[1].strip().strip('"').strip("'")
    whentocall_path = whentocall_path_line.split("=", 1)[1].strip().strip('"').strip("'")
    
    print(f"\nPROMPT_PATH = {prompt_path}")
    print(f"WHENTOCALL_PATH = {whentocall_path}")
    
    # 验证文件是否存在
    prompt_exists = (project_root / prompt_path).exists()
    whentocall_exists = (project_root / whentocall_path).exists()
    
    status1 = "✅" if prompt_exists else "❌"
    status2 = "✅" if whentocall_exists else "❌"
    
    print(f"\n{status1} PROMPT_PATH 文件存在")
    print(f"{status2} WHENTOCALL_PATH 文件存在")
    
    # 读取验证内容
    if prompt_exists:
        content1 = (project_root / prompt_path).read_text(encoding="utf-8")
        print(f"   review_prompt 长度: {len(content1)}")
        print(f"   内容开头: {repr(content1[:100])}")
    
    if whentocall_exists:
        content2 = (project_root / whentocall_path).read_text(encoding="utf-8")
        print(f"   whentocall_prompt 长度: {len(content2)}")
        print(f"   内容开头: {repr(content2[:100])}")
    
    return prompt_exists and whentocall_exists


def test_prompt_loader_compatibility():
    """测试 PromptLoader 的兼容性映射"""
    print("\n" + "=" * 60)
    print("验证 PromptLoader 新旧文件名映射")
    print("=" * 60)
    
    from src.utils.prompt_loader import PromptLoader
    
    loader = PromptLoader()
    
    # 测试旧文件名加载
    old_content = loader.load("PROMPT.md")
    print(f"\n✅ 旧文件名 'PROMPT.md' 加载成功，长度 {len(old_content)}")
    
    # 测试新路径加载
    new_content = loader.load("docreview-agent-system/agent-review-prompt.md")
    print(f"✅ 新路径 'docreview-agent-system/agent-review-prompt.md' 加载成功，长度 {len(new_content)}")
    
    # 验证内容一致
    assert old_content == new_content, "新旧路径内容应该一致！"
    print("✅ 新旧路径内容一致")
    
    return True


def test_file_structure():
    """验证 .trae 目录结构"""
    print("\n" + "=" * 60)
    print("验证 .trae 目录结构")
    print("=" * 60)
    
    print("\n当前 .trae 目录：")
    for p in sorted((project_root / ".trae").rglob("*.md")):
        print(f"   .trae/{p.relative_to(project_root / '.trae')}")
    
    return True


if __name__ == "__main__":
    results = []
    results.append(("docreview.py 路径常量", test_docreview_paths()))
    results.append(("PromptLoader 新旧映射", test_prompt_loader_compatibility()))
    results.append(("目录结构验证", test_file_structure()))
    
    print("\n" + "=" * 60)
    print("最终结果汇总")
    print("=" * 60)
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{name}: {status}")
    print("=" * 60)
    
    if all(passed for _, passed in results):
        print("✅ 所有验证通过！路径修复成功！")
        sys.exit(0)
    else:
        print("❌ 部分验证失败")
        sys.exit(1)
