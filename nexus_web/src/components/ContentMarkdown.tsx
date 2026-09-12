import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function safeUrl(url: string) {
  if (/^(https?:|mailto:)/i.test(url)) return url;
  return "";
}

export function ContentMarkdown({
  value,
  empty = "No content available.",
}: {
  value?: string | null;
  empty?: string;
}) {
  const content = value ?? "";
  if (!content.trim())
    return <p className="content-markdown-empty">{empty}</p>;
  return (
    <div className="content-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={safeUrl}
        components={{
          img: () => null,
          a: ({ children, href }) => {
            const safeHref = safeUrl(href ?? "");
            return safeHref ? (
              <a href={safeHref} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
