import type { SpatialPoint } from "./spatial-model";

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function index(value: unknown, length: number): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 0 &&
    value < length
  );
}

function matrix(
  value: unknown,
  rows: number,
  columns: number,
): value is unknown[][] {
  return (
    Array.isArray(value) &&
    value.length === rows &&
    value.every((row) => Array.isArray(row) && row.length === columns)
  );
}

/** Resolve renderer indices to current evidence, never renderer-supplied records. */
export function spatialPickedPoint(
  event: unknown,
  points: readonly SpatialPoint[],
): SpatialPoint | undefined {
  if (!object(event) || !Array.isArray(event.points)) return undefined;
  const pick: unknown = event.points[0];
  if (!object(pick) || !object(pick.data)) return undefined;
  const trace = pick.data;
  const { x, y, z, customdata } = trace;
  if (!Array.isArray(x) || !Array.isArray(y) || !Array.isArray(z))
    return undefined;
  let key: unknown;
  if (trace.type === "surface") {
    // Plotly 4.1.2 surface/convert.js emits [xIndex, yIndex], whereas
    // the figure's z and customdata matrices are stored as [y][x].
    const position = pick.pointNumber;
    if (
      !Array.isArray(position) ||
      position.length !== 2 ||
      !index(position[0], x.length) ||
      !index(position[1], y.length) ||
      !matrix(z, y.length, x.length) ||
      !matrix(customdata, y.length, x.length)
    )
      return undefined;
    key = customdata[position[1]][position[0]];
  } else if (trace.type === "scatter3d") {
    if (
      !Array.isArray(customdata) ||
      x.length !== customdata.length ||
      y.length !== customdata.length ||
      z.length !== customdata.length ||
      !index(pick.pointNumber, customdata.length)
    )
      return undefined;
    key = customdata[pick.pointNumber];
  }
  return typeof key === "string"
    ? points.find((point) => point.identity.key === key)
    : undefined;
}
