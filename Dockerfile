# Builds from a clean checkout: no pre-staged wheel bundle, nothing published to PyPI.
# Every dependency, including platform-backend-core at its reviewed Git commit, comes from
# uv.lock (`uv sync --frozen`), the same lock CI installs; tests/test_build_hygiene.py keeps
# the lock, [tool.uv.sources] and this file from drifting apart.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 PATH=/opt/venv/bin:$PATH
COPY --from=build /opt/venv /opt/venv
USER 65532:65532
EXPOSE 8080
CMD ["platform-workflow", "api"]
