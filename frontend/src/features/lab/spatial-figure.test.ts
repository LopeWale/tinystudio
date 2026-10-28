import { describe, expect, it } from "vitest";
import { spatialFigure, type SpatialTheme } from "./spatial-figure";
import {
  ribbonSlices,
  type SpatialGroup,
  type SpatialPoint,
} from "./spatial-model";
const theme: SpatialTheme = {
  text: "semantic-text",
  secondary: "semantic-secondary",
  border: "semantic-border",
  surface: "semantic-surface",
  colors: ["semantic-chart-low", "semantic-chart-high"],
  font: "semantic-font",
};
const options = {
  geometry: "points" as const,
  opacity: 0.7,
  revision: "measured-group",
  theme,
};
function point(
  x: number,
  y: number,
  z: number,
  rowIndex: number,
): SpatialPoint {
  return {
    coords: [x, y, z],
    groupKey: "one-measured-group",
    identity: {
      key: JSON.stringify(["run", "artifact", rowIndex]),
      runId: "run",
      artifactId: "artifact",
      rowIndex,
      kind: "original",
      sourceRecordIds: ["same-source-relationship"],
    },
    record: {},
    estimatedAxes: { x: false, y: false, z: false },
    qualifications: [],
  };
}
const group = (points: SpatialPoint[]): SpatialGroup => ({
  key: "one-measured-group",
  label: "Measured signal",
  kind: "original",
  axes: {
    x: { id: "x", label: "X · s", unit: "s" },
    y: { id: "y", label: "Y · direction", unit: "direction" },
    z: { id: "z", label: "Z · V", unit: "V" },
  },
  points,
});
describe("exact measured 3D figures", () => {
  it("sorts a permuted rectangular grid while keeping customdata at its exact measured cell", () => {
    const points = [
      point(2, 20, 4, 0),
      point(1, 10, -0, 1),
      point(2, 10, 2, 2),
      point(1, 20, 3, 3),
    ];
    const figure = spatialFigure(group(points), {
      ...options,
      geometry: "surface",
    });
    expect(figure.data).toHaveLength(1);
    expect(figure.data[0]).toMatchObject({
      type: "surface",
      x: [1, 2],
      y: [10, 20],
      z: [
        [-0, 2],
        [3, 4],
      ],
      customdata: [
        [points[1].identity.key, points[2].identity.key],
        [points[3].identity.key, points[0].identity.key],
      ],
      connectgaps: false,
    });
  });
  it("renders independent ribbons with explicit display Y edges and unmodified measured Z samples", () => {
    const points = [
      point(8, 20, 9, 0),
      point(1, 10, -3, 1),
      point(2, 10, 2, 2),
      point(3, 20, 3, 3),
    ];
    const ribbons = ribbonSlices(points);
    if (!ribbons.ok) throw new Error(ribbons.reason);
    const figure = spatialFigure(group(points), {
      ...options,
      geometry: "slices",
    });
    expect(figure.data).toHaveLength(2);
    figure.data.forEach((trace, index) => {
      const slice = ribbons.slices[index];
      expect(trace).toMatchObject({
        type: "surface",
        x: slice.points.map((item) => item.coords[0]),
        y: [slice.y - ribbons.width / 2, slice.y + ribbons.width / 2],
        z: [
          slice.points.map((item) => item.coords[2]),
          slice.points.map((item) => item.coords[2]),
        ],
        customdata: [
          slice.points.map((item) => item.identity.key),
          slice.points.map((item) => item.identity.key),
        ],
        cmin: -3,
        cmax: 9,
        showscale: index === 0,
        connectgaps: false,
      });
    });
    expect(points[0].coords).toEqual([8, 20, 9]);
  });
  it("pins only the exact artifact row key even when coordinates and source relationships repeat", () => {
    const points = [point(1, 2, 3, 7), point(1, 2, 3, 12)];
    const figure = spatialFigure(group(points), {
      ...options,
      selectedKey: points[1].identity.key,
    });
    expect(figure.data).toHaveLength(2);
    expect(figure.data[1]).toMatchObject({
      name: "Pinned record",
      x: [1],
      y: [2],
      z: [3],
      customdata: [points[1].identity.key],
    });
    expect(
      spatialFigure(group(points), {
        ...options,
        selectedKey: "same-source-relationship",
      }).data,
    ).toHaveLength(1);
    expect(
      spatialFigure(group(points), {
        ...options,
        selectedKey: "missing-artifact-row",
      }).data,
    ).toHaveLength(1);
  });
  it("preserves signed-zero scatter values and uses explicit estimated-field status", () => {
    const points = [
      point(-0, 2, -0, 0),
      { ...point(1, 2, 3, 1), estimatedAxes: { x: false, y: false, z: true } },
    ];
    const figure = spatialFigure(group(points), options);
    expect(figure.data[0]).toMatchObject({
      mode: "markers",
      x: [-0, 1],
      z: [-0, 3],
      marker: { symbol: ["circle", "diamond"] },
    });
  });
});
