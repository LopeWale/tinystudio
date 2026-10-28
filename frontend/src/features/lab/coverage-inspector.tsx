import { useState } from "react";
import type { EChartsOption } from "echarts";
import { useResource, useStudio } from "../../app/StudioContext";
import { AnalysisTemplate } from "../../components/studio/templates";
import { DataTable } from "../../components/studio/data-table";
import {
  Button,
  EmptyState,
  Evidence,
} from "../../components/studio/primitives";
import { ErrorNotice } from "../data/common";
import { useComponentAction } from "../assistant/actions";
import { Chart } from "./chart";
import { LabChoice } from "./inputs";
import { type RetainedIdentity } from "./representation-inspector";
export const coverageAxes = [
  "fault_type",
  "fault_severity",
  "operating_range",
  "device_id",
  "scenario_id",
  "episode_id",
  "partition",
  "units",
];
export type CoverageSource = {
  identity: RetainedIdentity;
  record: Record<string, unknown>;
};
export type CoverageCell = {
  row: string;
  column: string;
  count: number;
  sources: CoverageSource[];
};
export type CoverageInspection = {
  run_id: string;
  row_axis: string;
  column_axis: string;
  rows: string[];
  columns: string[];
  counts: Record<string, number>;
  partition_counts: Record<string, number>;
  cells: CoverageCell[];
  artifacts: unknown[];
  complete: boolean;
  qualification: string;
};
export function coverageOption(data: CoverageInspection): EChartsOption {
  return {
    tooltip: {
      renderMode: "richText",
      trigger: "item",
      formatter: (p: any) =>
        `${data.row_axis}: ${data.rows[p.value[1]]}; ${data.column_axis}: ${data.columns[p.value[0]]}; ${p.value[2]} records`,
    },
    xAxis: { type: "category", data: data.columns, name: data.column_axis },
    yAxis: { type: "category", data: data.rows, name: data.row_axis },
    visualMap: {
      min: 0,
      max: Math.max(1, ...data.cells.map((c) => c.count)),
      calculable: true,
    },
    series: [
      {
        type: "heatmap",
        data: data.cells.map((cell, index) => ({
          value: [
            data.columns.indexOf(cell.column),
            data.rows.indexOf(cell.row),
            cell.count,
          ],
          cellIndex: index,
        })),
      },
    ],
  };
}
export function CoverageInspector({
  runId,
  onSelect,
  available = true,
}: {
  runId: string;
  available?: boolean;
  onSelect: (source: CoverageSource) => void;
}) {
  const { scopeKey } = useStudio();
  const [rowAxis, setRowAxis] = useState("scenario_id"),
    [columnAxis, setColumnAxis] = useState("partition");
  const query = `/api/lab/runs/${encodeURIComponent(runId)}/coverage?row_axis=${rowAxis}&column_axis=${columnAxis}`;
  const resource = useResource<CoverageInspection>(available ? query : null);
  const selectionKey = JSON.stringify([scopeKey, query]);
  const response = resource.data;
  const countsValid = (value: unknown) =>
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.values(value).every(
      (count) => Number.isSafeInteger(count) && Number(count) >= 0,
    );
  const valid =
    response != null &&
    typeof response === "object" &&
    response.run_id === runId &&
    response.row_axis === rowAxis &&
    response.column_axis === columnAxis &&
    response.complete === true &&
    countsValid(response.counts) &&
    countsValid(response.partition_counts) &&
    Array.isArray(response.rows) &&
    response.rows.every((v) => typeof v === "string") &&
    Array.isArray(response.columns) &&
    response.columns.every((v) => typeof v === "string") &&
    Array.isArray(response.cells) &&
    response.cells.every(
      (c) =>
        c &&
        typeof c.row === "string" &&
        typeof c.column === "string" &&
        Number.isSafeInteger(c.count) &&
        c.count >= 0 &&
        Array.isArray(c.sources) &&
        c.sources.every(
          (source) =>
            source?.identity?.run_id === runId &&
            typeof source.identity.artifact_id === "string" &&
            Number.isSafeInteger(source.identity.row_index) &&
            source.identity.row_index >= 0 &&
            source.record &&
            typeof source.record === "object",
        ),
    ) &&
    Array.isArray(response.artifacts) &&
    typeof response.qualification === "string";
  const data = valid ? response : undefined;
  const payloadError =
    response !== undefined && !valid
      ? "Coverage response is invalid or belongs to a different query. Reload the retained run evidence."
      : null;
  const [picked, setPicked] = useState<{ query: string; index: number }>();
  const cell =
    picked?.query === selectionKey ? data?.cells[picked.index] : undefined;
  const options = coverageAxes.map((axis) => ({ id: axis, name: axis }));
  useComponentAction({
    id: "lab.coverage-row-axis",
    label: "Choose coverage row axis",
    kind: "select",
    state: rowAxis,
    disabled: !available,
    options: options.map((o) => ({ value: o.id, label: o.name })),
    run: (value) => value && setRowAxis(value),
  });
  useComponentAction({
    id: "lab.coverage-column-axis",
    label: "Choose coverage column axis",
    kind: "select",
    state: columnAxis,
    disabled: !available,
    options: options.map((o) => ({ value: o.id, label: o.name })),
    run: (value) => value && setColumnAxis(value),
  });
  useComponentAction({
    id: "lab.coverage-evidence",
    label: "Read dataset-wide retained coverage",
    kind: "open",
    state: { query, loading: resource.loading, evidence: data },
    disabled: !data,
    run: () => ({ query, ...data }),
  });
  useComponentAction({
    id: "lab.coverage-cell",
    label: "Inspect retained coverage cell",
    kind: "select",
    state: { query, selected_cell: cell },
    disabled: !data?.cells.length,
    options: data?.cells.map((candidate, index) => ({
      value: String(index),
      label: `${candidate.row} / ${candidate.column}: ${candidate.count} records`,
    })),
    run: (value) => {
      const index = Number(value);
      if (data && Number.isInteger(index) && data.cells[index])
        setPicked({ query: selectionKey, index });
    },
  });
  useComponentAction({
    id: "lab.coverage-source",
    label: "Inspect exact retained coverage observation",
    kind: "open",
    state: { query, sources: cell?.sources },
    disabled: !cell?.sources.length,
    options: cell?.sources.map((source, index) => ({
      value: String(index),
      label: `${source.identity.artifact_id} row ${source.identity.row_index}`,
    })),
    run: (value) => {
      const index = Number(value);
      if (Number.isInteger(index) && cell?.sources[index])
        onSelect(cell.sources[index]);
    },
  });
  if (!available)
    return (
      <EmptyState>
        Dataset coverage requires retained records in a transformed dataset
        artifact.
      </EmptyState>
    );
  return (
    <AnalysisTemplate
      label="Dataset coverage"
      toolbar={
        <>
          <LabChoice
            label="Coverage row axis"
            value={rowAxis}
            onChange={setRowAxis}
            options={options}
          />
          <LabChoice
            label="Coverage column axis"
            value={columnAxis}
            onChange={setColumnAxis}
            options={options}
          />
        </>
      }
    >
      {resource.loading && (
        <p role="status">Scanning all retained transformed records…</p>
      )}
      <ErrorNotice error={resource.error ?? payloadError} />
      {data && (
        <Evidence
          label="Dataset-wide retained coverage"
          data={{ query, ...data }}
        >
          <p>
            {data.counts.records ?? 0} retained transformed records.{" "}
            {data.qualification}
          </p>
          <dl>
            {Object.entries(data.counts).map(([name, count]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>{count}</dd>
              </div>
            ))}
          </dl>
          <h4>Canonical partition counts</h4>
          <pre>{JSON.stringify(data.partition_counts, null, 2)}</pre>
          {data.cells.length ? (
            <>
              <Chart
                label="Retained dataset coverage heatmap"
                option={coverageOption(data)}
                onPoint={(point: any) => {
                  if (Number.isInteger(point?.cellIndex))
                    setPicked({ query: selectionKey, index: point.cellIndex });
                }}
              />
              <DataTable
                label="Coverage cells"
                rows={data.cells}
                scopeKey={query}
                getRowId={(c) => JSON.stringify([c.row, c.column])}
                columns={[
                  { id: "row", label: rowAxis, value: (c) => c.row },
                  { id: "column", label: columnAxis, value: (c) => c.column },
                  {
                    id: "count",
                    label: "Retained records",
                    value: (c) => c.count,
                  },
                  {
                    id: "inspect",
                    label: "Exact evidence",
                    value: (c) => c.sources.length,
                    render: (c) => (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          setPicked({
                            query: selectionKey,
                            index: data.cells.indexOf(c),
                          })
                        }
                      >
                        Inspect records
                      </Button>
                    ),
                  },
                ]}
              />
            </>
          ) : (
            <EmptyState>
              No retained transformed dataset is attached to this run.
            </EmptyState>
          )}
          {cell && (
            <div>
              <h4>
                {cell.row} / {cell.column}: {cell.count} records
              </h4>
              <p>Showing {cell.sources.length} exact source links.</p>
              {cell.sources.map((source) => (
                <Button
                  key={`${source.identity.artifact_id}:${source.identity.row_index}`}
                  size="sm"
                  onClick={() => onSelect(source)}
                >
                  Inspect {source.identity.artifact_id} row{" "}
                  {source.identity.row_index}
                </Button>
              ))}
            </div>
          )}
        </Evidence>
      )}
    </AnalysisTemplate>
  );
}
