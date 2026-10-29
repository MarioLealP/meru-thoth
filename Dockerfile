FROM docker.io/library/python:3.15.2-slim@sha256:c4158543ecb725b99e57d129dfd43d761d4c43e4577b5e23326aabb223cf7f4e

COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /bin/uv

WORKDIR /app
# Dependencies first, so a code change doesn't rebuild them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev --no-editable

# The root filesystem is read-only in Meru, so no .pyc files; the numeric user matches nuwa-app's runAsUser.
# Migrations run from the same image: .venv/bin/alembic upgrade head (it reads alembic.ini + pyproject.toml here).
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080
CMD [".venv/bin/uvicorn", "thoth.api:app", "--host", "0.0.0.0", "--port", "8080"]
