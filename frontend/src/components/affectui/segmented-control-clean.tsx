// Adapted from AffectUI; MIT notice and upstream hashes: ./LICENSE and ./provenance.json.
"use client";

import { cx } from "../boardui/utils/cx";

import {
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";

/*
 * Segmented Control (Clean) – switch between two to six options, in a single self-contained file.
 * Clean style: "buttons" (the default): separate quiet chips with a hairline, the active one filled light gray;
 * "tray": a compact light-gray track with a white raised pill; "underline": text tabs with a selected line.
 * Selection is painted by the selected option itself, independent of animation and measured offsets.
 * Labels keep one weight, so nothing shifts; when a label or count changes the control morphs to its new size.
 * Arrow keys, Home and End move and select; press the pill and drag it across the options to pick as you go.
 * If it doesn't fit (phones), it scrolls sideways with soft edges and keeps the active option in view.
 * The styles are injected below, so no extra files are needed.
 */

export type SegmentOption<T extends string = string> = {
  value: T;
  label?: ReactNode;
  icon?: ReactNode;
  /** A small gray number after the label, e.g. unread items */
  count?: number | string;
  /** A small tinted tag after the label, e.g. "Save 20%" */
  badge?: ReactNode;
  disabled?: boolean;
  /** Accessible name – needed when the option only shows an icon */
  ariaLabel?: string;
  /** With kind="tabs": the id of the panel this tab shows */
  controls?: string;
};

export type SegmentedControlProps<T extends string = string> = {
  options: SegmentOption<T>[];
  /** Controlled value */
  value?: T;
  /** Starting value when uncontrolled – defaults to the first enabled option */
  defaultValue?: T;
  onChange?: (value: T) => void;
  size?: "sm" | "md" | "lg";
  /** "buttons" = separate chips, the active one filled; "tray" = light track with a raised pill; "underline" = text tabs with a selected line */
  variant?: "buttons" | "tray" | "underline";
  /** Fill the container with equal segments */
  fullWidth?: boolean;
  /** "radio" (a radiogroup, the default) or "tabs" (a tablist that switches panels) */
  kind?: "radio" | "tabs";
  disabled?: boolean;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  className?: string;
};

export function SegmentedControl<T extends string = string>({
  options: rawOptions,
  value,
  defaultValue,
  onChange,
  size: sizeProp = "md",
  variant: variantProp = "buttons",
  fullWidth = false,
  kind = "radio",
  disabled = false,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledby,
  className,
}: SegmentedControlProps<T>) {
  // Gaps and repeated values are dropped – one value, one option
  const options = useMemo(() => {
    const seen = new Set<string>();
    return (Array.isArray(rawOptions) ? rawOptions : []).filter(
      (o): o is SegmentOption<T> => {
        if (!o || o.value == null || seen.has(String(o.value))) return false;
        seen.add(String(o.value));
        return true;
      },
    );
  }, [rawOptions]);
  // Unknown sizes and variants fall back to the defaults
  const size = sizeProp === "sm" || sizeProp === "lg" ? sizeProp : "md";
  const variant =
    variantProp === "tray" || variantProp === "underline"
      ? variantProp
      : "buttons";
  const [inner, setInner] = useState<T | undefined>(
    () => defaultValue ?? options.find((o) => !o.disabled)?.value,
  );
  const current = value !== undefined ? value : inner;

  const root = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const track = useRef<HTMLDivElement>(null);
  // Latest value without waiting for a render – dragging picks several options in one frame
  const latest = useRef(current);
  latest.current = current;
  const previousSelection = useRef<T | undefined>(undefined);
  const press = useRef<{
    id: number;
    touch: boolean;
    drag: boolean;
    x: number;
    moved: boolean;
    lastValue?: T;
  } | null>(null);
  const pointerClickHandled = useRef(false);

  const buttons = () =>
    Array.from(
      track.current?.querySelectorAll<HTMLElement>(":scope > .ui-segc__opt") ??
        [],
    );
  const activeIndex = options.findIndex((o) => o.value === current);
  const tabbable =
    activeIndex >= 0 && !options[activeIndex].disabled
      ? activeIndex
      : options.findIndex((o) => !o.disabled);

  const select = (v: T) => {
    const opt = options.find((o) => o.value === v);
    if (!opt || opt.disabled || disabled || v === latest.current) return;
    if (value === undefined) {
      latest.current = v;
      setInner(v);
    }
    onChange?.(v);
  };

  /** Buttons get the width of their content, so a changed label or count makes them – and the control – glide to the new size */
  const measure = () => {
    const els = buttons();
    if (!els.length || !track.current) return;
    const cs = getComputedStyle(els[0]);
    const pad = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
    const widths = els.map(
      (b) => (b.firstElementChild as HTMLElement).offsetWidth + pad,
    );
    if (fullWidth) {
      track.current.style.setProperty(
        "--ui-segc-min",
        `${Math.max(...widths)}px`,
      );
      els.forEach((b) => (b.style.width = ""));
    } else els.forEach((b, i) => (b.style.width = `${widths[i]}px`));
  };

  /** Soft edges only on the sides that can scroll */
  const fades = () => {
    const s = scroller.current;
    if (!s) return;
    const max = s.scrollWidth - s.clientWidth;
    root.current?.toggleAttribute("data-overflow", max > 1);
    const rtl = getComputedStyle(s).direction === "rtl";
    const position = Math.abs(s.scrollLeft);
    s.style.setProperty(
      "--ui-segc-fade-l",
      (rtl ? position < max - 1 : position > 1) ? "24px" : "0px",
    );
    s.style.setProperty(
      "--ui-segc-fade-r",
      (rtl ? position > 1 : position < max - 1) ? "24px" : "0px",
    );
  };

  /** Keeps the active option in view when the control scrolls */
  const reveal = (smooth: boolean) => {
    const s = scroller.current;
    const t = track.current;
    const btn = t?.querySelector<HTMLElement>(
      ":scope > .ui-segc__opt[data-active]",
    );
    if (!s || !t || !btn || s.scrollWidth <= s.clientWidth + 1) return;
    const left =
      t.offsetLeft + btn.offsetLeft - (s.clientWidth - btn.offsetWidth) / 2;
    const rtl = getComputedStyle(s).direction === "rtl";
    s.scrollTo({
      left: rtl
        ? Math.min(0, left - (s.scrollWidth - s.clientWidth))
        : Math.max(0, left),
      behavior: smooth ? "smooth" : "auto",
    });
  };

  // Sizes: measured on mount and whenever a label, the container or the font changes
  useLayoutEffect(() => {
    const t = track.current;
    const s = scroller.current;
    if (!t || !s) return;
    const sync = () => {
      measure();
      fades();
    };
    sync();
    // Width transitions only after the first measure, so nothing grows in on load
    const ready = requestAnimationFrame(() =>
      root.current?.setAttribute("data-ready", ""),
    );
    // One frame later, so setting widths inside the observer never loops
    let frame = 0;
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(sync);
    });
    ro.observe(s);
    ro.observe(t);
    t.querySelectorAll(".ui-segc__opt, .ui-segc__content").forEach((el) =>
      ro.observe(el),
    );
    return () => {
      ro.disconnect();
      cancelAnimationFrame(frame);
      cancelAnimationFrame(ready);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options.length, fullWidth, variant, size]);

  // Selection styles are declarative. Only focus and overflow reveal need effects.
  useLayoutEffect(() => {
    if (previousSelection.current === current) return;
    const first = previousSelection.current === undefined;
    previousSelection.current = current;
    reveal(!first);
    const t = track.current;
    if (!first && t && t.contains(document.activeElement))
      buttons()[activeIndex]?.focus({ preventScroll: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  /* ---------- Keyboard ---------- */

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const enabled = options.filter((o) => !o.disabled);
    if (!enabled.length || disabled) return;
    const rtl = getComputedStyle(e.currentTarget).direction === "rtl";
    const previous = rtl ? "ArrowRight" : "ArrowLeft";
    const next = rtl ? "ArrowLeft" : "ArrowRight";
    const prevKeys = kind === "radio" ? [previous, "ArrowUp"] : [previous];
    const nextKeys = kind === "radio" ? [next, "ArrowDown"] : [next];
    const at = enabled.findIndex((o) => o.value === latest.current);
    let target: SegmentOption<T> | undefined;
    if (prevKeys.includes(e.key))
      target = enabled[(at - 1 + enabled.length) % enabled.length];
    else if (nextKeys.includes(e.key))
      target = enabled[(at + 1) % enabled.length];
    else if (e.key === "Home") target = enabled[0];
    else if (e.key === "End") target = enabled[enabled.length - 1];
    if (!target) return;
    e.preventDefault();
    select(target.value);
    buttons()[options.indexOf(target)]?.focus();
  };

  /* ---------- Pointer: press feedback and dragging across options ---------- */

  /** The option under (or nearest to) a horizontal pointer position */
  const optionAt = (clientX: number) => {
    let best = -1;
    let dist = Infinity;
    buttons().forEach((b, i) => {
      const r = b.getBoundingClientRect();
      const d =
        clientX < r.left
          ? r.left - clientX
          : clientX > r.right
            ? clientX - r.right
            : 0;
      if (d < dist) {
        dist = d;
        best = i;
      }
    });
    return options[best];
  };

  // Pointer release is followed by click. Gesture-local bookkeeping deduplicates
  // that activation even when the controlled consumer defers or rejects a value.
  const selectFromPointer = (v: T) => {
    const gesture = press.current;
    if (!gesture || gesture.lastValue === v) return;
    gesture.lastValue = v;
    select(v);
  };

  const endPress = () => {
    press.current = null;
    root.current?.removeAttribute("data-pressed");
  };

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    pointerClickHandled.current = false;
    if (disabled || e.button !== 0) return;
    const opt = optionAt(e.clientX);
    if (!opt || opt.disabled) return;
    const touch = e.pointerType === "touch";
    // Touch: picks on lift (so swiping to scroll doesn't pick), but the pill itself can be dragged right away
    const drag = !touch || opt.value === latest.current;
    press.current = {
      id: e.pointerId,
      touch,
      drag,
      x: e.clientX,
      moved: false,
    };
    if (drag) root.current?.setAttribute("data-pressed", "");
    if (!touch) {
      selectFromPointer(opt.value);
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        /* capture is a nicety */
      }
    }
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const p = press.current;
    if (!p || p.id !== e.pointerId) return;
    if (!p.moved && Math.abs(e.clientX - p.x) < (p.touch ? 8 : 3)) return;
    p.moved = true;
    if (!p.drag) return;
    const opt = optionAt(e.clientX);
    if (opt && !opt.disabled) selectFromPointer(opt.value);
  };

  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    const p = press.current;
    if (!p || p.id !== e.pointerId) return;
    if (p.touch && !p.moved) {
      const opt = optionAt(e.clientX);
      if (opt) selectFromPointer(opt.value);
    }
    pointerClickHandled.current = true;
    endPress();
  };

  const isTabs = kind === "tabs";

  return (
    <div
      ref={root}
      className={cx("ui-segc", className)}
      data-size={size}
      data-variant={variant}
      data-full={fullWidth || undefined}
      data-disabled={disabled || undefined}
    >
      {/* React 19 hoists this into <head> once, no matter how many controls render */}
      <style href="ui-segmented-control-clean" precedence="default">
        {css}
      </style>

      <div ref={scroller} className="ui-segc__scroller" onScroll={fades}>
        <div
          ref={track}
          className="ui-segc__track"
          role={isTabs ? "tablist" : "radiogroup"}
          aria-label={ariaLabel}
          aria-labelledby={ariaLabelledby}
          aria-disabled={disabled || undefined}
          aria-orientation={isTabs ? "horizontal" : undefined}
          onKeyDown={onKeyDown}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={endPress}
          onLostPointerCapture={endPress}
        >
          {options.map((o, i) => {
            const active = i === activeIndex;
            // Without a label or icon the value names the option; NaN counts show nothing
            const label = o.label ?? (o.icon ? null : String(o.value));
            const count = countText(o.count);
            return (
              <button
                key={String(o.value)}
                type="button"
                className="ui-segc__opt"
                role={isTabs ? "tab" : "radio"}
                aria-checked={isTabs ? undefined : active}
                aria-selected={isTabs ? active : undefined}
                aria-controls={isTabs ? o.controls : undefined}
                aria-label={o.ariaLabel}
                data-active={active || undefined}
                disabled={o.disabled || disabled}
                tabIndex={i === tabbable ? 0 : -1}
                // Keyboard (Enter / Space) and screen readers – pointers are handled on the track
                onClick={(event) => {
                  const handled = pointerClickHandled.current;
                  pointerClickHandled.current = false;
                  // detail=0 is keyboard/AT activation and must remain available.
                  if (handled && event.detail > 0) return;
                  select(o.value);
                }}
              >
                <span className="ui-segc__content">
                  {o.icon && (
                    <span
                      className="ui-segc__icon"
                      onErrorCapture={(e) =>
                        ((e.target as HTMLElement).style.visibility = "hidden")
                      }
                    >
                      {o.icon}
                    </span>
                  )}
                  {/* One weight for both states – only the color changes, so nothing shifts */}
                  {label != null && (
                    <span className="ui-segc__label" dir="auto">
                      {label}
                    </span>
                  )}
                  {count != null && count !== "" && (
                    <span key={String(count)} className="ui-segc__count">
                      {count}
                    </span>
                  )}
                  {o.badge != null && (
                    <span className="ui-segc__badge">{o.badge}</span>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** 1284 → "1,284"; NaN and Infinity show nothing */
const countText = (c: number | string | undefined) =>
  typeof c === "number"
    ? Number.isFinite(c)
      ? c.toLocaleString("en-US")
      : null
    : c;

const css = /* css */ `
/* Animatable custom properties: the equal segment width and the soft edges glide too */
@property --ui-segc-min { syntax: "<length>"; inherits: true; initial-value: 0px; }
@property --ui-segc-fade-l { syntax: "<length>"; inherits: false; initial-value: 0px; }
@property --ui-segc-fade-r { syntax: "<length>"; inherits: false; initial-value: 0px; }

.ui-segc {
  --ui-segc-ink: color-mix(in srgb, var(--color-text-primary) 88%, transparent);
  --ui-segc-text: var(--color-text-secondary);
  --ui-segc-hover-text: color-mix(in srgb, var(--color-text-primary) 75%, transparent);
  --ui-segc-line: color-mix(in srgb, var(--color-text-primary) 6%, transparent);
  --ui-segc-wash: color-mix(in srgb, var(--color-text-primary) 3%, transparent);
  --ui-segc-fill: color-mix(in srgb, var(--color-text-primary) 6%, transparent);
  --ui-segc-track: color-mix(in srgb, var(--color-text-primary) 4%, transparent);
  --ui-segc-card: var(--color-background-primary-default);
  --ui-segc-card-shadow: 0 0 0 0.5px color-mix(in srgb, var(--color-text-primary) 6%, transparent), 0 1px 1px -1px color-mix(in srgb, var(--color-text-primary) 10%, transparent), 0 1px 2px 0 color-mix(in srgb, var(--color-text-primary) 5%, transparent);
  --ui-segc-accent: color-mix(in srgb, var(--color-border-focus-ring) 76.5%, transparent);
  --ui-segc-accent-soft: color-mix(in srgb, var(--color-border-focus-ring) 8%, transparent);
  --ui-segc-focus: var(--color-border-focus-ring);
  --ui-segc-ease: cubic-bezier(0.25, 0.9, 0.3, 1);
  /* Size: md – 28px chips, 4px 10px */
  --ui-segc-h: 28px;
  --ui-segc-px: 10px;
  --ui-segc-fs: 13px;
  --ui-segc-lh: 20px;
  --ui-segc-icon: 16px;
  --ui-segc-gap: 8px;
  --ui-segc-pad: 2px;
  --ui-segc-ring: 3px;
  box-sizing: border-box;
  display: inline-flex;
  max-width: 100%;
  min-width: 0;
  vertical-align: middle;
  border-radius: 8px;
  font-family: "Inter", "InterVariable", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: var(--ui-segc-fs);
  font-weight: 500;
  line-height: var(--ui-segc-lh);
  color: var(--ui-segc-text);
  -webkit-font-smoothing: antialiased;
}
.ui-segc[data-size="sm"] { --ui-segc-h: 24px; --ui-segc-px: 8px; --ui-segc-fs: 12px; --ui-segc-lh: 16px; --ui-segc-icon: 14px; --ui-segc-gap: 6px; }
.ui-segc[data-size="lg"] { --ui-segc-h: 32px; --ui-segc-px: 12px; --ui-segc-icon: 16px; --ui-segc-gap: 8px; }
.ui-segc[data-full] { display: flex; width: 100%; }
.ui-segc[data-disabled] { opacity: 0.5; pointer-events: none; }
.ui-segc *, .ui-segc *::before, .ui-segc *::after { box-sizing: border-box; }
:where(.ui-segc) button { margin: 0; padding: 0; border: 0; background: none; font: inherit; color: inherit; cursor: pointer; }
:where(.ui-segc) svg { display: block; flex: none; }

/* Scrolls sideways only when it doesn't fit; the padding keeps focus rings from being cut off */
.ui-segc__scroller {
  position: relative;
  flex: 1;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  padding: var(--ui-segc-pad);
  border-radius: inherit;
  overscroll-behavior-x: contain;
  scrollbar-width: none;
  -webkit-mask-image: linear-gradient(to right, transparent, var(--color-text-primary) var(--ui-segc-fade-l), var(--color-text-primary) calc(100% - var(--ui-segc-fade-r)), transparent);
  mask-image: linear-gradient(to right, transparent, var(--color-text-primary) var(--ui-segc-fade-l), var(--color-text-primary) calc(100% - var(--ui-segc-fade-r)), transparent);
  transition: --ui-segc-fade-l 160ms ease, --ui-segc-fade-r 160ms ease;
}
.ui-segc__scroller::-webkit-scrollbar { display: none; }

.ui-segc__track {
  position: relative;
  display: flex;
  width: max-content;
  min-width: 100%;
  /* Horizontal drags move the indicator; vertical ones still scroll the page */
  touch-action: pan-y;
  -webkit-user-select: none;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
}
/* When it scrolls, swipes scroll it instead of dragging the indicator */
.ui-segc[data-overflow] .ui-segc__track { touch-action: pan-x pan-y; }
/* Equal segments, each as wide as the widest one needs */
.ui-segc[data-full] .ui-segc__track { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(var(--ui-segc-min), 1fr); }
.ui-segc[data-ready] .ui-segc__track { transition: --ui-segc-min 200ms var(--ui-segc-ease); }

/* An option */
.ui-segc__opt {
  position: relative;
  z-index: 1;
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  height: var(--ui-segc-h);
  padding: 0 var(--ui-segc-px);
  overflow: clip;
  border-radius: 6px;
  white-space: nowrap;
  /* Selection and deselection paint immediately, including when motion is suspended. */
}
.ui-segc[data-ready]:not([data-full]) .ui-segc__opt { transition: width 200ms var(--ui-segc-ease); }
.ui-segc__opt[data-active] { color: var(--ui-segc-ink); }
.ui-segc__opt:disabled { opacity: 0.4; cursor: not-allowed; }
.ui-segc__opt:focus-visible { outline: 2px solid var(--ui-segc-focus); outline-offset: 1px; }
@media (hover: hover) {
  .ui-segc__opt:not([data-active]):not(:disabled):hover { background-color: var(--ui-segc-wash); }
}

.ui-segc__content { position: relative; display: inline-flex; flex: none; height: 100%; align-items: center; gap: 6px; }
.ui-segc[data-size="sm"] .ui-segc__content { gap: 5px; }
.ui-segc__icon { display: grid; place-items: center; width: var(--ui-segc-icon); height: var(--ui-segc-icon); color: color-mix(in srgb, var(--color-text-primary) 44.7%, transparent); }
.ui-segc__opt[data-active] .ui-segc__icon { color: var(--ui-segc-ink); }
.ui-segc__icon svg, .ui-segc__icon img { width: 100%; height: 100%; }
/* Images fill the tile without leaving it, whatever their shape */
.ui-segc__icon { overflow: hidden; }
.ui-segc__icon img { display: block; object-fit: contain; }
.ui-segc__label { display: block; }

.ui-segc__count {
  display: inline-grid;
  place-items: center;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 5px;
  background: var(--ui-segc-fill);
  font-size: 11px;
  line-height: 16px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  color: var(--ui-segc-text);
  animation: ui-segc-in 200ms var(--ui-segc-ease) both;
}
.ui-segc__opt[data-active] .ui-segc__count { background: var(--ui-segc-card); box-shadow: 0 0 0 0.5px var(--ui-segc-line); color: var(--ui-segc-ink); }
.ui-segc[data-size="sm"] .ui-segc__count { min-width: 14px; height: 14px; padding: 0 3px; border-radius: 4px; font-size: 10px; line-height: 14px; }
.ui-segc__badge {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 5px;
  background: var(--ui-segc-accent-soft);
  font-size: 11px;
  line-height: 16px;
  font-weight: 500;
  color: var(--ui-segc-accent);
}
.ui-segc[data-size="sm"] .ui-segc__badge { height: 16px; padding: 0 5px; font-size: 10px; }

/* Each selected option owns its decoration. No moving element can point at an old value. */
.ui-segc__opt[data-active]::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: var(--ui-segc-fill);
  pointer-events: none;
}
/* Press feedback is decorative; the selected option remains recognizable. */
.ui-segc[data-pressed]:not([data-variant="underline"]) .ui-segc__opt[data-active]::before { scale: 0.97; }

/* Buttons (default): separate chips with a hairline; the active one has a light-gray fill.
   The control reaches a few px past its box so focus rings are never clipped, and still lines up with its neighbours */
.ui-segc[data-variant="buttons"] { --ui-segc-pad: var(--ui-segc-ring); margin: calc(var(--ui-segc-ring) * -1); max-width: calc(100% + var(--ui-segc-ring) * 2); }
.ui-segc[data-variant="buttons"][data-full] { width: calc(100% + var(--ui-segc-ring) * 2); }
.ui-segc[data-variant="buttons"] .ui-segc__track { gap: var(--ui-segc-gap); }
.ui-segc[data-variant="buttons"] .ui-segc__opt { box-shadow: inset 0 0 0 0.5px var(--ui-segc-line); }

/* Tray: a compact light-gray track with a white raised pill */
.ui-segc[data-variant="tray"] { background: var(--ui-segc-track); }
.ui-segc[data-variant="tray"] .ui-segc__opt { height: calc(var(--ui-segc-h) - var(--ui-segc-pad) * 2); }
/* The button owns outer elevation; the clipped decoration retains the inset border. */
.ui-segc[data-variant="tray"] .ui-segc__opt[data-active] { box-shadow: var(--ui-segc-card-shadow); }
.ui-segc[data-variant="tray"] .ui-segc__opt[data-active]::before { background: var(--ui-segc-card); box-shadow: var(--ui-segc-card-shadow); }

/* Underline: text tabs on a hairline */
.ui-segc[data-variant="underline"] { --ui-segc-px: 2px; --ui-segc-gap: 20px; border-radius: 0; box-shadow: inset 0 -0.5px 0 var(--ui-segc-line); }
.ui-segc[data-variant="underline"] .ui-segc__scroller { padding: 0; }
.ui-segc[data-variant="underline"]:not([data-full]) .ui-segc__track { gap: var(--ui-segc-gap); }
.ui-segc[data-variant="underline"][data-full] { --ui-segc-px: 10px; }
.ui-segc[data-variant="underline"] .ui-segc__opt { height: calc(var(--ui-segc-h) + 6px); }
.ui-segc[data-variant="underline"] .ui-segc__opt:focus-visible { outline-offset: -2px; }
.ui-segc[data-variant="underline"] .ui-segc__opt[data-active]::before { content: none; }
.ui-segc[data-variant="underline"] .ui-segc__opt[data-active] .ui-segc__content::after { content: ""; position: absolute; inset-inline: 0; inset-block-end: 0; height: 1.5px; border-radius: 1.5px 1.5px 0 0; background: var(--ui-segc-ink); pointer-events: none; }
@media (hover: hover) {
  .ui-segc[data-variant="underline"] .ui-segc__opt:not([data-active]):not(:disabled):hover { background-color: transparent; color: var(--ui-segc-hover-text); }
}

@keyframes ui-segc-in { from { opacity: 0; transform: translateY(2px); filter: blur(2px); } to { opacity: 1; transform: none; filter: none; } }

/* Touch screens: every option at least 40px tall */
@media (hover: none) and (pointer: coarse) {
  .ui-segc[data-size] { --ui-segc-h: 40px; }
  .ui-segc[data-size="lg"] { --ui-segc-h: 44px; }
  .ui-segc[data-variant="tray"][data-size] { --ui-segc-h: 44px; }
  .ui-segc[data-variant="underline"] .ui-segc__opt { height: var(--ui-segc-h); }
}
@media (prefers-reduced-motion: reduce) {
  .ui-segc *, .ui-segc *::before, .ui-segc *::after { animation: none !important; transition: none !important; }
}

`;
