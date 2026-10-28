import {
  DataTable,
  SimpleTable,
  AdvancedTable,
} from "../../components/studio/data-table";
import {
  CollectionTemplate,
  AnalysisTemplate,
  SetupTemplate,
} from "../../components/studio/templates";
import { useAnalyticalResource } from "./analytical-resource";
import "./analytical-workspace.css";
import { useComponentAction } from "../assistant/actions";
import { useState, useEffect } from "react";
import { useParams } from "react-router-dom";
import { useStudio, useResource } from "../../app/StudioContext";
import {
  Page,
  Panel,
  Button,
  Modal,
  Evidence,
  Badge,
  EmptyState,
} from "../../components/studio/primitives";
import {
  enc,
  pct,
  snapshots,
  useAction,
  ErrorNotice,
  Input,
  Select,
  Chips,
  DetailInspector,
  Messages,
  Histogram,
  track,
} from "./common";
export function scoreBins(rows: any[], model: string, metric: string) {
  const counts = Array(10).fill(0);
  for (const r of rows)
    if (
      r.model === model &&
      r.metric === metric &&
      typeof r.score === "number" &&
      Number.isFinite(r.score) &&
      r.score >= 0 &&
      r.score <= 1
    )
      counts[Math.min(9, Math.floor(r.score * 10))]++;
  return counts;
}
export function filterScoreRows(
  rows: any[],
  metric: string,
  model: string,
  bin: number | null,
) {
  return rows.filter(
    (r) =>
      r.metric === metric &&
      (!model || r.model === model) &&
      (bin === null ||
        (typeof r.score === "number" &&
          Number.isFinite(r.score) &&
          r.score >= bin / 10 &&
          (bin === 9 ? r.score <= 1 : r.score < (bin + 1) / 10))),
  );
}
export function EvaluationDialog({
  onClose,
  snapshot = "",
  models: initial = [],
}: {
  onClose: () => void;
  snapshot?: string;
  models?: string[];
}) {
  const s = useStudio(),
    a = useAction();
  const all = useResource<any[]>("/api/models"),
    metrics = useResource<any[]>("/api/metrics");
  const snaps = snapshots(s.overview);
  const [v, set] = useState<any>({
    name: "",
    snapshot: snapshot || snaps[0]?.name || "",
    limit: "",
    url: "",
    token: "",
  });
  const [mode, modeSet] = useState("agentic"),
    [models, modelsSet] = useState(initial),
    [chosen, chosenSet] = useState<string[]>([]);
  useEffect(() => {
    if (metrics.data)
      chosenSet(
        metrics.data
          .filter((m) => m.kind === "rubric")
          .slice(0, 3)
          .map((m) => m.name),
      );
  }, [metrics.data]);
  const field = (k: string) => (x: any) => set((p: any) => ({ ...p, [k]: x }));
  const submit = () =>
    a.run(async () => {
      if (!v.name.trim()) throw Error("Give the evaluation a name");
      if (!v.snapshot) throw Error("Create a snapshot first");
      if (!models.length) throw Error("Pick at least one model");
      if (mode === "agentic" && !chosen.length)
        throw Error("Pick at least one metric");
      if (mode === "byoe" && !v.url.trim())
        throw Error("Enter the evaluation URL");
      const body = {
        name: v.name.trim(),
        snapshot: v.snapshot,
        models,
        limit: v.limit === "" ? null : Number(v.limit),
        ...(mode === "agentic"
          ? { metrics: chosen }
          : { url: v.url.trim(), token: v.token.trim() || null }),
      };
      track(
        s,
        await s.api.post(
          mode === "agentic" ? "/api/evals" : "/api/evals/byoe",
          body,
        ),
      );
      onClose();
      s.navigate("#/evaluations");
    });
  return (
    <Modal
      open
      title="New evaluation"
      onClose={onClose}
      wide
      footer={
        <Button variant="primary" loading={a.busy} onClick={submit}>
          Create evaluation
        </Button>
      }
    >
      <SetupTemplate
        className="data-template data-setup"
        label="Evaluation setup"
      >
        <ErrorNotice error={a.error || all.error || metrics.error} />
        <Chips
          items={["agentic", "byoe"]}
          chosen={[mode]}
          onChange={(x) => modeSet(x.at(-1) || mode)}
        />
        <div className="data-grid">
          <Input label="Name" value={v.name} onChange={field("name")} />
          <Select
            label="Snapshot (validation split)"
            value={v.snapshot}
            onChange={field("snapshot")}
            options={snaps.map((x) => ({
              value: x.name,
              label: `${x.name} — ${x.validation_count} validation rows`,
            }))}
          />
          <Input
            label="Limit rows (optional)"
            type="number"
            min={1}
            value={v.limit}
            onChange={field("limit")}
          />
        </div>
        <h3>Models to evaluate</h3>
        <Chips
          items={(all.data || []).map((x) => x.id)}
          chosen={models}
          onChange={modelsSet}
        />
        {mode === "agentic" ? (
          <>
            <h3>Selected metrics</h3>
            <Chips
              items={(metrics.data || []).map((x) => x.name)}
              chosen={chosen}
              onChange={chosenSet}
            />
            <p>
              Rubric metrics use the helper LLM; code metrics require no model.{" "}
              <a href="#/metrics">Browse metrics</a>
            </p>
          </>
        ) : (
          <>
            <Input
              label="Evaluation URL"
              value={v.url}
              onChange={field("url")}
            />
            <Input
              label="URL token (optional)"
              type="password"
              value={v.token}
              onChange={field("token")}
            />
            <p>
              Your server receives a POST with datapoint messages, prediction
              and model_name; return a score from 0 to 1 and optional reason.
            </p>
          </>
        )}
      </SetupTemplate>
    </Modal>
  );
}
export function EvaluationsPage() {
  const s = useStudio();
  const r = useResource<any[]>("/api/evals");
  const [open, set] = useState(false),
    [query, setQuery] = useState("");
  return (
    <Page
      className="data-workspace analytical-workspace"
      title="Evaluations"
      description="Compare models and inspect the answers behind each score."
      actions={
        <Button variant="primary" onClick={() => set(true)}>
          Create evaluation
        </Button>
      }
    >
      <CollectionTemplate
        className="data-template"
        label="Evaluation collection workspace"
        summary={
          <div className="analytical-scope">
            <strong>{r.data?.length ?? "—"} evaluations</strong>
            <span>Validation evidence</span>
            <a href="#/metrics">Manage scoring metrics →</a>
          </div>
        }
        toolbar={
          <div className="data-toolbar">
            <Input
              label="Find an evaluation"
              type="search"
              value={query}
              onChange={setQuery}
            />
          </div>
        }
      >
        <ErrorNotice error={r.error} />
        {r.loading ? (
          <p role="status">Loading evaluations…</p>
        ) : (
          <>
            <DataTable
              scopeKey={s.scopeKey + ":" + s.route}
              label="Evaluations"
              rows={(r.data || []).filter((e) =>
                e.name.toLowerCase().includes(query.trim().toLowerCase()),
              )}
              getRowId={(e: any) => String(e.name)}
              pagination="client"
              columns={[
                {
                  id: "col-0",
                  label: "Evaluation",
                  value: (e: any) => e.name,
                  render: (e: any) => (
                    <>
                      <a
                        className="data-record-link"
                        href={`#/evaluations/${enc(e.name)}`}
                      >
                        {e.name}
                      </a>
                    </>
                  ),
                },
                {
                  id: "col-1",
                  label: "State",
                  value: (e: any) => e.state,
                  render: (e: any) => (
                    <>
                      <Badge>{e.state}</Badge>
                    </>
                  ),
                },
                {
                  id: "col-2",
                  label: "Snapshot",
                  value: (e: any) => e.snapshot,
                  render: (e: any) => <>{e.snapshot}</>,
                },
                {
                  id: "col-3",
                  label: "Models",
                  value: (e: any) => e.models.length,
                  render: (e: any) => <>{e.models.length}</>,
                },
                {
                  id: "col-4",
                  label: "Metrics",
                  value: (e: any) => e.metrics.join(", "),
                  render: (e: any) => <>{e.metrics.join(", ")}</>,
                },
                {
                  id: "col-5",
                  label: "Datapoints",
                  value: (e: any) => e.datapoints,
                  render: (e: any) => <>{e.datapoints}</>,
                },
              ]}
              renderRow={(e: any, { key, ...props }, cells) => (
                <Evidence
                  as="tr"
                  {...props}
                  key={key}
                  label="Evaluation"
                  data={e}
                >
                  {cells}
                </Evidence>
              )}
            />
            {!r.error && !r.data?.length && (
              <EmptyState>
                No evaluations yet. Create a snapshot to compare models.
              </EmptyState>
            )}
          </>
        )}
        {open && (
          <EvaluationDialog
            onClose={() => {
              set(false);
              r.reload();
            }}
          />
        )}
      </CollectionTemplate>
    </Page>
  );
}
export function EvaluationPage() {
  const params = useParams(),
    name = params.name || params.evaluation || "";
  const s = useStudio(),
    a = useAction(),
    r = useAnalyticalResource<any>(`/api/evals/${enc(name)}`);
  const [metric, M] = useState(""),
    [model, Mo] = useState(""),
    [bin, B] = useState<number | null>(null),
    [selected, S] = useState<any>(null),
    [tab, T] = useState("model");
  useEffect(() => {
    M("");
    Mo("");
    B(null);

    S(null);
  }, [name, s.scopeKey]);
  useComponentAction({
    id: "evaluation.metric",
    label: "Choose evaluation metric",
    kind: "filter",
    state: metric || r.data?.summary.metrics[0] || "",
    options: (r.data?.summary.metrics || []).map((value: string) => ({
      value,
      label: value,
    })),
    disabled: !r.data,
    run: (value) => {
      if (!r.data?.summary.metrics.includes(value))
        throw Error("Choose an available metric");
      S(null);
      M(value!);
      Mo("");
      B(null);
    },
  });
  useComponentAction({
    id: "evaluation.model",
    label: "Choose evaluation model",
    kind: "filter",
    state: model,
    options: [
      { value: "", label: "All models" },
      ...(r.data?.summary.models || []).map((value: string) => ({
        value,
        label: value,
      })),
    ],
    disabled: !r.data,
    run: (value) => {
      if (value && !r.data?.summary.models.includes(value))
        throw Error("Choose an available model");
      S(null);
      Mo(value || "");
      B(null);
    },
  });
  useComponentAction({
    id: "evaluation.case",
    label: "Inspect an evaluation case",
    kind: "select",
    state: selected ? JSON.stringify([selected.datapoint, selected.model]) : "",
    options: (r.data?.rows || [])
      .filter(
        (row: any) => row.metric === (metric || r.data?.summary.metrics[0]),
      )
      .map((row: any) => ({
        value: JSON.stringify([row.datapoint, row.model]),
        label: `Datapoint ${row.datapoint + 1} / ${row.model}`,
      })),
    disabled: !r.data,
    run: (value) => {
      const row = (r.data?.rows || []).find(
        (item: any) =>
          JSON.stringify([item.datapoint, item.model]) === value &&
          item.metric === (metric || r.data?.summary.metrics[0]),
      );
      if (!row) throw Error("Choose an available case");
      a.clearError();
      S(row);
      T("model");
    },
  });
  useComponentAction({
    id: "evaluation.reset",
    label: "Reset evaluation filters",
    kind: "filter",
    run: () => {
      S(null);
      B(null);
      Mo("");
    },
  });
  const d = r.data;
  if (!d)
    return (
      <Page title={name}>
        <ErrorNotice error={r.error} />
        <p role="status">
          {r.error
            ? "Evaluation evidence could not be loaded."
            : "Loading evaluation…"}
        </p>
        {r.error && (
          <Button onClick={r.reload}>Retry loading evaluation</Button>
        )}
      </Page>
    );
  const { summary, rows } = d,
    mt = metric || summary.metrics[0];
  const filtered = filterScoreRows(rows, mt, model, bin);
  const ids = [...new Set(filtered.map((x: any) => x.datapoint))].sort(
    (a: any, b: any) => a - b,
  );
  const result = (id: any, mo: string, metric = mt) =>
    rows.find(
      (x: any) => x.datapoint === id && x.model === mo && x.metric === metric,
    );
  const reset = () => {
    S(null);
    B(null);
    Mo("");
  };
  return (
    <Page
      className="data-workspace analytical-workspace"
      title={name}
      description={`Snapshot ${summary.snapshot} · ${summary.datapoints} datapoints`}
      actions={
        <Button
          variant="danger"
          onClick={() =>
            a.run(async () => {
              if (await s.confirm(`Delete evaluation "${name}"?`)) {
                await s.api.del(`/api/evals/${enc(name)}`);
                s.navigate("#/evaluations");
              }
            })
          }
        >
          Delete
        </Button>
      }
    >
      <AnalysisTemplate
        className="data-template"
        label="Evaluation evidence workspace"
        summary={
          <div className="analytical-scope">
            <Badge>{summary.state}</Badge>
            <span>
              {summary.datapoints} cases · {summary.models.length} models ·{" "}
              {summary.metrics.length} metrics
            </span>
            <span>
              {r.stale
                ? "Refresh failed or in progress · showing last received evidence"
                : `Received ${r.updated || "just now"}`}
            </span>
            <Button size="sm" onClick={r.reload}>
              Refresh evidence
            </Button>
          </div>
        }
      >
        <ErrorNotice error={r.error || a.error || summary.error} />

        <div className="analytical-evaluation-overview">
          <Panel title="Model comparison">
            <p className="data-muted">
              Mean metric scores over this evaluation's scored rows. A mean
              score is not necessarily a success or validity rate.
            </p>
            <SimpleTable
              label="Model comparison"
              scopeKey={s.scopeKey + ":" + name}
              rows={summary.leaderboard || []}
              getRowId={(b: any) => b.model}
              columns={[
                {
                  id: "model",
                  label: "Model",
                  value: (b: any) => b.model,
                  render: (b: any) => <strong>{b.model}</strong>,
                },
                ...summary.metrics.map((metric: string) => ({
                  id: `metric:${metric}`,
                  label: metric,
                  value: (b: any) => b.scores[metric],
                  render: (b: any) => (
                    <button
                      className="data-score-bar"
                      aria-label={`Compare ${b.model}, ${metric}`}
                      aria-pressed={mt === metric && model === b.model}
                      onClick={() => {
                        S(null);
                        M(metric);
                        Mo(b.model);
                        B(null);
                      }}
                    >
                      <span
                        style={{
                          width: `${Math.max(0, Math.min(100, (b.scores[metric] || 0) * 100))}%`,
                        }}
                      />
                      <b>{pct(b.scores[metric])}</b>
                    </button>
                  ),
                })),
                {
                  id: "overall",
                  label: "Overall",
                  value: (b: any) => b.overall,
                  render: (b: any) => pct(b.overall),
                },
              ]}
              renderRow={(b: any, { key, ...props }, cells) => (
                <Evidence
                  as="tr"
                  {...props}
                  key={key}
                  label="Evaluation leaderboard"
                  data={b}
                >
                  {cells}
                </Evidence>
              )}
            />
          </Panel>
          <Panel
            title="Score distribution"
            actions={<Button onClick={reset}>Reset filters</Button>}
          >
            <Chips
              items={summary.metrics}
              chosen={[mt]}
              onChange={(x) => {
                S(null);
                M(x.at(-1) || mt);
                reset();
              }}
            />
            <Select
              label="Compare model"
              value={model}
              onChange={(value: string) => {
                Mo(value);
                B(null);

                S(null);
              }}
              options={[
                { value: "", label: "All models" },
                ...summary.models.map((value: string) => ({
                  value,
                  label: value,
                })),
              ]}
            />
            <p>
              Click a score bin to filter the actual result rows. Missing scores
              are excluded.
            </p>
            <div className="data-grid">
              {summary.models.map((mo: string) => (
                <div key={mo}>
                  <h3>{mo}</h3>
                  <Histogram
                    label={`${mo} / ${mt}`}
                    counts={scoreBins(rows, mo, mt)}
                    bins={Array.from({ length: 11 }, (_, i) => i * 10)}
                    selected={model === mo ? bin : null}
                    onSelect={(i: number) => {
                      S(null);
                      Mo(mo);
                      B(i);
                    }}
                    data={{
                      model: mo,
                      metric: mt,
                      rows: rows.filter(
                        (x: any) => x.model === mo && x.metric === mt,
                      ),
                    }}
                  />
                </div>
              ))}
            </div>
            {bin !== null && (
              <p role="status">
                {model} · {bin * 10}–{(bin + 1) * 10}% · {ids.length} matching
                datapoints
              </p>
            )}
          </Panel>
        </div>
        <div
          className={`data-master-detail ${selected ? "has-selection" : ""}`}
        >
          <div className="data-master">
            <Panel title={`Results · ${mt}`}>
              <div
                className="analytical-scope"
                aria-label="Average scores over all scored rows"
              >
                <strong>Average (all scored rows)</strong>
                {summary.models.map((mo: string) => {
                  const vals = rows.filter(
                    (x: any) =>
                      x.model === mo &&
                      x.metric === mt &&
                      typeof x.score === "number" &&
                      Number.isFinite(x.score),
                  );
                  return (
                    <span key={mo}>
                      {mo}:{" "}
                      {pct(
                        vals.length
                          ? vals.reduce((n: number, x: any) => n + x.score, 0) /
                              vals.length
                          : null,
                      )}{" "}
                      ({vals.length})
                    </span>
                  );
                })}
              </div>
              <AdvancedTable
                label="Evaluation scores"
                scopeKey={`${s.scopeKey}:${name}:${mt}:${model}:${bin}`}
                rows={ids}
                getRowId={(id: any) => String(id)}
                columns={[
                  {
                    id: "datapoint",
                    label: "Datapoint",
                    value: (id: any) =>
                      filtered.find((x: any) => x.datapoint === id)?.user ||
                      `Datapoint ${id + 1}`,
                  },
                  ...summary.models.map((mo: string) => ({
                    id: `model:${mo}`,
                    label: mo,
                    value: (id: any) => result(id, mo)?.score,
                    render: (id: any) => {
                      const row = result(id, mo);
                      return row ? (
                        <Button
                          aria-label={`Inspect score for datapoint ${id + 1}, ${mo}`}
                          aria-pressed={
                            selected?.datapoint === id && selected?.model === mo
                          }
                          onClick={() => {
                            a.clearError();
                            S(row);
                            T("model");
                          }}
                        >
                          {pct(row.score)}
                        </Button>
                      ) : (
                        "—"
                      );
                    },
                  })),
                ]}
                renderRow={(id: any, { key, ...props }, cells) => (
                  <Evidence
                    as="tr"
                    {...props}
                    key={key}
                    label={`Datapoint ${id}`}
                    data={{
                      metric: mt,
                      rows: filtered.filter((x: any) => x.datapoint === id),
                    }}
                  >
                    {cells}
                  </Evidence>
                )}
              />
            </Panel>
          </div>
          {selected && (
            <DetailInspector
              title={`Evaluation datapoint ${selected.datapoint + 1}`}
              onClose={() => S(null)}
            >
              <p className="data-case-prompt">{selected.user}</p>
              <section className="data-reference">
                <h3>Reference answer</h3>
                <pre className="data-prose">
                  {selected.reference || "No reference supplied"}
                </pre>
              </section>
              <Chips
                items={["datapoint", "model"]}
                chosen={[tab]}
                onChange={(x) => T(x.at(-1) || tab)}
              />
              {tab === "datapoint" ? (
                <Messages
                  messages={[
                    { role: "system", content: selected.system || "(none)" },
                    { role: "user", content: selected.user },
                    { role: "reference", content: selected.reference },
                  ]}
                />
              ) : (
                <>
                  <Chips
                    items={summary.models}
                    chosen={[selected.model]}
                    onChange={(x) => {
                      const mo = x.at(-1) || selected.model;
                      S(
                        result(selected.datapoint, mo) || {
                          datapoint: selected.datapoint,
                          user: selected.user,
                          system: selected.system,
                          reference: selected.reference,
                          model: mo,
                        },
                      );
                    }}
                  />
                  {!rows.some(
                    (row: any) =>
                      row.datapoint === selected.datapoint &&
                      row.model === selected.model,
                  ) && (
                    <EmptyState>
                      No result is available for this model and case.
                    </EmptyState>
                  )}
                  {summary.metrics.map((m: string) => {
                    const row = result(selected.datapoint, selected.model, m);
                    return row ? (
                      <Evidence
                        key={m}
                        label={`${selected.model} / ${m}`}
                        data={row}
                      >
                        <Panel title={`${m} · ${pct(row.score)}`}>
                          <p>{row.reason}</p>
                          {(row.verdicts || []).map((v: any, i: number) => (
                            <p key={i}>
                              {v.pass ? "✓" : "✕"}{" "}
                              {v.kind === "should_not"
                                ? "Should not: "
                                : "Should: "}
                              {v.rule}
                              <br />
                              {v.reason}
                            </p>
                          ))}
                          <Messages
                            messages={[
                              { role: "assistant", content: row.prediction },
                            ]}
                          />
                        </Panel>
                      </Evidence>
                    ) : null;
                  })}
                </>
              )}
            </DetailInspector>
          )}
          {!selected && (
            <aside className="data-inspector data-inspector-empty">
              <h2>Case inspector</h2>
              <p>
                Select a score to compare the prediction, reference, and metric
                reasoning for that case.
              </p>
            </aside>
          )}
        </div>
      </AnalysisTemplate>
    </Page>
  );
}
