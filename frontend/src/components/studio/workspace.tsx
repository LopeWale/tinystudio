import { useId, type ReactNode } from "react";
import { cx } from "../boardui/utils/cx";

/** Secondary tools remain in the document without competing with the working area. */
export function WorkspaceDisclosure({
  title,
  summary,
  children,
  defaultOpen = false,
  open,
  onToggle,
  className,
}: {
  title: string;
  summary?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  open?: boolean;
  onToggle?: (open: boolean) => void;
  className?: string;
}) {
  return (
    <details
      className={cx("workspace-disclosure", className)}
      open={open ?? (defaultOpen || undefined)}
      onToggle={
        onToggle ? (event) => onToggle(event.currentTarget.open) : undefined
      }
    >
      <summary>
        <span>{title}</span>
        {summary && <small>{summary}</small>}
      </summary>
      <div className="workspace-disclosure-body">{children}</div>
    </details>
  );
}

/** A persistent master/detail layout: the selection is owned by its feature controller. */
export function InspectionWorkspace({
  children,
  inspector,
  label,
}: {
  children: ReactNode;
  inspector?: ReactNode;
  label: string;
}) {
  const id = useId();
  return (
    <div className={cx("inspection-workspace", inspector && "has-inspector")}>
      <div className="inspection-master">{children}</div>
      {inspector && (
        <aside className="inspection-detail" aria-labelledby={id}>
          <h2 id={id} className="inspection-label">
            {label}
          </h2>
          {inspector}
        </aside>
      )}
    </div>
  );
}
