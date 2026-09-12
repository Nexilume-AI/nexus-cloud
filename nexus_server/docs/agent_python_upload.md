# Python source → Nexus Container

Agent → Runtime → Nexus Container now has **Upload Python** alongside the existing registry/archive paths. Download the working example from that panel, upload a single UTF-8 `.py` file, optionally add `requirements.txt` and runtime secrets, then choose **Build image**. After successful verification, explicitly choose **Deploy this version**. A deployed version links to **Test Agent privately**. Neither upload nor build publishes the Agent or replaces a running container.

## Supported contract

- Python 3.12, top-level `NexusAgent` from `nexus_agent` (SDK 0.46.0+), `NexusMCPServer` from `nexus_agent.fastmcp`, or `FastMCP` from the standalone `fastmcp` package (3.x).
- One declared instance is detected automatically. Multiple instances require the variable name. `mcp.server.fastmcp`, factories, ZIP projects, arbitrary scripts and OS-package installation are not part of this first release.
- The platform imports the module, then starts the instance as Streamable HTTP at `/mcp`, port 8000. `if __name__ == '__main__'` is not executed. Initialize network clients lazily inside tools: verification has **no external network or runtime secrets**.
- SDK/FastMCP and their base dependencies are profile-managed. `requirements.txt` accepts public package names and version constraints, not URLs, local paths, VCS, pip flags or private indexes. Only binary wheels are installed. Base package constraints cannot be overridden. Actual resolved versions are retained in Build details.
- Source limit: 1 MiB. Requirements: 32 KiB / 100 direct entries. Secrets: 20 entries, up to 8192 characters each. Python/runtime control environment variables are reserved.
- Secret values are encrypted using the existing server secret encryption, excluded from API/audit/build output, and injected by environment name only into the deployed container. They are revision-scoped: a new source upload must supply its own secrets. Never hard-code credentials in source, descriptions or returned tool data.
- Source SHA-256 refers to the stored UTF-8 source text (a leading UTF-8 BOM is removed). No source download/publication endpoint is added.

## Single-source edge and Docker Agents

The panel's **OpenWrt + Docker example** is the SDK's
`examples/dual_runtime_agent.py`: run it directly for OpenWrt registration, or
upload the identical file for hosted MCP. Keep the `NexusAgent` instance and
decorators at module scope; guard `agent.run()` with `if __name__ == '__main__'`.
Do not force `runtime="openwrt"` in code intended for upload.

The trusted container adapter sets `NEXUS_AGENT_RUNTIME_MODE=hosted` before
import, so native declarations do not discover a Router, request Router
authentication or bind an edge listener. It exports only declared MCP tools,
with their original input schemas, execution policies, profiles and modalities.
Native sync/async and stream handlers receive the same per-call Run context;
Unicode payloads and interactive `ctx.chat.ask()` use the existing Cloud paths.
This mode is process configuration, never an inbound user parameter or a
fallback after an edge connection error.

Native Computer/Mobile requirements and capabilities are verified from the MCP
catalog and saved on the candidate version. They update Agent declarations only
after successful deployment, never during upload/build; caller grants and
bindings are not created or copied. An edge-managed resource is not automatically
converted to Docker ownership: upload to a Docker-managed Agent.

Rebuild the operator Python base profile with SDK 0.46.0+ before native uploads.
The builder checks this in the isolated profile and reports
`PYTHON_PROFILE_UPGRADE_REQUIRED` instead of attempting Router discovery on an
old SDK. This is zero Router/Dockerfile configuration for supported scripts,
not zero business configuration: required external secrets, extra dependencies,
Browser binaries and caller permissions remain explicit.

## Data and execution

`POST /api/v1/agents/{id}/runtime/python-builds/` accepts multipart fields `file`, optional `requirements`, `entrypoint`, and JSON-object `secrets`; returns 202 and a durable build ID. `GET` returns operator configuration and the newest 20 builds. Both require existing Agent runtime management permissions; tenant/project scoping is inherited from Agent access. Source, secret ciphertext, worker details and raw process output are never serialized.

Migrations 0047/0048 add `AgentPythonBuild` and the assigned Docker host. No existing Agent/source data is rewritten. The dedicated `run_agent_python_builds` process claims the PostgreSQL queue using row/advisory locks. It **never falls back to eager execution or SQLite**. Global concurrency is bounded at two, each Agent has at most one queued/running build, each Organization at most ten, each Agent at most 100 retained revisions. Queue listing does not require tenant-admin Job access.

Stages are `queued → dependencies → image → verify → artifact → ready`. Dependency installation runs in a non-root container, with no source, host mounts or credentials. Candidate image assembly uses a platform-owned COPY-only Dockerfile and no network. The offline verification container starts the MCP server, initializes it and lists tools; it never invokes them. Verified catalogs and Nexus tool policies belong to the image's `AgentVersion`, so an undeployed candidate cannot change the active tool catalog. Ordinary image, OpenWrt, billing and per-invocation Run semantics remain unchanged.

Container limits: 1 CPU, 1 GiB RAM/no extra swap, 128 PIDs, read-only root, no Linux capabilities, no-new-privileges; dependency scratch 768 MiB and returned bundle 512 MiB; validation scratch 256 MiB. CLI output is bounded. Dependency/image/verification stages time out at 300/180/60 seconds. A lost worker expires after 20 minutes; its build fails without replaying user tools. The assigned host reaps labeled stale build containers on worker recovery. Candidate image cleanup/archival is operator-owned; no broad Docker prune is used.

## Delete failed builds

Agent Runtime administrators can delete a failed source build with
`DELETE /api/v1/agents/{agent_id}/runtime/python-builds/{build_id}/`.
`POST /api/v1/agents/{agent_id}/runtime/python-builds/delete-failed/` accepts
`{"build_ids": ["..."]}` (1–100 explicit IDs) for atomic bulk cleanup. The listing
also returns ID/filename summaries of deletable failures, including those older
than the 20-entry timeline. The UI confirms these exact named targets; new failures
arriving after confirmation are not included. A missing or out-of-scope target returns 404;
a queued, running, successful or image-linked build returns
`409 PYTHON_BUILD_DELETE_CONFLICT`, with no partial batch deletion.

Deletion immediately removes uploaded source, requirements, saved secrets,
diagnostics and related Inbox/legacy notifications, and releases the source
revision slot. A private payload-free cleanup tombstone remains until the assigned
Python build worker removes residual, unreferenced artifacts. The worker uses a
per-build PostgreSQL execution lock so cleanup does not race a live old attempt;
late results cannot restore a deleted build. Only generated build-specific tags
and artifact files are eligible; active/registered images and the trusted base
profile are never removed. Docker/host failures leave cleanup queued for retry.
The audit retains IDs and the deletion count, not source or credentials.

There is no implicit cancellation or deletion of successful versions. Deletion
requires confirmation and cannot be undone. Restart the API and Python build
worker after upgrading to enable the routes and background cleanup; no database
migration is required.

## Remove an unused runtime image

`DELETE /api/v1/agents/{agent_id}/runtime/images/{image_id}/` is a separate,
Agent-admin-only operation for successfully built or registered images. It
soft-deletes the registration, not the Docker/Registry image or stored archive.
Successful source builds, saved build configuration, version records, deployment
references and audit history remain intact. This operation does not reclaim
disk space or successful-build revision capacity. Failed-build cleanup above
remains the destructive, payload-erasing operation for failed builds only.

The shared image list returns `usage.is_default`, `deployed_environments`,
`can_delete` and safe `blocking_reasons`. Default selection is not a claim that
an image is deployed. Active/deploying runtimes, queued deployments, unfinished
Runs, recovery, rollback and retiring-container references prevent removal with
`409 RUNTIME_IMAGE_IN_USE`. A concurrently committing Runtime returns a retryable
conflict without waiting in an inverted lock order. Missing or inaccessible
images return 404. The API never contacts Docker during deletion.

Removing the default selects the already-active production image, if available,
otherwise clears the default. It never deploys anything or changes the published
Agent version. The existing implicit latest-image fallback for an explicit
deployment with no default remains unchanged. A removed image cannot be selected
or deployed by ID; background recovery cannot resurrect it. Python build history
returns `image_removed=true`, so the UI hides its obsolete deployment action.

API/Web/lifecycle workers must load the updated code together; no database
migration or live data rewrite is needed. External Registry deletion and physical
image/archive garbage collection are deliberately separate operator actions.

## Local activation

From the repository root:

```powershell
python nexus_server/scripts/prepare_agent_python_profile.py
```

This builds the operator-owned Python profile, verifies SDK imports and the native `NexusAgent.as_mcp_server()` adapter (SDK 0.46.0+), resolves an immutable digest, and stores `.local/nexus-cloud/python-profile.json`. An old profile is rejected without replacing the current configuration. It does not touch the database or restart anything. `--use-existing --image <vetted-local-image>` may reuse a reviewed profile. The profile build context contains only trusted SDK sources, not local uploads or credentials.

After preparing a new profile, restart the Cloud API and Python builder during an authorized maintenance window so new uploads use its digest. Running Agent deployments remain pinned to their existing images; failed uploads must be submitted again. Rebuilding the profile alone does not change settings in already-running processes.

On the next authorized `start-nexus-cloud.ps1` restart, the launcher reads this file, applies incremental migrations, and starts the independently tracked `python_builder` worker. Set `NEXUS_AGENT_PYTHON_BUILDS_ENABLED=0` explicitly to keep uploads disabled. Other runtime strategies continue to work when builds are disabled. Do not restart a shared Cloud installation in the middle of active work without operator coordination.

## Production gate

Use the dedicated `agent-python-builder` and `agent-runtime-controller` processes
on the assigned Docker host. Verified Python images are saved by digest to
`NEXUS_SHARED_STORAGE_ROOT`, allowing the same logical worker to restore an image
after its local Docker cache or host is replaced. Lifecycle ownership remains
bound to `NEXUS_AGENT_RUNTIME_HOST_ID`; this is durable recovery, not an active/active
multi-host scheduler. Web and ordinary Celery workers never receive a Docker socket.

Required settings:

```text
NEXUS_AGENT_PYTHON_BUILDS_ENABLED=1
NEXUS_AGENT_PYTHON_BASE_IMAGE=<reviewed local sha256 ID or repository@sha256 digest>
NEXUS_AGENT_PYTHON_DEPENDENCY_NETWORK=<operator-controlled egress network>
NEXUS_AGENT_PYTHON_ISOLATION_READY=1
NEXUS_AGENT_RUNTIME_HOST_ID=<assigned stable Docker worker identity>
NEXUS_AGENT_PYTHON_IMAGE_ARTIFACT_MAX_BYTES=<bounded artifact size; default 2 GiB>
```

`ISOLATION_READY` is an operator attestation, **not a firewall**. Before setting it, enforce PyPI-only dependency egress with private-address/metadata-service denial, dedicated daemon/VM boundaries, host disk/download budgets, monitoring and retention. Existing production image admission and Runtime egress gates still apply. Do not claim production multitenant sandboxing merely because local Docker tests pass. Protect the database and server encryption key; backup/rotation follow the existing Nexus secret policy.

Failure messages deliberately omit raw dependency/import logs, which can contain arbitrary Agent data. The UI retains stage, safe error classification, build ID, source digest, dependency versions and verification milestones. For unsupported dependencies or more complex entrypoints, retain the registry/archive workflow.
