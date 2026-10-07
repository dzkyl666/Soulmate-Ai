# ══════════════════════════════════════════════════════════════
# Soulmate AI · 生产镜像
# ══════════════════════════════════════════════════════════════
#
# 构建：  docker build -t soulmate-ai .
# 运行：  docker run -d -p 8501:8501 --env-file .env -v soulmate-data:/data soulmate-ai
# 或直接用：docker compose up -d
#
# 设计取舍：
# - 分阶段构建（builder 装依赖 → runtime 只拷产物），镜像更小、不含编译工具链；
# - 用非 root 用户跑（uid 10001），降低容器逃逸后的影响面；
# - 健康检查打 Streamlit 的 /_stcore/health（官方就绪探针）；
# - 数据目录固定为 /data，挂卷即可持久化，容器删了数据还在。
# ──────────────────────────────────────────────────────────────

# ── 阶段 1：构建依赖 ──
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 先只拷依赖清单：改代码不会让依赖层缓存失效，重建更快
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# ── 阶段 2：运行时 ──
FROM python:3.12-slim AS runtime

# 只装运行期需要的最小系统包；curl 供 HEALTHCHECK 使用
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin soulmate

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SOULMATE_DATA_DIR=/data \
    SOULMATE_ENV=production \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ENABLE_CORS=false \
    STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=true

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# 只拷运行必需的代码（.dockerignore 已排除 data/、tests/、docs/ 等）
COPY --chown=soulmate:soulmate soulmate/ ./soulmate/
COPY --chown=soulmate:soulmate main.py pyproject.toml ./
COPY --chown=soulmate:soulmate .streamlit/ ./.streamlit/

# 数据卷（持久化所有用户数据）
RUN mkdir -p /data && chown -R soulmate:soulmate /data /app
VOLUME ["/data"]

USER soulmate

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8501/_stcore/health || exit 1

# 用 exec 形式保证信号能传到 Streamlit（Ctrl+C / docker stop 能优雅退出）
CMD ["streamlit", "run", "main.py"]