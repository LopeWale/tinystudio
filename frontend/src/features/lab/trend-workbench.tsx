import { useEffect, useMemo, useState } from "react";
import type { EChartsOption, LineSeriesOption } from "echarts";
import { Checkbox } from "@/components/boardui/base/checkbox/checkbox";
import { Input } from "@/components/boardui/base/input/input";
import { Button, EmptyState } from "../../components/studio/primitives";
import { LabChoice } from "./inputs";
import { Chart } from "./chart";
import { exactNumber, fieldValue, trendTime, type TrendPoint } from "./trends";
import type { TrendController } from "./trend-controller";
import { SpatialChart } from "./spatial-chart";
import {
  spatialAxes,
  rectangularSurface,
  ribbonSlices,
  coordinateEstimated,
} from "./spatial-model";

export function TrendWorkbench({
  controller: c,
}: {
  controller: TrendController;
}) {
  const [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [rangeError, setRangeError] = useState("");
  const extentStart = c.range?.[0] ?? c.model.extent?.[0],
    extentEnd = c.range?.[1] ?? c.model.extent?.[1];
  useEffect(() => {
    setStart(extentStart === undefined ? "" : String(extentStart));
    setEnd(extentEnd === undefined ? "" : String(extentEnd));
    setRangeError("");
  }, [extentStart, extentEnd]);
  const selectedTime = c.selected
    ? trendTime(c.selected.record, c.basis)
    : undefined;
  const height = Math.max(360, c.model.lanes.length * 180 + 110);
  const option = useMemo<EChartsOption>(() => {
    const lanes = c.model.lanes;
    const axes = lanes.map((_, index) => index);
    return {
      animation: false,
      tooltip: {
        trigger: "axis",
        renderMode: "richText",
        axisPointer: { type: "cross" },
        formatter: (params: any) =>
          (Array.isArray(params) ? params : [params])
            .map((item: any) => {
              const point = item.data as TrendPoint | undefined;
              return point?.identity
                ? `${item.seriesName}\n${exactNumber(point.value[1])} · time ${exactNumber(point.value[0])}\n${point.identity.kind} row ${point.identity.rowIndex + 1}`
                : "";
            })
            .filter(Boolean)
            .join("\n\n"),
      },
      legend: {
        type: "scroll",
        top: 0,
        data: [...new Set(c.model.series.map((series) => series.label))],
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: lanes.map((lane, index) => ({
        id: lane.key,
        top: 56 + index * 180,
        height: 120,
        left: 80,
        right: 28,
      })),
      xAxis: lanes.map((lane, index) => ({
        id: lane.key,
        type: "value",
        gridIndex: index,
        min: c.model.extent?.[0],
        max: c.model.extent?.[1],
        name:
          index === lanes.length - 1
            ? `${c.basis === "event" ? "Event / sample time" : "Availability time"} · ${c.model.clock?.unit ?? "time unit not recorded"}`
            : "",
        nameLocation: "middle",
        nameGap: 30,
        axisLabel: { show: index === lanes.length - 1 },
      })),
      yAxis: lanes.map((lane, index) => ({
        id: lane.key,
        type: "value",
        gridIndex: index,
        name: lane.label,
        nameLocation: "end",
        scale: true,
      })),
      dataZoom: [
        {
          id: "inside",
          type: "inside",
          xAxisIndex: axes,
          filterMode: "none",
          preventDefaultMouseMove: false,
          startValue: extentStart,
          endValue: extentEnd,
        },
        {
          id: "slider",
          type: "slider",
          xAxisIndex: axes,
          filterMode: "none",
          bottom: 0,
          startValue: extentStart,
          endValue: extentEnd,
        },
      ],
      series: [
        ...c.model.series.map<LineSeriesOption>((series) => {
          const lane = lanes.findIndex(
            (candidate) => candidate.key === series.laneKey,
          );
          return {
            id: series.key,
            name: series.label,
            type: "line",
            xAxisIndex: lane,
            yAxisIndex: lane,
            connectNulls: false,
            smooth: false,
            showSymbol: true,
            lineStyle: {
              type: series.kind === "original" ? "dashed" : "solid",
              width: 1.5,
            },
            data: series.points.map((point) => ({
              ...point,
              symbol: coordinateEstimated(point.record, series.field)
                ? "diamond"
                : "circle",
              symbolSize:
                point.identity.key === c.selected?.identity.key ? 10 : 4,
            })),
          };
        }),
        ...lanes.map<LineSeriesOption>((lane, index) => ({
          id: `selection:${lane.key}`,
          type: "line",
          xAxisIndex: index,
          yAxisIndex: index,
          data: [],
          silent: true,
          markLine:
            selectedTime !== undefined
              ? {
                  silent: true,
                  symbol: "none",
                  label: { formatter: "Selected" },
                  data: [{ xAxis: selectedTime }],
                }
              : undefined,
        })),
      ],
    };
  }, [
    c.model,
    c.basis,
    c.selected?.identity.key,
    selectedTime,
    extentStart,
    extentEnd,
  ]);
  const outside =
    selectedTime !== undefined &&
    c.range &&
    (selectedTime < c.range[0] || selectedTime > c.range[1]);
  const choices = spatialAxes(c.sources, c.basis).map((axis) => ({
    id: axis.id,
    name: axis.label,
  }));
  const surface = rectangularSurface(c.spatialGroup?.points ?? []);
  const ribbons = ribbonSlices(c.spatialGroup?.points ?? []);
  const selectKey: React.KeyboardEventHandler<HTMLDivElement> = (event) => {
    const index =
      event.key === "ArrowRight"
        ? c.cursor + 1
        : event.key === "ArrowLeft"
          ? c.cursor - 1
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? c.samples.length - 1
              : undefined;
    if (index !== undefined) {
      event.preventDefault();
      c.navigate(index);
    }
  };
  return (
    <section className="lab-trends" aria-label="Interactive signal trends">
      <header className="lab-visual-heading">
        <div>
          <h3>Signal space</h3>
          <span className="text-text-secondary">
            {c.view === "3d"
              ? `${c.spatialGroup?.points.length ?? 0} retained points`
              : `${c.samples.length} loaded observations`}
          </span>
        </div>
        <div className="lab-actions" role="group" aria-label="Graph dimensions">
          <Button
            size="sm"
            variant={c.view === "3d" ? "secondary" : "ghost"}
            aria-pressed={c.view === "3d"}
            onClick={() => c.changeView("3d")}
          >
            3D graph
          </Button>
          <Button
            size="sm"
            variant={c.view === "2d" ? "secondary" : "ghost"}
            aria-pressed={c.view === "2d"}
            onClick={() => c.changeView("2d")}
          >
            2D trends
          </Button>
          {c.view === "3d" && (
            <Button size="sm" variant="ghost" onClick={c.resetCamera}>
              Reset camera
            </Button>
          )}
        </div>
      </header>
      {c.view === "3d" && (
        <>
          <details className="lab-graph-settings">
            <summary>Graph settings</summary>
            <div className="lab-spatial-tools">
              {(["x", "y", "z"] as const).map((axis) => (
                <LabChoice
                  key={axis}
                  label={`${axis.toUpperCase()} axis`}
                  value={c.mapping[axis]}
                  options={choices}
                  onChange={(value) => c.changeMapping(axis, value)}
                />
              ))}
              <LabChoice
                label="Geometry"
                value={c.geometry}
                options={[
                  { id: "points", name: "Points" },
                  {
                    id: "slices",
                    name: "Direction slices",
                    disabled: !ribbons.ok,
                  },
                  {
                    id: "surface",
                    name: "Landscape surface",
                    disabled: !surface.ok,
                  },
                ]}
                onChange={(value) =>
                  c.changeGeometry(value as "points" | "surface" | "slices")
                }
              />
            </div>
            <div className="lab-spatial-options">
              {c.spatial.groups.length > 1 && (
                <LabChoice
                  label="Source / unit group"
                  value={c.spatialGroup?.key ?? ""}
                  options={c.spatial.groups.map((group, index) => ({
                    id: group.key,
                    name: `${group.label} · ${group.points.length} points · group ${index + 1}`,
                  }))}
                  onChange={c.changeGroup}
                />
              )}
              <LabChoice
                label="Opacity"
                value={String(c.opacity)}
                options={[0.1, 0.25, 0.5, 0.75, 0.85, 1].map((value) => ({
                  id: String(value),
                  name: `${Math.round(value * 100)}%`,
                }))}
                onChange={(value) => c.changeOpacity(Number(value))}
              />
            </div>
          </details>
          {c.spatialGroup &&
          (c.geometry === "points" ||
            (c.geometry === "surface" && surface.ok) ||
            (c.geometry === "slices" && ribbons.ok)) ? (
            <SpatialChart controller={c} onKeyDown={selectKey} />
          ) : (
            <EmptyState>
              {c.geometry === "slices" && !ribbons.ok
                ? ribbons.reason
                : c.geometry === "surface" && !surface.ok
                  ? surface.reason
                  : (c.spatial.error ??
                    "Choose three retained coordinates to inspect the 3D graph.")}
            </EmptyState>
          )}
          <p className="lab-graph-caption">
            Drag to rotate · scroll to zoom · click a point to inspect its
            source.
            {c.geometry === "slices"
              ? " Each ribbon connects samples within one Y direction. Ribbon width is visual spacing, not additional measurements."
              : c.geometry === "surface"
                ? " Surface geometry connects retained grid vertices; no values are filled in."
                : " Each point is one retained observation; diamonds mark estimated coordinates."}
            {c.spatial.omittedRows > 0 &&
              ` ${c.spatial.omittedRows} rows omitted because coordinate or time metadata is missing.`}
            {c.spatial.filteredRows > 0 &&
              ` ${c.spatial.filteredRows} rows outside the selected time range.`}
            {c.spatialGroup?.points.some(
              (point) => point.qualifications.length,
            ) && " Availability time includes explicitly assumed values."}
            {!c.spatial.clock?.qualified &&
              " Clock alignment is limited to this retained source; origin or session metadata is incomplete."}
          </p>
          {!surface.ok && (
            <details className="lab-graph-help">
              <summary>When can I show a landscape?</summary>
              <p>
                {surface.reason} Choose two sampled parameter axes and a
                retained result as Z. The display requires a complete grid from
                one source and unit group.
              </p>
            </details>
          )}
        </>
      )}
      <details className="lab-trend-settings">
        <summary>Clock and time range</summary>
        <div className="lab-trend-tools">
          <LabChoice
            label="Trend clock"
            value={c.model.clock?.key ?? ""}
            options={c.model.clocks.map((clock) => ({
              id: clock.key,
              name: clock.label,
            }))}
            onChange={c.changeClock}
          />
          <LabChoice
            label="Time basis"
            value={c.basis}
            options={[
              { id: "event", name: "Event / sample time" },
              { id: "availability", name: "Availability time" },
            ]}
            onChange={(value) =>
              c.changeBasis(value as "event" | "availability")
            }
          />
          <Button
            size="sm"
            disabled={!c.range}
            onClick={() => c.setRange(null)}
          >
            Reset zoom
          </Button>
        </div>
        <div className="lab-trend-range">
          <Input
            label="Trend range start"
            value={start}
            onChange={setStart}
            inputMode="decimal"
            inputDir="ltr"
          />
          <Input
            label="Trend range end"
            value={end}
            onChange={setEnd}
            inputMode="decimal"
            inputDir="ltr"
          />
          <Button
            size="sm"
            onClick={() => {
              const from = Number(start),
                to = Number(end);
              if (
                !start.trim() ||
                !end.trim() ||
                !Number.isFinite(from) ||
                !Number.isFinite(to) ||
                from >= to
              ) {
                setRangeError("Enter a finite start below the end.");
                return;
              }
              if (
                !c.model.extent ||
                from < c.model.extent[0] ||
                to > c.model.extent[1]
              ) {
                setRangeError(
                  "Choose a range within the loaded evidence window.",
                );
                return;
              }
              c.setRange([from, to]);
              setRangeError("");
            }}
          >
            Apply time range
          </Button>
        </div>
        {rangeError && <p role="alert">{rangeError}</p>}
      </details>
      {c.view === "2d" && (
        <>
          <fieldset className="lab-trend-signals">
            <legend>Signals</legend>
            {c.availableFields.map((field) => (
              <Checkbox
                key={field}
                size="sm"
                isSelected={c.fields.includes(field)}
                onChange={(selected) => c.toggleField(field, selected)}
              >
                {field}
              </Checkbox>
            ))}
          </fieldset>
          {!c.model.clock?.qualified && c.model.clocks.length > 0 && (
            <p className="text-text-secondary">
              Clock origin, unit or reset/session metadata is incomplete.
              Alignment is limited to the retained source; undeclared resets
              cannot be established.
            </p>
          )}
          {c.model.series.length ? (
            <Chart
              option={option}
              height={height}
              label="Interactive trends; left and right select records, Home and End select endpoints"
              onPoint={(point) => c.pointEvent(point, true)}
              onPreview={(point) => c.pointEvent(point, false)}
              onZoom={c.zoom}
              onKeyDown={selectKey}
            />
          ) : (
            <EmptyState>
              Select a signal with retained numeric timestamps in this clock.
              Missing values remain gaps.
            </EmptyState>
          )}
          <p className="text-text-secondary">
            Click a sample to pin its exact artifact row. Pan or zoom to inspect
            a time range. Diamonds mark estimates; dashed lines show originals.{" "}
            {c.model.omittedTimeRows > 0 &&
              `${c.model.omittedTimeRows} rows lack a comparable numeric time in this basis; lines break there.`}
          </p>
        </>
      )}
      <div className="lab-trend-readout" aria-live="polite">
        <strong>{c.preview ? "Hover preview" : "Pinned record"}</strong>{" "}
        {(() => {
          const point = c.preview ?? c.selected;
          return point ? (
            <>
              {point.identity.kind} row {point.identity.rowIndex + 1} ·{" "}
              {c.fields
                .map(
                  (field) =>
                    `${field}: ${exactNumber(fieldValue(point.record, field))}`,
                )
                .join(" · ")}
              {outside &&
                !c.preview &&
                " · selected record is outside the visible range"}
            </>
          ) : (
            "No record selected"
          );
        })()}
      </div>
      <details>
        <summary>Exact values and source links</summary>
        <table className="lab-table" aria-label="Trend sample values">
          <thead>
            <tr>
              <th>Record</th>
              <th>Time</th>
              <th>Values</th>
              <th>Source records</th>
            </tr>
          </thead>
          <tbody>
            {c.samples.map((point) => (
              <tr
                key={point.identity.key}
                aria-selected={point.identity.key === c.selected?.identity.key}
              >
                <td>
                  <Button
                    size="sm"
                    onClick={() => c.select(point.identity.key)}
                  >
                    Select {point.identity.kind} row{" "}
                    {point.identity.rowIndex + 1}
                  </Button>
                </td>
                <td>{exactNumber(trendTime(point.record, c.basis))}</td>
                <td>
                  {c.fields.map((field) => (
                    <div key={field}>
                      {field}: {exactNumber(fieldValue(point.record, field))}{" "}
                      {field === "value"
                        ? (point.record.units ?? "units not recorded")
                        : (point.record.field_units?.[field] ??
                          "units not recorded")}
                    </div>
                  ))}
                </td>
                <td>
                  {point.identity.sourceRecordIds.join(", ") ||
                    "Source relationship not recorded"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}
