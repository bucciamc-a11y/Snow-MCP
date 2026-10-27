# Read-only ServiceNow MCP server. Contains no secrets: credentials are injected at runtime
# as Docker secrets (files) and settings as environment variables.
FROM python:3.13-slim-trixie AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/venv && /opt/venv/bin/pip install .

FROM python:3.13-slim-trixie
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd --system --gid 10001 mcp \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin mcp
COPY --from=build /opt/venv /opt/venv
USER 10001:10001
WORKDIR /tmp
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-I", "-c", "import urllib.request as u,sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8765/healthz', timeout=3).status == 200 else 1)"]
CMD ["python", "-I", "-m", "servicenow_mcp.server"]
