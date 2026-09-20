import { t, useLocale } from "../localization";
import { Download, FileText } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useRef, type ReactNode } from "react";
import { ChatCopyButton } from "./ChatCopyButton";

import type { AgentChatContentBlock } from "../lib/types";
import { ProtectedRunImage } from "./ProtectedRunImage";

type Props = {
  blocks?: AgentChatContentBlock[];
  fallback: string;
  role: "user" | "assistant";
  onDownloadFile?: (
    block: Extract<AgentChatContentBlock, { type: "file" }>,
  ) => void;
};

export function AgentChatMessageContent({
  blocks,
  fallback,
  role,
  onDownloadFile,
}: Props) {
  useLocale();
  const visible = blocks?.length
    ? blocks
    : [{ type: "markdown", text: fallback } as AgentChatContentBlock];
  return (
    <div className="grid gap-3">
      {visible.map((block, index) => {
        if (block.type === "markdown") {
          return (
            <div
              key={`markdown-${index}`}
              className="agent-chat-markdown min-w-0 leading-6"
            >
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                skipHtml
                disallowedElements={["img"]}
                unwrapDisallowed
                components={{
                  a: ({ href = "", children }) => {
                    const safe = /^(https?:|mailto:)/i.test(href);
                    return safe ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="underline decoration-current/40 underline-offset-2 hover:decoration-current"
                      >
                        {children}
                      </a>
                    ) : (
                      <span>{children}</span>
                    );
                  },
                  pre: ChatCodeBlock,
                  blockquote: ({ children }) => <blockquote className="my-2 border-l-2 border-current/25 pl-3 text-sm opacity-80">{children}</blockquote>,
                  code: ({ children }) => (
                    <code className="font-mono text-[0.9em]">{children}</code>
                  ),
                  table: ({ children }) => (
                    <div className="my-3 max-w-full overflow-x-auto rounded-md border border-[#d7ddd4] bg-[#fbfbf7]">
                      <table className="min-w-full border-collapse text-left text-xs text-[#34322d]">
                        {children}
                      </table>
                    </div>
                  ),
                  thead: ({ children }) => (
                    <thead className="bg-[#eef1e9]">{children}</thead>
                  ),
                  th: ({ children }) => (
                    <th className="border border-current/15 px-2 py-1.5 font-semibold">
                      {children}
                    </th>
                  ),
                  td: ({ children }) => (
                    <td className="border border-current/15 px-2 py-1.5">
                      {children}
                    </td>
                  ),
                }}
              >
                {block.text}
              </ReactMarkdown>
            </div>
          );
        }
        if (block.type === "fields") {
          return (
            <dl
              key={`fields-${index}`}
              data-content-block="fields"
              className="grid gap-px overflow-hidden rounded-md border border-[#d7ddd4] bg-[#d7ddd4] text-[#34322d]"
            >
              {block.items.map((item, itemIndex) => (
                <div
                  key={`${item.label}-${itemIndex}`}
                  className="grid grid-cols-[minmax(7rem,0.42fr)_1fr] gap-3 bg-[#fbfbf7] px-3 py-2.5"
                >
                  <dt className="text-xs font-medium text-[#68736b]">
                    {item.label}
                  </dt>
                  <dd className="min-w-0 break-words text-sm">{item.value}</dd>
                </div>
              ))}
            </dl>
          );
        }
        if (block.type === "file") {
          const safe = block.url.startsWith("/api/v1/agent-runs/");
          return (
            <div
              key={`file-${index}`}
              className={`flex items-center gap-3 rounded-md border p-3 ${role === "user" ? "border-black/10 bg-white" : "border-white/10 bg-white/5"}`}
            >
              <FileText size={18} className="shrink-0 opacity-70" />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{block.name}</div>
                <div className="mt-0.5 text-xs opacity-55">
                  {[block.content_type, block.size_bytes != null ? formatAttachmentSize(block.size_bytes) : "", block.status === "attached" ? t("Attached to this turn") : block.status].filter(Boolean).join(" · ") || t("Agent output")}
                </div>
              </div>
              {onDownloadFile ? (
                <button
                  type="button"
                  onClick={() => onDownloadFile(block)}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-md hover:bg-current/10"
                  aria-label={t("Download {{0}}", { 0: block.name })}
                >
                  <Download size={15} />
                </button>
              ) : safe ? (
                <a
                  href={block.url}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-md hover:bg-current/10"
                  aria-label={t("Download {{0}}", { 0: block.name })}
                >
                  <Download size={15} />
                </a>
              ) : null}
            </div>
          );
        }
        return <ProtectedRunImage key={`image-${index}`} path={block.url} title={block.title} alt={block.alt} />;
      })}
    </div>
  );
}

function formatAttachmentSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${Math.ceil(bytes / 1024)} KiB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
}

function ChatCodeBlock({ children }: { children?: ReactNode }) {
  useLocale();
  const code = useRef<HTMLPreElement>(null);
  return <div className="my-2 min-w-0 max-w-full overflow-hidden rounded-md border border-black/10 bg-[#f0f0ed] text-[#34322d]">
    <div className="flex justify-end border-b border-black/10 px-2"><ChatCopyButton text={() => code.current?.textContent || ""} label={t("Copy code")} caption="Copy code" /></div>
    <pre ref={code} className="max-w-full overflow-x-auto p-3 text-xs">{children}</pre>
  </div>;
}
