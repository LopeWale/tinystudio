import { useEffect, useRef, useState, type KeyboardEventHandler } from "react";
import type { EChartsOption, EChartsType } from "echarts";

export type ChartZoom = {
  start?: number;
  end?: number;
  startValue?: number;
  endValue?: number;
  batch?: ChartZoom[];
};
export function Chart({
  option,
  label,
  height,
  onPoint,
  onPreview,
  onZoom,
  onKeyDown,
}: {
  option: EChartsOption;
  label: string;
  height?: number;
  onPoint?: (data: unknown) => void;
  onPreview?: (data: unknown | null) => void;
  onZoom?: (event: ChartZoom) => void;
  onKeyDown?: KeyboardEventHandler<HTMLDivElement>;
}) {
  const host = useRef<HTMLDivElement>(null);
  const instance = useRef<EChartsType | null>(null);
  const latest = useRef({ option, onPoint, onPreview, onZoom });
  latest.current = { option, onPoint, onPreview, onZoom };
  const [error, setError] = useState("");
  const update = useRef<(() => void) | undefined>(undefined);
  useEffect(() => {
    let active = true;
    let dispose: (() => void) | undefined;
    async function mount() {
      const [echarts, charts, components, renderers] = await Promise.all([
        import("echarts/core"),
        import("echarts/charts"),
        import("echarts/components"),
        import("echarts/renderers"),
      ]);
      if (!active || !host.current) return;
      echarts.use([
        charts.LineChart,
        charts.HeatmapChart,
        components.GridComponent,
        components.TooltipComponent,
        components.LegendComponent,
        components.DataZoomComponent,
        components.MarkLineComponent,
        components.MarkAreaComponent,
        components.VisualMapComponent,
        renderers.SVGRenderer,
      ]);
      const chart = echarts.init(host.current, undefined, { renderer: "svg" });
      instance.current = chart;
      let observer: ResizeObserver | undefined,
        appearance: MutationObserver | undefined;
      let closed = false;
      dispose = () => {
        if (closed) return;
        closed = true;
        observer?.disconnect();
        appearance?.disconnect();
        if (instance.current === chart) {
          update.current = undefined;
          instance.current = null;
        }
        chart.dispose();
      };
      update.current = () => {
        if (!active || !host.current) return;
        const style = getComputedStyle(host.current);
        const token = (name: string) =>
          style.getPropertyValue(`--color-${name}`).trim();
        const primary = token("text-primary"),
          secondary = token("text-secondary"),
          border = token("separator-border");
        const axes = (axis: any) => {
          const decorate = (item: any) => ({
            ...item,
            axisLabel: { color: secondary, ...item?.axisLabel },
            nameTextStyle: { color: secondary, ...item?.nameTextStyle },
            axisLine: {
              ...item?.axisLine,
              lineStyle: { color: border, ...item?.axisLine?.lineStyle },
            },
            splitLine: {
              ...item?.splitLine,
              lineStyle: { color: border, ...item?.splitLine?.lineStyle },
            },
          });
          return Array.isArray(axis)
            ? axis.map(decorate)
            : axis
              ? decorate(axis)
              : undefined;
        };
        const current = latest.current.option;
        const visualMap = (entry: any) => ({
          ...entry,
          inRange: {
            ...entry?.inRange,
            color: [token("chart-1"), token("chart-5")].filter(Boolean),
          },
        });
        chart.setOption(
          {
            ...current,
            color: [1, 2, 3, 4, 5]
              .map((index) => token(`chart-${index}`))
              .filter(Boolean),
            textStyle: { color: primary, ...current.textStyle },
            backgroundColor: "transparent",
            xAxis: axes(current.xAxis),
            yAxis: axes(current.yAxis),
            visualMap: Array.isArray(current.visualMap)
              ? current.visualMap.map(visualMap)
              : current.visualMap
                ? visualMap(current.visualMap)
                : undefined,
            legend: { textStyle: { color: secondary }, ...current.legend },
            tooltip: {
              backgroundColor: token("background-secondary-default"),
              borderColor: border,
              textStyle: { color: primary },
              ...current.tooltip,
            },
          },
          { replaceMerge: ["series", "grid", "xAxis", "yAxis", "dataZoom", "visualMap"] },
        );
      };
      update.current();
      chart.on("click", "series", (event: any) => {
        if (active) latest.current.onPoint?.(event.data);
      });
      chart.on("mouseover", "series", (event: any) => {
        if (active) latest.current.onPreview?.(event.data);
      });
      chart.on("globalout", () => {
        if (active) latest.current.onPreview?.(null);
      });
      chart.on("datazoom", (event: unknown) => {
        if (active && event && typeof event === "object")
          latest.current.onZoom?.(event as ChartZoom);
      });
      observer = new ResizeObserver(() => {
        if (active) chart.resize();
      });
      observer.observe(host.current);
      appearance = new MutationObserver(() => update.current?.());
      appearance.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["class", "style"],
      });
    }
    mount().catch((cause) => {
      dispose?.();
      if (active)
        setError(cause instanceof Error ? cause.message : String(cause));
      active = false;
    });
    return () => {
      active = false;
      dispose?.();
    };
  }, []);
  useEffect(() => {
    update.current?.();
  }, [option]);
  return (
    <>
      {error && <p role="alert">Chart could not render: {error}</p>}
      <div
        role={onKeyDown ? "group" : "img"}
        aria-label={label}
        tabIndex={onKeyDown ? 0 : undefined}
        onKeyDown={onKeyDown}
        className="lab-chart"
        style={height ? { height } : undefined}
        ref={host}
      />
    </>
  );
}
