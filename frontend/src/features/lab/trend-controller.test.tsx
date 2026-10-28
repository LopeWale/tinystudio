import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTrendController } from "./trend-controller";
import type { TrendSource } from "./trends";
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
const sources: TrendSource[] = [
  {
    runId: "R",
    artifactId: "A",
    kind: "derived",
    records: [
      {
        source_record_id: "shared-root",
        time: 0,
        available_at: 0,
        value: -1.25,
        units: "V",
        episode_id: "E",
      },
      {
        source_record_id: "shared-root",
        time: 0,
        available_at: 1,
        value: 0.125,
        units: "V",
        episode_id: "E",
      },
      {
        source_record_id: "third",
        time: 4,
        available_at: 4,
        value: 2.5,
        units: "V",
        episode_id: "E",
      },
    ],
  },
];
describe("shared trend selection and viewport", () => {
  it("pins an exact duplicate-time row and preserves zoom during selection and hover", () => {
    const { result } = renderHook(() => useTrendController(sources));
    const second = result.current.model.series[0].points[1];
    act(() => result.current.zoom({ batch: [{ start: 25, end: 75 }] }));
    expect(result.current.range).toEqual([1, 3]);
    act(() => result.current.pointEvent(second, true));
    expect(result.current.selected?.identity.rowIndex).toBe(1);
    expect(result.current.selected?.record.value).toBe(0.125);
    act(() =>
      result.current.pointEvent(
        result.current.model.series[0].points[2],
        false,
      ),
    );
    expect(result.current.preview?.record.value).toBe(2.5);
    expect(result.current.selected?.identity.rowIndex).toBe(1);
    expect(result.current.range).toEqual([1, 3]);
    act(() => result.current.pointEvent(null, false));
    expect(result.current.preview).toBeNull();
    expect(result.current.selected?.identity.rowIndex).toBe(1);
  });
  it("does not accept a stale point from a different artifact", () => {
    const { result } = renderHook(() => useTrendController(sources));
    const point = result.current.model.series[0].points[1];
    act(() =>
      result.current.pointEvent(
        {
          ...point,
          identity: { ...point.identity, key: '["other","artifact",1]' },
        },
        true,
      ),
    );
    expect(result.current.selected?.identity.rowIndex).toBe(0);
  });
  it("pauses playback on manual selection", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useTrendController(sources));
    act(() => result.current.setPlaying(true));
    act(() => vi.advanceTimersByTime(150));
    expect(result.current.selected?.identity.rowIndex).toBe(1);
    act(() => result.current.navigate(0));
    act(() => vi.advanceTimersByTime(600));
    expect(result.current.playing).toBe(false);
    expect(result.current.selected?.identity.rowIndex).toBe(0);
  });
  it("resets viewport when the clock or time basis changes", () => {
    const { result } = renderHook(() => useTrendController(sources));
    act(() => result.current.setRange([1, 3]));
    act(() => result.current.changeBasis("availability"));
    expect(result.current.range).toBeNull();
    expect(result.current.basis).toBe("availability");
    act(() => result.current.setRange([1, 3]));
    act(() => result.current.changeClock(result.current.model.clocks[0].key));
    expect(result.current.range).toBeNull();
  });
  it("keeps a selected artifact row on a record refresh and retains upstream source links separately", () => {
    const { result, rerender } = renderHook(
      ({ input }) => useTrendController(input),
      { initialProps: { input: sources } },
    );
    act(() => result.current.navigate(1));
    rerender({
      input: [
        {
          ...sources[0],
          records: sources[0].records.map((row) => ({ ...row })),
        },
      ],
    });
    expect(result.current.selected?.identity.rowIndex).toBe(1);
    expect(result.current.selected?.identity.sourceRecordIds).toEqual([
      "shared-root",
    ]);
  });
});
describe("3D evidence state", () => {
  it("keeps graph, pin and keyboard rows together when entering 3D from an original record", () => {
    const both: TrendSource[] = [
      { ...sources[0], sourceGroup: "same-recording", kind: "derived" },
      {
        ...sources[0],
        sourceGroup: "same-recording",
        artifactId: "RAW",
        kind: "original",
      },
    ];
    const { result } = renderHook(() => useTrendController(both));
    const original = result.current.model.series.find(
      (series) => series.kind === "original",
    )!.points[1];
    act(() => result.current.pointEvent(original, true));
    act(() => result.current.changeView("3d"));
    expect(result.current.spatialGroup?.kind).toBe("original");
    expect(
      result.current.samples.map((point) => point.identity.rowIndex),
    ).toEqual([0, 1, 2]);
    expect(result.current.cursor).toBe(1);
    expect(result.current.selected?.identity.artifactId).toBe("RAW");
    act(() => result.current.navigate(2));
    expect(result.current.selected?.record.value).toBe(2.5);
  });
  const rows: TrendSource[] = [
    {
      runId: "R3",
      artifactId: "A3",
      kind: "derived",
      records: [
        {
          time: 0,
          available_at: 0,
          value: 10,
          current: 1,
          units: "V",
          field_units: { current: "A" },
        },
        {
          time: 1,
          available_at: 1,
          value: 20,
          current: null,
          units: "V",
          field_units: { current: "A" },
        },
        {
          time: 2,
          available_at: 3,
          value: -30,
          current: 3,
          units: "V",
          field_units: { current: "A" },
        },
      ],
    },
  ];
  it("navigates the same exact XYZ observations that the graph and table display", () => {
    const { result } = renderHook(() =>
      useTrendController(rows, "event", "3d"),
    );
    expect(result.current.mapping).toEqual({
      x: "$time",
      y: "value",
      z: "current",
    });
    expect(result.current.samples.map((p) => p.identity.rowIndex)).toEqual([
      0, 2,
    ]);
    act(() => result.current.navigate(1));
    expect(result.current.selected?.identity.rowIndex).toBe(2);
    expect(result.current.selected?.record.value).toBe(-30);
    expect(result.current.spatialGroup?.points[1].coords).toEqual([2, -30, 3]);
  });
  it("retains camera on pin/opacity changes and resets it explicitly on mapping changes", () => {
    const { result } = renderHook(() =>
      useTrendController(rows, "event", "3d"),
    );
    const camera = { eye: { x: 2, y: 1, z: 3 } };
    act(() => result.current.changeCamera(camera));
    const revision = result.current.cameraRevision;
    act(() => {
      result.current.navigate(1);
      result.current.changeOpacity(0.5);
    });
    expect(result.current.camera).toEqual(camera);
    expect(result.current.cameraRevision).toBe(revision);
    act(() => result.current.changeMapping("x", "current"));
    expect(result.current.camera).toBeUndefined();
    expect(result.current.cameraRevision).toBe(revision + 1);
  });
  it("filters on retained time when X is another numerical field", () => {
    const { result } = renderHook(() =>
      useTrendController(rows, "event", "3d"),
    );
    act(() => {
      result.current.changeMapping("x", "current");
      result.current.changeMapping("z", "$availability");
    });
    act(() => result.current.setRange([1, 3]));
    expect(result.current.spatialGroup?.points.map((p) => p.coords)).toEqual([
      [3, -30, 3],
    ]);
    expect(result.current.samples.map((p) => p.identity.rowIndex)).toEqual([2]);
  });
  it("rejects stale or forged hover records and resolves current retained evidence", () => {
    const { result } = renderHook(() =>
      useTrendController(rows, "event", "3d"),
    );
    const point = result.current.spatialGroup!.points[1];
    act(() =>
      result.current.pointEvent({ ...point, record: { value: 999 } }, false),
    );
    expect(result.current.preview?.record.value).toBe(-30);
    act(() =>
      result.current.pointEvent(
        { ...point, identity: { ...point.identity, key: "stale" } },
        false,
      ),
    );
    expect(result.current.preview).toBeNull();
  });
});
