import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import { cx } from "@/components/boardui/utils/cx";
import { AssistantScrollArea } from "./scrollArea";

/** Explicit HTTP(S) URLs only: no relative/app routes, credentials, scripts, data or auto-fetches. */
export function safeMessageUrl(value: string) {
  if (!/^https?:\/\//i.test(value) || /[\u0000-\u001f\u007f]/.test(value))
    return "";
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : "";
  } catch {
    return "";
  }
}
const components: Components = {
  a: ({ href, children }) => {
    const url = safeMessageUrl(href || "");
    return url ? (
      <a href={url} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    );
  },
  img: ({ alt }) => (
    <span className="assistant-image-omitted text-caption-1-regular text-text-tertiary">
      [Image omitted{alt ? `: ${alt}` : ""}]
    </span>
  ),
  input: ({ checked }) => (
    <span aria-label={checked ? "Completed item" : "Incomplete item"}>
      {checked ? "☑" : "☐"}
    </span>
  ),
  h1: ({ children }) => <h3 className="text-title-3-semibold">{children}</h3>,
  h2: ({ children }) => <h3 className="text-title-3-semibold">{children}</h3>,
  h3: ({ children }) => <h4 className="text-headline-medium">{children}</h4>,
  h4: ({ children }) => <h4 className="text-headline-medium">{children}</h4>,
  h5: ({ children }) => <h5 className="text-body-medium">{children}</h5>,
  h6: ({ children }) => <h6 className="text-body-medium">{children}</h6>,
  code: ({ children, className }) => (
    <code
      className={cx(
        "assistant-message-code font-mono text-body-2-regular",
        className && /^language-[\w-]+$/.test(className)
          ? className
          : undefined,
      )}
    >
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="assistant-message-pre text-body-2-regular">{children}</pre>
  ),
  table: ({ children }) => (
    <AssistantScrollArea
      className="assistant-message-table"
      role="region"
      aria-label="Assistant table"
      tabIndex={0}
    >
      <table>{children}</table>
    </AssistantScrollArea>
  ),
};
const allowedElements = [
  "p",
  "a",
  "strong",
  "em",
  "del",
  "code",
  "pre",
  "blockquote",
  "ul",
  "ol",
  "li",
  "hr",
  "br",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "img",
  "input",
  "sup",
  "section",
];
export default function MarkdownBody({ content }: { content: string }) {
  return (
    <div className="assistant-message-markdown text-body-regular">
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        allowedElements={allowedElements}
        unwrapDisallowed
        urlTransform={safeMessageUrl}
        components={components}
      >
        {content}
      </Markdown>
    </div>
  );
}
