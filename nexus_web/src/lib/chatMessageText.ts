import type { AgentChatContentBlock } from "./types";

// Export only visible message content, never protected resource URLs or tokens.
export function chatMessageText(blocks: AgentChatContentBlock[] | undefined, fallback: string) {
  if (!blocks?.length) return fallback;
  return blocks.map(block => {
    if (block.type === "markdown") return block.text;
    if (block.type === "fields") return block.items.map(item => `${item.label}: ${item.value}`).join("\n");
    if (block.type === "file") return `[File: ${block.name}]`;
    return `[Image: ${block.alt || block.title || "image"}]`;
  }).join("\n\n");
}
