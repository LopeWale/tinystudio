import { clockReference } from "./clock";

/** Scientific trend data: chart coordinates never substitute for evidence identity. */
export type TrendRecord = Record<string, any>;
export type TimeBasis = "event" | "availability";
export type TrendSource = {
  runId: string;
  artifactId: string;
  sourceGroup?: string;
  kind: "original" | "derived";
  records: TrendRecord[];
  offset?: number;
  rowIndices?: number[];
};
export type TrendIdentity = {
  key: string;
  runId: string;
  artifactId: string;
  rowIndex: number;
  kind: TrendSource["kind"];
  sourceRecordIds: string[];
};
export type TrendPoint = {
  value: [number, number | null];
  identity: TrendIdentity;
  record: TrendRecord;
};
export type TrendClock = {
  key: string;
  label: string;
  unit: string;
  qualified: boolean;
};
export type TrendSeries = {
  key: string;
  label: string;
  laneKey: string;
  kind: TrendSource["kind"];
  field: string;
  points: TrendPoint[];
};
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const text = (value: unknown) =>
  typeof value === "string" && value.length ? value : undefined;
const metadata = new Set([
  "time",
  "event_time",
  "decision_at",
  "available_at",
  "source_position",
  "arrival_ordinal",
  "seed",
  "step",
  "index",
  "seq",
  "position",
  "timestamp",
  "clock_origin",
  "clock_epoch",
  "time_origin",
  "clock_session",
  "clock_reset",
]);
export const fieldValue = (row: TrendRecord, field: string): unknown =>
  field.startsWith("features.") ? row.features?.[field.slice(9)] : row[field];
export function exactNumber(value: unknown): string {
  return finite(value)
    ? Object.is(value, -0)
      ? "-0"
      : String(value)
    : "Missing";
}
export function trendFields(sources: TrendSource[]): string[] {
  const fields = new Set<string>();
  for (const { records } of sources)
    for (const row of records) {
      for (const field of Object.keys(row)) {
        if (
          field === "value" ||
          (!metadata.has(field) &&
            !/(?:^id$|_id$|_count$)/.test(field) &&
            finite(row[field]))
        )
          fields.add(field);
      }
      if (
        row.features &&
        typeof row.features === "object" &&
        !Array.isArray(row.features)
      ) {
        for (const [field, value] of Object.entries(row.features))
          if (finite(value)) fields.add(`features.${field}`);
      }
    }
  return [...fields];
}
export function trendTime(
  row: TrendRecord,
  basis: TimeBasis,
): number | undefined {
  if (
    basis === "availability" &&
    row.fit_availability?.clock_comparable === false
  )
    return undefined;
  const value =
    basis === "availability" ? row.available_at : (row.event_time ?? row.time);
  return finite(value) ? value : undefined;
}
export function trendIdentity(
  source: TrendSource,
  row: TrendRecord,
  index: number,
): TrendIdentity {
  const rowIndex = source.rowIndices?.[index] ?? (source.offset ?? 0) + index;
  const links = Array.isArray(row.source_record_ids)
    ? row.source_record_ids
    : [row.source_record_id ?? row.record_id];
  return {
    key: JSON.stringify([source.runId, source.artifactId, rowIndex]),
    runId: source.runId,
    artifactId: source.artifactId,
    rowIndex,
    kind: source.kind,
    sourceRecordIds: links.filter(
      (value: unknown): value is string => typeof value === "string",
    ),
  };
}
function clockFor(
  source: TrendSource,
  row: TrendRecord,
  index: number,
): TrendClock & { invalid?: boolean } {
  try {
    const clock = clockReference(row, source.sourceGroup ?? source.artifactId);
    return {
      key: clock.key,
      unit: clock.units ?? "time unit not recorded",
      qualified: clock.qualified,
      label:
        `${clock.domain ?? "Clock not recorded"} · ${clock.units ?? "time unit not recorded"}` +
        (clock.origin !== null
          ? ` · ${clock.origin}`
          : " · origin not recorded") +
        (clock.session !== null
          ? ` · session ${clock.session}`
          : " · session not recorded") +
        (clock.reset !== null ? ` · reset ${clock.reset}` : "") +
        (clock.qualified
          ? ""
          : ` · source ${JSON.stringify(clock.source_scope)}`),
    };
  } catch (error) {
    return {
      key: JSON.stringify([
        "invalid-clock",
        trendIdentity(source, row, index).key,
      ]),
      unit: "invalid clock metadata",
      qualified: false,
      invalid: true,
      label: `Invalid clock · ${error instanceof Error ? error.message : "invalid metadata"}`,
    };
  }
}
export function trendModel(
  sources: TrendSource[],
  fields: string[],
  basis: TimeBasis,
  clockKey?: string,
) {
  const clocks = new Map<string, TrendClock>();
  for (const source of sources)
    source.records.forEach((row, index) => {
      const clock = clockFor(source, row, index);
      clocks.set(clock.key, clock);
    });
  const clock =
    clockKey === undefined
      ? clocks.values().next().value
      : clocks.get(clockKey);
  const lanes = new Map<string, { key: string; label: string }>();
  const series = new Map<string, TrendSeries>();
  const segments = new Map<
    string,
    { index: number; lastTime?: number; clockKey?: string }
  >();
  let omittedTimeRows = 0;
  let extent: [number, number] | undefined;
  for (const source of sources)
    source.records.forEach((row, index) => {
      const rowClock = clockFor(source, row, index);
      const time = trendTime(row, basis);
      const signal =
        [row.device_id, row.signal_id]
          .filter((value) => value != null)
          .join(" / ") || "Signal";
      const capture =
        row.capture_id ??
        row.acquisition_id ??
        row.source_artifact_id ??
        source.sourceGroup ??
        source.artifactId;
      const streamKey = JSON.stringify([
        source.runId,
        source.artifactId,
        source.kind,
        row.device_id ?? null,
        row.signal_id ?? null,
        capture,
        row.episode_id ?? null,
        row.segment_id ?? null,
      ]);
      const segment = segments.get(streamKey) ?? { index: 0 };
      // Boundaries belong to the stream, even when a selected channel is absent.
      if (
        rowClock.invalid ||
        (segment.clockKey !== undefined && segment.clockKey !== rowClock.key) ||
        time === undefined ||
        row.discontinuity === true ||
        row.clock_reset === true ||
        (segment.lastTime !== undefined && time < segment.lastTime)
      )
        segment.index++;
      segment.lastTime = rowClock.invalid ? undefined : time;
      segment.clockKey = rowClock.key;
      segments.set(streamKey, segment);
      if (!clock || rowClock.key !== clock.key) return;
      if (rowClock.invalid || time === undefined) {
        omittedTimeRows++;
        return;
      }
      extent = extent
        ? [Math.min(extent[0], time), Math.max(extent[1], time)]
        : [time, time];
      for (const field of new Set(fields)) {
        const present = field.startsWith("features.")
          ? Object.hasOwn(row.features ?? {}, field.slice(9))
          : Object.hasOwn(row, field) ||
            Object.hasOwn(row.missing_mask ?? {}, field) ||
            Object.hasOwn(row.field_units ?? {}, field);
        if (!present) continue;
        const unit =
          field === "value" ? text(row.units) : text(row.field_units?.[field]);
        const laneKey = unit
          ? JSON.stringify(["unit", unit])
          : JSON.stringify(["unknown-unit", signal, field]);
        lanes.set(laneKey, {
          key: laneKey,
          label: unit ?? `Units not recorded · ${signal} ${field}`,
        });
        const key = JSON.stringify([streamKey, field, laneKey, segment.index]);
        if (!series.has(key))
          series.set(key, {
            key,
            label: `${source.kind === "original" ? "Original" : "Derived"} · ${signal} · ${field}`,
            laneKey,
            kind: source.kind,
            field,
            points: [],
          });
        const value = fieldValue(row, field);
        const masked =
          field === "value" &&
          row.missing_mask?.value === true &&
          row.estimated !== true;
        series.get(key)!.points.push({
          value: [time, finite(value) && !masked ? value : null],
          identity: trendIdentity(source, row, index),
          record: row,
        });
      }
    });
  const allSeries = [...series.values()];
  return {
    clocks: [...clocks.values()],
    clock,
    lanes: [...lanes.values()],
    series: allSeries,
    omittedTimeRows,
    extent,
  };
}
