# Private Agent large files

Files use an authenticated HTTP data plane. MCP receives small, server-verified references, not Base64 or binary content. This works without attaching a Computer: the SDK reads/writes the Agent's own local filesystem and transfers bytes through Nexus object storage.

## Limits and lifecycle

- `NEXUS_AGENT_FILE_MAX_BYTES`: 5 GiB per input/output by default.
- `NEXUS_AGENT_FILE_TENANT_BYTES`: 50 GiB logical reservation across ready files and uploads per consumer Organization; 32 active transfers maximum, eight files per invocation/turn.
- Upload requests are at most 1 MiB. Every chunk requires SHA-256 and an exact offset; repeating the same chunk is idempotent, conflicting content returns 409.
- Incomplete/unattached transfers expire after 24 hours. Cancel and expiry release quota **after** storage cleanup, not before.
- Ready Run files and output snapshots remain retained and count toward the storage cap; this release does not automatically erase delivered Run files to make space.
- Storage must have room for both staged chunks and the assembled object (at least twice logical capacity plus operational headroom). Completed chunk cleanup is asynchronous.
- The dedicated `dataset-imports` Worker and Beat finalize files, renew leases, fence expired workers, and clean abandoned chunks. Local development uses `run_dataset_import_worker` and `run_dataset_maintenance`; do not run this in web request threads.
- Production requires PostgreSQL and shared object storage. For S3, configure bucket lifecycle to abort incomplete multipart uploads after worker crashes.
- The browser retains the selected File during a transient failure; **Retry upload** resumes from the server's confirmed offset. Reloading the page does not restore the browser's local File handle; API/SDK clients can persist the opaque file ID and explicitly resume with the unchanged source file.

## Caller upload API

Use the same identity, Organization, Project, and optional `X-Nexus-End-User` on every request.

1. `POST /api/v1/agent-files/` with `agent_id`, `name`, `size_bytes`, optional `content_type` and full-file `sha256`.
2. `PUT /api/v1/agent-files/{file_id}/` with binary body, `X-Nexus-Upload-Offset`, and `X-Nexus-Chunk-SHA256`.
3. `GET` the same URL after an interruption to obtain `received_bytes`.
4. `POST` the same URL with `{}` to queue finalization. Poll until `state=ready`; `failed` retains source chunks and can be retried, `DELETE` cancels an undelivered transfer.
5. Private Display start/resume: pass top-level `files: [file_id]`.
6. Standard MCP `tools/call`: pass `arguments.files: [{"file_id":"..."}]`. Nexus validates ownership and substitutes authoritative name, MIME type, size and hash before dispatch.

The tool must declare an array property named `files` in its input schema. Its items must accept `file_id`, `name`, `content_type`, `size_bytes`, and `sha256`. An existing text-only Agent does not gain file parsing by changing a UI flag. Update its implementation/manifest using the SDK example first.

A file is bound transactionally to one Run; cross-caller, cross-project, and cross-Run attachment attempts fail. The SDK uses only a trusted, Run-scoped delegate URL and token, never an arbitrary URL supplied in a reference. Public Marketplace Demo cannot read these files.

## Agent SDK 0.41.0

```python
# Inside a NexusRunContext handler:
for reference in payload.get("files", []):
    ctx.files.download(reference, "/tmp/input.bin")  # streams; verifies size/SHA-256
    # Process the input with your own bounded-memory implementation.
    result = ctx.output.upload_file("/tmp/result.zip", content_type="application/zip")
```

- `ctx.files.list()`, `iter_bytes(ref, offset=0)`, `download(ref, destination, resume=True, overwrite=False)`.
- `ctx.files.upload(path, resume_id=None)` / `ctx.output.upload_file(...)` finalize an immutable output snapshot before returning.
- Async: `ctx.aio.files.list/download/upload` and `ctx.aio.output.upload_file`.
- `NexusFileError.file_id` supplies a safe resume ID after an interrupted output transfer, if creation was acknowledged. Keep the Run active and use the same unchanged source file. Do not serialize exceptions' transport internals or credentials.
- Explicit filesystem operations fail outside an active hosted invocation; they do not silently pretend to transfer data in no-op mode.
- Output upload does not transfer a file from an attached user's Computer. Download/read it through the appropriate Computer capability first if that is the intended source. SSH credentials are never passed to this API.

See `nexus_openwrt/sdk/nexus-agent-sdk-python/examples/router_file_agent.py` for a complete bounded-memory round-trip Agent. It verifies transfer only; it does not claim PDF, spreadsheet, archive or model understanding.

## Downloads and security

- Caller input list: `GET /api/v1/agent-runs/{run_id}/files/` with identity and Display Token.
- Input download: `/api/v1/agent-runs/{run_id}/files/{file_id}/download/`.
- Output download: `/api/v1/agent-runs/{run_id}/outputs/{artifact_id}/download/`.
- SDK/API clients use authenticated GET, optional single `Range`, and `If-Range` with ETag. HEAD reports authoritative size/hash. Invalid ranges return 416.
- Browser POST first authorizes the Run and issues a one-hour HttpOnly, path-scoped SameSite download cookie. Native browser GET then streams to disk without a complete Blob in JavaScript. URL contains no JWT, Display Token or SSH token. Expired access can be renewed by clicking Download again.
- Stream reads are 256 KiB under both WSGI and ASGI. S3 assembly buffers at most an 8 MiB multipart part.
- Files are forced downloads (`application/octet-stream`, attachment, nosniff); no automatic execution, preview or archive extraction.
- Output snapshots are immutable and enter the existing artifact workflow with scanning pending. Upload success is **not** malware clearance or permission to publish. Existing Data Asset scan, rights and export gates remain enforced; binary publication requires an appropriate scanner.

## Verification

Run `manage.py test tests.test_agent_files` against a disposable Docker PostgreSQL database, SDK `test_run_files.py`, and the Private Display Playwright suite. Coverage includes a real 64 MiB round trip, parallel duplicate chunks, authorization, native Range downloads, SDK resume and mobile UI. This is not a 5 GiB soak-test or production load certification.
