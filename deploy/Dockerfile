# FieldForce — container image for Cloud Run / Vertex AI Agent Engine.
#
# Bundles the FastAPI app (which also serves the frontend) together with:
#   • uv            — to install the frozen Python dependency set
#   • Node.js/npx   — required at runtime for Elastic's MCP server
#                     (npx -y @elastic/mcp-server-elasticsearch)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # uv: install into the system environment, copy (not symlink) for a clean image
    UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy

# ── System deps: Node.js (for the Elastic MCP server's npx) + curl/ca-certs ──
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── uv (Astral's Python package manager) ──
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies first (better layer caching) using the committed lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy the rest of the project and install it.
COPY . .
RUN uv sync --frozen --no-dev

# Cloud Run sends traffic to $PORT (defaults to 8080); FastAPI serves API + frontend.
ENV PORT=8080
EXPOSE 8080

# Honor Cloud Run's injected $PORT, falling back to 8080 for local `docker run`.
CMD ["sh", "-c", "uv run uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
