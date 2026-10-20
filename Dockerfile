# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.6.3 AS uv
FROM python:3.11.11-slim-bookworm

COPY --from=uv /uv /uvx /usr/local/bin/
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DATA_DIR=/app/data

# ffmpegがlibavcodec等を依存として導入する。開発用ヘッダー・コンパイラはwheel利用のため不要。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 libsodium23 libffi8 ca-certificates \
    && dpkg-query -W -f='${binary:Package}\t${Version}\n' > /usr/local/share/tts-os-packages.txt \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project --no-build
COPY main.py ./
COPY ttsbot ./ttsbot
COPY scripts ./scripts

# 新しいホストでのビルド時にも、Discordへ接続せずOS依存を検証する。
RUN DATA_DIR=/tmp/tts-build-check uv run --frozen --no-sync python main.py --check \
    && rm -rf /tmp/tts-build-check

RUN groupadd --gid 10001 tts \
    && useradd --uid 10001 --gid tts --no-create-home --home-dir /app tts \
    && mkdir -p /app/data /app/secrets \
    && chown -R tts:tts /app/data
USER tts

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-m", "ttsbot.healthcheck"]
CMD ["uv", "run", "--frozen", "--no-sync", "python", "main.py"]
