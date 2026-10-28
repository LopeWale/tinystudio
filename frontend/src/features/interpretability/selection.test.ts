import { describe, expect, it } from "vitest";
import {
  EvidenceSelection,
  filterEntries,
  labelDenominator,
  number,
  parseLayers,
  percent,
  type Activation,
} from "./selection";

const deferred = <T>() => {
  let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
describe("EvidenceSelection owns asynchronous selection", () => {
  it("publishes immutable cached snapshots and rejects a slower obsolete success", async () => {
    const selection = new EvidenceSelection<string>(),
      a = deferred<string>(),
      b = deferred<string>();
    const initial = selection.getSnapshot();
    expect(selection.getSnapshot()).toBe(initial);
    let oldSignal: AbortSignal | undefined;
    const first = selection.select("L15N433", (signal) => {
      oldSignal = signal;
      return a.promise;
    });
    const second = selection.select("L15N434", () => b.promise);
    expect(oldSignal?.aborted).toBe(true);
    b.resolve("new neuron");
    await second;
    const newest = selection.getSnapshot();
    expect(Object.isFrozen(newest)).toBe(true);
    expect(newest).toMatchObject({
      key: "L15N434",
      status: "ready",
      data: "new neuron",
    });
    a.resolve("obsolete neuron");
    await first;
    expect(selection.getSnapshot()).toBe(newest);
  });
  it("cancellation rejects late errors and permits a fresh strict-mode mount", async () => {
    const selection = new EvidenceSelection<string>(),
      pending = deferred<string>();
    const first = selection.select("old project", () => pending.promise);
    selection.cancel();
    await selection.select("new project", async () => "current");
    pending.reject(new Error("old failure"));
    await first;
    expect(selection.getSnapshot()).toMatchObject({
      key: "new project",
      status: "ready",
      data: "current",
    });
    selection.clear();
    expect(selection.getSnapshot()).toEqual({ key: "", status: "idle" });
  });
  it("reports a current error, then can recover without recreating the controller", async () => {
    const selection = new EvidenceSelection<string>();
    await selection.select("same", async () => {
      throw new Error("expired capture");
    });
    expect(selection.getSnapshot().error?.message).toBe("expired capture");
    await selection.select("same", async () => "restored");
    expect(selection.getSnapshot()).toEqual({
      key: "same",
      status: "ready",
      data: "restored",
    });
  });
});
describe("scientific presentation expectations", () => {
  it("keeps zero, negatives and undefined measurements distinct", () => {
    expect(number(0)).toBe("0.00");
    expect(number(-1.394)).toBe("-1.39");
    expect(number(null)).toBe("—");
    expect(number(NaN)).toBe("—");
    expect(percent(0)).toBe("0%");
    expect(percent(0.005, 1)).toBe("0.5%");
  });
  it("uses distinct retained datapoints instead of top-k tokens as a label denominator", () => {
    const view = {
      topk: 40,
      gate_open: [{}, {}],
      datapoints: { gate_open: [4, 4, 46], positives: [4, 46, 148] },
    };
    expect(labelDenominator(view, true)).toBe(2);
    expect(labelDenominator(view, false)).toBe(3);
    expect(labelDenominator({ ...view, gate_open: [] }, true)).toBe(3);
  });
  it("filters preserved entries by label and actual signed regime without recomputing signs", () => {
    const entries = [
      { datapoint: 4, regime: "+/+", labels: ["lighting"], value: 5.36 },
      { datapoint: 132, regime: "+/-", labels: ["blinds"], value: -1.39 },
      { datapoint: 7, regime: "-/-", labels: [], value: 0.01 },
    ] as Activation[];
    expect(
      filterEntries(entries, { label: "lighting", regime: "+/+" }),
    ).toEqual([entries[0]]);
    expect(filterEntries(entries, { regime: "+/-" })).toEqual([entries[1]]);
    expect(filterEntries(entries, { label: "(unlabelled)" })).toEqual([
      entries[2],
    ]);
    expect(entries).toHaveLength(3);
  });
  it("accepts explicit complete layers and rejects invalid configuration before submission", () => {
    expect(parseLayers("all")).toEqual([]);
    expect(parseLayers("15, 0,15")).toEqual([0, 15]);
    for (const invalid of ["-1", "1.5", "all,15", "x"])
      expect(() => parseLayers(invalid)).toThrow();
  });
});
