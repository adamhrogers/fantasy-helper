# The image the scheduled Cloud Run jobs run (ADR 0025). Built by GitHub Actions, never on the
# laptop. Base images are pinned to digests; Dependabot proposes updates (ADR 0024).

# uv, as its own stage so Dependabot sees and updates it like any base image.
FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv

# --- Build: resolve the locked dependencies, then install the project into one venv ---------
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS build
COPY --from=uv /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src

# Dependencies first, so a code-only change reuses this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# --- Run: the venv on a clean base, no uv, no source tree, not root ------------------------
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

RUN useradd --system --uid 10001 --no-create-home fh
COPY --from=build /app/.venv /app/.venv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    FH_DATA_DIR=/tmp/fantasy-helper
WORKDIR /app
USER fh

ENTRYPOINT ["fh"]
CMD ["--help"]
