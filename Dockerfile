# ---- Stage 1: Build ----
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Opt-in flag (default OFF). Set to "1" at build time to install the ONNX
# runtime + tokenizers needed by the MedCPT cross-encoder reranker:
#     docker build --build-arg INSTALL_MEDCPT=1 ...
# Keeps the base image lean for teams that do not use RANKING_MEDCPT.
ARG INSTALL_MEDCPT=0

# Install dependencies first (layer cache)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
RUN if [ "$INSTALL_MEDCPT" = "1" ]; then \
        uv sync --frozen --no-dev --no-install-project --extra medcpt-onnx; \
    fi

# Copy source and install the project itself
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY scripts/ scripts/
RUN uv sync --frozen --no-dev
RUN if [ "$INSTALL_MEDCPT" = "1" ]; then \
        uv sync --frozen --no-dev --extra medcpt-onnx; \
    fi

# Pre-exported quantized weights are expected under ./models/medcpt-cross-onnx-qint8
# (produced offline via scripts/export_medcpt_onnx.py). The COPY below is a
# no-op when the directory is absent so builds without MedCPT still succeed.
# Keeping the weights outside the image (mounted via Coolify volume) is also
# supported — see docs for the ``RANKING_MEDCPT_MODEL_PATH`` override.
COPY models/ models/


# ---- Stage 2: Runtime ----
FROM python:3.12-slim AS runtime

RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --shell /bin/bash --create-home app

WORKDIR /app

# Copy the entire virtual env + source from builder
COPY --from=builder /app/.venv .venv
COPY --from=builder /app/src src
COPY --from=builder /app/alembic alembic
COPY --from=builder /app/alembic.ini .
COPY --from=builder /app/scripts scripts
COPY --from=builder /app/models models

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER app

EXPOSE 3000

# Run Alembic migrations then start Gunicorn with Uvicorn workers
CMD ["sh", "-c", "alembic upgrade head && gunicorn src.main:app --bind 0.0.0.0:3000 --workers 2 --worker-class uvicorn.workers.UvicornWorker --timeout 120 --graceful-timeout 30 --keep-alive 65"]
