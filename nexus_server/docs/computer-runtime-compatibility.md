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
- [All seven SDK CI jobs passed](https://github.com/Nexilume-AI/nexus-agent-sdk-python/actions/runs/34965351268): Python 3.12/3.14 on macOS, Linux and Windows, plus Python 3.9 core installation. This does not establish real macOS system-proxy or launchd acceptance.

### Enterprise verification completed (2026-09-15)

- Enterprise: all 28 Runtime/Inbox/SSE tests passed against an isolated PostgreSQL database after full migrations. Runtime uploads used a writable temporary directory.
- Community: all 11 signed enrollment, connection-check, ownership and SSE tests passed again against the exported Community source.
- Corrected SSE regression-test cleanup: the Django test client already closes the response through its async iterator. Closing it again emitted `request_finished` inside the test transaction and invalidated the PostgreSQL connection used by subsequent tests. Both suites now assert that the response was closed by the client.
- Docker Desktop was restored by backing up and recreating stale socket-only runtime directories. The dedicated test database container was removed after verification; Docker remains available. No production database was used.

The existing install-then-pair workflow and all Runtime authentication behavior remain unchanged.

### macOS default shell (SDK 0.46.4)

Cloud now forwards `auto` to Computer Runtime instead of choosing sh from cached host facts. SDK 0.46.4 selects zsh on macOS (falling back to `/bin/zsh` when PATH lookup fails), while preserving explicit bash/sh choices. Both the live terminal stream and command-RPC paths carry this setting. Legacy SSH resolution is unchanged.

Deploy the updated Cloud code and upgrade the Computer Runtime to 0.46.4 in its existing Python environment. Restart Runtime, close existing terminal sessions and open a new terminal to use the new default. Upgrading only one side is insufficient for the default behavior. The terminal still uses pipes, not a PTY.
