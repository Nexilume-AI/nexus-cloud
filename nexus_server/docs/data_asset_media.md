# Data Assets: images, files and Agent outputs

## Import your files

1. Open a collection that you can manage in **Data Assets**.
2. Choose **Import assets → Files & images**.
3. Select files and confirm permission to store/share them with the collection's authorized users.
4. Choose **Import files**. Each file reports its own result. **Retry remaining files** preserves successful imports and reuses the failed file's request key.
5. Use the image icon in Files to open a protected preview; Download keeps the original bytes.

Supported: PNG, JPEG, WebP, UTF-8 TXT, Markdown, CSV, JSON, JSONL and NDJSON. The default per-file limit is 10 MiB; images also obey the Cloud image byte/pixel limits. Animated images are not accepted. Large text files may be stored without content indexing under the existing index limit.

Imports do not publish a collection. Creating a release, completing Marketplace listing/price/license fields and publishing remain explicit operations.

## Archive Agent-generated images

An Agent can report an image through the existing SDK:

```python
ctx.output.image(png_bytes, content_type="image/png", title="Generated result")
```

The SDK uploads bounded bytes and emits `nexus.image.created`. Cloud registers a durable output snapshot with its SHA-256, Run and source event. No Attached Computer or local workspace is needed for this image path. The existing SDK image-output limit remains 2 MiB.

After the Run completes, use **Import assets → Agent assets → Output files & images**, select the Run and output, scan it, then import into the collection. Existing Agent/collection management permissions and license/policy checks still apply. Generated images additionally require the producing caller; managing an Agent does not grant its publisher access to another caller's generated image.

Browser frames, input attachments and Demo images do not automatically become reusable output artifacts. Merely returning an external image URL is not an archival operation. Already-uploaded images can be resolved from explicit image-output events while their protected bytes still exist.

## Security boundaries

- Server checks real bytes, MIME/extension consistency, raster decoding, dimensions, frame count, text-secret rules and sensitive text/GPS metadata. Client-supplied scan flags cannot approve a file.
- Unknown/active binary formats, including PDF, Office, ZIP, SVG, HTML and executables, remain blocked until a dedicated scanner is available.
- These checks are not OCR, antivirus, visual-content moderation or a copyright determination.
- Previews reuse authenticated download/acquisition authorization, verify immutable hashes and return a resized PNG without source metadata. They never create public or bearer-token URLs.
- Private/cross-Organization and paid acquisition restrictions still apply to previews; viewing a listing is not permission to view its files.
- Storage/export capacity, immutable releases and acquisition snapshots use the existing Data Assets services. The change needs no schema migration.
