# Computer Runtime compatibility update

This update adopts the runtime fixes from [PR #5](https://github.com/Nexilume-AI/nexus-cloud/pull/5) while preserving the existing installation and pairing flow.

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

SDK 0.46.3 uses a pipe-based shell, not a PTY; see the SDK 0.46.5 PTY update below. SOCKS support requires a reachable configured proxy; a custom User-Agent does not guarantee that a Cloud firewall will permit the connection.

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

Deploy the updated Cloud code and upgrade the Computer Runtime to 0.46.4 in its existing Python environment. Restart Runtime, close existing terminal sessions and open a new terminal to use the new default. Upgrading only one side is insufficient for the default behavior. SDK 0.46.4 still uses pipes, not a PTY; see the SDK 0.46.5 update below.


### Interactive POSIX terminals (SDK 0.46.5)

SDK 0.46.5 allocates a PTY, acquires it as the shell's controlling terminal, and starts an interactive shell. macOS automatic selection uses zsh; Linux keeps bash with sh fallback. This restores the initial prompt, input echo, line editing and Ctrl+C. Both RPC and live WebSocket paths apply initial dimensions and resize requests. The shell startup helper runs in a separate process to avoid unsafe pre-exec callbacks in the threaded Runtime.

Upgrade the SDK in the Computer's existing Python environment, restart Runtime and open a new Cloud terminal. Existing pairing keys and the Cloud protocol remain unchanged. Windows still uses the existing PowerShell pipe transport. This source fix is not present in the published 0.46.4 wheel.

Validation (2026-09-18): Windows Runtime tests: 32 passed, 6 skipped (including POSIX-only acceptance), 6 shell-selection subtests passed. Isolated Linux Runtime tests: 31 passed, 2 platform skips; all 5 additional real PTY tests passed using bash and zsh, covering initial prompts, TTY descriptors, echo, zsh editing, Ctrl+C recovery, dimensions, foreground-job cleanup, RPC/stream resizing and final streamed output. The wheel contains the updated Runtime and PTY helper. Subsequently, [all seven SDK CI jobs passed](https://github.com/Nexilume-AI/nexus-agent-sdk-python/actions/runs/35330472828) for commit `a1c7083`, including native macOS Python 3.12/3.14 runs of the real PTY acceptance tests, Linux/Windows suites, package verification and Python 3.9 installation. This validates terminal behavior on macOS CI runners; it does not test every user shell customization or launchd installation.
