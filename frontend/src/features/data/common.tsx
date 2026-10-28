import "./affect-data.css";
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
  const operationOwner = useRef(0);
  const clearError = useCallback(() => {
    errorOwner.current++;
    setError("");
  }, []);
  useEffect(() => {
    generation.current++;
    setBusy(false);
    setError("");
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
      const operation = ++operationOwner.current;
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
        if (own === generation.current && operation === operationOwner.current)
          setBusy(false);
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
/** Human-readable collection date with the original timestamp available in full. */
export function RecordDate({ value }: { value?: string | null }) {
  if (!value) return <>—</>;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return <>{value}</>;
  return (
    <time dateTime={value} title={value}>
      {date.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      })}
    </time>
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
