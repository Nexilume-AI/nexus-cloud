# Computer Runtime compatibility update

This update adopts the runtime fixes from [PR #5](https://github.com/Nexilume-AI/nexus-cloud-community/pull/5) while preserving the existing installation and pairing flow.

## Scope

- SDK 0.46.3 declares the SOCKS dependency required by WebSocket proxy support, sends a dedicated HTTP/WebSocket User-Agent, and converts browser Enter to POSIX pipe-shell newlines.
- Community exposes the owner-scoped `POST /api/v1/workspace-connections/{id}/test/` endpoint. It calls the configured runtime transport and persists success/failure, error, timestamp and runtime facts.
- Enterprise retains its existing connection test, audit and health-history behavior.
- Inbox SSE accepts the browser's `Accept: text/event-stream` header in both editions.
- Pairing tickets, device authentication, outbound connections and frontend installation steps remain unchanged. There is no generated installer or installed-package monkeypatch.

## Upgrade

Install the [SDK 0.46.3 release wheel](https://github.com/Nexilume-AI/nexus-agent-sdk-python/releases/tag/v0.46.3) in the same Python environment used by Runtime:

```sh
python -m pip install --upgrade "./nexus_openwrt_agent_sdk-0.46.3-py3-none-any.whl[computer,browser]"
nexus-computer restart
nexus-computer status
```

Keep the existing configuration directory and device keys. Existing pairings do not need to be recreated. New computers still install Runtime before pairing.

The terminal remains a pipe-based shell, not a PTY. SOCKS support requires a reachable configured proxy; a custom User-Agent does not guarantee that a Cloud firewall will permit the connection.

## Validation

- Windows SDK suite: 353 passed, 21 skipped.
- Linux SDK suite (Ubuntu 24.04 / Python 3.12): 345 passed, 29 skipped, including real shell execution using browser Enter.
- Community signed enrollment, connection checks, ownership isolation and authenticated SSE: 11 passed.
- Wheel and sdist pass metadata and source-content verification.
- macOS is included in the SDK CI matrix. Local Linux/Windows verification does not establish real macOS proxy or launchd acceptance.
