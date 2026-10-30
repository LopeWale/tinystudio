import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { TrendWorkbench } from "./trend-workbench";
import { useTrendController } from "./trend-controller";
import type { TrendSource } from "./trends";
const chartOptions = vi.hoisted(() => vi.fn());
// Keep settings, controller and figure construction real; replace renderer boundaries.
vi.mock("./chart", () => ({
  Chart: ({ option }: any) => {
    chartOptions(option);
    return <div data-testid="trend-chart" />;
  },
}));
vi.mock("./spatial-chart", () => ({
  SpatialChart: () => <div data-testid="spatial-chart" />,
}));
afterEach(() => {
  cleanup();
  chartOptions.mockClear();
});
const sources: TrendSource[] = [
  {
    runId: "run",
    artifactId: "derived",
    kind: "derived",
    records: [
      {
        time: 0,
        value: 1,
        current: 10,
        units: "V",
        field_units: { current: "A" },
      },
      {
        time: 1,
        value: 2,
        current: 20,
        units: "V",
        field_units: { current: "A" },
      },
    ],
  },
];
function Workspace() {
  const controller = useTrendController(sources, "event", "3d");
  return (
    <>
      <button onClick={() => controller.navigate(0)}>
        Pin first observation
      </button>
      <button onClick={() => controller.navigate(1)}>
        Pin second observation
      </button>
      <output data-testid="pinned-identity">
        {controller.selected?.identity.key}
      </output>
      <button onClick={() => controller.setRange([0.2, 0.8])}>
        Zoom evidence window
      </button>
      <output data-testid="trend-range">
        {JSON.stringify(controller.range)}
      </output>
      <TrendWorkbench controller={controller} />
    </>
  );
}
it("offers signal selection only for 2D and updates its real series while preserving the pin across dimensions", async () => {
  const user = userEvent.setup();
  render(<Workspace />);
  fireEvent.click(
    screen.getByRole("button", { name: "Pin second observation" }),
  );
  const pinned = '["run","derived",1]';
  expect(screen.getByTestId("pinned-identity")).toHaveTextContent(pinned);
  await user.click(screen.getByRole("button", { name: "Graph settings" }));
  expect(
    screen.queryByRole("group", { name: /signals/i }),
  ).not.toBeInTheDocument();
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "2D trends" }));
  expect(screen.getByTestId("pinned-identity")).toHaveTextContent(pinned);
  await user.click(screen.getByRole("button", { name: "Graph settings" }));
  const signals = within(screen.getByRole("group", { name: "2D signals" }));
  expect(signals.getByRole("checkbox", { name: "value" })).toBeChecked();
  expect(signals.getByRole("checkbox", { name: "current" })).not.toBeChecked();
  await user.click(signals.getByRole("checkbox", { name: "current" }));
  await user.click(signals.getByRole("checkbox", { name: "value" }));
  const series = chartOptions.mock.lastCall![0].series.filter(
    (item: any) => item.data?.length,
  );
  expect(
    series.map((item: any) => item.data.map((point: any) => point.value)),
  ).toEqual([
    [
      [0, 10],
      [1, 20],
    ],
  ]);
  expect(screen.getByTestId("pinned-identity")).toHaveTextContent(pinned);
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "3D graph" }));
  await user.click(screen.getByRole("button", { name: "Graph settings" }));
  expect(
    screen.queryByRole("group", { name: /signals/i }),
  ).not.toBeInTheDocument();
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "2D trends" }));
  await user.click(screen.getByRole("button", { name: "Graph settings" }));
  expect(screen.getByRole("checkbox", { name: "current" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "value" })).not.toBeChecked();
  expect(screen.getByTestId("pinned-identity")).toHaveTextContent(pinned);
});

it("updates the real ECharts engine from two unit lanes to the surviving lane without losing pin or zoom", async () => {
  const echarts = await import("echarts");
  // SVG SSR runs the real coordinate/merge engine without browser text measurement.
  echarts.setPlatformAPI({
    measureText: (text) => ({ width: String(text).length * 8 }),
  });
  const engine = echarts.init(null, undefined, {
    renderer: "svg",
    ssr: true,
    width: 800,
    height: 500,
  });
  const replaceMerge = [
    "series",
    "grid",
    "xAxis",
    "yAxis",
    "dataZoom",
    "visualMap",
  ];
  const applyFigure = () =>
    engine.setOption(chartOptions.mock.lastCall![0], { replaceMerge });
  const user = userEvent.setup();
  try {
    render(<Workspace />);
    await user.click(
      screen.getByRole("button", { name: "Pin second observation" }),
    );
    await user.click(screen.getByRole("button", { name: "2D trends" }));
    await user.click(
      screen.getByRole("button", { name: "Zoom evidence window" }),
    );
    applyFigure();
    await user.click(screen.getByRole("button", { name: "Graph settings" }));
    await user.click(screen.getByRole("checkbox", { name: "current" }));
    expect(chartOptions.mock.lastCall![0].grid).toHaveLength(2);
    applyFigure();
    const survivingLane = chartOptions.mock.lastCall![0].yAxis.find(
      (axis: any) => axis.name === "A",
    ).id;
    await user.click(screen.getByRole("checkbox", { name: "value" }));
    expect(chartOptions.mock.lastCall![0].grid).toHaveLength(1);
    expect(() => applyFigure()).not.toThrow();
    const option = engine.getOption() as any;
    const plottedSeries = option.series.filter(
      (series: any) => series?.data.length,
    );
    expect(plottedSeries).toHaveLength(1);
    expect(plottedSeries[0].data.map((point: any) => point.value)).toEqual([
      [0, 10],
      [1, 20],
    ]);
    expect(plottedSeries[0].xAxisId).toBe(survivingLane);
    expect(plottedSeries[0].yAxisId).toBe(survivingLane);
    expect(
      option.dataZoom.map((zoom: any) => [zoom.startValue, zoom.endValue]),
    ).toEqual([
      [0.2, 0.8],
      [0.2, 0.8],
    ]);
    expect(screen.getByTestId("pinned-identity")).toHaveTextContent(
      '["run","derived",1]',
    );
    expect(screen.getByTestId("trend-range")).toHaveTextContent("[0.2,0.8]");
    expect(
      option.xAxis.find((axis: any) => axis?.id === survivingLane).gridId,
    ).toBe(survivingLane);
    expect(
      option.yAxis.find((axis: any) => axis?.id === survivingLane).gridId,
    ).toBe(survivingLane);
    const seriesName = plottedSeries[0].name;
    engine.dispatchAction({ type: "legendUnSelect", name: seriesName });
    await user.keyboard("{Escape}");
    await user.click(
      screen.getByRole("button", { name: "Pin first observation" }),
    );
    expect(() => applyFigure()).not.toThrow();
    const updated = engine.getOption() as any;
    expect(updated.legend[0].selected[seriesName]).toBe(false);
    expect(
      updated.dataZoom.map((zoom: any) => [zoom.startValue, zoom.endValue]),
    ).toEqual([
      [0.2, 0.8],
      [0.2, 0.8],
    ]);
    expect(screen.getByTestId("pinned-identity")).toHaveTextContent(
      '["run","derived",0]',
    );
  } finally {
    engine.dispose();
  }
});
