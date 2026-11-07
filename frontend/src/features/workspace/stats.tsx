import { useEffect, useRef, useState } from "react";
import { SimpleTable } from "../../components/studio/data-table";
import { AnalysisTemplate } from "../../components/studio/templates";
import { useComponentAction } from "../assistant/actions";
import { useStudio } from "../../app/StudioContext";
import { useAnalyticalResource } from "../data/analytical-resource";
import {
  Page,
  Panel,
  Evidence,
  EmptyState,
  Button,
} from "../../components/studio/primitives";
import { ErrorNotice } from "../data/common";
import "./stats-workspace.css";

type Count = number | null | undefined;
type Stats = {
  requests?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  training_seconds?: number;
  models_loaded?: string[];
  requests_per_day?: unknown;
  requests_per_model?: unknown;
  datasets?: number;
  datapoints?: number;
  snapshots?: number;
  jobs?: number;
  experiments?: Record<string, number>;
  evaluations?: number;
  scored_traces?: number;
};
const knownCount = (value: Count): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;
const countLabel = (value: Count) =>
  knownCount(value) ? value.toLocaleString() : "—";
function countEntries(value: unknown): [string, number][] {
  return Array.isArray(value)
    ? value.filter(
        (entry): entry is [string, number] =>
          Array.isArray(entry) &&
          typeof entry[0] === "string" &&
          Number.isInteger(entry[1]) &&
          entry[1] >= 0,
      )
    : [];
}
/** Whole-count grid with a little headroom, including the one-request boundary. */
export function requestAxis(maximum: number) {
  const target = Math.max(1, maximum / 2);
  const magnitude = 10 ** Math.floor(Math.log10(target));
  const step = [1, 2, 4, 5, 10]
    .map((n) => n * magnitude)
    .find((n) => n >= target)!;
  return { ceiling: step * 2, ticks: [0, step, step * 2] };
}
function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="stats-metric">
      <span className="text-caption-1-medium stats-muted">{label}</span>
      <strong className="text-title-2-medium">{value}</strong>
      {detail && (
        <span className="text-caption-1-regular stats-muted">{detail}</span>
      )}
    </div>
  );
}
export function StatsPage() {
  const studio = useStudio(),
    r = useAnalyticalResource<Stats>("/api/stats"),
    s = r.data;
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const dayRefs = useRef<(SVGGElement | null)[]>([]);
  useEffect(() => setSelectedDay(null), [studio.scopeKey]);
  const days = countEntries(s?.requests_per_day),
    models = countEntries(s?.requests_per_model);
  const incompleteDays =
    Array.isArray(s?.requests_per_day) &&
    days.length !== s.requests_per_day.length;
  const incompleteModels =
    Array.isArray(s?.requests_per_model) &&
    models.length !== s.requests_per_model.length;
  const selected = days.find(([day]) => day === selectedDay);
  const axis = requestAxis(Math.max(0, ...days.map(([, count]) => count)));
  const modelMax = Math.max(1, ...models.map(([, count]) => count));
  useComponentAction({
    id: "stats.day",
    label: "Inspect request volume for a day",
    kind: "select",
    disabled: !days.length,
    state: { selected },
    options: days.map(([day, count]) => ({
      value: day,
      label: `${day}: ${count} requests`,
    })),
    run: (day) =>
      setSelectedDay(
        days.some(([value]) => value === day) ? day || null : null,
      ),
  });
  const totals = s
    ? [
        {
          label: "Requests served",
          value: countLabel(s.requests),
          detail: "Recorded serving requests",
        },
        {
          label: "Tokens served",
          value: countLabel(
            knownCount(s.prompt_tokens) && knownCount(s.completion_tokens)
              ? s.prompt_tokens + s.completion_tokens
              : null,
          ),
          detail: "Prompt + completion tokens",
        },
        {
          label: "Models loaded",
          value: Array.isArray(s.models_loaded)
            ? countLabel(s.models_loaded.length)
            : "—",
          detail: "Currently loaded",
        },
        {
          label: "Training time",
          value: knownCount(s.training_seconds)
            ? `${Math.round(s.training_seconds / 60).toLocaleString()} min`
            : "—",
          detail: "Recorded experiment time",
        },
      ]
    : [];
  return (
    <Page
      className="stats-workspace"
      title="Stats"
      description="Serving activity and recorded workspace totals."
      actions={
        <Button size="sm" onClick={r.reload}>
          Refresh statistics
        </Button>
      }
    >
      <ErrorNotice error={r.error} />
      {!s ? (
        <p role="status">
          {r.error ? "Statistics could not be loaded." : "Loading stats…"}
        </p>
      ) : (
        <AnalysisTemplate
          label="Serving statistics"
          summary={
            <div className="stats-summary" aria-label="Serving totals">
              {totals.map((metric) => (
                <Evidence key={metric.label} label={metric.label} data={metric}>
                  <Metric {...metric} />
                </Evidence>
              ))}
            </div>
          }
        >
          <div className="stats-scope text-caption-1-regular">
            <strong>Project {studio.project}</strong>
            <span>
              {days.length
                ? `${days[0][0]} – ${days.at(-1)![0]}`
                : "No recorded serving days"}
            </span>
            <span aria-live="polite">
              {r.stale
                ? r.error
                  ? "Refresh failed · showing last received statistics"
                  : "Refreshing · showing last received statistics"
                : `Received ${r.updated || "just now"}`}
            </span>
          </div>
          <div className="stats-analysis">
            <Panel
              title="Requests per day"
              className="stats-daily"
              actions={
                <span className="text-caption-1-regular stats-muted">
                  Recorded daily counts
                </span>
              }
            >
              <div className="stats-chart-headline">
                <strong className="text-title-1-medium">
                  {countLabel(s.requests)}
                </strong>
                <span className="text-caption-1-regular stats-muted">
                  requests served
                </span>
              </div>
              {incompleteDays && (
                <p className="text-caption-1-regular stats-muted">
                  Some daily request counts are invalid and could not be
                  displayed.
                </p>
              )}
              {days.length ? (
                <>
                  <Evidence
                    as="svg"
                    className="stats-volume-chart"
                    label="Requests per day"
                    data={days}
                    viewBox="0 0 680 220"
                    role="group"
                    aria-label="Requests per day"
                    aria-describedby="stats-chart-help"
                  >
                    {axis.ticks.map((tick) => {
                      const y = 180 - (tick / axis.ceiling) * 164;
                      return (
                        <g key={tick}>
                          <line
                            x1="40"
                            x2="668"
                            y1={y}
                            y2={y}
                            className="stats-chart-grid"
                          />
                          <text className="text-caption-1-regular" x="28" y={y + 4} textAnchor="end">
                            {tick}
                          </text>
                        </g>
                      );
                    })}
                    {days.map(([day, n], i) => {
                      const slot = 628 / days.length,
                        width = Math.min(32, slot * 0.55),
                        x = 40 + slot * (i + 0.5);
                      return (
                        <g
                          key={day}
                          ref={(element) => {
                            dayRefs.current[i] = element;
                          }}
                          role="button"
                          tabIndex={
                            selectedDay === day || (!selected && i === 0)
                              ? 0
                              : -1
                          }
                          aria-label={`${day}: ${n} requests`}
                          aria-pressed={selectedDay === day}
                          onClick={() => setSelectedDay(day)}
                          onKeyDown={(event) => {
                            if (event.key === "Escape") {
                              event.preventDefault();
                              setSelectedDay(null);
                            } else if (
                              [
                                "ArrowRight",
                                "ArrowLeft",
                                "Home",
                                "End",
                              ].includes(event.key)
                            ) {
                              event.preventDefault();
                              const next =
                                event.key === "Home"
                                  ? 0
                                  : event.key === "End"
                                    ? days.length - 1
                                    : Math.min(
                                        days.length - 1,
                                        Math.max(
                                          0,
                                          i +
                                            (event.key === "ArrowRight"
                                              ? 1
                                              : -1),
                                        ),
                                      );
                              setSelectedDay(days[next][0]);
                              dayRefs.current[next]?.focus();
                            } else if (
                              event.key === "Enter" ||
                              event.key === " "
                            ) {
                              event.preventDefault();
                              setSelectedDay(day);
                            }
                          }}
                        >
                          <rect
                            x={40 + slot * i}
                            y="12"
                            width={slot}
                            height="172"
                            fill="transparent"
                          />
                          <rect
                            className="stats-bar"
                            x={x - width / 2}
                            y={180 - (n / axis.ceiling) * 164}
                            width={width}
                            height={(n / axis.ceiling) * 164}
                            rx="2"
                          />
                          <title>{`${day}: ${n} requests`}</title>
                        </g>
                      );
                    })}
                    {[
                      ...new Set([
                        0,
                        Math.floor((days.length - 1) / 2),
                        days.length - 1,
                      ]),
                    ].map((i) => (
                      <text
                        key={days[i][0]}
                        className="text-caption-1-regular"
                        x={40 + (628 / days.length) * (i + 0.5)}
                        y="208"
                        textAnchor={
                          i === 0
                            ? "start"
                            : i === days.length - 1
                              ? "end"
                              : "middle"
                        }
                      >
                        {days[i][0]}
                      </text>
                    ))}
                  </Evidence>
                  <div className="stats-day-readout">
                    <div role="status" className="text-body-2-medium">
                      {selected ? (
                        <>
                          <span className="stats-muted">{selected[0]}: </span>
                          <strong>
                            {selected[1].toLocaleString()} requests
                          </strong>
                          <span className="stats-readout-note text-caption-1-regular stats-muted">
                            Recorded aggregate · no per-day request drilldown.
                          </span>
                        </>
                      ) : (
                        <span className="stats-muted">
                          Select a day to inspect its count.
                        </span>
                      )}
                    </div>
                    {selected && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          const index = days.findIndex(
                            ([day]) => day === selectedDay,
                          );
                          dayRefs.current[index]?.focus();
                          setSelectedDay(null);
                        }}
                      >
                        Clear day
                      </Button>
                    )}
                  </div>
                  <p
                    id="stats-chart-help"
                    className="text-caption-1-regular stats-muted"
                  >
                    Arrow keys move between days · Enter selects · Escape
                    clears. Recorded dates only.
                  </p>
                </>
              ) : (
                <EmptyState>
                  {Array.isArray(s.requests_per_day)
                    ? incompleteDays
                      ? "No valid daily request counts are available."
                      : "No requests yet. Serve a model in the Playground or API."
                    : "Daily request counts are unavailable."}
                </EmptyState>
              )}
            </Panel>
            <Panel
              title="Requests per model"
              className="stats-models"
              actions={
                <span className="text-caption-1-regular stats-muted">
                  Top 10
                </span>
              }
            >
              <p className="text-caption-1-regular stats-muted">
                Recorded requests across all serving dates.
              </p>
              {incompleteModels && (
                <p className="text-caption-1-regular stats-muted">
                  Some model request counts are invalid and could not be
                  displayed.
                </p>
              )}
              <SimpleTable
                label="Requests served per model"
                rows={models}
                getRowId={([model]) => model}
                scopeKey={studio.scopeKey}
                pagination="none"
                renderRow={([model, requests], { key, ...props }, cells) => (
                  <Evidence
                    key={key}
                    as="tr"
                    {...props}
                    label="Requests per model"
                    data={{ model, requests }}
                  >
                    {cells}
                  </Evidence>
                )}
                columns={[
                  {
                    id: "model",
                    label: "Model",
                    value: ([model]) => model,
                    hideable: false,
                    render: ([model, requests]) => (
                      <>
                        <span className="stats-model-name">{model}</span>
                        <span className="stats-model-track">
                          <span
                            style={{ width: `${(requests / modelMax) * 100}%` }}
                          />
                        </span>
                      </>
                    ),
                  },
                  {
                    id: "requests",
                    label: "Requests",
                    value: ([, requests]) => requests,
                    render: ([, requests]) => <>{requests.toLocaleString()}</>,
                  },
                ]}
              />
              {!models.length && (
                <EmptyState>
                  {Array.isArray(s.requests_per_model)
                    ? incompleteModels
                      ? "No valid model request counts are available."
                      : "No model requests yet."
                    : "Model request counts are unavailable."}
                </EmptyState>
              )}
              <p className="stats-loaded text-caption-1-regular stats-muted">
                Models loaded now:{" "}
                {Array.isArray(s.models_loaded)
                  ? s.models_loaded.join(", ") || "none"
                  : "unavailable"}
              </p>
            </Panel>
          </div>
          <Panel title="Workspace inventory" className="stats-inventory">
            <div className="stats-inventory-grid" aria-label="Workspace totals">
              {Object.entries({
                Datasets: s.datasets,
                Datapoints: s.datapoints,
                Snapshots: s.snapshots,
                Jobs: s.jobs,
                "Experiments succeeded": s.experiments
                  ? (s.experiments.succeeded ?? 0)
                  : null,
                Evaluations: s.evaluations,
                "Scored traces": s.scored_traces,
              }).map(([label, value]) => (
                <Evidence key={label} label={label} data={{ label, value }}>
                  <Metric label={label} value={countLabel(value)} />
                </Evidence>
              ))}
            </div>
          </Panel>
        </AnalysisTemplate>
      )}
    </Page>
  );
}
