import { describe, expect, it } from "vitest";
import {
  trendModel,
  trendFields,
  exactNumber,
  type TrendSource,
} from "./trends";

function source(
  records: any[],
  kind: "original" | "derived" = "derived",
  artifactId = kind,
): TrendSource {
  return { runId: "run", artifactId, sourceGroup: "capture", kind, records };
}
const row = (changes: any = {}) => ({
  record_id: "r0",
  source_record_id: "root",
  episode_id: "E",
  device_id: "pump",
  signal_id: "voltage",
  clock_domain: "reference",
  clock_units: "s",
  clock_origin: "experiment-start",
  time: 0,
  available_at: 0,
  value: -1.25,
  units: "V",
  ...changes,
});

describe("scientific trend model", () => {
  it("retains duplicate timestamps as distinct artifact rows and source IDs as relationships", () => {
    const model = trendModel(
      [source([row(), row({ record_id: "r1", value: 0.125 })])],
      ["value"],
      "event",
    );
    expect(model.series).toHaveLength(1);
    expect(model.series[0].points.map((point) => point.value)).toEqual([
      [0, -1.25],
      [0, 0.125],
    ]);
    const keys = model.series[0].points.map((point) => point.identity.key);
    expect(new Set(keys).size).toBe(2);
    expect(model.series[0].points[0].identity.sourceRecordIds).toEqual([
      "root",
    ]);
  });
  it("never puts volts and amperes on the same ordinate", () => {
    const model = trendModel(
      [
        source([
          row(),
          row({
            record_id: "amps",
            signal_id: "current",
            value: 3,
            units: "A",
          }),
        ]),
      ],
      ["value"],
      "event",
    );
    expect(model.lanes.map((lane) => lane.label).sort()).toEqual(["A", "V"]);
    expect(new Set(model.series.map((series) => series.laneKey)).size).toBe(2);
  });
  it("keeps unknown units distinct from declared dimensionless values", () => {
    const model = trendModel(
      [
        source([
          row({ units: undefined }),
          row({ record_id: "unitless", units: "unitless" }),
        ]),
      ],
      ["value"],
      "event",
    );
    expect(model.lanes).toHaveLength(2);
    expect(
      model.lanes.some((lane) => lane.label.includes("not recorded")),
    ).toBe(true);
  });
  it("does not mix clock origins or reset sessions", () => {
    const model = trendModel(
      [
        source([
          row(),
          row({ record_id: "origin", clock_origin: "other-start" }),
          row({ record_id: "reset", clock_reset_id: "reset-2" }),
        ]),
      ],
      ["value"],
      "event",
    );
    expect(model.clocks).toHaveLength(3);
    const filtered = trendModel(
      [
        source([
          row(),
          row({ record_id: "origin", clock_origin: "other-start" }),
        ]),
      ],
      ["value"],
      "event",
      model.clocks[0].key,
    );
    expect(filtered.series.flatMap((series) => series.points)).toHaveLength(1);
  });
  it("does not align unknown clocks from separate captures on the same device", () => {
    const model = trendModel(
      [
        source([
          row({
            clock_domain: undefined,
            clock_origin: undefined,
            capture_id: "one",
          }),
          row({
            record_id: "other",
            clock_domain: undefined,
            clock_origin: undefined,
            capture_id: "two",
          }),
        ]),
      ],
      ["value"],
      "event",
    );
    expect(model.clocks).toHaveLength(2);
    expect(model.clocks.every((clock) => clock.qualified === false)).toBe(true);
  });
  it("keeps null gaps and breaks lines at invalid time and explicit discontinuities", () => {
    const model = trendModel(
      [
        source([
          row(),
          row({ record_id: "missing", time: 1, value: null }),
          row({ record_id: "unknown-time", time: undefined, value: 90 }),
          row({ record_id: "resume", time: 3, value: 4 }),
          row({
            record_id: "boundary",
            time: 4,
            value: 5,
            discontinuity: true,
          }),
        ]),
      ],
      ["value"],
      "event",
    );
    expect(model.omittedTimeRows).toBe(1);
    expect(
      model.series.map((series) => series.points.map((point) => point.value)),
    ).toEqual([
      [
        [0, -1.25],
        [1, null],
      ],
      [[3, 4]],
      [[4, 5]],
    ]);
  });
  it("preserves original-only evidence and never requires a derived result", () => {
    const model = trendModel([source([row()], "original")], ["value"], "event");
    expect(model.series).toHaveLength(1);
    expect(model.series[0].kind).toBe("original");
  });
  it("uses availability only when recorded and keeps both timestamps on the source row", () => {
    const original = row({ time: 1, event_time: 1, available_at: 3 });
    const event = trendModel([source([original])], ["value"], "event");
    const availability = trendModel(
      [source([original])],
      ["value"],
      "availability",
    );
    expect(event.series[0].points[0].value[0]).toBe(1);
    expect(availability.series[0].points[0].value[0]).toBe(3);
    expect(original.time).toBe(1);
    expect(
      trendModel(
        [source([row({ available_at: undefined })])],
        ["value"],
        "availability",
      ).series,
    ).toEqual([]);
  });
  it("avoids numeric metadata and discovers retained signal fields without changing values", () => {
    expect(
      trendFields([
        source([
          row({
            source_position: 5,
            arrival_ordinal: 2,
            device_id: 42,
            features: { mean: 0.125, count: 3 },
            control: 0.5,
          }),
        ]),
      ]),
    ).toEqual(["value", "control", "features.mean", "features.count"]);
    expect(exactNumber(-0)).toBe("-0");
    expect(exactNumber(-1.2345678901234567)).toBe("-1.2345678901234567");
    expect(exactNumber(null)).toBe("Missing");
  });
});

it("keeps global artifact row positions after episode filtering", () => {
  const input = {
    ...source([row(), row({ record_id: "later", time: 2 })]),
    rowIndices: [7, 12],
  };
  const model = trendModel([input], ["value"], "event");
  expect(
    model.series[0].points.map((point) => point.identity.rowIndex),
  ).toEqual([7, 12]);
});

it("does not fabricate an undeclared channel for another sensor", () => {
  const model = trendModel(
    [
      source([
        row(),
        row({
          record_id: "thermal",
          signal_id: "temperature",
          value: undefined,
          temperature: 22,
          units: "degC",
        }),
      ]),
    ],
    ["temperature"],
    "event",
  );
  expect(model.series).toHaveLength(1);
  expect(model.series[0].points[0].record.signal_id).toBe("temperature");
  expect(model.series[0].points[0].value).toEqual([0, 22]);
});

it("does not plot source observation time as fitted-value availability across clocks", () => {
  const input = source([
    row({ available_at: 2, fit_availability: { clock_comparable: false } }),
  ]);
  expect(trendModel([input], ["value"], "availability").series).toEqual([]);
  expect(
    trendModel([input], ["value"], "event").series[0].points[0].value,
  ).toEqual([0, -1.25]);
});

it.each(["discontinuity", "invalid-time"])(
  "applies a %s boundary even when that row lacks the selected channel",
  (kind) => {
    const { value: _absent, ...boundary } = row({
      record_id: "boundary",
      time: 1,
    });
    if (kind === "discontinuity") boundary.discontinuity = true;
    else boundary.time = undefined;
    const model = trendModel(
      [
        source([
          row(),
          boundary,
          row({ record_id: "after", time: 2, value: 3 }),
        ]),
      ],
      ["value"],
      "event",
    );
    expect(
      model.series.map((series) => series.points.map((point) => point.value)),
    ).toEqual([[[0, -1.25]], [[2, 3]]]);
  },
);

it("shows contradictory clock metadata without plotting it as aligned evidence", () => {
  const model = trendModel(
    [source([row({ clock_epoch: "conflict" })])],
    ["value"],
    "event",
  );
  expect(model.clocks[0].qualified).toBe(false);
  expect(model.clocks[0].label).toContain("Invalid clock");
  expect(model.series).toEqual([]);
  expect(model.omittedTimeRows).toBe(1);
});

it("separates sessions even when reset identity is shared and accepts numeric zero origins", () => {
  const model = trendModel(
    [
      source([
        row({ clock_origin: 0, clock_session: "A", clock_reset_id: 0 }),
        row({ clock_origin: 0, clock_session: "B", clock_reset_id: 0 }),
      ]),
    ],
    ["value"],
    "event",
  );
  expect(model.clocks).toHaveLength(2);
  expect(model.clocks.every((clock) => clock.qualified)).toBe(true);
});

it("does not let an unrelated signal's absent-channel boundary break another stream", () => {
  const model = trendModel(
    [
      source([
        row(),
        row({
          signal_id: "other",
          time: undefined,
          value: undefined,
          discontinuity: true,
        }),
        row({ time: 2 }),
      ]),
    ],
    ["value"],
    "event",
  );
  expect(
    model.series.map((series) => series.points.map((point) => point.value)),
  ).toEqual([
    [
      [0, -1.25],
      [2, -1.25],
    ],
  ]);
});

it("breaks a stream when intervening rows have conflicting clock aliases", () => {
  const model = trendModel(
    [
      source([
        row(),
        row({ time: 1, clock_epoch: "conflict" }),
        row({ time: 2 }),
      ]),
    ],
    ["value"],
    "event",
  );
  expect(
    model.series.map((series) => series.points.map((point) => point.value)),
  ).toEqual([[[0, -1.25]], [[2, -1.25]]]);
});
