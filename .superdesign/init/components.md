# Components

Captured 2026-10-27T23:43:08.381443+00:00 from the current shared working tree. Local discovery only: application source is frozen. No application edits, remote calls, live-data/credential requests, deployments, branches, worktrees or commits were performed by this init. These discovery files are not an instruction to upload the entire bundle. No `.superdesign/resume.json` is created.

## Verified stack

react `19.3.1`, react-router-dom `7.18.4`, typescript `5.9.3`, vite `8.3.5`, tailwindcss `4.3.3`, react-aria-components `1.22.1`. React HashRouter, Vite, TypeScript, CSS-first Tailwind v4 and source-owned BoardUI; no Next.js meta-framework is used. Shared wrappers: `frontend/src/components/studio`; BoardUI: `frontend/src/components/boardui`; feature-shared records/forms: `frontend/src/features/data`.

## Reported alignment issue: owners, not fixes

The parent reports screenshots with inconsistent raw table headings versus BoardUI Button padding/type and form alignment. This init does not claim independent screenshot reinspection. Source confirms multiple owners: `styles/studio.css` sets raw `th,td` padding and native control dimensions; `styles/workbench.css` adds scoped typography/padding; `features/data/data.css`, `records-workspace.css`, `workspace/serving-records.css`, and `styles/workspace.css` add route-level table/form rules. `features/data/common.tsx` owns raw Records headers and feature Input/Text/Select/Check/Pager. `components/studio/primitives.tsx` maps Button md/sm to BoardUI medium/small. `boardui/base/buttons/button.tsx` owns medium `h-9 p-2 text-body-medium`, small `h-8 px-2 py-1.5 text-body-medium`, plus nested label-wrapper padding. A cell label wrapped in Button therefore has different nested insets/type than raw th text. Field alignment also depends on Field wrapper/label structure, BoardUI Label/Input, native controls in page source and global `.studio-field` rules. Application fixes remain frozen.

## `frontend/src/components/studio/primitives.tsx`

Page, Panel, Button, Field, EmptyState, Badge, Modal, Evidence. Key props declared inline: title/actions/description; variant/size/loading; label/hint/error; open/title/onClose/footer/wide; label/data/as.

SHA-256: `d6dffcdf94f06c047c0cace49c3eaacd54b74a2e881a44e393cdeb18656a695a`

```tsx
import {
  createContext,
  useContext,
  Children,
  cloneElement,
  isValidElement,
  createElement,
  useEffect,
  useId,
  useRef,
  type ReactNode,
  type HTMLAttributes,
  type ButtonHTMLAttributes,
  type ElementType,
} from "react";
import { createPortal } from "react-dom";
import { UNSAFE_PortalProvider } from "react-aria/PortalProvider";
import { Button as BoardButton } from "../boardui/base/buttons/button";
import { cx } from "../boardui/utils/cx";
import { useStudio } from "../../app/StudioContext";
import { modalStack } from "../../app/modalStack";
type Box = HTMLAttributes<HTMLElement> & {
  children?: ReactNode;
  className?: string;
};
export function Page({
  title,
  description,
  actions,
  children,
  className,
  ...props
}: Box & { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <section {...props} className={cx("studio-page", className)}>
      <header className="page-heading">
        <div>
          <h1>{title}</h1>
          {description && <p className="muted">{description}</p>}
        </div>
        {actions && <div className="actions">{actions}</div>}
      </header>
      {children}
    </section>
  );
}
export function Panel({
  title,
  actions,
  children,
  className,
  ...props
}: Omit<Box, "title"> & { title?: ReactNode; actions?: ReactNode }) {
  return (
    <section {...props} className={cx("studio-panel", className)}>
      {(title || actions) && (
        <header className="panel-heading">
          <h2>{title}</h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}
export function Button({
  size = "md",
  variant = "secondary",
  loading,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md";
  loading?: boolean;
}) {
  return (
    <BoardButton
      {...props}
      variant={variant}
      size={size === "sm" ? "small" : "medium"}
      disabled={props.disabled || loading}
      aria-busy={loading || undefined}
    >
      {loading ? "Working…" : children}
    </BoardButton>
  );
}
export function Field({
  label,
  hint,
  error,
  children,
  className,
  ...props
}: Box & { label: ReactNode; hint?: ReactNode; error?: ReactNode }) {
  const id = useId(),
    child =
      Children.count(children) === 1 && isValidElement(children)
        ? children
        : null;
  const control =
    child &&
    (typeof child.type !== "string" ||
      ["input", "select", "textarea", "button"].includes(child.type))
      ? child
      : null;
  const controlProps = control?.props as Record<string, any> | undefined,
    inputId = controlProps?.id ?? id;
  const describedBy =
    [
      controlProps?.["aria-describedby"],
      hint ? id + "-hint" : null,
      error ? id + "-error" : null,
    ]
      .filter(Boolean)
      .join(" ") || undefined;
  return (
    <div {...props} className={cx("studio-field", className)}>
      {control ? (
        <label htmlFor={inputId}>{label}</label>
      ) : (
        <div id={id + "-label"}>{label}</div>
      )}
      <div
        role={control ? undefined : "group"}
        aria-labelledby={control ? undefined : id + "-label"}
      >
        {control
          ? cloneElement(control as any, {
              id: inputId,
              "aria-describedby": describedBy,
              "aria-invalid": error ? true : controlProps?.["aria-invalid"],
            })
          : children}
      </div>
      {hint && (
        <small id={id + "-hint"} className="muted">
          {hint}
        </small>
      )}
      {error && (
        <small id={id + "-error"} role="alert">
          {error}
        </small>
      )}
    </div>
  );
}
export function EmptyState({ children, className, ...props }: Box) {
  return (
    <div {...props} className={cx("empty-state", className)}>
      {children}
    </div>
  );
}
export function Badge({ children, className, ...props }: Box) {
  return (
    <span {...props} className={cx("badge", className)}>
      {children}
    </span>
  );
}
const ModalParentContext = createContext<string | null>(null);
export function Modal({
  open,
  title,
  onClose,
  footer,
  wide,
  children,
  className,
}: Box & {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const parentId = useContext(ModalParentContext),
    dialog = useRef<HTMLDivElement>(null),
    backdrop = useRef<HTMLDivElement>(null),
    close = useRef(onClose);
  close.current = onClose;
  const id = useId();
  useEffect(() => {
    if (!open || !dialog.current || !backdrop.current) return;
    return modalStack(dialog.current.ownerDocument).register({
      id,
      parentId,
      dialog: dialog.current,
      backdrop: backdrop.current,
      close: () => close.current(),
    });
  }, [open, id, parentId]);
  if (!open) return null;
  return createPortal(
    <ModalParentContext.Provider value={id}>
      <UNSAFE_PortalProvider getContainer={() => dialog.current}>
        <div
          ref={backdrop}
          className="modal-overlay"
          onMouseDown={(event) => {
            if (
              event.target === event.currentTarget &&
              modalStack(event.currentTarget.ownerDocument).isTop(id)
            )
              close.current();
          }}
        >
          <div
            ref={dialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby={id}
            tabIndex={-1}
            className={cx("studio-modal", wide && "wide", className)}
          >
            <header>
              <h2 id={id}>{title}</h2>
              <Button
                variant="ghost"
                aria-label="Close dialog"
                onClick={onClose}
              >
                ×
              </Button>
            </header>
            <div className="modal-content">{children}</div>
            {footer && <footer>{footer}</footer>}
          </div>
        </div>
      </UNSAFE_PortalProvider>
    </ModalParentContext.Provider>,
    document.body,
  );
}
export function Evidence({
  label,
  data,
  as = "div",
  children,
  className,
  ...props
}: Omit<Box, "data"> & {
  label: string;
  data: unknown;
  as?: ElementType;
  [key: string]: any;
}) {
  const studio = useStudio(),
    id = useId();
  useEffect(
    () =>
      studio.evidence.register({
        id,
        label,
        data,
        route: studio.route,
        project: studio.project,
        user: String(studio.user?.id ?? ""),
      }),
    [
      studio.evidence,
      studio.route,
      studio.project,
      studio.user?.id,
      id,
      label,
      data,
    ],
  );
  return createElement(
    as,
    {
      ...props,
      className,
      "data-evidence-id": id,
      "data-evidence-label": label,
    },
    children,
  );
}
```

## `frontend/src/components/studio/workspace.tsx`

WorkspaceDisclosure(open/defaultOpen/onToggle) and InspectionWorkspace(inspector/label): shared secondary tools and master/detail.

SHA-256: `473db43d059158f9dc12dc973260325d103649e62962bf3a612258749b54e7f8`

```tsx
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
```

## `frontend/src/components/studio/uploads.tsx`

FileUploadField(files/onChange/accept/maxBytes/disabled) and UploadStatus(phase/progress/error/onCancel/onRetry).

SHA-256: `38689a60f4efb98732379a19d1d35b92fcef52d2ddb8ced9f92a3b31509c6da7`

```tsx
import { Button } from "./primitives";
import { FileUpload } from "../boardui/base/file-upload/file-upload";
import { useId, useState } from "react";
import {
  validateFiles,
  type UploadPhase,
  type UploadProgress,
} from "../../controllers/uploads";

export interface FileUploadFieldProps {
  label: string;
  accept?: string;
  multiple?: boolean;
  files: File[];
  onChange(files: File[]): void;
  maxBytes?: number;
  disabled?: boolean;
}
export function FileUploadField({
  label,
  accept,
  multiple,
  files,
  onChange,
  maxBytes,
  disabled,
}: FileUploadFieldProps) {
  const id = useId();
  const [errors, setErrors] = useState<string[]>([]);
  const select = (incoming: File[]) => {
    const checked = validateFiles(incoming, accept, maxBytes);
    setErrors(checked.filter((x) => x.error).map((x) => x.error));
    const valid = checked.filter((x) => !x.error).map((x) => x.file);
    onChange(
      multiple
        ? [...files, ...valid].filter(
            (file, index, all) =>
              all.findIndex(
                (x) =>
                  x.name === file.name &&
                  x.size === file.size &&
                  x.lastModified === file.lastModified,
              ) === index,
          )
        : valid.slice(0, 1),
    );
  };
  return (
    <div className="upload-field">
      <label htmlFor={id}>{label}</label>
      <FileUpload
        inputId={id}
        label={label}
        onFilesSelected={select}
        allowedExtensions={(accept || "")
          .split(",")
          .filter((x) => x.startsWith("."))
          .map((x) => x.slice(1))}
        accept={accept}
        multiple={multiple}
        maxBytes={maxBytes ?? Number.MAX_SAFE_INTEGER}
        disabled={disabled}
      />
      <p>
        {accept || "All file types"}
        {maxBytes !== undefined
          ? ` · up to ${maxBytes.toLocaleString()} bytes each`
          : ""}
      </p>
      {errors.map((error) => (
        <p role="alert" key={error}>
          {error}
        </p>
      ))}
      <ul>
        {files.map((file, i) => (
          <li key={`${file.name}-${file.lastModified}-${i}`}>
            {file.name} · {file.size.toLocaleString()} bytes{" "}
            <Button
              type="button"
              disabled={disabled}
              aria-label={`Remove ${file.name}`}
              onClick={() => onChange(files.filter((_, index) => index !== i))}
            >
              Remove
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
export interface UploadStatusProps {
  phase: UploadPhase | string;
  progress?: UploadProgress | number;
  error?: string;
  onCancel?(): void;
  onRetry?(): void;
}
export function UploadStatus({
  phase,
  progress,
  error,
  onCancel,
  onRetry,
}: UploadStatusProps) {
  const measured =
    typeof progress === "object" && progress.total && progress.total > 0
      ? Math.min(100, (progress.loaded / progress.total) * 100)
      : typeof progress === "number"
        ? progress
        : undefined;
  return (
    <div className="upload-status" aria-live="polite">
      <span>
        {phase === "importing"
          ? "Transferred · importing on server"
          : phase === "indexing"
            ? "Transferred · indexing on server"
            : phase}
      </span>
      {phase === "uploading" && (
        <progress aria-label="Bytes transferred" value={measured} max={100} />
      )}{" "}
      {typeof progress === "object" && (
        <span>
          {progress.loaded.toLocaleString()}
          {progress.total ? ` / ${progress.total.toLocaleString()}` : ""} bytes
        </span>
      )}
      {error && <p role="alert">{error}</p>}
      {onCancel && ["uploading", "importing", "indexing"].includes(phase) && (
        <Button type="button" onClick={onCancel}>
          Cancel
        </Button>
      )}
      {onRetry && ["failed", "cancelled"].includes(phase) && (
        <Button type="button" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}
```

## `frontend/src/components/boardui/base/buttons/button.tsx`

Button and ButtonLink: variant, size, iconOnly, leadingIcon, trailingIcon, children and native command/link props.

SHA-256: `e2235fa9d8d15a215a34935754e117393dcc3b5364873f75315c1c279b13b38a`

```tsx
import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  ComponentType,
  ReactNode,
  Ref,
} from "react";
import { cx, sortCx } from "@/components/boardui/utils/cx";
import { directionalIconClass } from "@/components/boardui/utils/directional-icon";

/**
 * Figma source: Board UI → Buttons (node 3656:13819).
 *
 * Variant matrix from Figma:
 *   Type     = Primary | Secondary | Ghost | Danger
 *   Size     = Medium  | Small | Xs
 *   State    = Default | Hover | Active | Disabled        (CSS pseudo)
 *   OnlyIcon = false   | true
 *
 * Sizing (1:1 with Figma):
 *
 *                       Medium                    Small                     Xs
 *   container          h=36, p=8,   r=10         h=32, px=8 py=6, r=8      h=24, px=8, r=4
 *   gap                 2px                       2px                      1.33px→1
 *   icon                20×20                     18×18                    14×14
 *   label wrapper       px=4                      px=2                     px=2
 *   text style          Body 1/Medium             Body 1/Medium            Caption 1/Semibold
 *   icon-only square    36×36 (content-derived)   32×32 (forced size)      24×24 (forced size)
 *
 * `xs` is the smallest tier — first needed for the calendar template's
 * event-details modal ("Join" / edit-icon buttons, node 3920:10954), which
 * scales every dimension down by the same ~0.667 factor from Figma; the
 * table above rounds those to clean pixel values rather than reproducing
 * the fractional source numbers.
 *
 * Icons are rendered by the component itself via the `leadingIcon` /
 * `trailingIcon` props so the consumer can't pass the wrong size. Pass
 * a Remix Icon component reference (`RiAddLine`, not `<RiAddLine />`).
 *
 * For icon-only buttons:
 *   <Button iconOnly leadingIcon={RiAddLine} aria-label="Add" />
 *
 * The HTML `type` prop is preserved; Figma's "Type" enum is renamed to
 * `variant` to avoid the clash.
 */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "medium" | "small" | "xs";

type IconComponent = ComponentType<{
  className?: string;
  "aria-hidden"?: boolean | "true" | "false";
}>;

export interface ButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  iconOnly?: boolean;
  leadingIcon?: IconComponent;
  trailingIcon?: IconComponent;
  children?: ReactNode;
  ref?: Ref<HTMLButtonElement>;
}

export interface ButtonLinkProps
  extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "children"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  iconOnly?: boolean;
  leadingIcon?: IconComponent;
  trailingIcon?: IconComponent;
  children?: ReactNode;
  ref?: Ref<HTMLAnchorElement>;
}

const styles = sortCx({
  base: [
    "inline-flex items-center justify-center gap-0.5 whitespace-nowrap overflow-hidden",
    "font-sans select-none cursor-pointer",
    "button-press-motion",
    "outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-border-focus-ring",
    "disabled:cursor-not-allowed aria-disabled:cursor-not-allowed",
  ].join(" "),

  // Base shape per size (used when label is present OR medium icon-only).
  size: {
    medium: "h-9 rounded-2lg p-2 text-body-medium",
    small:  "h-8 rounded-lg px-2 py-1.5 text-body-medium",
    xs:     "h-6 rounded-sm px-2 text-caption-1-semibold",
  },

  // Icon-only override:
  //   Medium → keep p-2; w expands from content (8+20+8 = 36) → square.
  //   Small  → Figma forces 32×32 even though 8+18+8=34, so we hard-set size-8
  //            and zero the padding; the inner flex centers the 18px icon.
  //   Xs     → forces 24×24, content-centered — used for the calendar
  //            template's edit-icon buttons (timezone/participants/reminder).
  iconOnlySize: {
    medium: "",                // base size already produces 36×36 with a 20px icon
    small:  "size-8 p-0",      // hard 32×32, content-centered
    xs:     "size-6 p-0",      // hard 24×24, content-centered
  },

  icon: {
    medium: "size-5 shrink-0",        // 20px
    small:  "size-[18px] shrink-0",   // 18px
    xs:     "size-3.5 shrink-0",      // 14px
  },

  label: {
    medium: "inline-flex items-center justify-center px-1 shrink-0",    // px=4
    small:  "inline-flex items-center justify-center px-0.5 shrink-0",  // px=2
    xs:     "inline-flex items-center justify-center px-0.5 shrink-0",  // px=2
  },

  variant: {
    primary: [
      "bg-button-primary text-text-white shadow-xs",
      "disabled:text-button-primary-disabled-foreground disabled:shadow-none",
      "aria-disabled:text-button-primary-disabled-foreground aria-disabled:shadow-none",
    ].join(" "),
    danger: [
      "bg-button-danger text-text-white shadow-xs",
      "disabled:text-foreground-disabled-danger disabled:shadow-none",
      "aria-disabled:text-foreground-disabled-danger aria-disabled:shadow-none",
    ].join(" "),
    secondary: [
      "bg-background-primary-default text-text-primary",
      "border border-border-button-default shadow-xs",
      "hover:bg-background-primary-hover  hover:border-border-button-hover",
      "active:bg-background-primary-active active:border-border-button-active",
      "disabled:bg-background-primary-disabled disabled:border-border-button-default disabled:text-text-tertiary disabled:shadow-none",
      "aria-disabled:bg-background-primary-disabled aria-disabled:border-border-button-default aria-disabled:text-text-tertiary aria-disabled:shadow-none",
    ].join(" "),
    ghost: [
      "bg-button-ghost-background text-button-ghost-foreground",
      "hover:bg-button-ghost-hover active:bg-button-ghost-active",
      "disabled:bg-button-ghost-disabled disabled:text-button-ghost-disabled-foreground disabled:shadow-none",
      "aria-disabled:bg-button-ghost-disabled aria-disabled:text-button-ghost-disabled-foreground aria-disabled:shadow-none",
    ].join(" "),
  },
});

export function Button({
  variant = "primary",
  size = "medium",
  iconOnly = false,
  leadingIcon: Leading,
  trailingIcon: Trailing,
  children,
  className,
  type = "button",
  ref,
  ...props
}: ButtonProps) {
  return (
    <button
      ref={ref}
      type={type}
      className={cx(
        styles.base,
        styles.size[size],
        styles.variant[variant],
        iconOnly && styles.iconOnlySize[size],
        className,
      )}
      {...props}
    >
      {Leading ? <Leading className={cx(styles.icon[size], directionalIconClass(Leading))} aria-hidden /> : null}
      {!iconOnly && children !== undefined && children !== null && (
        <span className={styles.label[size]}>{children}</span>
      )}
      {!iconOnly && Trailing ? (
        <Trailing className={cx(styles.icon[size], directionalIconClass(Trailing))} aria-hidden />
      ) : null}
    </button>
  );
}

/** Anchor counterpart to Button for navigational actions. */
export function ButtonLink({
  variant = "primary",
  size = "medium",
  iconOnly = false,
  leadingIcon: Leading,
  trailingIcon: Trailing,
  children,
  className,
  ref,
  ...props
}: ButtonLinkProps) {
  return (
    <a
      ref={ref}
      className={cx(
        styles.base,
        styles.size[size],
        styles.variant[variant],
        iconOnly && styles.iconOnlySize[size],
        className,
      )}
      {...props}
    >
      {Leading ? <Leading className={cx(styles.icon[size], directionalIconClass(Leading))} aria-hidden /> : null}
      {!iconOnly && children !== undefined && children !== null && (
        <span className={styles.label[size]}>{children}</span>
      )}
      {!iconOnly && Trailing ? (
        <Trailing className={cx(styles.icon[size], directionalIconClass(Trailing))} aria-hidden />
      ) : null}
    </a>
  );
}

/** Style maps, exported for advanced composition and the dev Design Tuner. */
export const buttonStyles = styles;
```

## `frontend/src/components/boardui/base/input/input.tsx`

BoardUI Input: actual accessible field/control anatomy, value/change and validity props.

SHA-256: `04fbb4b75490818cc20637a22841839af2393db3dd601bc3c89d1995734b0f9d`

```tsx
"use client";

import {
  createContext,
  useContext,
  type ComponentType,
  type ReactNode,
  type Ref,
} from "react";
import {
  Group as AriaGroup,
  Input as AriaInput,
  TextField as AriaTextField,
  useLocale,
} from "react-aria-components";
import type {
  InputProps as AriaInputProps,
  TextFieldProps as AriaTextFieldProps,
} from "react-aria-components";
import { Label } from "./label";
import { HintText } from "./hint-text";
import { cx, sortCx } from "@/components/boardui/utils/cx";

/**
 * Figma source: Board UI → Input (node 3665:1849).
 *
 * Architecture (mirrors the Untitled UI / React Aria pattern):
 *
 *   InputBase   — the styled field shell. Renders <AriaGroup> + adornments
 *                 + <AriaInput>. State (hover / focus / disabled / invalid)
 *                 arrives as render-prop booleans from Aria.
 *   TextField   — wraps <AriaTextField> and a TextFieldContext so size /
 *                 classes can flow from the composer down into InputBase
 *                 without prop drilling.
 *   Input       — opinionated composition: TextField → Label → InputBase
 *                 → HintText. The component most consumers use.
 *
 * Why React Aria:
 *   - Label ↔ input ↔ hint association via aria-labelledby / aria-describedby
 *   - aria-required, aria-invalid auto-applied
 *   - Form library / native validation integration
 *   - i18n, RTL, autofill edge cases handled
 *   - Render-prop state surfaces `isFocusWithin`, `isHovered`, `isDisabled`,
 *     `isInvalid`, `isRequired` — no CSS pseudo gymnastics
 *
 * Visuals stay 1:1 with Figma — see `styles/theme.css` for the tokens.
 */

export type InputSize = "medium" | "small";

type IconComponent = ComponentType<{
  className?: string;
  "aria-hidden"?: boolean | "true" | "false";
}>;

/* -------------------------------------------------------------------------- */
/*  TextFieldContext                                                           */
/* -------------------------------------------------------------------------- */

export interface TextFieldContextValue {
  size?: InputSize;
  fieldClassName?: string;
  inputClassName?: string;
}

/**
 * Shared by every field that wants Input's shell — `Textarea` reads the same
 * size and class overrides out of it, so a composed form stays consistent.
 */
export const TextFieldContext = createContext<TextFieldContextValue>({});

/* -------------------------------------------------------------------------- */
/*  TextField                                                                  */
/* -------------------------------------------------------------------------- */

export interface TextFieldProps
  extends Omit<AriaTextFieldProps, "className">,
    TextFieldContextValue {
  className?: string;
  children?: ReactNode | ((state: { isRequired: boolean; isInvalid: boolean; isDisabled: boolean; isReadOnly: boolean }) => ReactNode);
}

export function TextField({
  size = "medium",
  fieldClassName,
  inputClassName,
  className,
  children,
  ...props
}: TextFieldProps) {
  return (
    <TextFieldContext.Provider value={{ size, fieldClassName, inputClassName }}>
      <AriaTextField
        {...props}
        data-input-size={size}
        className={cx(
          "group flex h-max w-full flex-col items-start gap-1",
          className,
        )}
      >
        {children as never /* RAC accepts render-prop children */}
      </AriaTextField>
    </TextFieldContext.Provider>
  );
}

TextField.displayName = "TextField";

/* -------------------------------------------------------------------------- */
/*  InputBase                                                                  */
/* -------------------------------------------------------------------------- */

export interface InputBaseProps extends Omit<AriaInputProps, "size" | "className"> {
  size?: InputSize;
  className?: string;
  leadingIcon?: IconComponent;
  trailingIcon?: IconComponent;
  /** Custom element rendered in the leading slot (Phone basic uses this). */
  leadingAddon?: ReactNode;
  /** Class for the field shell. */
  fieldClassName?: string;
  /** Ref to the <input> element. */
  ref?: Ref<HTMLInputElement>;
  /** Ref to the field shell wrapper. */
  groupRef?: Ref<HTMLDivElement>;
}

const inputStyles = sortCx({
  field: [
    "relative flex w-full items-center",
    "rounded-2lg",
    "bg-background-tertiary-default text-foreground-icon-tertiary",
    "ring-2 ring-inset ring-transparent",
    "transition-[background-color,box-shadow,color] duration-[var(--input-transition-ms)] ease",
  ].join(" "),

  fieldSize: {
    medium: "p-2",                // 8px all sides → h auto = 36
    small:  "h-8 px-1.5 py-2",    // 32 / 6 / 8
  },

  // When a leadingAddon is present (Phone basic): tighten left padding.
  fieldWithAddonSize: {
    medium: "h-9 ps-1 pe-2 py-2", // 36 / 4 / 8 / 8
    small:  "h-8 ps-1 pe-1.5 py-2",
  },

  content: "flex w-full items-center gap-2 min-w-0",
  leftSection: "flex flex-1 items-center gap-0.5 min-w-0",

  input: [
    "min-w-0 flex-1 bg-transparent border-0 outline-none p-0 m-0",
    "font-sans text-body-regular text-text-primary",
    "placeholder:text-text-tertiary",
    "focus:placeholder:text-text-primary",
    "disabled:text-input-disabled-text disabled:placeholder:text-input-disabled-text",
    "disabled:cursor-not-allowed",
    "aria-invalid:placeholder:text-text-error-placeholder",
  ].join(" "),

  icon: "size-5 shrink-0",
});

export function InputBase({
  size: sizeProp,
  leadingIcon: Leading,
  trailingIcon: Trailing,
  leadingAddon,
  fieldClassName,
  className,
  ref,
  groupRef,
  ...inputProps
}: InputBaseProps) {
  const { direction } = useLocale();
  const ctx = useContext(TextFieldContext);
  const size: InputSize = sizeProp ?? ctx.size ?? "medium";
  const hasAddon = leadingAddon !== undefined && leadingAddon !== null;

  return (
    <AriaGroup
      ref={groupRef}
      className={({ isFocusWithin, isHovered, isDisabled, isInvalid }) =>
        cx(
          inputStyles.field,
          hasAddon
            ? inputStyles.fieldWithAddonSize[size]
            : inputStyles.fieldSize[size],
          // Hover: idle, no focus, no disabled, no invalid
          isHovered &&
            !isFocusWithin &&
            !isDisabled &&
            !isInvalid &&
            "ring-border-button-hover",
          // Focus wins over hover
          isFocusWithin &&
            !isDisabled &&
            !isInvalid &&
            "ring-border-button-active",
          // Disabled
          isDisabled &&
            "bg-input-disabled-background text-input-disabled-foreground",
          // Invalid
          isInvalid && "bg-background-tertiary-error text-foreground-icon-error",
          ctx.fieldClassName,
          fieldClassName,
        )
      }
    >
      <div className={inputStyles.content}>
        <div className={inputStyles.leftSection}>
          {hasAddon ? (
            leadingAddon
          ) : Leading ? (
            <Leading className={inputStyles.icon} aria-hidden />
          ) : null}
          <AriaInput
            ref={ref}
            {...inputProps}
            // Align to the field's start even when email/URL values use LTR.
            className={cx(inputStyles.input, direction === "rtl" ? "pr-1 text-right" : "pl-1 text-left", ctx.inputClassName, className)}
          />
        </div>
        {Trailing ? (
          <Trailing className={inputStyles.icon} aria-hidden />
        ) : null}
      </div>
    </AriaGroup>
  );
}

InputBase.displayName = "InputBase";

/* -------------------------------------------------------------------------- */
/*  Input (composed)                                                           */
/* -------------------------------------------------------------------------- */

export interface InputProps
  extends Omit<TextFieldProps, "children">,
    Pick<
      InputBaseProps,
      | "leadingIcon"
      | "trailingIcon"
      | "leadingAddon"
      | "fieldClassName"
      | "groupRef"
      | "ref"
    > {
  label?: ReactNode;
  hint?: ReactNode;
  /** Show an info icon next to the label. Replace with tooltip when Tooltip lands. */
  tooltip?: boolean | string;
  placeholder?: string;
  /** Direction of the editable value, independent of the label and adornments.
   * Email, URL and telephone inputs default to LTR inside RTL forms. */
  inputDir?: "ltr" | "rtl" | "auto";
}

export function Input({
  label,
  hint,
  tooltip,
  placeholder,
  inputDir,
  leadingIcon,
  trailingIcon,
  leadingAddon,
  fieldClassName,
  ref,
  groupRef,
  className,
  ...textFieldProps
}: InputProps) {
  return (
    <TextField
      {...textFieldProps}
      className={className}
      // Don't clobber an explicit aria-label; fall back to the placeholder
      // only for unlabelled fields that don't provide one.
      aria-label={
        textFieldProps["aria-label"] ??
        (!label && typeof placeholder === "string" ? placeholder : undefined)
      }
    >
      {({ isRequired, isInvalid }) => (
        <>
          {label && (
            <Label
              isRequired={isRequired}
              isInvalid={isInvalid}
              tooltip={tooltip}
            >
              {label}
            </Label>
          )}
          <InputBase
            dir={inputDir ?? (["email", "url", "tel"].includes(textFieldProps.type ?? "") ? "ltr" : undefined)}
            ref={ref}
            groupRef={groupRef}
            placeholder={placeholder}
            leadingIcon={leadingIcon}
            trailingIcon={trailingIcon}
            leadingAddon={leadingAddon}
            fieldClassName={fieldClassName}
          />
          {hint && <HintText isInvalid={isInvalid}>{hint}</HintText>}
        </>
      )}
    </TextField>
  );
}

Input.displayName = "Input";
```

## `frontend/src/components/boardui/base/input/label.tsx`

BoardUI Label: shared form labels.

SHA-256: `890ae13df3f21388257d91545f533332642da687cbfc7dd10faa1805ced47fb8`

```tsx
"use client";

import type { ReactNode, Ref } from "react";
import { RiInformationFill } from "@remixicon/react";
import type { LabelProps as AriaLabelProps } from "react-aria-components";
import { Label as AriaLabel } from "react-aria-components";
import { cx } from "@/components/boardui/utils/cx";

/**
 * Field label. Wraps `react-aria-components`' Label so the parent `TextField`
 * automatically wires `htmlFor` / `aria-labelledby`.
 *
 * Visuals come from Figma:
 *   - Inter Medium 14/20, color text/primary
 *   - 2px gap between label + required asterisk + info icon
 *   - asterisk is text/error-primary (red/500)
 *   - info icon is 16×16 in foreground/icon/quaternary
 *
 * Pass `isRequired` to show the asterisk. Pass `tooltip` to show the info
 * icon (currently a static glyph — when the Tooltip component lands it'll
 * wire up here).
 */

export interface LabelProps extends AriaLabelProps {
  children: ReactNode;
  isRequired?: boolean;
  /** Reserved for invalid-aware styling (asterisk color, etc.) once we need it. */
  isInvalid?: boolean;
  /** Show the info icon next to the label. */
  tooltip?: boolean | string;
  ref?: Ref<HTMLLabelElement>;
}

export function Label({
  isRequired = false,
  isInvalid: _isInvalid,
  tooltip,
  className,
  children,
  ...props
}: LabelProps) {
  void _isInvalid;
  return (
    <AriaLabel
      data-label="true"
      {...props}
      className={cx(
        "flex cursor-default items-center gap-0.5",
        "text-body-medium text-text-primary",
        className,
      )}
    >
      {children}
      {isRequired && (
        <span
          aria-hidden="true"
          className="text-body-medium text-text-error-primary"
        >
          *
        </span>
      )}
      {tooltip && (
        <RiInformationFill
          className="size-4 shrink-0 text-foreground-icon-quaternary"
          aria-hidden
        />
      )}
    </AriaLabel>
  );
}
```

## `frontend/src/components/boardui/base/input/hint-text.tsx`

BoardUI HintText: shared form descriptions/errors.

SHA-256: `7255614de7be5cd81732b4ead2b80307cdb71700ce3687f209128366f98d0cc2`

```tsx
"use client";

import type { ReactNode, Ref } from "react";
import type { TextProps as AriaTextProps } from "react-aria-components";
import { Text as AriaText } from "react-aria-components";
import { cx } from "@/components/boardui/utils/cx";

/**
 * Caption text rendered below a field. Wraps `react-aria-components`' Text
 * so the parent `TextField` automatically wires `aria-describedby` (when
 * `slot="description"`) or `aria-errormessage` (when `slot="errorMessage"`).
 *
 * Visuals come from Figma:
 *   - Inter Medium 12/16, letter-spacing 0.15px
 *   - color text/secondary (default) or text/error-primary (invalid)
 *   - 1px top padding to align with the field's bottom edge
 */

export interface HintTextProps extends AriaTextProps {
  children: ReactNode;
  isInvalid?: boolean;
  ref?: Ref<HTMLElement>;
}

export function HintText({
  isInvalid = false,
  className,
  ...props
}: HintTextProps) {
  return (
    <AriaText
      slot={isInvalid ? "errorMessage" : "description"}
      {...props}
      className={cx(
        "pt-px text-caption-1-medium text-text-secondary",
        isInvalid && "text-text-error-primary",
        className,
      )}
    />
  );
}
```

## `frontend/src/components/boardui/base/select/select.tsx`

BoardUI Select and SelectItem: selectedKey/onSelectionChange, trigger/listbox and items.

SHA-256: `38f9518a9f983a900763e0f7ed83fa4a1659bc261ac4692793b0f6b60545a499`

```tsx
"use client";

import { createContext, useContext, useRef, useState } from "react";
import type { ReactNode, Ref } from "react";
import {
  Button as AriaButton,
  ListBox as AriaListBox,
  ListBoxItem as AriaListBoxItem,
  Popover as AriaPopover,
  Select as AriaSelect,
  SelectValue as AriaSelectValue,
} from "react-aria-components";
import type {
  ListBoxItemProps as AriaListBoxItemProps,
  SelectProps as AriaSelectProps,
  SelectValueRenderProps as AriaSelectValueRenderProps,
} from "react-aria-components";
import {
  MENU_ITEM,
  MENU_ITEM_ACTIVE,
  MENU_ITEMS_CONTAINER,
  MENU_POPOVER_SURFACE,
  MENU_POPOVER_WIDTH,
} from "@/components/boardui/base/dropdown/menu-styles";
import { ChevronDownSmall } from "@/components/boardui/foundations/icons/chevrons";
import { cx } from "@/components/boardui/utils/cx";
import { useDismissOnOutsidePress, useTriggerToggle } from "@/components/boardui/utils/use-dismiss-on-outside-press";

/**
 * Figma source: Board UI → dashboard 1 dropdown triggers ("All prices" filter
 * node 3731:3208, table status dropdown node 3731:3264).
 *
 * Select with a button trigger styled 1:1 with Figma:
 *   trigger  bg color/white, 1px border/button/default, shadow/xs,
 *            px 10 py 8, radius/2lg (10px), gap 6,
 *            Body 1/Medium text color/neutral/950,
 *            16px chevron (custom stroke glyph) in text/secondary
 *
 * The open popover/listbox shares the BoardUI menu recipe with Dropdown:
 * radius/2xl panel, p 10, shadow/dropdown, 150ms fade/scale/blur motion,
 * and radius/2lg rows with the same hover/selected surface.
 *
 * Item content is free-form: pass a `StatusDot` + text for status selects.
 */

export type SelectSize = "sm" | "md";

const SelectSizeContext = createContext<SelectSize>("md");

export interface SelectProps<T extends object>
  extends Omit<AriaSelectProps<T>, "children"> {
  /** Trigger width. Defaults to hug content; the menu uses the shared 266px width. */
  className?: string;
  triggerClassName?: string;
  /** Classes for the open popover (e.g. constrain its width). */
  popoverClassName?: string;
  /** `md` (default) or `sm` for compact/dense contexts (e.g. compact tables). */
  size?: SelectSize;
  children: ReactNode;
  items?: Iterable<T>;
  /** Customise the trigger's rendered value (e.g. a compact flag + dial code).
   *  Falls back to the selected item's own content when omitted. */
  renderValue?: ReactNode | ((values: AriaSelectValueRenderProps<T>) => ReactNode);
  ref?: Ref<HTMLDivElement>;
}

export function Select<T extends object>({
  className,
  triggerClassName,
  popoverClassName,
  size = "md",
  children,
  items,
  renderValue,
  ref,
  ...props
}: SelectProps<T>) {
  // The popover is `isNonModal`: react-aria's modal scroll lock puts
  // `overflow: hidden` on <html>, which — with this app's `h-full` root —
  // collapses the page scroll position and visibly yanks the sticky
  // sidebar upward every time a select opens. Non-modal skips the lock,
  // but also disables react-aria's outside-press dismissal (`isDismissable`
  // is hard-coupled to `!isNonModal` in usePopover), so open state is
  // controlled here and dismissal restored manually — the same fix as the
  // date-picker family (see utils/use-dismiss-on-outside-press.ts).
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  useDismissOnOutsidePress(isOpen, () => setIsOpen(false), [triggerRef, popoverRef]);
  // Pressing the trigger while open closes the popover instead of reopening
  const allowOpenChange = useTriggerToggle(isOpen, triggerRef);

  return (
    <AriaSelect
      ref={ref}
      {...props}
      isOpen={isOpen}
      onOpenChange={(o) => allowOpenChange(o) && setIsOpen(o)}
      className={cx("group flex flex-col", className)}
    >
      {({ isOpen }) => (
        <>
          <AriaButton
            ref={triggerRef}
            className={cx(
              "flex w-full cursor-pointer items-center justify-between rounded-2lg",
              "border border-border-button-default bg-background-primary-default shadow-xs",
              "text-text-primary",
              "transition-[background-color,border-color,box-shadow,padding,font-size] duration-200 ease",
              "hover:bg-background-primary-hover hover:border-border-button-hover",
              "outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-border-focus-ring",
              "disabled:cursor-not-allowed disabled:bg-background-primary-disabled disabled:text-text-tertiary disabled:shadow-none",
              size === "sm"
                ? "gap-1 px-[7px] py-1 text-body-2-medium"
                : "gap-1.5 px-2.5 py-2 text-body-medium",
              triggerClassName,
            )}
          >
            <AriaSelectValue
              className={cx(
                "flex min-w-0 items-center truncate",
                size === "sm" ? "gap-1" : "gap-[5px]",
              )}
            >
              {renderValue as never /* RAC accepts node or render-prop */}
            </AriaSelectValue>
            <ChevronDownSmall
              className={cx(
                "shrink-0 text-text-secondary transition-transform duration-200 ease",
                size === "sm" ? "size-3.5" : "size-4",
                isOpen && "rotate-180",
              )}
            />
          </AriaButton>
          <AriaPopover
            ref={popoverRef}
            isNonModal
            offset={4}
            className={cx(
              MENU_POPOVER_WIDTH,
              MENU_POPOVER_SURFACE,
              // Listbox rows already space themselves 4px apart, so the
              // surface sits a touch tighter than the action-menu Dropdown.
              "p-2",
              popoverClassName,
            )}
          >
            <AriaListBox
              items={items}
              className={cx(MENU_ITEMS_CONTAINER, "max-h-[240px] overflow-auto")}
            >
              <SelectSizeContext.Provider value={size}>{children}</SelectSizeContext.Provider>
            </AriaListBox>
          </AriaPopover>
        </>
      )}
    </AriaSelect>
  );
}

export interface SelectItemProps extends Omit<AriaListBoxItemProps, "children"> {
  children?: ReactNode;
}

export function SelectItem({ className, children, ...props }: SelectItemProps) {
  const size = useContext(SelectSizeContext);
  return (
    <AriaListBoxItem
      {...props}
      className={(state) =>
        cx(
          MENU_ITEM,
          size === "sm" ? "px-2 py-1.5 text-body-2-medium" : "text-body-medium",
          (state.isFocused || state.isSelected) && MENU_ITEM_ACTIVE,
          state.isDisabled && "cursor-not-allowed text-text-disabled",
          typeof className === "function" ? className(state) : className,
        )
      }
    >
      {children}
    </AriaListBoxItem>
  );
}
```

## `frontend/src/components/boardui/base/checkbox/checkbox.tsx`

BoardUI Checkbox: isSelected/onChange, size and React Aria keyboard behavior.

SHA-256: `9d7a3fe519b73d11ace9b0753701a24f349211c488da2ba4bf9d9f12ab34a736`

```tsx
"use client";

import type { ReactNode, Ref } from "react";
import { Checkbox as AriaCheckbox } from "react-aria-components";
import type { CheckboxProps as AriaCheckboxProps } from "react-aria-components";
import { cx } from "@/components/boardui/utils/cx";
import { CheckboxGlyph, checkboxSizes } from "./checkbox-glyph";
import type { CheckboxSize } from "./checkbox-glyph";

/**
 * Figma source: Board UI → Checkbox (node 3699:2557 family; used in
 * dashboard 1 table, nodes 3731:3258 etc.).
 *
 * radius/sm (4px). Two sizes:
 *   md  16×16, label Body 1/Medium (14)
 *   sm  14×14, label Body 2/Medium (13)
 *
 * States (per Figma):
 *   default        bg background/primary/default, 1px border/checkbox/default,
 *                  shadow/xs
 *   hover          border darkens to neutral/400
 *   checked /      Button/Primary gradient (blue/500 → blue/600) with the
 *   indeterminate  checkbox inner highlight (see --shadow-checkbox-selected),
 *                  white 2px rounded stroke glyph; on hover the gradient
 *                  lightens to blue/400 → blue/500
 *
 * Built on react-aria Checkbox: keyboard toggling, aria-checked="mixed" for
 * indeterminate, label association when `children` is passed.
 */

export interface CheckboxProps extends Omit<AriaCheckboxProps, "children"> {
  children?: ReactNode;
  size?: CheckboxSize;
  ref?: Ref<HTMLLabelElement>;
}

export function Checkbox({ className, children, size = "md", ref, ...props }: CheckboxProps) {
  const s = checkboxSizes[size];

  return (
    <AriaCheckbox
      ref={ref}
      {...props}
      className={(state) =>
        cx(
          "group inline-flex items-center select-none",
          s.gap,
          state.isDisabled ? "cursor-not-allowed" : "cursor-pointer",
          typeof className === "function" ? className(state) : className,
        )
      }
    >
      {(state) => (
        <>
          <CheckboxGlyph state={state} size={size} />
          {children !== undefined && children !== null && (
            <span className={cx(s.label, "text-text-primary")}>{children}</span>
          )}
        </>
      )}
    </AriaCheckbox>
  );
}
```

## `frontend/src/components/boardui/base/checkbox/checkbox-glyph.tsx`

CheckboxGlyph: checked/indeterminate visual anatomy.

SHA-256: `828055f3abe64bc4830896f9931c488769c04ab3d77adada14448c76e47ca6f7`

```tsx
import { cx } from "@/components/boardui/utils/cx";

export type CheckboxSize = "sm" | "md";

export const checkboxSizes: Record<
  CheckboxSize,
  { box: string; glyph: string; label: string; gap: string }
> = {
  md: { box: "size-4", glyph: "size-4", label: "text-body-medium", gap: "gap-2" },
  sm: { box: "size-3.5", glyph: "size-3.5", label: "text-body-2-medium", gap: "gap-1.5" },
};

export interface CheckboxGlyphState {
  isSelected: boolean;
  isIndeterminate: boolean;
  isFocusVisible: boolean;
  isDisabled: boolean;
  isHovered: boolean;
}

/**
 * The 16px (or 14px) checkbox box + tick/indeterminate glyph. Shared by the
 * standalone Checkbox and the CheckboxCard so their visuals stay identical.
 */
export function CheckboxGlyph({
  state,
  size = "md",
}: {
  state: CheckboxGlyphState;
  size?: CheckboxSize;
}) {
  const { isSelected, isIndeterminate, isFocusVisible, isDisabled, isHovered } = state;
  const s = checkboxSizes[size];
  const isMarked = isSelected || isIndeterminate;
  const hover = isHovered && !isDisabled;

  return (
    <span
      aria-hidden
      className={cx(
        "flex shrink-0 items-center justify-center rounded-sm",
        "transition-[background-color,border-color,box-shadow] duration-150 ease",
        s.box,
        isMarked
          ? cx(
              "bg-linear-to-b shadow-checkbox-selected",
              hover ? "from-accent-400 to-accent-500" : "from-accent-500 to-accent-600",
            )
          : cx(
              "border bg-background-primary-default shadow-xs",
              hover ? "border-border-checkbox-hover" : "border-border-checkbox-default",
            ),
        isDisabled && "opacity-50",
        isFocusVisible && "ring-2 ring-border-focus-ring ring-offset-2",
      )}
    >
      <svg viewBox="0 0 16 16" fill="none" className={s.glyph}>
        {isIndeterminate ? (
          <path d="M4.5 8H8H11.5" stroke="white" strokeWidth="2" strokeLinecap="round" />
        ) : isSelected ? (
          <path
            d="M4 7.7002L6.64645 10.3466C6.84171 10.5419 7.15829 10.5419 7.35355 10.3466L12 5.7002"
            stroke="white"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            pathLength={1}
            className="animate-check-draw"
          />
        ) : null}
      </svg>
    </span>
  );
}
```

## `frontend/src/components/boardui/base/textarea/textarea.tsx`

BoardUI Textarea: multiline value/change and field controls.

SHA-256: `640ea5a5099b30c7ab775cbc801f87ba820869f8f30fc8c45805d0eef3473da1`

```tsx
"use client";

import {
  useContext,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type Ref,
} from "react";
import { Group as AriaGroup, TextArea as AriaTextArea } from "react-aria-components";
import type { TextAreaProps as AriaTextAreaProps } from "react-aria-components";
import { HintText } from "@/components/boardui/base/input/hint-text";
import { Label } from "@/components/boardui/base/input/label";
import {
  TextField,
  TextFieldContext,
  type InputSize,
  type TextFieldProps,
} from "@/components/boardui/base/input/input";
import { cx, sortCx } from "@/components/boardui/utils/cx";

/**
 * Multiline sibling of `Input` — same React Aria plumbing, same field shell,
 * same tokens. It reuses `Input`'s `TextField`, `Label` and `HintText`, so a
 * textarea sitting next to an input in a form lines up to the pixel.
 *
 * Architecture (mirrors input.tsx):
 *
 *   TextareaBase — the styled shell. Renders <AriaGroup> + <AriaTextArea>.
 *                  State (hover / focus / disabled / invalid) arrives as
 *                  render-prop booleans from Aria.
 *   Textarea     — opinionated composition: TextField → Label → TextareaBase
 *                  → HintText (+ optional character counter). The component
 *                  most consumers use.
 *
 * What a textarea adds over the single-line field:
 *   - `rows` sets the resting height (3 lines by default)
 *   - `autoResize` grows the field a line at a time up to `maxRows`, then
 *     scrolls — the pattern the composer prompt uses
 *   - `resize` exposes the native grab handle, off when `autoResize` is on
 *   - `showCount` pairs with `maxLength` for the counter settings pages want
 */

type TextareaSize = InputSize;

export type TextareaResize = "none" | "vertical";

/* -------------------------------------------------------------------------- */
/*  TextareaBase                                                               */
/* -------------------------------------------------------------------------- */

export interface TextareaBaseProps
  extends Omit<AriaTextAreaProps, "size" | "className" | "rows"> {
  size?: TextareaSize;
  className?: string;
  /** Resting height, in lines. Also the floor when `autoResize` is on. */
  rows?: number;
  /** Grow with the content instead of scrolling at `rows`. */
  autoResize?: boolean;
  /** Ceiling for `autoResize`, in lines. Past it the field scrolls. */
  maxRows?: number;
  /** Native resize handle. Ignored (forced `none`) while `autoResize` is on. */
  resize?: TextareaResize;
  /** Class for the field shell. */
  fieldClassName?: string;
  /** Ref to the <textarea> element. */
  ref?: Ref<HTMLTextAreaElement>;
  /** Ref to the field shell wrapper. */
  groupRef?: Ref<HTMLDivElement>;
}

const textareaStyles = sortCx({
  field: [
    "relative flex w-full flex-col",
    "rounded-2lg",
    "bg-background-tertiary-default text-foreground-icon-tertiary",
    "ring-2 ring-inset ring-transparent",
    "transition-[background-color,box-shadow,color] duration-[var(--input-transition-ms)] ease",
  ].join(" "),

  // Matches Input's insets, so a stacked input and textarea share one edge:
  // 8px shell + 4px on the control = 12px to the text (10px at `small`).
  fieldSize: {
    medium: "p-2",
    small: "px-1.5 py-2",
  },

  textarea: [
    "block w-full min-w-0 bg-transparent border-0 outline-none m-0 p-0 px-1",
    "font-sans text-body-regular text-text-primary",
    "placeholder:text-text-tertiary",
    "focus:placeholder:text-text-primary",
    "disabled:text-input-disabled-text disabled:placeholder:text-input-disabled-text",
    "disabled:cursor-not-allowed",
    "aria-invalid:placeholder:text-text-error-placeholder",
  ].join(" "),

  resize: {
    none: "resize-none",
    vertical: "resize-y",
  },

  footer: "flex w-full items-start justify-between gap-3",
  count: "ms-auto shrink-0 pt-px text-caption-1-medium text-text-tertiary tabular-nums",
});

/** Fallback line box for `text-body-regular` (14/20) before styles resolve. */
const FALLBACK_LINE_HEIGHT = 20;

export function TextareaBase({
  size: sizeProp,
  rows = 3,
  autoResize = false,
  maxRows,
  resize = "vertical",
  fieldClassName,
  className,
  ref,
  groupRef,
  onInput,
  ...textareaProps
}: TextareaBaseProps) {
  const ctx = useContext(TextFieldContext);
  const size: TextareaSize = sizeProp ?? ctx.size ?? "medium";
  const innerRef = useRef<HTMLTextAreaElement | null>(null);

  const attachRef = (node: HTMLTextAreaElement | null) => {
    innerRef.current = node;
    if (typeof ref === "function") ref(node);
    else if (ref) (ref as { current: HTMLTextAreaElement | null }).current = node;
  };

  // Measure from zero so the field shrinks back as well as grows. Reading the
  // computed line box keeps the floor and ceiling honest when a consumer
  // overrides the type scale through `className`.
  const fit = () => {
    const field = innerRef.current;
    if (!field) return;
    if (!autoResize) {
      field.style.height = "";
      field.style.maxHeight = "";
      field.style.overflowY = "";
      return;
    }
    const line =
      parseFloat(window.getComputedStyle(field).lineHeight) || FALLBACK_LINE_HEIGHT;
    const ceiling = maxRows ? maxRows * line : Infinity;
    field.style.height = "0px";
    const next = Math.max(field.scrollHeight, rows * line);
    field.style.height = `${Math.min(next, ceiling)}px`;
    field.style.maxHeight = maxRows ? `${ceiling}px` : "";
    field.style.overflowY = next > ceiling ? "auto" : "hidden";
  };

  // No dependency list: typing on an uncontrolled field re-renders nothing,
  // so `onInput` handles keystrokes and this catches every other reason the
  // value moved (a controlled parent, a form reset, an autofill).
  useLayoutEffect(fit);

  return (
    <AriaGroup
      ref={groupRef}
      className={({ isFocusWithin, isHovered, isDisabled, isInvalid }) =>
        cx(
          textareaStyles.field,
          textareaStyles.fieldSize[size],
          // Hover: idle, no focus, no disabled, no invalid
          isHovered &&
            !isFocusWithin &&
            !isDisabled &&
            !isInvalid &&
            "ring-border-button-hover",
          // Focus wins over hover
          isFocusWithin &&
            !isDisabled &&
            !isInvalid &&
            "ring-border-button-active",
          // Disabled
          isDisabled &&
            "bg-input-disabled-background text-input-disabled-foreground",
          // Invalid
          isInvalid && "bg-background-tertiary-error text-foreground-icon-error",
          ctx.fieldClassName,
          fieldClassName,
        )
      }
    >
      <AriaTextArea
        ref={attachRef}
        rows={rows}
        {...textareaProps}
        onInput={(event) => {
          fit();
          onInput?.(event);
        }}
        className={cx(
          textareaStyles.textarea,
          textareaStyles.resize[autoResize ? "none" : resize],
          ctx.inputClassName,
          className,
        )}
      />
    </AriaGroup>
  );
}

TextareaBase.displayName = "TextareaBase";

/* -------------------------------------------------------------------------- */
/*  Textarea (composed)                                                        */
/* -------------------------------------------------------------------------- */

export interface TextareaProps
  extends Omit<TextFieldProps, "children">,
    Pick<
      TextareaBaseProps,
      | "rows"
      | "autoResize"
      | "maxRows"
      | "resize"
      | "fieldClassName"
      | "groupRef"
      | "ref"
    > {
  label?: ReactNode;
  hint?: ReactNode;
  /** Show an info icon next to the label. Replace with tooltip when Tooltip lands. */
  tooltip?: boolean | string;
  placeholder?: string;
  /** Hard character limit, enforced by the browser. */
  maxLength?: number;
  /** Show the character counter under the field (`12/280` with `maxLength`). */
  showCount?: boolean;
}

export function Textarea({
  label,
  hint,
  tooltip,
  placeholder,
  rows,
  autoResize,
  maxRows,
  resize,
  maxLength,
  showCount = false,
  fieldClassName,
  ref,
  groupRef,
  className,
  value,
  defaultValue,
  onChange,
  ...textFieldProps
}: TextareaProps) {
  // The counter needs the value, and React Aria's render props don't carry it.
  // Controlled fields read straight from the prop; uncontrolled ones keep the
  // length here so a keystroke re-renders the counter and nothing else.
  const [typedLength, setTypedLength] = useState(() => (defaultValue ?? "").length);
  const count = value !== undefined ? value.length : typedLength;

  return (
    <TextField
      {...textFieldProps}
      value={value}
      defaultValue={defaultValue}
      onChange={(next) => {
        if (showCount && value === undefined) setTypedLength(next.length);
        onChange?.(next);
      }}
      className={className}
      // Don't clobber an explicit aria-label; fall back to the placeholder
      // only for unlabelled fields that don't provide one.
      aria-label={
        textFieldProps["aria-label"] ??
        (!label && typeof placeholder === "string" ? placeholder : undefined)
      }
    >
      {({ isRequired, isInvalid }) => (
        <>
          {label && (
            <Label
              isRequired={isRequired}
              isInvalid={isInvalid}
              tooltip={tooltip}
            >
              {label}
            </Label>
          )}
          <TextareaBase
            ref={ref}
            groupRef={groupRef}
            placeholder={placeholder}
            rows={rows}
            autoResize={autoResize}
            maxRows={maxRows}
            resize={resize}
            maxLength={maxLength}
            fieldClassName={fieldClassName}
          />
          {(hint || showCount) && (
            <div className={textareaStyles.footer}>
              {hint && <HintText isInvalid={isInvalid}>{hint}</HintText>}
              {showCount && (
                <span
                  className={cx(
                    textareaStyles.count,
                    isInvalid && "text-text-error-primary",
                  )}
                >
                  {maxLength ? `${count}/${maxLength}` : count}
                </span>
              )}
            </div>
          )}
        </>
      )}
    </TextField>
  );
}

Textarea.displayName = "Textarea";
```

## `frontend/src/components/boardui/utils/cx.ts`

Class merger with BoardUI composite typography recognition.

SHA-256: `db35ea5afa8b2e0fd51319bc49288c4df7c98ec53440b43903636a32e3319d9d`

```ts
import { extendTailwindMerge } from "tailwind-merge";

/**
 * Text-style classes from styles/typography.css.
 *
 * IMPORTANT: every text-* utility we define via @theme (e.g. `text-body-medium`,
 * `text-title-1-semibold`) must be listed here. Otherwise tailwind-merge — which
 * has no view of our Tailwind theme — treats them as text-color utilities and
 * silently drops them when they appear in the same className as a real color
 * (`text-foreground-full`, `text-text-primary`, etc).
 *
 * If you add or rename a text style in typography.css, mirror the change here.
 */
const TEXT_FAMILIES = [
  "large-title",
  "display-1",
  "display-2",
  "display-3",
  "display-4",
  "title-1",
  "title-2",
  "title-3",
  "headline",
  "body",
  "body-2",
  "caption-1",
  "caption-2",
] as const;

const TEXT_WEIGHTS = ["regular", "medium", "semibold", "bold"] as const;

const TEXT_STYLE_SUFFIXES = TEXT_FAMILIES.flatMap((family) =>
  TEXT_WEIGHTS.map((weight) => `${family}-${weight}`),
);

const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: TEXT_STYLE_SUFFIXES }],
    },
  },
});

/**
 * Merge Tailwind classes safely. Last-write-wins on conflicting utilities.
 */
export const cx = twMerge;

/**
 * Identity helper that gives the Tailwind IntelliSense extension a hook for
 * sorting classes inside style objects (the extension doesn't sort inside
 * plain object literals otherwise).
 */
export function sortCx<
  T extends Record<
    string,
    string | number | Record<string, string | number | Record<string, string | number>>
  >,
>(classes: T): T {
  return classes;
}
```

## `frontend/src/features/data/common.tsx`

Complete actual feature-shared Input, Text, Select, Check, Records, Pager, Messages, original DetailInspector and colocated utilities. No excerpts omit implementation.

SHA-256: `507f4571c543603fd047f2c328e927aec770e1949db030c451134570d941db2a`

```tsx
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useStudio } from "../../app/StudioContext";
import { Button, Field, Evidence } from "../../components/studio/primitives";
export const enc = encodeURIComponent;
export const lines = (v: string) =>
  v
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
export const pct = (v: unknown) =>
  typeof v === "number" && Number.isFinite(v)
    ? `${(v * 100).toFixed(1)}%`
    : "—";
export function snapshots(overview: any): any[] {
  return (overview?.tree?.datasets || []).flatMap(
    (d: any) => d.snapshots || [],
  );
}
export function useAction() {
  const s = useStudio();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const errorOwner = useRef(0);
  const clearError = useCallback(() => {
    errorOwner.current++;
    setError("");
  }, []);
  useEffect(() => {
    generation.current++;
    return () => {
      generation.current++;
    };
  }, [s.project, s.user?.id]);
  return {
    busy,
    error,
    clearError,
    run: async (fn: () => Promise<any>) => {
      const own = generation.current;
      const ownError = ++errorOwner.current;
      setBusy(true);
      setError("");
      try {
        return await fn();
      } catch (e: any) {
        if (own === generation.current && e.name !== "AbortError") {
          if (ownError === errorOwner.current) setError(e.message);
          s.toast(e.message, true);
        }
      } finally {
        if (own === generation.current) setBusy(false);
      }
    },
  };
}
export function ErrorNotice({ error }: { error: any }) {
  return error ? (
    <p role="alert" className="data-error">
      {String(error.message || error)}
    </p>
  ) : null;
}
export function Input({
  label,
  value,
  onChange,
  type = "text",
  ...props
}: any) {
  return (
    <Field label={label}>
      <input
        aria-label={label}
        type={type}
        value={value ?? ""}
        onChange={(e) =>
          onChange(
            type === "number"
              ? e.target.value === ""
                ? ""
                : Number(e.target.value)
              : e.target.value,
          )
        }
        {...props}
      />
    </Field>
  );
}
export function Text({ label, value, onChange, ...props }: any) {
  return (
    <Field label={label}>
      <textarea
        aria-label={label}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        {...props}
      />
    </Field>
  );
}
export function Select({ label, value, onChange, options, ...props }: any) {
  return (
    <Field label={label}>
      <select
        aria-label={label}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        {...props}
      >
        {options.map((o: any) =>
          typeof o === "string" ? (
            <option key={o}>{o}</option>
          ) : (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ),
        )}
      </select>
    </Field>
  );
}
export function Check({ label, checked, onChange }: any) {
  return (
    <label className="data-check">
      <input
        type="checkbox"
        checked={!!checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}
export function Chips({
  items,
  chosen,
  onChange,
}: {
  items: string[];
  chosen: string[];
  onChange: (v: string[]) => void;
}) {
  return (
    <div className="data-actions">
      {items.map((x) => (
        <Button
          key={x}
          variant={chosen.includes(x) ? "primary" : "secondary"}
          aria-pressed={chosen.includes(x)}
          onClick={() =>
            onChange(
              chosen.includes(x)
                ? chosen.filter((v) => v !== x)
                : [...chosen, x],
            )
          }
        >
          {x}
        </Button>
      ))}
    </div>
  );
}
export function Pager({ offset, limit, total, onChange }: any) {
  return (
    <div className="data-pager">
      <span>
        Results {total ? offset + 1 : 0}–{Math.min(offset + limit, total)} of{" "}
        {total}
      </span>
      <Button
        aria-label="Previous page"
        disabled={!offset}
        onClick={() => onChange(Math.max(0, offset - limit))}
      >
        Previous
      </Button>
      <Button
        aria-label="Next page"
        disabled={offset + limit >= total}
        onClick={() => onChange(offset + limit)}
      >
        Next
      </Button>
    </div>
  );
}
export function Records({
  headers,
  children,
  label,
}: {
  headers: string[];
  children: ReactNode;
  label: string;
}) {
  return (
    <div className="data-table">
      <table aria-label={label}>
        <thead>
          <tr>
            {headers.map((x) => (
              <th key={x}>{x}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
export function Messages({ messages }: any) {
  return (
    <>
      {(messages || []).map((m: any, i: number) => (
        <section key={i}>
          <strong>{m.role}</strong>
          <pre className="data-prose">{m.content}</pre>
        </section>
      ))}
    </>
  );
}
export function Histogram({
  label,
  counts,
  bins,
  onSelect,
  selected,
  data,
}: any) {
  const max = Math.max(1, ...counts);
  return (
    <Evidence
      as="svg"
      label={label}
      data={data || { counts, bins }}
      viewBox="0 0 400 130"
      role="img"
      aria-label={label}
      className="data-histogram"
    >
      {counts.map((n: number, i: number) => (
        <g
          key={i}
          role={onSelect ? "button" : undefined}
          tabIndex={onSelect ? 0 : undefined}
          aria-label={`${bins[i]}–${bins[i + 1]}: ${n}`}
          onClick={() => onSelect?.(i)}
          onKeyDown={(e: any) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onSelect?.(i);
            }
          }}
        >
          <rect
            x={(i * 390) / counts.length + 5}
            y={105 - (n / max) * 95}
            width={Math.max(1, 390 / counts.length - 3)}
            height={(n / max) * 95}
            fill={selected === i ? "var(--color-accent-600)" : "currentColor"}
            opacity={selected == null || selected === i ? 0.9 : 0.35}
          />
          <title>{`${bins[i]}–${bins[i + 1]}: ${n}`}</title>
        </g>
      ))}
      <text x="5" y="125" fill="currentColor">
        {bins[0]}
      </text>
      <text x="390" y="125" textAnchor="end" fill="currentColor">
        {bins[bins.length - 1]}
      </text>
    </Evidence>
  );
}
export function scopedDownload(project: string, path: string) {
  return path.replace("/api/", `/api/p/${enc(project)}/`);
}
// Inline resource views also have ids. Tasks additionally carry a kind and
// lifecycle state, as specified by services/tasks.task_dict.
type BackgroundTask = { id: string; kind: string; state: string };
function isTaskView(value: unknown): value is BackgroundTask {
  if (!value || typeof value !== "object") return false;
  const task = value as Record<string, unknown>;
  return (
    typeof task.id === "string" &&
    task.id.length > 0 &&
    typeof task.kind === "string" &&
    task.kind.length > 0 &&
    typeof task.state === "string" &&
    ["queued", "running", "succeeded", "failed", "stopped"].includes(task.state)
  );
}
export function track(
  s: { trackTask: (task: BackgroundTask) => void; showTasks: () => void },
  result: unknown,
) {
  const envelope =
    result && typeof result === "object"
      ? (result as Record<string, unknown>)
      : null;
  const task = envelope?.queued === true ? envelope.task : result;
  if (!isTaskView(task)) return false;
  s.trackTask(task);
  s.showTasks();
  return true;
}

export function DetailInspector({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    if (window.matchMedia?.("(max-width: 850px)").matches) ref.current?.focus();
    return () => {
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return (
    <aside
      className="data-inspector"
      aria-label={title}
      tabIndex={-1}
      ref={ref}
    >
      <header className="data-inspector-heading">
        <h2>{title}</h2>
        <Button size="sm" variant="ghost" onClick={onClose}>
          Back to results
        </Button>
      </header>
      <div className="data-inspector-content">{children}</div>
    </aside>
  );
}
```

## `frontend/src/features/data/records-workspace.tsx`

RecordsInspector, RecordsScroll, RecordComparison, scoped retained resource data; shared by datasets/repositories/models/traces.

SHA-256: `107e16b2efa0fd5eb0c8f60d135734b0f20a47c829b734a4545b734b4987f9cb`

```tsx
import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type CSSProperties,
} from "react";
import { useResource, useStudio } from "../../app/StudioContext";
import { Button } from "../../components/studio/primitives";
import { cx } from "../../components/boardui/utils/cx";

// Preserve a successful response only while its exact resource and user/project
// scope are unchanged. Never show another cohort as this resource's stale data.
export function useRetainedResource<T>(path: string) {
  const resource = useResource<T>(path),
    { scopeKey } = useStudio();
  const key = JSON.stringify([scopeKey, path]);
  const [saved, setSaved] = useState<{ key: string; data: T }>();
  useEffect(() => {
    if (resource.data !== undefined) setSaved({ key, data: resource.data });
  }, [key, resource.data]);
  const isStale = resource.data === undefined && saved?.key === key;
  return {
    ...resource,
    data: resource.data ?? (isStale ? saved?.data : undefined),
    isStale,
  };
}

// Selection owns a retained set of records; inspection owns one visible identity.
export function RecordComparison({
  records,
  fields,
  onRemove,
  onClose,
}: {
  records: any[];
  fields: { label: string; value: (record: any) => string }[];
  onRemove: (record: any) => void;
  onClose: () => void;
}) {
  return (
    <section className="records-comparison" aria-label="Compare records">
      <header className="records-section-heading">
        <div>
          <h2>Compare records</h2>
          <p>{records.length} selected · shared fields stay aligned</p>
        </div>
        <Button onClick={onClose}>Back to collection</Button>
      </header>
      {records.length < 2 && (
        <p role="status">
          Select at least two records to compare. Your selection is retained.
        </p>
      )}
      <div className="data-table">
        <table aria-label="Record comparison">
          <thead>
            <tr>
              <th>Field</th>
              {records.map((record) => (
                <th key={record.id ?? record.name}>
                  <div>{record.name ?? `Datapoint ${record.id}`}</div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onRemove(record)}
                  >
                    Remove {record.name ?? record.id}
                  </Button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {fields.map((field) => {
              const values = records.map(field.value),
                differs = new Set(values).size > 1;
              return (
                <tr
                  key={field.label}
                  className={cx(differs && "records-difference")}
                >
                  <th scope="row">
                    {field.label}
                    {differs && (
                      <span className="records-field-note">Different</span>
                    )}
                  </th>
                  {records.map((record, index) => (
                    <td key={record.id ?? record.name}>
                      <div className="records-compare-value">
                        {values[index]}
                      </div>
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
export function RecordsScroll({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  const [scrolled, setScrolled] = useState(0);
  return (
    <div className="records-scroll-wrap">
      <div
        className="records-scroll-fade"
        aria-hidden="true"
        style={{ "--records-scroll": scrolled } as CSSProperties}
      >
        <i />
        <i />
        <i />
      </div>
      <div
        className="records-scroll"
        role="region"
        aria-label={label}
        tabIndex={0}
        onScroll={(event) =>
          setScrolled(Math.min(event.currentTarget.scrollTop / 24, 1))
        }
      >
        {children}
      </div>
    </div>
  );
}

export function RecordsInspector({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (window.matchMedia?.("(max-width: 850px)").matches) ref.current?.focus();
  }, []);
  return (
    <aside
      ref={ref}
      className="data-inspector records-local-inspector"
      aria-label={title}
      tabIndex={-1}
    >
      <header className="data-inspector-heading">
        <h2>{title}</h2>
        <Button size="sm" variant="ghost" onClick={onClose}>
          Back to results
        </Button>
      </header>
      <RecordsScroll label={`${title} detail`}>
        <div className="records-inspector-body">{children}</div>
      </RecordsScroll>
    </aside>
  );
}
```

## `frontend/boardui.json`

Actual BoardUI aliases and paths.

SHA-256: `db771b6661091b3e5946ca221e76b027b2491c29353ae3fa1cda1d9732e96357`

```json
{
  "paths": {
    "components": "src/components/boardui",
    "utils": "src/components/boardui/utils",
    "hooks": "src/components/boardui/hooks",
    "styles": "src/styles",
    "lib": "src/components/boardui/lib"
  },
  "aliases": {
    "components": "@/components/boardui",
    "utils": "@/components/boardui/utils",
    "hooks": "@/components/boardui/hooks",
    "styles": "@/styles",
    "lib": "@/components/boardui/lib"
  }
}
```

