import { Component, lazy, Suspense, type ReactNode } from "react";
import "./messageContent.css";

const MarkdownBody = lazy(() => import("./markdownBody"));
export interface AssistantMessageContentProps {
  role: "user" | "assistant";
  content: string;
}
function PlainMessage({ content }: { content: string }) {
  return (
    <div className="assistant-message-plain text-body-regular">{content}</div>
  );
}
/** A formatting/chunk failure belongs to this message, never to the composer or conversation. */
class MessageFormattingBoundary extends Component<
  { content: string; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <>
        <p className="text-caption-1-regular text-text-tertiary" role="status">
          Formatting unavailable. Plain text is retained.
        </p>
        <PlainMessage content={this.props.content} />
      </>
    ) : (
      this.props.children
    );
  }
}
/** User prompts remain literal text. Only assistant content requests the parser chunk. */
export function AssistantMessageContent({
  role,
  content,
}: AssistantMessageContentProps) {
  if (role === "user" || !content) return <PlainMessage content={content} />;
  return (
    <MessageFormattingBoundary content={content}>
      <Suspense fallback={<PlainMessage content={content} />}>
        <MarkdownBody content={content} />
      </Suspense>
    </MessageFormattingBoundary>
  );
}
