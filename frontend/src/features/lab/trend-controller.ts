import { useEffect, useMemo, useState } from "react";
import type { ChartZoom } from "./chart";
import { spatialModel, type SpatialMapping } from "./spatial-model";
import { sceneCamera, type SceneCamera } from "./spatial-runtime";
import {
  trendFields,
  trendIdentity,
  trendModel,
  fieldValue,
  trendTime,
  type TimeBasis,
  type TrendPoint,
  type TrendSource,
} from "./trends";

export function useTrendController(
  sources: TrendSource[],
  defaultBasis: TimeBasis = "event",
  defaultView: "2d" | "3d" = "2d",
) {
  const [view, setView] = useState(defaultView);
  const [mappingChoice, setMapping] = useState<SpatialMapping | null>(null);
  const [groupChoice, setGroup] = useState<string | null>(null);
  const [geometry, setGeometry] = useState<"points" | "surface" | "slices">(
    "points",
  );
  const [opacity, setOpacity] = useState(0.85);
  const [camera, setCamera] = useState<SceneCamera | undefined>();
  const [cameraRevision, setCameraRevision] = useState(0);
  const [basis, setBasis] = useState<TimeBasis>(defaultBasis);
  const [chosenFields, setFields] = useState<string[] | null>(null);
  const [clockKey, setClockKey] = useState<string | undefined>();
  const [selectionKey, setSelectionKey] = useState<string | null>(null);
  const [preview, setPreview] = useState<TrendPoint | null>(null);
  const [range, setRange] = useState<[number, number] | null>(null);
  const [playing, setPlaying] = useState(false);
  const availableFields = useMemo(() => trendFields(sources), [sources]);
  const fields = useMemo(
    () =>
      chosenFields ??
      (availableFields.includes("value")
        ? ["value"]
        : availableFields.slice(0, 1)),
    [chosenFields, availableFields],
  );
  const model = useMemo(
    () => trendModel(sources, fields, basis, clockKey),
    [sources, fields, basis, clockKey],
  );
  const mapping = useMemo<SpatialMapping>(() => {
    if (mappingChoice) return mappingChoice;
    // Defaults require fields that coexist on one observation, never a join.
    for (const source of sources)
      for (const row of source.records) {
        const candidates = availableFields.filter(
          (field) =>
            typeof fieldValue(row, field) === "number" &&
            Number.isFinite(fieldValue(row, field)) &&
            row.missing_mask?.[field] !== true,
        );
        if (trendTime(row, basis) === undefined) continue;
        if (candidates.length > 1)
          return { x: "$time", y: candidates[0], z: candidates[1] };
        if (
          candidates.length &&
          basis === "event" &&
          trendTime(row, "availability") !== undefined
        )
          return { x: "$time", y: "$availability", z: candidates[0] };
      }
    return { x: "$time", y: availableFields[0] ?? "", z: "" };
  }, [mappingChoice, sources, availableFields, basis]);
  const spatial = useMemo(
    () => spatialModel(sources, mapping, basis, model.clock?.key, range),
    [sources, mapping, basis, model.clock?.key, range],
  );
  const spatialGroup =
    spatial.groups.find((group) => group.key === groupChoice) ??
    spatial.groups.find((group) => group.kind === "derived") ??
    spatial.groups[0];
  const observations = useMemo(
    () =>
      sources.flatMap((source) =>
        source.records.map((record, index) => ({
          identity: trendIdentity(source, record, index),
          record,
        })),
      ),
    [sources],
  );
  const visiblePoints = useMemo(
    () =>
      view === "3d"
        ? (spatialGroup?.points ?? []).map((point) => ({
            identity: point.identity,
            record: point.record,
            value: [point.coords[0], null] as [number, null],
          }))
        : model.series.flatMap((series) => series.points),
    [model, view, spatialGroup, basis],
  );
  const selected =
    observations.find((point) => point.identity.key === selectionKey) ??
    visiblePoints.find((point) => point.identity.kind === "derived") ??
    visiblePoints[0];
  const kind = selected?.identity.kind ?? "derived";
  const samples = useMemo(
    () =>
      [
        ...new Map(
          visiblePoints
            .filter((point) => view === "3d" || point.identity.kind === kind)
            .map((point) => [point.identity.key, point]),
        ).values(),
      ].sort((left, right) => left.identity.rowIndex - right.identity.rowIndex),
    [visiblePoints, kind, view],
  );
  const cursor = selected
    ? samples.findIndex((point) => point.identity.key === selected.identity.key)
    : -1;
  function select(key: string) {
    if (observations.some((point) => point.identity.key === key)) {
      setSelectionKey(key);
      setPlaying(false);
    }
  }
  function pointEvent(data: unknown, pin: boolean) {
    const point = data as TrendPoint | null;
    if (
      !point?.identity?.key ||
      !visiblePoints.some(
        (candidate) => candidate.identity.key === point.identity.key,
      )
    ) {
      if (!pin) setPreview(null);
      return;
    }
    if (pin) select(point.identity.key);
    else {
      const canonical = visiblePoints.find(
        (candidate) => candidate.identity.key === point.identity.key,
      );
      if (canonical) setPreview(canonical);
    }
  }
  const navigate = (index: number) => {
    const point = samples[Math.max(0, Math.min(samples.length - 1, index))];
    if (point) select(point.identity.key);
  };
  useEffect(() => {
    if (!playing || samples.length < 2) return;
    const timer = setTimeout(() => {
      if (cursor >= samples.length - 1) {
        setPlaying(false);
        return;
      }
      setSelectionKey(samples[Math.max(0, cursor + 1)].identity.key);
    }, 150);
    return () => clearTimeout(timer);
  }, [playing, cursor, samples]);
  function zoom(payload: ChartZoom) {
    const event = payload.batch?.[0] ?? payload;
    if (!model.extent) return;
    const [min, max] = model.extent;
    const finite = (value: unknown): value is number =>
      typeof value === "number" && Number.isFinite(value);
    const start = finite(event.startValue)
      ? event.startValue
      : finite(event.start)
        ? min + ((max - min) * event.start) / 100
        : undefined;
    const end = finite(event.endValue)
      ? event.endValue
      : finite(event.end)
        ? min + ((max - min) * event.end) / 100
        : undefined;
    if (start !== undefined && end !== undefined && start < end)
      setRange([start, end]);
  }
  return {
    view,
    mapping,
    spatial,
    spatialGroup,
    geometry,
    opacity,
    camera,
    cameraRevision,
    model,
    sources,
    fields:
      view === "3d"
        ? [
            ...new Set(
              Object.values(mapping).filter((field) => !field.startsWith("$")),
            ),
          ]
        : fields,
    availableFields,
    basis,
    range,
    selected,
    preview,
    playing,
    cursor,
    samples,
    select,
    navigate,
    pointEvent,
    zoom,
    setRange,
    setPlaying,
    reset: () => {
      setBasis(defaultBasis);
      setFields(null);
      setClockKey(undefined);
      setSelectionKey(null);
      setPreview(null);
      setRange(null);
      setPlaying(false);
      setView(defaultView);
      setMapping(null);
      setGroup(null);
      setCamera(undefined);
      setCameraRevision((value) => value + 1);
    },
    changeClock: (key: string) => {
      setClockKey(key);
      setRange(null);
      setSelectionKey(null);
      setPreview(null);
      setPlaying(false);
      setGroup(null);
      setCamera(undefined);
      setCameraRevision((value) => value + 1);
    },
    changeBasis: (next: TimeBasis) => {
      setBasis(next);
      setRange(null);
      setPreview(null);
      setPlaying(false);
      setMapping(null);
      setGroup(null);
      setSelectionKey(null);
      setCamera(undefined);
      setCameraRevision((value) => value + 1);
    },
    changeView: (next: "2d" | "3d") => {
      if (next === "3d" && view !== "3d") {
        const group = spatial.groups.find((candidate) =>
          candidate.points.some(
            (point) => point.identity.key === selected?.identity.key,
          ),
        );
        setGroup(group?.key ?? null);
        if (!group) setSelectionKey(null);
        setCamera(undefined);
        setCameraRevision((value) => value + 1);
      }
      setView(next);
      setPreview(null);
      setPlaying(false);
    },
    changeMapping: (axis: keyof SpatialMapping, field: string) => {
      setMapping((current) => ({ ...(current ?? mapping), [axis]: field }));
      setGroup(null);
      setSelectionKey(null);
      setPreview(null);
      setPlaying(false);
      setCamera(undefined);
      setCameraRevision((value) => value + 1);
    },
    changeGroup: (key: string) => {
      setGroup(key);
      setSelectionKey(null);
      setPreview(null);
      setPlaying(false);
      setCamera(undefined);
      setCameraRevision((value) => value + 1);
    },
    changeGeometry: (next: "points" | "surface" | "slices") => {
      setGeometry(next);
      setPreview(null);
    },
    changeOpacity: (next: number) => {
      if (Number.isFinite(next) && next >= 0.1 && next <= 1) setOpacity(next);
    },
    changeCamera: (value: SceneCamera) => {
      const next = sceneCamera(value);
      if (next) setCamera(next);
    },
    resetCamera: () => {
      setCamera(undefined);
      setCameraRevision((value) => value + 1);
    },
    toggleField: (field: string, enabled: boolean) => {
      setFields(
        enabled
          ? [...new Set([...fields, field])]
          : fields.filter((value) => value !== field),
      );
      setPreview(null);
      setPlaying(false);
    },
  };
}
export type TrendController = ReturnType<typeof useTrendController>;
