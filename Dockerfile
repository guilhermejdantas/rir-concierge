# syntax=docker/dockerfile:1

# --------------------------------------------------------------------------- #
# Stage 1 – builder: install dependencies into an isolated prefix.            #
# --------------------------------------------------------------------------- #
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Install into an isolated prefix. builder and runtime share the same
# python:3.11-slim base, so /install/{bin,lib/python3.11/site-packages} line up
# exactly with /usr/local/... in the runtime stage.
RUN python -m pip install --upgrade pip \
    && pip install --prefix=/install -r requirements.txt

# --------------------------------------------------------------------------- #
# Stage 2 – runtime: slim image with only the installed packages + app code.  #
# --------------------------------------------------------------------------- #
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Non-root runtime user.
RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

# Merge the installed prefix into the image's real site-packages / bin. Because
# both stages use python:3.11-slim, paths align 1:1 and no PATH/PYTHONPATH
# tweaks are needed — `python -m uvicorn` and the `uvicorn` script both resolve.
COPY --from=builder /install /usr/local

COPY --chown=app:app . .

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
