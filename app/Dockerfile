# Build context = repo root (see docker-compose.yml). Multi-stage; non-root runtime.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /repo
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Copy the workspace and install only the app package (no dev, non-editable).
COPY pyproject.toml uv.lock ./
COPY engine/ engine/
COPY app/ app/
COPY verifier/ verifier/
RUN uv sync --package app --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
WORKDIR /repo
RUN useradd --create-home --uid 10001 lacta
COPY --from=build /repo /repo
ENV PATH="/repo/.venv/bin:$PATH"
USER lacta
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
