# Build from a clean Community source export (repository root as context).
ARG PYTHON_IMAGE=python:3.14-slim
ARG NODE_IMAGE=node:22-bookworm-slim
FROM ${NODE_IMAGE} AS web
WORKDIR /build/nexus_web
COPY nexus_web/package.json nexus_web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY nexus_web/ ./
RUN npm run build:community

FROM ${PYTHON_IMAGE} AS server
WORKDIR /build
COPY nexus_server/nexus_personal/build-requirements-linux-py314.txt ./build-requirements.txt
COPY nexus_server/nexus_personal/requirements-linux-py314.txt ./requirements.txt
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --require-hashes -r build-requirements.txt && \
    /opt/venv/bin/pip install --require-hashes --only-binary=:all: --no-binary=http-ece --no-build-isolation -r requirements.txt
COPY nexus_server/ ./server/
# Reject accidental builds from the mixed development tree before packaging.
RUN test -f server/LICENSE && test ! -d server/nexus_enterprise && \
    test ! -d server/apps/billing && test ! -d server/apps/iam && test ! -d server/apps/tokenbank && \
    test ! -d server/apps/marketplace && test ! -d server/apps/api_keys && \
    /opt/venv/bin/pip install --no-deps --no-build-isolation ./server && /opt/venv/bin/pip check && \
    /opt/venv/bin/python -c "import importlib.util; assert all(importlib.util.find_spec(name) is None for name in ('config', 'nexus_enterprise', 'apps.billing', 'apps.iam', 'apps.tokenbank', 'apps.marketplace', 'apps.api_keys'))"

FROM ${PYTHON_IMAGE} AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 libatomic1 && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 nexus && useradd --uid 10001 --gid nexus --create-home nexus
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=server /opt/venv /opt/venv
COPY --from=web /build/nexus_web/dist/community /opt/nexus/web
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    NEXUS_PERSONAL_CONFIG=/var/lib/nexus/installation/host.json
WORKDIR /opt/nexus
USER 10001:10001
ENTRYPOINT ["python", "-m", "nexus_personal.container"]
CMD ["web"]
