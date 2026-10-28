import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Chart } from "./chart";
const fake = vi.hoisted(() => ({
  handlers: new Map<string, (...args: any[]) => void>(),
  setOption: vi.fn(),
  dispose: vi.fn(),
  resize: vi.fn(),
  on: vi.fn((name: string, query: any, callback?: (...args: any[]) => void) => {
    fake.handlers.set(name, callback ?? query);
  }),
}));
vi.mock("echarts/core", () => ({ use: vi.fn(), init: vi.fn(() => fake) }));
vi.mock("echarts/charts", () => ({ LineChart: {}, HeatmapChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  TooltipComponent: {},
  LegendComponent: {},
  DataZoomComponent: {},
  MarkLineComponent: {},
  MarkAreaComponent: {},
  VisualMapComponent: {},
}));
vi.mock("echarts/renderers", () => ({ SVGRenderer: {} }));
beforeEach(() => {
  fake.handlers.clear();
  fake.setOption.mockReset();
  fake.dispose.mockClear();
  fake.on.mockClear();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(cleanup);
describe("ECharts adapter lifecycle and events", () => {
  it("maps coverage heatmap colors through shared semantic tokens and replaces visual maps", async () => {
    const computed = vi.spyOn(globalThis, "getComputedStyle").mockReturnValue({
      getPropertyValue: (name: string) => name === "--color-chart-1" ? "semantic-start" : name === "--color-chart-5" ? "semantic-end" : "",
    } as CSSStyleDeclaration);
    try {
      render(<Chart label="Coverage heatmap" option={{ visualMap: { min: 0, max: 12 }, series: [{ type: "heatmap", data: [[0, 0, 12]] }] }} />);
      await waitFor(() => expect(fake.setOption).toHaveBeenCalled());
      expect(fake.setOption.mock.lastCall![0].visualMap.inRange.color).toEqual(["semantic-start", "semantic-end"]);
      expect(fake.setOption.mock.lastCall![1].replaceMerge).toContain("visualMap");
    } finally {
      computed.mockRestore();
    }
  });
  it("forwards exact point and zoom events, uses current callbacks, and ignores events after disposal", async () => {
    const first = vi.fn(),
      latest = vi.fn(),
      zoom = vi.fn();
    const { rerender, unmount } = render(
      <Chart option={{}} label="fixture chart" onPoint={first} onZoom={zoom} />,
    );
    await waitFor(() => expect(fake.handlers.has("datazoom")).toBe(true));
    const point = {
      identity: { key: '["run","artifact",3]' },
      value: [1, -0.125],
    };
    act(() => fake.handlers.get("click")!({ data: point }));
    expect(first).toHaveBeenCalledWith(point);
    rerender(
      <Chart
        option={{}}
        label="fixture chart"
        onPoint={latest}
        onZoom={zoom}
      />,
    );
    act(() => fake.handlers.get("click")!({ data: point }));
    expect(latest).toHaveBeenCalledWith(point);
    act(() =>
      fake.handlers.get("datazoom")!({ batch: [{ start: 20, end: 60 }] }),
    );
    expect(zoom).toHaveBeenCalledWith({ batch: [{ start: 20, end: 60 }] });
    unmount();
    act(() => fake.handlers.get("click")!({ data: point }));
    expect(latest).toHaveBeenCalledTimes(1);
    expect(fake.dispose).toHaveBeenCalledTimes(1);
  });
  it("releases the instance if initial rendering fails", async () => {
    fake.setOption.mockImplementationOnce(() => {
      throw new Error("controlled render failure");
    });
    const { unmount } = render(<Chart option={{}} label="fixture chart" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "controlled render failure",
    );
    expect(fake.dispose).toHaveBeenCalledTimes(1);
    unmount();
    expect(fake.dispose).toHaveBeenCalledTimes(1);
  });
});
