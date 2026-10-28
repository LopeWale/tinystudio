import { useId, type ReactNode } from "react";
import { cx } from "../boardui/utils/cx";
import "./settings-section.css";

export interface SettingsSectionProps {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** Public AffectUI settings composition reference; no Pro source is copied. */
export function SettingsSection({
  title,
  description,
  children,
  className,
}: SettingsSectionProps) {
  const headingId = useId();
  return (
    <section
      aria-labelledby={headingId}
      className={cx("studio-settings-section", className)}
    >
      <div className="studio-settings-section__heading">
        <h3 id={headingId} className="text-body-medium text-text-primary">
          {title}
        </h3>
        {description != null && (
          <div className="text-body-2-regular text-text-tertiary">
            {description}
          </div>
        )}
      </div>
      <div className="studio-settings-section__controls">{children}</div>
    </section>
  );
}
