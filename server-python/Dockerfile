FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 PATH="/app/.venv/bin:$PATH"
COPY --from=astral/uv:0.13 /uv /usr/local/bin/uv

FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM deps AS dev
COPY . .
EXPOSE 4000
CMD ["fastapi", "dev", "app/main.py", "--host", "0.0.0.0", "--port", "4000"]

FROM deps AS prod
COPY app ./app
EXPOSE 4000
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "4000"]
