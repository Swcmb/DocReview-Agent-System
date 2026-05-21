"""终端工具测试模块 / Terminal Tool Test Module"""

import pytest

from src.tools.terminal import TerminalTool, CommandResult


@pytest.fixture
def terminal_tool() -> TerminalTool:
    """终端工具 fixture / Terminal Tool Fixture"""
    return TerminalTool(default_timeout=30)


@pytest.mark.asyncio
async def test_execute_simple_command(terminal_tool: TerminalTool) -> None:
    """测试执行简单命令 / Test Execute Simple Command"""
    result = terminal_tool.execute("echo 'test'")

    assert result.success is True
    assert "test" in result.data.get("stdout", "")


@pytest.mark.asyncio
async def test_execute_with_error(terminal_tool: TerminalTool) -> None:
    """测试命令执行错误 / Test Command Execution Error"""
    result = terminal_tool.execute("false")

    assert result.success is False


@pytest.mark.asyncio
async def test_execute_command_with_timeout(terminal_tool: TerminalTool) -> None:
    """测试命令超时 / Test Command Timeout"""
    result = terminal_tool.execute("sleep 10", timeout=1)

    assert result.success is False


@pytest.mark.asyncio
async def test_execute_dangerous_command_rm_rf(terminal_tool: TerminalTool) -> None:
    """测试危险命令拦截 - rm -rf / / Test Dangerous Command Block - rm -rf /"""
    result = terminal_tool.execute("rm -rf /")

    assert result.success is False


@pytest.mark.asyncio
async def test_execute_dangerous_command_fork_bomb(terminal_tool: TerminalTool) -> None:
    """测试危险命令拦截 - Fork 炸弹 / Test Dangerous Command Block - Fork Bomb"""
    result = terminal_tool.execute(":(){ :|:& };:")

    assert result.success is False


@pytest.mark.asyncio
async def test_execute_dangerous_command_mkfs(terminal_tool: TerminalTool) -> None:
    """测试危险命令拦截 - mkfs / Test Dangerous Command Block - mkfs"""
    result = terminal_tool.execute("mkfs /dev/sda")

    assert result.success is False


@pytest.mark.asyncio
async def test_execute_command_with_working_directory(
    terminal_tool: TerminalTool,
    temp_dir
) -> None:
    """测试指定工作目录 / Test Execute with Working Directory"""
    result = terminal_tool.execute(
        "pwd",
        cwd=str(temp_dir)
    )

    assert result.success is True
    assert str(temp_dir) in result.data.get("stdout", "")


@pytest.mark.asyncio
async def test_execute_command_stderr_capture(terminal_tool: TerminalTool) -> None:
    """测试 stderr 捕获 / Test Stderr Capture"""
    result = terminal_tool.execute("echo 'error' >&2")

    assert result.success is True
    assert "error" in result.data.get("stderr", "")


@pytest.mark.asyncio
async def test_execute_piped_command(terminal_tool: TerminalTool) -> None:
    """测试管道命令 / Test Piped Command"""
    result = terminal_tool.execute("echo 'hello world' | grep 'world'")

    assert result.success is True
    assert "world" in result.data.get("stdout", "")


@pytest.mark.asyncio
async def test_command_whitelist_allowed(terminal_tool: TerminalTool) -> None:
    """测试白名单允许的命令 / Test Whitelist Allowed Commands"""
    result = terminal_tool.execute("ls -la")
    assert result.success is True


@pytest.mark.asyncio
async def test_command_whitelist_blocked(terminal_tool: TerminalTool) -> None:
    """测试白名单拦截的命令 / Test Whitelist Blocked Commands"""
    result = terminal_tool.execute("curl http://example.com")
    assert result.success is False


@pytest.mark.asyncio
async def test_execute_retry_on_failure(terminal_tool: TerminalTool) -> None:
    """测试失败重试 / Test Retry on Failure"""
    result = terminal_tool.execute(
        "echo 'retry test'",
        retry=True
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_execute_no_retry(terminal_tool: TerminalTool) -> None:
    """测试禁用重试 / Test Disable Retry"""
    result = terminal_tool.execute(
        "exit 1",
        retry=False
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_command_duration_recorded(terminal_tool: TerminalTool) -> None:
    """测试命令执行时长记录 / Test Command Duration Recording"""
    result = terminal_tool.execute("sleep 0.1")

    assert result.success is True
    assert result.data.get("duration_ms", 0) > 0


@pytest.mark.asyncio
async def test_unsafe_redirect_blocked(terminal_tool: TerminalTool) -> None:
    """测试危险重定向拦截 / Test Dangerous Redirect Blocked"""
    result = terminal_tool.execute("echo 'test' > /etc/passwd")

    assert result.success is False


@pytest.mark.asyncio
async def test_command_with_environment_variable(terminal_tool: TerminalTool) -> None:
    """测试带环境变量的命令 / Test Command with Environment Variable"""
    result = terminal_tool.execute("echo $HOME")

    assert result.success is True


@pytest.mark.asyncio
async def test_multiple_commands_sequential(terminal_tool: TerminalTool) -> None:
    """测试顺序执行多条命令 / Test Sequential Multiple Commands"""
    result = terminal_tool.execute(
        "echo 'first' && echo 'second' && echo 'third'"
    )

    assert result.success is True
    assert "first" in result.data.get("stdout", "")
    assert "second" in result.data.get("stdout", "")
    assert "third" in result.data.get("stdout", "")


def test_command_result_dataclass() -> None:
    """测试 CommandResult 数据类 / Test CommandResult Dataclass"""
    result = CommandResult(
        command="test",
        stdout="output",
        stderr="error",
        exit_code=0,
        duration_ms=100.0,
        timed_out=False
    )

    assert result.command == "test"
    assert result.stdout == "output"
    assert result.stderr == "error"
    assert result.exit_code == 0
    assert result.duration_ms == 100.0
    assert result.timed_out is False


def test_terminal_tool_initialization() -> None:
    """测试 TerminalTool 初始化 / Test TerminalTool Initialization"""
    tool = TerminalTool(default_timeout=60, enable_whitelist=True, cwd="/tmp")

    assert tool.default_timeout == 60
    assert tool.enable_whitelist is True
    assert tool.cwd == "/tmp"
    assert tool.max_retries == 3


def test_check_command_safety() -> None:
    """测试命令安全检查 / Test Command Safety Check"""
    tool = TerminalTool()

    safe, _ = tool._check_command_safety("ls -la")
    assert safe is True

    safe, error = tool._check_command_safety("rm -rf /")
    assert safe is False
    assert "危险命令" in error


def test_terminal_tool_dangerous_patterns() -> None:
    """测试危险命令模式 / Test Dangerous Command Patterns"""
    tool = TerminalTool()

    dangerous_commands = [
        "rm -rf /",
        "rm -rf /*",
        ":(){ :|:& };:",
        "dd if=/dev/zero of=/dev/sda",
        "mkfs.ext4 /dev/sda",
    ]

    for cmd in dangerous_commands:
        safe, _ = tool._check_command_safety(cmd)
        assert safe is False, f"命令应该被拦截: {cmd}"


def test_terminal_tool_whitelist() -> None:
    """测试白名单 / Test Whitelist"""
    tool = TerminalTool(enable_whitelist=True)

    allowed = ["ls", "cat", "pwd", "echo", "grep", "find"]
    for cmd in allowed:
        safe, _ = tool._check_command_safety(cmd)
        assert safe is True, f"命令应该被允许: {cmd}"
