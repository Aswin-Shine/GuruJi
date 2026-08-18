# =============================================================================
#  GuruJi API — multistage build
#
#  Replaces the single-stage Dockerfile in the backend repo, which had three
#  problems that matter in production and none that matter locally:
#    1. it ran as root,
#    2. `COPY . .` copied the whole working tree including .env if present,
#    3. build toolchain and pip caches stayed in the shipped layer.
#
#  BASE IMAGE POLICY (read before bumping)
#  ---------------------------------------
#  Verified against upstream release data on 2026-08-04:
#    * Python 3.13 is in bugfix support; 3.12 is now the legacy security-only
#      line. 3.14 is the newest feature release, but `psycopg2-binary==2.9.*`
#      is pinned in requirements.txt and its wheel coverage for 3.14 is not
#      confirmed — moving there without checking trades a CVE-posture win for a
#      build that fails on a missing wheel and silently compiles from source.
#      Upgrade path: switch to psycopg3 (`psycopg[binary]`), which changes the
#      DATABASE_URL driver string, then move to 3.14.
#    * -slim over -alpine deliberately. Alpine's musl means no manylinux wheels,
#      so psycopg2-binary and friends compile from source: a slower build, a
#      larger toolchain surface, and a class of runtime bugs that only appear
#      under musl. Slim is Debian, gets Debian's security updates, and the
#      distroless option below removes more surface than Alpine would.
#
#  As with the frontend image: nobody can assert "zero CVEs" in a Dockerfile.
#  Gate it in CI instead —
#      trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 guruji-api:latest
#  and pin to digests once a scan passes:  FROM python:3.13-slim@sha256:<digest>
# =============================================================================

ARG PYTHON_VERSION=3.13

# ---------- stage 1: build ---------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential and libpq-dev exist ONLY in this stage. They are what makes a
# source build possible if a wheel is missing, and they are exactly what should
# never reach a running container.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# A venv is the cleanest thing to hand to the next stage: one directory, no
# interaction with the system site-packages, trivially COPY --from'd.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel \
 && pip install -r requirements.txt

# ---------- stage 2: runtime -------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

LABEL org.opencontainers.image.title="guruji-api" \
      org.opencontainers.image.description="GuruJi Phase 1 backend (FastAPI)" \
      org.opencontainers.image.licenses="UNLICENSED"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# libpq5 is the runtime shared library psycopg2 links against — the only piece
# of the build toolchain that has to survive. No compiler ships.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin guruji

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
# Ownership is root, not guruji: the application user must be able to read its
# own code and not able to rewrite it. A writable app directory turns any file
# write primitive into persistent code execution.
COPY --chown=root:root app ./app

USER guruji
EXPOSE 8000

# Uses the venv's own Python — no curl, no extra package — and exercises the real
# /health route, which executes SELECT 1 and returns 503 when Postgres is
# unreachable. An unhealthy database therefore marks the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)" || exit 1

# One worker on purpose. The backend keeps rate-limit buckets, PIN attempt
# counters and the once-per-day spend alert in module-level process memory
# (conversation.service._rate_buckets, student_profile.service._pin_attempts,
# llm._alerted_on). Adding workers silently multiplies every one of those
# limits by the worker count and re-opens the PIN brute-force hole.
# Do not add --workers without moving that state to Postgres or Redis first.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--timeout-keep-alive", "65", \
     "--no-server-header"]
