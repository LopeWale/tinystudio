import { SimpleTable } from "../../components/studio/data-table";
import { AnalysisTemplate } from "../../components/studio/templates";
import "./admin-workspace.css";
import { useComponentAction } from "../assistant/actions";
import { useEffect, useState } from "react";
import { useStudio } from "../../app/StudioContext";
import { useAnalyticalResource } from "../data/analytical-resource";
import {
  Page,
  Panel,
  Evidence,
  EmptyState,
  Button,
} from "../../components/studio/primitives";
import { ErrorNotice, Select } from "../data/common";
export function StatsPage() {
  const studio = useStudio(),
    r = useAnalyticalResource<any>("/api/stats"),
    s = r.data;
  const [selectedDay, S] = useState<string | null>(null);
  useEffect(() => S(null), [studio.scopeKey]);
  const days: [string, number][] = (s?.requests_per_day || []).filter(
    (entry: any) =>
      typeof entry[0] === "string" &&
      typeof entry[1] === "number" &&
      Number.isFinite(entry[1]) &&
      entry[1] >= 0,
  );
  const models: [string, number][] = (s?.requests_per_model || []).filter(
    (entry: any) =>
      typeof entry[1] === "number" &&
      Number.isFinite(entry[1]) &&
      entry[1] >= 0,
  );
  const selected = days.find(([day]) => day === selectedDay),
    max = Math.max(1, ...days.map(([, count]) => count)),
    modelMax = Math.max(1, ...models.map(([, count]) => count));
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
    run: (day) => S(days.some(([value]) => value === day) ? day || null : null),
  });
  return (
    <Page
      className="stats-workspace admin-workspace"
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
            <div className="admin-compact-totals" aria-label="Serving totals">
              {Object.entries({
                "Requests served": s.requests,
                "Tokens served":
                  typeof s.prompt_tokens === "number" &&
                  typeof s.completion_tokens === "number"
                    ? s.prompt_tokens + s.completion_tokens
                    : null,
                "Models loaded": s.models_loaded?.length,
                "Training time":
                  typeof s.training_seconds === "number"
                    ? `${Math.round(s.training_seconds / 60)} min`
                    : null,
              }).map(([label, value]) => (
                <Evidence key={label} label={label} data={{ label, value }}>
                  <div className="metric-cell">
                    <span className="metric-label">{label}</span>
                    <strong className="data-stat">
                      {typeof value === "number"
                        ? value.toLocaleString()
                        : (value ?? "—")}
                    </strong>
                  </div>
                </Evidence>
              ))}
            </div>
          }
        >
          <div className="admin-scope">
            <strong>Project {studio.project}</strong>
            <span>
              {days.length
                ? `${days[0][0]} – ${days.at(-1)![0]}`
                : "No recorded serving days"}
            </span>
            <span>
              {r.stale
                ? r.error
                  ? "Refresh failed · showing last received statistics"
                  : "Refreshing · showing last received statistics"
                : `Received ${r.updated || "just now"}`}
            </span>
          </div>

          <div className="admin-stats-evidence">
            <Panel
              title="Requests per day"
              actions={
                <span className="data-muted">Recorded daily counts</span>
              }
            >
              {days.length ? (
                <>
                  <Evidence
                    as="svg"
                    className="admin-volume-chart"
                    label="Requests per day"
                    data={days}
                    viewBox="0 0 680 270"
                    role="group"
                    aria-label="Requests per day"
                  >
                    <text x="12" y="16">
                      Requests
                    </text>
                    {[0, 0.5, 1].map((t) => (
                      <g key={t}>
                        <line
                          x1="52"
                          x2="662"
                          y1={30 + t * 192}
                          y2={30 + t * 192}
                          className="admin-chart-grid"
                        />
                        <text x="8" y={34 + t * 192}>
                          {Math.round(max * (1 - t))}
                        </text>
                      </g>
                    ))}
                    {days.map(([day, n], i) => {
                      const width = 610 / days.length;
                      return (
                        <g
                          key={day}
                          role="button"
                          tabIndex={0}
                          aria-label={`${day}: ${n} requests`}
                          aria-pressed={selectedDay === day}
                          onClick={() => S(day)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              S(day);
                            }
                          }}
                        >
                          <rect
                            x={52 + i * width}
                            y="28"
                            width={Math.max(1, width - 2)}
                            height="194"
                            fill="transparent"
                          />
                          <rect
                            x={52 + i * width}
                            y={222 - (n / max) * 192}
                            width={Math.max(1, width - 3)}
                            height={(n / max) * 192}
                            fill={
                              selectedDay === day
                                ? "var(--color-chart-2)"
                                : "var(--color-chart-1)"
                            }
                          />
                          <title>{`${day}: ${n} requests`}</title>
                        </g>
                      );
                    })}
                    <text x="52" y="248">
                      {days[0][0]}
                    </text>
                    <text x="662" y="248" textAnchor="end">
                      {days.at(-1)![0]}
                    </text>
                  </Evidence>
                  <div className="admin-chart-selection">
                    {selected ? (
                      <p role="status">
                        {selected[0]}: {selected[1]} requests. This aggregate
                        has no per-day request drilldown.
                      </p>
                    ) : (
                      <p className="data-muted">
                        Select a day to pin its recorded request count.
                      </p>
                    )}
                    {selected && (
                      <Button size="sm" onClick={() => S(null)}>
                        Clear day
                      </Button>
                    )}
                  </div>
                  <Select
                    label="Inspect request day"
                    value={selectedDay || ""}
                    onChange={(value: string) => S(value || null)}
                    options={[
                      { value: "", label: "Choose a recorded day" },
                      ...days.map(([day, n]) => ({
                        value: day,
                        label: `${day} · ${n} requests`,
                      })),
                    ]}
                  />
                </>
              ) : (
                <EmptyState>
                  No requests yet. Serve a model in the Playground or API.
                </EmptyState>
              )}
            </Panel>
            <Panel title="Requests per model">
              <p className="data-muted">
                All recorded requests · bars scaled to the busiest model.
              </p>
              <SimpleTable
                label="Requests served per model"
                rows={models}
                getRowId={([model, requests]) => model}
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
                    value: ([model, requests]) => model,
                    hideable: false,
                    render: ([model, requests]) => (
                      <>
                        {model}
                        <span
                          className="admin-model-share"
                          style={{ width: `${(requests / modelMax) * 100}%` }}
                        />
                      </>
                    ),
                  },
                  {
                    id: "requests",
                    label: "Requests",
                    value: ([model, requests]) => requests,
                    render: ([model, requests]) => (
                      <>{requests.toLocaleString()}</>
                    ),
                  },
                ]}
              />
              {!models.length && (
                <EmptyState>No model requests yet.</EmptyState>
              )}
              <p className="data-muted">
                Models loaded now: {s.models_loaded?.join(", ") || "none"}
              </p>
            </Panel>
          </div>
          <Panel title="Workspace inventory">
            <div className="admin-compact-totals" aria-label="Workspace totals">
              {Object.entries({
                Datasets: s.datasets,
                Datapoints: s.datapoints,
                Snapshots: s.snapshots,
                Jobs: s.jobs,
                "Experiments succeeded": s.experiments?.succeeded,
                Evaluations: s.evaluations,
                "Scored traces": s.scored_traces,
              }).map(([label, value]) => (
                <Evidence key={label} label={label} data={{ label, value }}>
                  <div className="metric-cell">
                    <span className="metric-label">{label}</span>
                    <strong className="data-stat">
                      {typeof value === "number" ? value.toLocaleString() : "—"}
                    </strong>
                  </div>
                </Evidence>
              ))}
            </div>
          </Panel>
        </AnalysisTemplate>
      )}
    </Page>
  );
}
