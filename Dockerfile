# The worst-route image (docs/reference/hosting.md): the API and the built frontend from one
# process, `worst-route serve --frontend`, the API under /api. The data is never inside it:
# /data (read only: the builds, the places index, the basemap) and /state (written: the route
# store, the geocoder's cache, the share images, numba's compiled kernels) are volumes.
#
#   docker build -t worst-route .
#   docker run -p 127.0.0.1:8000:8000 -v "$PWD/data:/data:ro" -v worst-route-state:/state \
#     worst-route serve --region portland --host 0.0.0.0 --port 8000 --store /state/routes.sqlite \
#     --frontend /app/frontend --public --log-format json --trust-proxy
#
# Bound to 127.0.0.1, the container is reached only through a reverse proxy on this machine, whose
# X-Forwarded-For it can then believe (--trust-proxy). The image's own command does not trust it,
# so a container exposed directly never takes a client's word for its address.

# The frontend's build, dist/.
FROM node:22-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# The Python environment, from the lockfile, without the dev tools.
FROM python:3.12-slim-bookworm AS python
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 --create-home --home-dir /home/worst-route worst-route \
    && mkdir -p /data /state \
    && chown worst-route /state
COPY --from=python /app/.venv /app/.venv
COPY --from=frontend /src/frontend/dist /app/frontend
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    WORST_ROUTE_DATA=/data \
    WORST_ROUTE_BASEMAP=pmtiles \
    NUMBA_CACHE_DIR=/state/numba \
    MPLCONFIGDIR=/home/worst-route/.cache/matplotlib
USER worst-route
# matplotlib builds its font cache on its first import (seconds): once, here.
RUN python -c "import matplotlib.font_manager"
VOLUME ["/state"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=4)"]
ENTRYPOINT ["worst-route"]
CMD ["serve", "--region", "portland", "--host", "0.0.0.0", "--port", "8000", \
     "--store", "/state/routes.sqlite", "--frontend", "/app/frontend", \
     "--public", "--log-format", "json"]
