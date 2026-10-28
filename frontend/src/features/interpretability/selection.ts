export type SelectionSnapshot<T> = Readonly<{
  key: string;
  status: "idle" | "loading" | "ready" | "error";
  data?: T;
  error?: Error;
}>;

/** One owner for a selected artifact's async evidence; obsolete reads cannot publish. */
export class EvidenceSelection<T> {
  private generation = 0;
  private controller?: AbortController;
  private listeners = new Set<() => void>();
  private snapshot: SelectionSnapshot<T> = { key: "", status: "idle" };
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(value: SelectionSnapshot<T>) {
    this.snapshot = Object.freeze(value);
    this.listeners.forEach((listener) => listener());
  }
  async select(
    key: string,
    load: (signal: AbortSignal) => Promise<T>,
  ): Promise<void> {
    this.cancel();
    const generation = this.generation,
      controller = new AbortController();
    this.controller = controller;
    this.publish({ key, status: "loading" });
    try {
      const data = await load(controller.signal);
      if (generation === this.generation && !controller.signal.aborted)
        this.publish({ key, status: "ready", data });
    } catch (error) {
      if (generation === this.generation && !controller.signal.aborted)
        this.publish({
          key,
          status: "error",
          error: error instanceof Error ? error : new Error(String(error)),
        });
    }
  }
  cancel() {
    this.generation++;
    this.controller?.abort();
    this.controller = undefined;
  }
  clear() {
    this.cancel();
    this.publish({ key: "", status: "idle" });
  }
}

export const REGIMES = {
  "+/+": { label: "Open gate · positive value", short: "Open / positive" },
  "+/-": { label: "Open gate · negative value", short: "Open / negative" },
  "-/+": { label: "Closed gate · positive value", short: "Closed / positive" },
  "-/-": {
    label: "Closed gate · negative value (echo)",
    short: "Closed / negative",
  },
} as const;
export type Regime = keyof typeof REGIMES;
export interface Activation {
  datapoint: number;
  position: number;
  value: number;
  g: number;
  v: number;
  regime: Regime;
  first_token: boolean;
  before: string;
  token: string;
  after: string;
  labels: string[];
  bucket: string;
  user: string;
}
export const number = (value: unknown, digits = 2) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits)
    : "—";
export const percent = (value: unknown, digits = 0) =>
  typeof value === "number" && Number.isFinite(value)
    ? `${(value * 100).toFixed(digits)}%`
    : "—";
export const readableToken = (token: string) =>
  String(token ?? "")
    .replace(/Ġ|▁/g, " ")
    .replace(/Ċ/g, "\n")
    .replace(/\n/g, "↵")
    .replace(/^ +$/, "␠");
export function labelDenominator(view: any, open: boolean): number {
  return new Set<number>(
    view.datapoints?.[
      open && view.gate_open?.length ? "gate_open" : "positives"
    ] ?? [],
  ).size;
}
/** The API supplies labels only for gate-open or overall-positive retained sets. */
export function labelSummary(
  view: any,
  requestedPole: string,
): {
  pole: "gate_open" | "positives";
  pairs: [string, number][];
  denominator: number;
} {
  const pole =
    requestedPole === "gate_open" && view.gate_open?.length
      ? "gate_open"
      : "positives";
  return {
    pole,
    pairs:
      (pole === "gate_open" ? view.label_share : view.label_share_all) || [],
    denominator: labelDenominator(view, pole === "gate_open"),
  };
}
export function filterEntries(
  entries: Activation[],
  { label = "", regime = "" }: { label?: string; regime?: string } = {},
) {
  return entries.filter(
    (entry) =>
      (!label ||
        (entry.labels?.length ? entry.labels : ["(unlabelled)"]).includes(
          label,
        )) &&
      (!regime || entry.regime === regime),
  );
}
export function parseLayers(text: string): number[] {
  const values = text
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  if (
    !values.length ||
    (values.length === 1 && values[0].toLowerCase() === "all")
  )
    return [];
  const layers = values.map(Number);
  if (layers.some((value) => !Number.isInteger(value) || value < 0))
    throw new Error("Use comma-separated non-negative layer numbers, or all.");
  return [...new Set(layers)].sort((a, b) => a - b);
}
