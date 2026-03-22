# claimsight API image.
#
# Multi-stage so the runtime image carries no build toolchain, no dev
# dependencies and no test suite. The full 34 MB CMS file is deliberately NOT
# baked in: the image serves the committed stratified sample by default, and a
# deployment that wants the full file mounts it or runs `make data` in CI.

FROM python:3.11-slim AS builder

WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md ./
COPY src ./src
# Build a wheel so the runtime stage installs a plain artifact, not a source tree.
RUN pip install --upgrade pip build \
 && python -m build --wheel --outdir /build/dist


FROM python:3.11-slim AS runtime

WORKDIR /app
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CLAIMSIGHT_DATA_DIR=/app/data

# Non-root: the service only reads a CSV and serves HTTP.
RUN groupadd --system app && useradd --system --gid app --home /app app

COPY --from=builder /build/dist/*.whl /tmp/wheels/
RUN pip install /tmp/wheels/*.whl && rm -rf /tmp/wheels

# Committed sample only (4.2 MB). data/raw stays out of the image.
COPY --chown=app:app data/sample /app/data/sample

USER app
EXPOSE 8000

# Fail fast rather than serving traffic before the pipeline is scored.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

# 0.0.0.0 is required for the port to be reachable from outside the container.
CMD ["uvicorn", "claimsight.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
