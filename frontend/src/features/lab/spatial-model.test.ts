import { describe, expect, it } from "vitest";
import {
  rectangularSurface,
  ribbonSlices,
  spatialAxes,
  spatialModel,
  type SpatialPoint,
} from "./spatial-model";
import type { TrendSource } from "./trends";
const row = (changes: Record<string, any> = {}) => ({
  clock_domain: "reference",
  clock_units: "s",
  clock_origin: 0,
  clock_session: "S",
  episode_id: "E",
  device_id: "pump",
  signal_id: "voltage",
  time: 0,
  available_at: 1,
  value: -0,
  other: 4,
  units: "V",
  field_units: { other: "A" },
  ...changes,
});
const source = (
  records: any[],
  changes: Partial<TrendSource> = {},
): TrendSource => ({
  runId: "run",
  artifactId: "artifact",
  kind: "original",
  records,
  ...changes,
});
const mapping = { x: "$time", y: "value", z: "other" };
const points = (model: ReturnType<typeof spatialModel>) =>
  model.groups.flatMap((group) => group.points);
describe("measured spatial evidence", () => {
  it("offers retained fields and labeled time coordinates without deriving a third variable", () => {
    expect(spatialAxes([source([row()])], "event")).toEqual([
      { id: "$time", label: "Event / sample time" },
      { id: "$availability", label: "Availability time" },
      { id: "value", label: "value" },
      { id: "other", label: "other" },
    ]);
    expect(spatialAxes([], "availability")).toEqual([
      { id: "$time", label: "Availability time" },
    ]);
  });
  it("preserves exact duplicate coordinates, signed zero, row identity, and source relationships", () => {
    const rows = [row({ source_record_ids: ["root-A", "root-B"] }), row()];
    const model = spatialModel(
      [source(rows, { rowIndices: [7, 12] })],
      mapping,
      "event",
    );
    expect(points(model).map((point) => point.coords)).toEqual([
      [0, -0, 4],
      [0, -0, 4],
    ]);
    expect(Object.is(points(model)[0].coords[1], -0)).toBe(true);
    expect(points(model).map((point) => point.identity.rowIndex)).toEqual([
      7, 12,
    ]);
    expect(new Set(points(model).map((point) => point.identity.key)).size).toBe(
      2,
    );
    expect(points(model)[0].identity.sourceRecordIds).toEqual([
      "root-A",
      "root-B",
    ]);
    expect(points(model)[0].record).toBe(rows[0]);
    expect(model.groups[0].axes.z.label).toBe("other · A");
  });
  it("never joins independently sampled rows to invent a complete XYZ observation", () => {
    const model = spatialModel(
      [
        source([
          row({ value: undefined }),
          row({ other: undefined }),
          row({ value: null }),
          row({ other: Infinity }),
          row({ missing_mask: { other: true } }),
          row({ missing_mask: { value: true }, estimated: true }),
        ]),
      ],
      mapping,
      "event",
    );
    expect(points(model)).toEqual([]);
    expect(model.omittedRows).toBe(6);
  });
  it("filters complete clocks and scopes incomplete clocks by capture and episode", () => {
    const model = spatialModel(
      [source([row(), row({ clock_session: "other" })])],
      mapping,
      "event",
    );
    expect(model.clocks).toHaveLength(2);
    expect(points(model)).toHaveLength(1);
    const incomplete = spatialModel(
      [
        source([
          row({ clock_session: undefined, capture_id: "A" }),
          row({ clock_session: undefined, capture_id: "B" }),
          row({
            clock_session: undefined,
            capture_id: "A",
            episode_id: "other",
          }),
        ]),
      ],
      mapping,
      "event",
    );
    expect(incomplete.clocks).toHaveLength(3);
    expect(points(incomplete)).toHaveLength(1);
    expect(incomplete.clock?.qualified).toBe(false);
  });
  it("keeps units, kinds, captures and stream discontinuities in separate groups", () => {
    const model = spatialModel(
      [
        source([
          row(),
          row({ time: 1, units: "A" }),
          row({ time: 2, discontinuity: true }),
          row({ time: 3, capture_id: "new" }),
        ]),
        source([row()], { artifactId: "derived", kind: "derived" }),
      ],
      mapping,
      "event",
    );
    expect(model.groups).toHaveLength(5);
    expect(
      spatialModel(
        [source([row()]), source([row()], { kind: "derived" })],
        mapping,
        "event",
        undefined,
        undefined,
        "derived",
      ).groups.every((group) => group.kind === "derived"),
    ).toBe(true);
  });
  it("applies inclusive numeric time windows and counts missing time separately from excluded rows", () => {
    const model = spatialModel(
      [
        source([
          row(),
          row({ time: 1 }),
          row({ time: 2 }),
          row({ time: undefined }),
        ]),
      ],
      mapping,
      "event",
      undefined,
      [1, 2],
    );
    expect(points(model).map((point) => point.coords[0])).toEqual([1, 2]);
    expect(model.filteredRows).toBe(1);
    expect(model.omittedRows).toBe(1);
  });
  it("uses recorded availability and retains assumed-time qualification", () => {
    const input = source([
      row({ availability_assumed: true }),
      row({ available_at: undefined }),
      row({ fit_availability: { clock_comparable: false } }),
    ]);
    const model = spatialModel(
      [input],
      { x: "$time", y: "$availability", z: "value" },
      "event",
    );
    expect(points(model)).toHaveLength(1);
    expect(points(model)[0].coords).toEqual([0, 1, -0]);
    expect(points(model)[0].qualifications).toEqual([
      "Availability time assumed",
    ]);
    expect(model.omittedRows).toBe(2);
  });
  it("rejects duplicate axes, unknown mappings, semantic availability duplication and invalid windows", () => {
    const input = [source([row()])];
    expect(
      spatialModel(input, { ...mapping, z: "value" }, "event").error,
    ).toContain("distinct");
    expect(
      spatialModel(input, { ...mapping, z: "invented" }, "event").error,
    ).toContain("retained");
    expect(
      spatialModel(
        input,
        { x: "$time", y: "$availability", z: "value" },
        "availability",
      ).error,
    ).toBeDefined();
    expect(
      spatialModel(input, mapping, "event", undefined, [2, 1]).error,
    ).toBeDefined();
  });
});
function point(x: number, y: number, z: number, index: number): SpatialPoint {
  return {
    coords: [x, y, z],
    identity: {
      key: String(index),
      artifactId: "artifact",
      runId: "run",
      rowIndex: index,
      kind: "original",
      sourceRecordIds: [],
    },
    record: {},
    qualifications: [],
    groupKey: "one-measured-grid",
    estimatedAxes: { x: false, y: false, z: false },
  };
}
const grid = [
  point(2, 20, 4, 0),
  point(1, 10, -0, 1),
  point(2, 10, 2, 2),
  point(1, 20, 3, 3),
];
describe("explicit measured rectangular surfaces", () => {
  it("sorts numeric axes and retains exact point cells without interpolation", () => {
    const surface = rectangularSurface(grid);
    expect(surface.ok).toBe(true);
    if (!surface.ok) throw new Error(surface.reason);
    expect(surface.x).toEqual([1, 2]);
    expect(surface.y).toEqual([10, 20]);
    expect(surface.z).toEqual([
      [-0, 2],
      [3, 4],
    ]);
    expect(surface.points[0][0]).toBe(grid[1]);
  });
  it.each(
    [
      [],
      grid.slice(1),
      [...grid, grid[0]],
      [point(0, 0, 1, 0), point(1, 0, 2, 1)],
      [...grid.slice(1), point(2, 20, NaN, 5)],
    ].map((input) => ({ input })),
  )(
    "rejects insufficient, incomplete, duplicated and nonfinite grids",
    ({ input }) => {
      const surface = rectangularSurface(input);
      expect(surface.ok).toBe(false);
      if (!surface.ok) expect(surface.reason.length).toBeGreaterThan(0);
    },
  );
  it("treats signed-zero X/Y duplicates as one physical coordinate and rejects them", () => {
    const zeroGrid = [
      point(0, 0, 1, 0),
      point(1, 0, 2, 1),
      point(0, 1, 3, 2),
      point(1, 1, 4, 3),
    ];
    expect(rectangularSurface([...zeroGrid, point(-0, 0, 1, 8)]).ok).toBe(
      false,
    );
  });
  it("rejects an assembled grid across provenance, clock or unit groups", () => {
    const foreign = { ...grid[0], groupKey: "unrelated-source" };
    expect(rectangularSurface([foreign, ...grid.slice(1)]).ok).toBe(false);
  });
});

it("qualifies only explicitly estimated coordinate fields", () => {
  const model = spatialModel(
    [
      source([
        row({ estimated: true, field_quality: { other: { estimated: true } } }),
      ]),
    ],
    mapping,
    "event",
  );
  expect(points(model)[0].estimatedAxes).toEqual({
    x: false,
    y: true,
    z: true,
  });
  const ordinary = spatialModel(
    [source([row({ estimated: true })])],
    mapping,
    "event",
  );
  expect(points(ordinary)[0].estimatedAxes).toEqual({
    x: false,
    y: true,
    z: false,
  });
});

it("does not combine same-named signals or unknown units across independent artifacts", () => {
  const model = spatialModel(
    [
      source([row({ units: undefined })]),
      source([row({ units: undefined })], { artifactId: "another" }),
    ],
    mapping,
    "event",
  );
  expect(model.groups).toHaveLength(2);
  expect(rectangularSurface(points(model)).ok).toBe(false);
});

describe("independent measured ribbon slices", () => {
  it("sorts permuted observations within each Y slice and allows unequal measured X grids", () => {
    const input = [
      point(8, 20, 4, 0),
      point(1, 10, -0, 1),
      point(2, 10, 2, 2),
      point(3, 20, 3, 3),
      point(5, 20, 6, 4),
    ];
    const result = ribbonSlices(input);
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error(result.reason);
    expect(result.width).toBe(6);
    expect(
      result.slices.map((slice) => ({
        y: slice.y,
        coordinates: slice.points.map((item) => item.coords),
      })),
    ).toEqual([
      {
        y: 10,
        coordinates: [
          [1, 10, -0],
          [2, 10, 2],
        ],
      },
      {
        y: 20,
        coordinates: [
          [3, 20, 3],
          [5, 20, 6],
          [8, 20, 4],
        ],
      },
    ]);
    expect(result.slices[0].points[0]).toBe(input[1]);
    expect(result.slices[1].points[2].identity.rowIndex).toBe(0);
    expect(input[0].coords).toEqual([8, 20, 4]);
    expect(rectangularSurface(input).ok).toBe(false);
  });
  it("uses only visual width for a single slice and preserves signed-zero evidence", () => {
    const input = [point(-0, 5, -0, 0), point(2, 5, 3, 1)];
    const result = ribbonSlices(input);
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error(result.reason);
    expect(result.width).toBe(1);
    expect(Object.is(result.slices[0].points[0].coords[0], -0)).toBe(true);
    expect(result.slices[0].points[0]).toBe(input[0]);
  });
  it("uses the minimum measured Y gap solely to choose visual extent", () => {
    const input = [0, 10, 12].flatMap((y, index) => [
      point(0, y, 1, index * 2),
      point(1, y, 2, index * 2 + 1),
    ]);
    const result = ribbonSlices(input);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.width).toBeCloseTo(1.2);
  });
  it.each([
    { input: [] },
    { input: [point(0, 1, 2, 0)] },
    { input: [...grid, grid[0]] },
    { input: [...grid, point(0, 30, 1, 7)] },
    { input: [{ ...grid[0], groupKey: "other" }, ...grid.slice(1)] },
    { input: [point(0, 1, Infinity, 0), point(1, 1, 3, 1)] },
  ])(
    "rejects absent, singleton, duplicate, mixed and invalid measured slices",
    ({ input }) => {
      const result = ribbonSlices(input);
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.reason.length).toBeGreaterThan(0);
    },
  );
});
