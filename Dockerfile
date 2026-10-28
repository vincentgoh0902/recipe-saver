# recipe-saver: FastAPI app + import worker, with whisper.cpp for voiceovers.
# Builds for linux/arm64 (Oracle Ampere). The LLM is not in this image: point
# LLM_BASE_URL at an OpenAI-compatible server (Ollama or llama-server).

# 1. whisper.cpp CLI and the English base model
FROM debian:bookworm-slim AS whisper
ARG WHISPER_VERSION=v1.9.5
ARG WHISPER_MODEL_SHA256=a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${WHISPER_VERSION} https://github.com/ggml-org/whisper.cpp /src
WORKDIR /src
# Not GGML_NATIVE: the image is built on an Apple chip but runs on Neoverse N1
# (armv8.2-a with dotprod and fp16). Static, no OpenMP, so the binary needs no extra libs.
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
        -DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16 -DGGML_OPENMP=OFF \
        -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF \
    && cmake --build build -j --target whisper-cli
RUN mkdir /models \
    && curl -fsSL -o /models/ggml-base.en.bin \
        https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin \
    && echo "${WHISPER_MODEL_SHA256}  /models/ggml-base.en.bin" | sha256sum -c -

# 2. Python dependencies from the lockfile (no dev/test deps)
FROM python:3.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY recipe_saver ./recipe_saver
RUN uv sync --locked --no-dev --no-editable

# 3. Runtime
FROM python:3.13-slim-bookworm
COPY --from=whisper /src/build/bin/whisper-cli /usr/local/bin/whisper-cli
COPY --from=whisper /models /models
COPY --from=build /app/.venv /app/.venv
# UID 1000 matches the server's ubuntu user, who owns projects/<name>/data.
RUN useradd --uid 1000 --create-home app && mkdir -p /data && chown app /data
USER app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DATA_DIR=/data \
    WHISPER_BIN=whisper-cli \
    WHISPER_MODEL=/models/ggml-base.en.bin
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=3)"
CMD ["python", "-m", "recipe_saver"]
