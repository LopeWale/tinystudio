import { clockReference } from "./clock";
import {
  fieldValue,
  trendFields,
  trendIdentity,
  trendModel,
  trendTime,
  type TimeBasis,
  type TrendIdentity,
  type TrendRecord,
  type TrendSource,
} from "./trends";

export type SpatialMapping = { x: string; y: string; z: string };
export type SpatialPoint = {
  coords: [number, number, number];
  identity: TrendIdentity;
  record: TrendRecord;
  qualifications: string[];
  groupKey: string;
  estimatedAxes: { x: boolean; y: boolean; z: boolean };
};
export type SpatialAxis = { id: string; label: string; unit: string };
export type SpatialGroup = {
  key: string;
  label: string;
  kind: TrendSource["kind"];
  axes: { x: SpatialAxis; y: SpatialAxis; z: SpatialAxis };
  points: SpatialPoint[];
};
const axisNames = ["x", "y", "z"] as const;
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const timeLabel = (basis: TimeBasis) =>
  basis === "event" ? "Event / sample time" : "Availability time";
export function spatialAxes(
  sources: TrendSource[],
  basis: TimeBasis,
): { id: string; label: string }[] {
  return [
    { id: "$time", label: timeLabel(basis) },
    ...(basis === "event"
      ? [{ id: "$availability", label: "Availability time" }]
      : []),
    ...trendFields(sources).map((field) => ({ id: field, label: field })),
  ];
}
function masked(row: TrendRecord, field: string): boolean {
  if (field === "$time" || field === "$availability") return false;
  return (
    row.missing_mask?.[field] === true ||
    (field.startsWith("features.") &&
      row.missing_mask?.features?.[field.slice(9)] === true)
  );
}
export function coordinateEstimated(row: TrendRecord, field: string): boolean {
  if (field === "value") return row.estimated === true;
  if (field === "$time" || field === "$availability") return false;
  const quality = row.field_quality?.[field];
  return quality === "estimated" || quality?.estimated === true;
}
function axis(
  field: string,
  row: TrendRecord,
  basis: TimeBasis,
  clockUnit: string,
): SpatialAxis {
  const recorded =
    field === "$time" || field === "$availability"
      ? clockUnit
      : field === "value"
        ? row.units
        : row.field_units?.[field];
  const unit =
    typeof recorded === "string" && recorded.trim()
      ? recorded.trim()
      : "units not recorded";
  return {
    id: field,
    label: `${field === "$time" ? timeLabel(basis) : field === "$availability" ? "Availability time" : field} · ${unit}`,
    unit,
  };
}
export function spatialModel(
  sources: TrendSource[],
  mapping: SpatialMapping,
  basis: TimeBasis,
  clockKey?: string,
  range?: [number, number] | null,
  kindFilter: TrendSource["kind"] | "all" = "all",
) {
  const model = trendModel(sources, [], basis, clockKey);
  const result = {
    clocks: model.clocks,
    clock: model.clock,
    groups: [] as SpatialGroup[],
    omittedRows: 0,
    filteredRows: 0,
    error: undefined as string | undefined,
  };
  const choices = new Set(
    spatialAxes(sources, basis).map((choice) => choice.id),
  );
  if (axisNames.some((name) => !choices.has(mapping[name]))) {
    result.error =
      "Choose three retained numeric fields or recorded time coordinates.";
    return result;
  }
  if (new Set(axisNames.map((name) => mapping[name])).size !== 3) {
    result.error = "Choose three distinct coordinate axes.";
    return result;
  }
  if (range && (!range.every(finite) || range[0] > range[1])) {
    result.error = "Choose a finite time range with start at or before end.";
    return result;
  }
  if (!model.clock) {
    result.error = "No matching clock is selected.";
    return result;
  }
  const groups = new Map<string, SpatialGroup>();
  const streams = new Map<
    string,
    { segment: number; lastTime?: number; clockKey?: string }
  >();
  for (const source of sources)
    source.records.forEach((record, index) => {
      if (kindFilter !== "all" && source.kind !== kindFilter) return;
      let clock: ReturnType<typeof clockReference> | undefined;
      try {
        clock = clockReference(record, source.sourceGroup ?? source.artifactId);
      } catch {
        /* Invalid metadata cannot establish a coordinate. */
      }
      const time = trendTime(record, basis);
      const streamKey = JSON.stringify([
        source.runId,
        source.artifactId,
        source.kind,
        record.device_id ?? null,
        record.signal_id ?? null,
        record.capture_id ??
          record.acquisition_id ??
          record.source_artifact_id ??
          source.sourceGroup ??
          source.artifactId,
        record.episode_id ?? null,
        record.segment_id ?? null,
      ]);
      const stream = streams.get(streamKey) ?? { segment: 0 };
      if (
        !clock ||
        (stream.clockKey !== undefined && stream.clockKey !== clock.key) ||
        record.discontinuity === true ||
        record.clock_reset === true ||
        time === undefined ||
        (stream.lastTime !== undefined && time < stream.lastTime)
      )
        stream.segment++;
      stream.lastTime = time;
      stream.clockKey = clock?.key ?? "invalid";
      streams.set(streamKey, stream);
      if (!clock) {
        result.omittedRows++;
        return;
      }
      if (clock.key !== model.clock!.key) return;
      if (range) {
        if (time === undefined) {
          result.omittedRows++;
          return;
        }
        if (time < range[0] || time > range[1]) {
          result.filteredRows++;
          return;
        }
      }
      const fields = axisNames.map((name) => mapping[name]);
      const coordinates = fields.map((field) =>
        masked(record, field)
          ? undefined
          : field === "$time"
            ? time
            : field === "$availability"
              ? trendTime(record, "availability")
              : fieldValue(record, field),
      );
      if (!coordinates.every(finite)) {
        result.omittedRows++;
        return;
      }
      const axes = {
        x: axis(mapping.x, record, basis, model.clock!.unit),
        y: axis(mapping.y, record, basis, model.clock!.unit),
        z: axis(mapping.z, record, basis, model.clock!.unit),
      };
      const key = JSON.stringify([
        streamKey,
        clock.key,
        stream.segment,
        axisNames.map((name) => axes[name].unit),
      ]);
      if (!groups.has(key))
        groups.set(key, {
          key,
          label: `${source.kind === "original" ? "Original" : "Derived"} · ${[record.device_id, record.signal_id].filter((value) => value != null).join(" / ") || "Signal"}`,
          kind: source.kind,
          axes,
          points: [],
        });
      groups.get(key)!.points.push({
        coords: coordinates as [number, number, number],
        groupKey: key,
        estimatedAxes: {
          x: coordinateEstimated(record, mapping.x),
          y: coordinateEstimated(record, mapping.y),
          z: coordinateEstimated(record, mapping.z),
        },
        identity: trendIdentity(source, record, index),
        record,
        qualifications:
          record.availability_assumed === true &&
          (fields.includes("$availability") ||
            (fields.includes("$time") && basis === "availability"))
            ? ["Availability time assumed"]
            : [],
      });
    });
  result.groups = [...groups.values()];
  if (!result.groups.length)
    result.error =
      "No complete retained XYZ rows match the selected clock, fields and time range.";
  return result;
}
export type RectangularSurface =
  | {
      ok: true;
      x: number[];
      y: number[];
      z: number[][];
      points: SpatialPoint[][];
    }
  | { ok: false; reason: string };
/** Only a complete retained rectangular grid permits a surface; no resampling occurs. */
export function rectangularSurface(points: SpatialPoint[]): RectangularSurface {
  if (!points.length)
    return {
      ok: false,
      reason: "No retained points are available for a surface.",
    };
  if (
    !points[0].groupKey ||
    points.some((point) => point.groupKey !== points[0].groupKey)
  )
    return {
      ok: false,
      reason:
        "A surface requires points from one clock, source stream and unit group.",
    };
  if (points.some((point) => !point.coords.every(finite)))
    return {
      ok: false,
      reason: "Every surface coordinate must be finite and recorded.",
    };
  const cells = new Map<number, Map<number, SpatialPoint>>();
  for (const point of points) {
    const [x, y] = point.coords;
    if (!cells.has(y)) cells.set(y, new Map());
    if (cells.get(y)!.has(x))
      return {
        ok: false,
        reason:
          "Duplicate X/Y coordinates do not define one measured value per cell.",
      };
    cells.get(y)!.set(x, point);
  }
  const x = [...new Set(points.map((point) => point.coords[0]))].sort(
    (a, b) => a - b,
  );
  const y = [...cells.keys()].sort((a, b) => a - b);
  if (x.length < 2 || y.length < 2)
    return {
      ok: false,
      reason: "A surface requires at least two distinct X and Y coordinates.",
    };
  if (points.length !== x.length * y.length)
    return {
      ok: false,
      reason:
        "The measured rectangular grid has missing cells; interpolation is disabled.",
    };
  const matrix = y.map((ordinate) =>
    x.map((abscissa) => cells.get(ordinate)!.get(abscissa)!),
  );
  return {
    ok: true,
    x,
    y,
    z: matrix.map((row) => row.map((point) => point.coords[2])),
    points: matrix,
  };
}

export type RibbonSlices =
  | { ok: true; slices: { y: number; points: SpatialPoint[] }[]; width: number }
  | { ok: false; reason: string };
/** Independent measured Y slices. Width is display geometry, never an evidence coordinate. */
export function ribbonSlices(points: SpatialPoint[]): RibbonSlices {
  if (!points.length)
    return {
      ok: false,
      reason: "No retained points are available for ribbons.",
    };
  if (
    !points[0].groupKey ||
    points.some((point) => point.groupKey !== points[0].groupKey)
  )
    return {
      ok: false,
      reason:
        "Ribbons require points from one clock, source stream and unit group.",
    };
  if (points.some((point) => !point.coords.every(finite)))
    return {
      ok: false,
      reason: "Every ribbon coordinate must be finite and recorded.",
    };
  const slices = new Map<number, Map<number, SpatialPoint>>();
  for (const point of points) {
    const [x, y] = point.coords;
    if (!slices.has(y)) slices.set(y, new Map());
    const slice = slices.get(y)!;
    if (slice.has(x))
      return {
        ok: false,
        reason:
          "Duplicate X/Y coordinates do not define one measured value per ribbon position.",
      };
    slice.set(x, point);
  }
  if ([...slices.values()].some((slice) => slice.size < 2))
    return {
      ok: false,
      reason:
        "Every ribbon requires at least two distinct measured X coordinates.",
    };
  const ordered = [...slices.entries()]
    .sort(([left], [right]) => left - right)
    .map(([y, slice]) => ({
      y,
      points: [...slice.values()].sort(
        (left, right) => left.coords[0] - right.coords[0],
      ),
    }));
  // A single slice has no measured spacing: one display unit is the explicit visual fallback.
  const gaps = ordered
    .slice(1)
    .map((slice, index) => slice.y - ordered[index].y);
  const minimumGap = Math.min(...gaps);
  const width =
    gaps.length && finite(minimumGap) && minimumGap > 0
      ? minimumGap * 0.6 || minimumGap
      : 1;
  return { ok: true, slices: ordered, width };
}
