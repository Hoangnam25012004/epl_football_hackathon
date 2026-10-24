# Pundit Copilot: one image, the API serving the static UI on one origin (docs/day4-design.md
# section 17, Day 5 deploy). Synthetic data only: fictional teams and players.
#
#   docker build -t pundit-copilot .
#   docker run -p 8000:8000 pundit-copilot            # mock LLM and mock voice by default
#
# The submission build sets the landing page's links:
#   docker build --build-arg NEXT_PUBLIC_REPO_URL=https://... --build-arg NEXT_PUBLIC_VIDEO_URL=https://...
# Azure settings (Foundry, Speech, Blob, App Insights) come from the environment at run time;
# no key is ever built into the image.

FROM node:22-bookworm-slim AS ui
WORKDIR /app/frontend
ENV NEXT_TELEMETRY_DISABLED=1
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ARG NEXT_PUBLIC_REPO_URL=""
ARG NEXT_PUBLIC_VIDEO_URL=""
ENV NEXT_PUBLIC_REPO_URL=${NEXT_PUBLIC_REPO_URL} NEXT_PUBLIC_VIDEO_URL=${NEXT_PUBLIC_VIDEO_URL}
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.12-slim-bookworm AS api
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app/backend
# dependencies first: a code change does not reinstall them
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --locked --no-dev --no-editable
COPY --from=ui /app/frontend/out /app/ui
RUN useradd --create-home --uid 10001 pundit && mkdir -p .data && chown pundit .data
USER pundit
ENV PATH="/app/backend/.venv/bin:${PATH}"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["pundit", "serve", "--host", "0.0.0.0", "--port", "8000", "--ui", "/app/ui"]
