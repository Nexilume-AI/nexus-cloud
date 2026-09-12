# Operator-owned profile, built from the repository root; never supplied by users.
FROM python:3.12-slim
COPY nexus_openwrt/sdk/nexus-agent-sdk-python/pyproject.toml nexus_openwrt/sdk/nexus-agent-sdk-python/README.md nexus_openwrt/sdk/nexus-agent-sdk-python/LICENSE /tmp/nexus-sdk/
COPY nexus_openwrt/sdk/nexus-agent-sdk-python/src /tmp/nexus-sdk/src
RUN python -m pip install --no-cache-dir '/tmp/nexus-sdk[fastmcp]' && rm -rf /tmp/nexus-sdk
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
