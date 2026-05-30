# syntax=docker/dockerfile:1.7
# Multi-stage backend image. `dev` = hot-reload (used by docker-compose);
# `prod` = hardened, non-root, lean venv (dev deps excluded).

# ---- base: python + uv, venv OUTSIDE the app dir so bind-mounts never shadow it ----
FROM python:3.12-slim AS base
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
COPY --from=ghcr.io/astral-sh/uv:0.9.29 /uv /uvx /bin/
WORKDIR /app

# ---- deps-dev: full dependency set incl. dev tools (cached on lockfile) ----
FROM base AS deps-dev
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# ---- deps-prod: runtime dependencies only ----
FROM base AS deps-prod
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ---- dev: source bind-mounted by compose; this COPY is the non-compose fallback ----
FROM deps-dev AS dev
ENV APP_ENV=local
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---- prod: lean, non-root, multi-worker ----
FROM base AS prod
ENV APP_ENV=production
COPY --from=deps-prod /opt/venv /opt/venv
COPY . .
RUN groupadd --system app && useradd --system --gid app --no-create-home app \
    && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
