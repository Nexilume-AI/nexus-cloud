import { describe, expect, it } from "vitest";
import { filePreviewKind, fileReference, runFiles } from "./runFiles";
import type { AgentFileTransfer } from "./api";
import type { AgentOutputArtifact, AgentRunMessage } from "./types";

describe("Run files directory", () => {
  const input = { file_id: "file-1", name: "notes.md", content_type: "text/markdown", state: "ready", size_bytes: 20, turn_index: 1 } as AgentFileTransfer;
  it("keeps input/output identity distinct and records repeated use across turns", () => {
    const output = { id: "file-1", original_file_name: "notes.md", content_type: "text/markdown", snapshot_status: "ready", size_bytes: 30 } as AgentOutputArtifact;
    const messages = [{ turn_index: 3, content_blocks: [{ type: "file", url: "/api/v1/agent-runs/run-1/files/file-1/download/" }] }] as AgentRunMessage[];
    const rows = runFiles("run-1", [input], [output], messages);
    expect(rows.map(row => row.key)).toEqual(["input:file-1", "output:file-1"]);
    expect(rows[0].turns).toEqual([1, 3]);
    expect(rows[1].turns).toEqual([]);
  });
  it("blocks failed scans and retains protected image references only from this Run", () => {
    const messages = [{ role: "user", turn_index: 2, content_blocks: [
      { type: "image", url: "/api/v1/agent-runs/run-1/display-assets/image-1/", alt: "Photo" },
      { type: "image", url: "/api/v1/agent-runs/other/display-assets/image-1/" },
      { type: "image", url: "https://untrusted.test/image.png" },
      { type: "image", url: "/api/v1/agent-runs/run-1/display-assets/../../private/" },
    ] }] as AgentRunMessage[];
    const rows = runFiles("run-1", [], [{ id: "bad", scan_status: "failed", snapshot_status: "ready" } as AgentOutputArtifact], messages);
    expect(rows).toHaveLength(2); expect(rows[0].state).toBe("blocked");
    expect(rows[1].turns).toEqual([2]);
    expect(fileReference(rows[1])).toBe("> Input file: Photo\n> Reference: image-1 · Turn 2");
  });
  it("never treats HTML or SVG as an executable preview", () => {
    expect(filePreviewKind({ name: "attack.html", mime: "text/html" })).toBe("text");
    expect(filePreviewKind({ name: "attack.svg", mime: "image/svg+xml" })).toBe("unsupported");
    expect(filePreviewKind({ name: "notes.md", mime: "application/octet-stream" })).toBe("markdown");
    expect(filePreviewKind({ name: "report.pdf", mime: "application/pdf" })).toBe("pdf");
    expect(filePreviewKind({ name: "photo.png", mime: "image/png" })).toBe("image");
  });
});
