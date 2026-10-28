import { useState, type HTMLAttributes } from "react";
import { cx } from "@/components/boardui/utils/cx";
/** Progressive top fade belongs to the scrolling region, never to its content. */
export function AssistantScrollArea({
  children,
  className,
  onScroll,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  const [fade, setFade] = useState(0);
  return (
    <div
      {...props}
      className={cx("assistant-scroll-area", className)}
      onScroll={(event) => {
        setFade(Math.min(1, event.currentTarget.scrollTop / 24));
        onScroll?.(event);
      }}
    >
      <div className="assistant-scroll-fade" aria-hidden="true">
        <span className="assistant-fade-soft" style={{ opacity: fade }} />
        <span className="assistant-fade-dense" style={{ opacity: fade }} />
        <span className="assistant-fade-surface" style={{ opacity: fade }} />
      </div>
      {children}
    </div>
  );
}
