# ============================================================
# DocReview Agent System — Docker 镜像
# 基于 python:3.11-slim，包含 Node.js（MCP 服务依赖）
# ============================================================

FROM python:3.13-slim

# 避免交互式安装提示
ENV DEBIAN_FRONTEND=noninteractive

# ---- 系统依赖 + Node.js ----
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        gnupg \
        build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ---- 工作目录 ----
WORKDIR /app

# ---- 复制全部项目文件 ----
COPY pyproject.toml README.md main.py mcp_server_start.py mcp_stdio_start.py ./
COPY src/ src/
COPY prompts/ prompts/
COPY specs/ specs/
COPY docs/ docs/
COPY examples/ examples/
COPY tests/ tests/

# ---- 安装 Python 依赖（含开发/测试依赖） ----
RUN pip install --no-cache-dir ".[dev]"

# ---- 创建运行时目录 ----
RUN mkdir -p logs data reviews workspace

# ---- 创建非 root 用户 ----
RUN groupadd -r docreview && useradd -r -g docreview -d /app docreview \
    && chown -R docreview:docreview /app

USER docreview

# ---- 环境变量默认值 ----
ENV PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO \
    WORKSPACE_DIR=/app/workspace

# ---- 暴露 HTTP MCP Server 端口 ----
EXPOSE 8000

# ---- 健康检查 ----
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ---- 默认启动 HTTP MCP Server ----
CMD ["python", "mcp_server_start.py", "--host", "0.0.0.0", "--port", "8000"]
