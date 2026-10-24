FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
# platform-backend-core is not on PyPI (the name is unclaimed there), so install it from the
# reviewed commit first; `pip install .` then finds the pin satisfied and never asks PyPI for it.
# Keep this SHA in step with [tool.uv.sources] in pyproject.toml (and so uv.lock).
RUN pip install --no-cache-dir \
      'platform-backend-core @ https://github.com/bookchaowalit-backend/platform-backend-core/archive/d00e0119c2e47da3942d9c7b4b88828953f8bc5a.tar.gz' \
    && pip install --no-cache-dir .
USER 65532:65532
EXPOSE 8080
CMD ["platform-workflow", "api"]
