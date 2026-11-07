import { useState, type ReactNode } from "react";
import "./scroll-pane.css";

/** A bounded, keyboard-scrollable pane with a stationary progressive top edge. */
export function ScrollPane({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  const [depth, setDepth] = useState(0);
  return (
    <div className="studio-scroll-pane">
      <div className="studio-scroll-pane__edge" aria-hidden="true">
        <i style={{ opacity: depth }} />
        <i style={{ opacity: depth }} />
        <i style={{ opacity: depth }} />
      </div>
      <div
        className="studio-scroll-pane__body"
        role="region"
        aria-label={label}
        tabIndex={0}
        onScroll={(event) =>
          setDepth(Math.max(0, Math.min(1, event.currentTarget.scrollTop / 24)))
        }
      >
        {children}
      </div>
    </div>
  );
}
