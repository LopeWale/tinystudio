import { describe, expect, it } from "vitest";
import { spatialPickedPoint } from "./spatial-picking";
import { spatialFigure, type SpatialTheme } from "./spatial-figure";
import type { SpatialGroup, SpatialPoint } from "./spatial-model";
const theme: SpatialTheme = {
  text: "semantic-text",
  secondary: "semantic-secondary",
  border: "semantic-border",
  surface: "semantic-surface",
  colors: ["semantic-chart-low", "semantic-chart-high"],
  font: "semantic-font",
};
function point(x: number, y: number, index: number): SpatialPoint {
  return {
    coords: [x, y, index],
    groupKey: "group",
    identity: {
      key: `artifact-row-${index}`,
      runId: "run",
      artifactId: "artifact",
      rowIndex: index,
      kind: "original",
      sourceRecordIds: [],
    },
    record: {},
    qualifications: [],
    estimatedAxes: { x: false, y: false, z: false },
  };
}
const points = [
  point(3, 20, 5),
  point(1, 10, 0),
  point(2, 20, 4),
  point(3, 10, 2),
  point(1, 20, 3),
  point(2, 10, 1),
];
const group: SpatialGroup = {
  key: "group",
  label: "Measured",
  kind: "original",
  axes: {
    x: { id: "x", label: "X", unit: "s" },
    y: { id: "y", label: "Y", unit: "unitless" },
    z: { id: "z", label: "Z", unit: "V" },
  },
  points,
};
const trace = (
  geometry: "surface" | "slices" | "points",
  selectedKey?: string,
) =>
  spatialFigure(group, {
    geometry,
    selectedKey,
    opacity: 1,
    revision: "group",
    theme,
  }).data;
const event = (
  data: Record<string, unknown>,
  pointNumber: unknown,
  customdata: unknown = "forged-emitted-key",
) => ({ points: [{ data, pointNumber, customdata }] });
describe("Plotly 4.1.2 evidence picking", () => {
  it("resolves non-square permuted surface indices as X,Y against the Y,X identity matrix", () => {
    const data = trace("surface")[0];
    expect(
      spatialPickedPoint(event(data, [1, 0], "artifact-row-3"), points),
    ).toBe(points[5]);
    expect(spatialPickedPoint(event(data, [2, 1], undefined), points)).toBe(
      points[0],
    );
    expect(
      spatialPickedPoint(event(data, [0, 1], "artifact-row-1"), points),
    ).toBe(points[4]);
  });
  it("maps both ribbon display edges to the actual source identity for X index greater than one", () => {
    const data = trace("slices")[1];
    expect(spatialPickedPoint(event(data, [2, 0]), points)).toBe(points[0]);
    expect(spatialPickedPoint(event(data, [2, 1]), points)).toBe(points[0]);
  });
  it("resolves scatter and selected overlay through trace indices rather than emitted keys", () => {
    const data = trace("points", points[4].identity.key);
    expect(
      spatialPickedPoint(event(data[0], 2, points[1].identity.key), points),
    ).toBe(points[2]);
    expect(
      spatialPickedPoint(event(data[1], 0, points[1].identity.key), points),
    ).toBe(points[4]);
  });
  it.each(
    [[3, 0], [0, 2], [-1, 0], [0.5, 0], [0, NaN], [1], [0, 1, 2], 1, "0"].map(
      (pointNumber) => ({ pointNumber }),
    ),
  )(
    "rejects malformed or out-of-range surface index $pointNumber without emitted-key fallback",
    ({ pointNumber }) => {
      expect(
        spatialPickedPoint(
          event(trace("surface")[0], pointNumber, points[0].identity.key),
          points,
        ),
      ).toBeUndefined();
    },
  );
  it.each([-1, 0.5, NaN, Infinity, 99, "0", [0]].map((index) => ({ index })))(
    "rejects invalid scatter index $index",
    ({ index }) => {
      expect(
        spatialPickedPoint(
          event(trace("points")[0], index, points[0].identity.key),
          points,
        ),
      ).toBeUndefined();
    },
  );
  it("rejects malformed, mismatched, unsupported and stale trace metadata", () => {
    const data = trace("surface")[0];
    const variants = [
      { ...data, customdata: "artifact-row-0" },
      { ...data, customdata: [["artifact-row-0"], ["artifact-row-3"]] },
      {
        ...data,
        customdata: [
          ["artifact-row-0", "artifact-row-1", "artifact-row-2"],
          ["artifact-row-3"],
        ],
      },
      {
        ...data,
        customdata: [
          ["old-artifact-row", "old-artifact-row", "old-artifact-row"],
          ["old-artifact-row", "old-artifact-row", "old-artifact-row"],
        ],
      },
      { ...data, type: "heatmap" },
      { ...data, z: [[0]] },
    ];
    for (const variant of variants)
      expect(
        spatialPickedPoint(
          event(variant, [0, 0], points[0].identity.key),
          points,
        ),
      ).toBeUndefined();
    expect(
      spatialPickedPoint(event(data, [0, 0], points[0].identity.key), []),
    ).toBeUndefined();
    expect(
      spatialPickedPoint(
        { points: [{ customdata: points[0].identity.key }] },
        points,
      ),
    ).toBeUndefined();
  });
  it("rejects stale scatter identities and mismatched coordinate dimensions", () => {
    const data = trace("points")[0];
    for (const variant of [
      { ...data, customdata: Array(6).fill("old-artifact-row") },
      { ...data, customdata: Array(6).fill({ key: points[0].identity.key }) },
      { ...data, x: [] },
      { ...data, y: [] },
      { ...data, z: [] },
    ]) {
      expect(
        spatialPickedPoint(event(variant, 0, points[0].identity.key), points),
      ).toBeUndefined();
    }
  });
  it("returns the canonical current record instead of event-supplied record fields", () => {
    const pick = event(trace("points")[0], 2);
    Object.assign(pick.points[0], {
      record: { forged: true },
      identity: points[1].identity,
    });
    expect(spatialPickedPoint(pick, points)).toBe(points[2]);
    expect(
      spatialPickedPoint(
        pick,
        points.filter((point) => point !== points[2]),
      ),
    ).toBeUndefined();
  });
  it.each([
    null,
    undefined,
    {},
    { points: null },
    { points: [] },
    { points: [null] },
    { points: [{ data: null }] },
  ])("ignores malformed event %j", (input) => {
    expect(spatialPickedPoint(input, points)).toBeUndefined();
  });
});
