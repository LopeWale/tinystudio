import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  ControlTrajectories,
  controlTrajectoryGroups,
} from "./control-trajectories";

const chart = vi.hoisted(() => ({ current: undefined as any, mounts: 0 }));
vi.mock("./chart", () => ({
  Chart: (props: any) => {
    const [mount] = useState(() => ++chart.mounts);
    chart.current = { ...props, mount };
    return (
      <div
        role="group"
        aria-label={props.label}
        tabIndex={0}
        onKeyDown={props.onKeyDown}
      />
    );
  },
}));
// The BoardUI choice boundary is covered by app tests; these checks exercise
// filter state and chart-to-record selection without a canvas renderer.
vi.mock("./inputs", () => ({
  LabChoice: (props: any) => (
    <select
      aria-label={props.label}
      value={props.value}
      onChange={(event) => props.onChange(event.target.value)}
    >
      {props.options.map((option: any) => (
        <option key={option.id} value={option.id}>
          {option.name}
        </option>
      ))}
    </select>
  ),
}));
afterEach(() => {
  cleanup();
  chart.current = undefined;
});
const row = (
  id: string,
  step: number,
  arm = "policy",
  overrides: Record<string, unknown> = {},
) => ({
  id,
  record: {
    episode: 0,
    seed: 40,
    step,
    arm,
    source: "heldout_environment",
    observation: [999, 0, 888],
    next_observation: [2, 0.5, 5],
    ...overrides,
  },
});
const series = () => chart.current.option.series as any[];

it("plots completed next-observation state with distinct arm targets and independently known absolute errors", () => {
  render(
    <ControlTrajectories
      rows={[
        row("p", 0),
        row("b", 0, "baseline", { next_observation: [-1, 0, -3] }),
      ]}
      onSelect={vi.fn()}
    />,
  );
  expect(series().map((entry) => [entry.name, entry.data[0].value])).toEqual([
    ["Policy", [1, 2]],
    ["Policy target", [1, 5]],
    ["Baseline", [1, -1]],
    ["Baseline target", [1, -3]],
  ]);
  fireEvent.change(screen.getByLabelText("Trajectory metric"), {
    target: { value: "error" },
  });
  expect(series().map((entry) => entry.data[0].value)).toEqual([
    [1, 3],
    [1, 2],
  ]);
  expect(chart.current.option.xAxis.name).toContain("Completed transition");
  expect(chart.current.option.yAxis.name).toContain("unit not recorded");
});

it("keeps different seeds in separate trajectories and selects a raw row when changing filter", () => {
  const onSelect = vi.fn();
  const rows = [row("a", 0), row("b", 1, "policy", { seed: 41 })];
  const view = render(
    <ControlTrajectories rows={rows} selectedId="a" onSelect={onSelect} />,
  );
  expect(series()[0].data.map((point: any) => point.rowId)).toEqual(["a"]);
  fireEvent.change(screen.getByLabelText("Episode and seed"), {
    target: { value: "[0,41]" },
  });
  expect(series()[0].data.map((point: any) => point.rowId)).toEqual(["b"]);
  expect(onSelect).toHaveBeenLastCalledWith("b");
  view.rerender(
    <ControlTrajectories rows={rows} selectedId="b" onSelect={onSelect} />,
  );
  view.rerender(
    <ControlTrajectories rows={rows} selectedId="a" onSelect={onSelect} />,
  );
  expect(series()[0].data[0].rowId).toBe("a");
});

it("breaks missing steps without allocating a large step range and reports incomplete pairs", () => {
  render(
    <ControlTrajectories
      rows={[row("p0", 0), row("p2", 2), row("far", 1_000_000_000)]}
      onSelect={vi.fn()}
    />,
  );
  expect(series()[0].data.map((point: any) => point.value)).toEqual([
    [1, 2],
    [2, null],
    [3, 2],
    [4, null],
    [1_000_000_001, 2],
  ]);
  expect(series()[0].connectNulls).toBe(false);
  expect(screen.getByRole("status")).toHaveTextContent(
    "3 incomplete policy/baseline pairs",
  );
});

it("rejects both copies of ambiguous identity, including corrupt duplicates, and breaks that step", () => {
  const model = controlTrajectoryGroups([
    row("a", 0),
    row("b", 1),
    row("corrupt", 1, "policy", { next_observation: [NaN, 0, 1] }),
    row("c", 2),
  ]);
  expect(model.rejected).toBe(2);
  expect(model.groups[0].points.map((point) => point.id)).toEqual(["a", "c"]);
});

it("rejects unsafe identities and nonfinite observations without coercing strings", () => {
  const invalid = [
    row("string", 0, "policy", { seed: "40" }),
    row("fraction", 0.5),
    row("unsafe", Number.MAX_SAFE_INTEGER),
    row("negative", -1),
    row("episode", 0, "policy", { episode: Infinity }),
    row("bad", 0, "policy", { next_observation: [1, Infinity, 2] }),
    row("overflow", 1, "policy", {
      next_observation: [Number.MAX_VALUE, 0, -Number.MAX_VALUE],
    }),
  ];
  const model = controlTrajectoryGroups(invalid);
  expect(model.rejected).toBe(7);
  expect(model.groups.flatMap((group) => group.points)).toEqual([]);
});

it("links chart clicks and keyboard traversal to exact raw rows and ignores gap points", () => {
  const onSelect = vi.fn();
  const rows = [row("p0", 0), row("b0", 0, "baseline"), row("p1", 1)];
  const view = render(
    <ControlTrajectories rows={rows} selectedId="p0" onSelect={onSelect} />,
  );
  chart.current.onPoint(series()[2].data[0]);
  expect(onSelect).toHaveBeenLastCalledWith("b0");
  onSelect.mockClear();
  chart.current.onPoint({ value: [2, null] });
  chart.current.onPoint({ rowId: "stale" });
  expect(onSelect).not.toHaveBeenCalled();
  const plot = screen.getByRole("group", { name: "Control trajectory chart" });
  fireEvent.keyDown(plot, { key: "ArrowRight" });
  expect(onSelect).toHaveBeenLastCalledWith("b0");
  view.rerender(
    <ControlTrajectories rows={rows} selectedId="b0" onSelect={onSelect} />,
  );
  fireEvent.keyDown(plot, { key: "ArrowRight" });
  expect(onSelect).toHaveBeenLastCalledWith("p1");
  fireEvent.keyDown(plot, { key: "Home" });
  expect(onSelect).toHaveBeenLastCalledWith("p0");
  fireEvent.keyDown(plot, { key: "End" });
  expect(onSelect).toHaveBeenLastCalledWith("p1");
});

it("initializes a remounted view from the parent-selected raw row", () => {
  render(
    <ControlTrajectories
      rows={[row("a", 0), row("b", 0, "baseline", { seed: 99 })]}
      selectedId="b"
      onSelect={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("Episode and seed")).toHaveValue("[0,99]");
  expect(series()[2].data[0].rowId).toBe("b");
});

it("clears evidence selection for an empty group and resets chart viewport only for group or metric changes", () => {
  const onSelect = vi.fn();
  const rows = [
    row("a", 0),
    row("b", 0, "policy", { seed: 41 }),
    row("invalid", 0, "policy", { seed: 42, next_observation: [NaN, 0, 1] }),
  ];
  const view = render(
    <ControlTrajectories rows={rows} selectedId="a" onSelect={onSelect} />,
  );
  const firstMount = chart.current.mount;
  view.rerender(
    <ControlTrajectories
      rows={rows}
      selectedId={undefined}
      onSelect={onSelect}
    />,
  );
  expect(chart.current.mount).toBe(firstMount);
  fireEvent.change(screen.getByLabelText("Trajectory metric"), {
    target: { value: "error" },
  });
  expect(chart.current.mount).not.toBe(firstMount);
  const metricMount = chart.current.mount;
  fireEvent.change(screen.getByLabelText("Episode and seed"), {
    target: { value: "[0,41]" },
  });
  expect(chart.current.mount).not.toBe(metricMount);
  fireEvent.change(screen.getByLabelText("Episode and seed"), {
    target: { value: "[0,42]" },
  });
  expect(onSelect).toHaveBeenLastCalledWith(undefined);
  expect(
    screen.queryByRole("group", { name: "Control trajectory chart" }),
  ).not.toBeInTheDocument();
});

it("preserves actual ECharts zoom when selecting a raw trajectory record", async () => {
  const echarts = await import("echarts");
  // SVG SSR needs only deterministic text geometry, not a browser canvas.
  echarts.setPlatformAPI({
    measureText: (text) => ({ width: text.length * 7 }),
  });
  const runtime = echarts.init(null, undefined, {
    renderer: "svg",
    ssr: true,
    width: 800,
    height: 360,
  });
  const rows = [row("a", 0), row("b", 1), row("c", 2), row("d", 3)];
  const view = render(
    <ControlTrajectories rows={rows} selectedId="a" onSelect={vi.fn()} />,
  );
  const merge = {
    replaceMerge: ["series", "grid", "xAxis", "yAxis", "dataZoom", "visualMap"],
  };
  try {
    runtime.setOption(chart.current.option, merge);
    runtime.dispatchAction({ type: "dataZoom", start: 25, end: 75 });
    expect(
      (runtime.getOption().dataZoom as any[]).map((zoom) => [
        zoom.start,
        zoom.end,
      ]),
    ).toEqual([
      [25, 75],
      [25, 75],
    ]);
    view.rerender(
      <ControlTrajectories rows={rows} selectedId="b" onSelect={vi.fn()} />,
    );
    runtime.setOption(chart.current.option, merge);
    expect(
      (runtime.getOption().dataZoom as any[]).map((zoom) => [
        zoom.start,
        zoom.end,
      ]),
    ).toEqual([
      [25, 75],
      [25, 75],
    ]);
  } finally {
    runtime.dispose();
  }
});
