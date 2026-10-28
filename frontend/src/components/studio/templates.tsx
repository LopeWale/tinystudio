import type { ReactNode } from "react";
import { cx } from "../boardui/utils/cx";
import "./templates.css";

/** Layout slots only. Features retain their data, selection and operation state. */
export type TemplateProps = {
  children: ReactNode;
  toolbar?: ReactNode;
  summary?: ReactNode;
  inspector?: ReactNode;
  className?: string;
  label?: string;
  hidden?: boolean;
};
type TemplateKind =
  | "collection"
  | "detail"
  | "analysis"
  | "run"
  | "conversation"
  | "settings"
  | "setup";
function Template({
  kind,
  children,
  toolbar,
  summary,
  inspector,
  className,
  label,
  hidden,
}: TemplateProps & { kind: TemplateKind }) {
  return (
    <section
      hidden={hidden}
      aria-label={label}
      className={cx("studio-template", `studio-template--${kind}`, className)}
      data-template={kind}
    >
      {summary != null && (
        <div className="studio-template__summary">{summary}</div>
      )}
      {toolbar != null && (
        <div className="studio-template__toolbar">{toolbar}</div>
      )}
      <div
        className={cx(
          "studio-template__layout",
          inspector != null && "studio-template__layout--inspected",
        )}
      >
        <div className="studio-template__content">{children}</div>
        {inspector != null && (
          <aside
            aria-label={label ? `${label} details` : "Context and details"}
            className="studio-template__inspector"
          >
            {inspector}
          </aside>
        )}
      </div>
    </section>
  );
}
export function CollectionTemplate(props: TemplateProps) {
  return <Template {...props} kind="collection" />;
}
export function DetailTemplate(props: TemplateProps) {
  return <Template {...props} kind="detail" />;
}
export function AnalysisTemplate(props: TemplateProps) {
  return <Template {...props} kind="analysis" />;
}
export function RunTemplate(props: TemplateProps) {
  return <Template {...props} kind="run" />;
}
export function ConversationTemplate(props: TemplateProps) {
  return <Template {...props} kind="conversation" />;
}
export function SettingsTemplate(props: TemplateProps) {
  return <Template {...props} kind="settings" />;
}
export function SetupTemplate(props: TemplateProps) {
  return <Template {...props} kind="setup" />;
}
