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
  type Ref,
} from "react";
import { createPortal } from "react-dom";
import { UNSAFE_PortalProvider } from "react-aria/PortalProvider";
import { Button as AffectButton } from "../affectui/button-clean";
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
  navigation,
  headingRef,
  children,
  className,
  ...props
}: Box & {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  navigation?: ReactNode;
  headingRef?: Ref<HTMLHeadingElement>;
}) {
  return (
    <section {...props} className={cx("studio-page", className)}>
      {navigation && (
        <div className="studio-page__navigation">{navigation}</div>
      )}
      <header className="page-heading studio-page__heading">
        <div>
          <h1 ref={headingRef} tabIndex={headingRef ? -1 : undefined}>
            {title}
          </h1>
          {description && <p className="muted">{description}</p>}
        </div>
        {actions && (
          <div className="actions studio-page__actions">{actions}</div>
        )}
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
    <AffectButton
      {...props}
      className={cx("studio-button", props.className)}
      variant={variant === "danger" ? "destructive" : variant}
      size={size}
      loading={loading}
      disabled={props.disabled || loading}
      aria-busy={loading || props["aria-busy"] || undefined}
    >
      {loading ? "Working…" : children}
    </AffectButton>
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
