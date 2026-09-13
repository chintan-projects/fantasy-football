# Versions here are read off the registry, never from memory. An earlier pass in this repo
# pinned a Next.js version with a published CVE because it came from training data.
#   python  3.13.12-slim-bookworm  -- confirmed on Docker Hub 2026-09-13
#   uv      0.12.13                -- confirmed on PyPI 2026-09-13
FROM python:3.13.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

RUN pip install --no-cache-dir uv==0.12.13

WORKDIR /app

# Dependencies first, from the lockfile, so a source edit does not re-resolve the world
# and so the image gets the versions the tests ran against.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY backend/src ./src
COPY backend/README.md ./README.md
RUN uv sync --frozen --no-dev

# The volume mount point. Fly mounts over it at runtime; creating it here keeps the image
# runnable without one, which is what lets a smoke test happen before a deploy.
RUN mkdir -p /data
ENV FF_DATABASE_PATH=/data/ff.db

# Not root. Nothing in here needs it.
RUN useradd --create-home --uid 10001 ff && chown -R ff:ff /app /data
USER ff

EXPOSE 8080
CMD ["uvicorn", "ff.api.mcp:http_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
