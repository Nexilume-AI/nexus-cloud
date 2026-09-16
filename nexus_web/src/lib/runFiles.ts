import { t } from "../localization";
import type { AgentFileTransfer } from "./api";
import type { AgentOutputArtifact, AgentRunMessage } from "./types";

export type RunFile = {
  key: string; id: string; kind: "input" | "output"; name: string; mime: string;
  size: number | null; turns: number[]; path: string; state: string; source: string;
};
export function runFiles(runId: string, inputs: AgentFileTransfer[], outputs: AgentOutputArtifact[], messages: AgentRunMessage[]): RunFile[] {
  const turn = (value?: number | null) => value && value > 0 ? [value] : [];
  const rows: RunFile[] = inputs.map(file => ({ key: `input:${file.file_id}`, id: file.file_id, kind: "input", name: file.name,
    mime: file.content_type || "application/octet-stream", size: file.size_bytes, turns: turn(file.turn_index),
    path: `/api/v1/agent-runs/${runId}/files/${file.file_id}/download/`, state: file.state,
    source: file.source_label || (file.source_kind === "computer" ? t("Computer import") : "Upload") }));
  rows.push(...outputs.map(file => ({ key: `output:${file.id}`, id: file.id, kind: "output" as const, name: file.original_file_name,
    mime: file.content_type, size: file.size_bytes, turns: turn(file.turn_index),
    path: `/api/v1/agent-runs/${runId}/outputs/${file.id}/download/`, state: file.policy_status === "blocked" || file.scan_status === "failed" ? "blocked" : file.scan_status === "pending" || file.policy_status === "pending" ? "scanning" : file.snapshot_status,
    source: file.producer_step || "Agent output" })));
  for (const message of messages) for (const block of message.content_blocks) {
    if (block.type === "file") {
      const row = rows.find(item => item.path === block.url);
      if (row && message.turn_index && !row.turns.includes(message.turn_index)) row.turns.push(message.turn_index);
    }
    if (block.type !== "image" || !block.url.startsWith(`/api/v1/agent-runs/${runId}/display-assets/`)
        || !/^\/api\/v1\/agent-runs\/[^/]+\/display-assets\/[a-zA-Z0-9-]+\/$/.test(block.url)) continue;
    const existing = rows.find(item => item.path === block.url);
    if (existing) { if (message.turn_index && !existing.turns.includes(message.turn_index)) existing.turns.push(message.turn_index); continue; }
    const id = block.url.split("/").filter(Boolean).at(-1)!;
    rows.push({ key: `image:${id}`, id, kind: message.role === "user" ? "input" : "output",
      name: block.title || block.alt || "Attached image", mime: "image/*", size: null, turns: turn(message.turn_index),
      path: block.url, state: "ready", source: message.role === "user" ? t("Message image") : "Agent image" });
  }
  return rows;
}
export function filePreviewKind(file: Pick<RunFile, "name" | "mime">): "image" | "pdf" | "markdown" | "text" | "unsupported" {
  const mime = (file.mime || "").toLowerCase().split(";")[0];
  if (["image/png", "image/jpeg", "image/webp", "image/*"].includes(mime)) return "image";
  if (mime === "application/pdf" || /\.pdf$/i.test(file.name)) return "pdf";
  if (mime === "text/markdown" || /\.(md|markdown)$/i.test(file.name)) return "markdown";
  if (mime.startsWith("text/") || ["application/json", "application/xml"].includes(mime)
      || /\.(txt|json|csv|log|py|js|ts|tsx|jsx|yaml|yml|toml|xml|html|css|sh|ps1)$/i.test(file.name)) return "text";
  return "unsupported";
}
export function fileReference(file: RunFile): string {
  const name = file.name.replace(/[\r\n\u0000-\u001f]/g, " ").slice(0, 240);
  return `> ${file.kind === "input" ? t("Input file") : t("Run output")}: ${name}\n> Reference: ${file.id}${file.turns.length ? ` · Turn ${file.turns.join(", ")}` : ""}`;
}
