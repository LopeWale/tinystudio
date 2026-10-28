import { DataTable, SimpleTable } from "../../components/studio/data-table";
import { useComponentAction } from "../assistant/actions";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useResource, useStudio } from "../../app/StudioContext";
import { cx } from "../../components/boardui/utils/cx";
import {
  Badge,
  Button,
  EmptyState,
  Evidence,
  Field,
  Modal,
  Page,
  Panel,
} from "../../components/studio/primitives";
import {
  EvidenceSelection,
  REGIMES,
  filterEntries,
  labelSummary,
  number,
  parseLayers,
  percent,
  readableToken,
  type Activation,
  type Regime,
  type SelectionSnapshot,
} from "./selection";
import {
  CollectionTemplate,
  AnalysisTemplate,
} from "../../components/studio/templates";
import { LabelBars, NeuronMap } from "./plots";
import "./interp.css";

function useReadEvidence(path: string | null, data: unknown, ready: boolean) {
  const studio = useStudio(),
    id = useId();
  useEffect(() => {
    if (!path || !ready || data === undefined) return;
    return studio.evidence.register({
      id: `interp-api:${id}`,
      label: `GET ${path}`,
      data,
      route: studio.route,
      project: studio.project,
      user: String(studio.user?.id || ""),
    });
  }, [
    studio.evidence,
    studio.route,
    studio.project,
    studio.user?.id,
    id,
    path,
    data,
    ready,
  ]);
}
function useSelected<T = any>(path: string | null) {
  const { api, scopeKey } = useStudio(),
    store = useMemo(() => new EvidenceSelection<T>(), [api, scopeKey]);
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    if (path) void store.select(path, (signal) => api.get<T>(path, signal));
    else store.clear();
    return () => store.cancel();
  }, [store, api, path, version]);
  const current: SelectionSnapshot<T> =
    snapshot.key === path
      ? snapshot
      : { key: path || "", status: path ? "loading" : "idle" };
  useReadEvidence(path, current.data, current.status === "ready");
  return { ...current, reload: () => setVersion((value) => value + 1) };
}
function useLayerRows(path: string | null, paged = true) {
  const { api, scopeKey } = useStudio(),
    store = useMemo(() => new EvidenceSelection<any>(), [api, scopeKey]);
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot);
  useEffect(() => {
    if (!path) {
      store.clear();
      return;
    }
    void store.select(path, async (signal) => {
      let offset = 0,
        rows: any[] = [],
        first: any;
      for (;;) {
        const result = await api.get<any>(
          `${path}${path.includes("?") ? "&" : "?"}limit=2000${paged ? `&offset=${offset}` : ""}`,
          signal,
        );
        first ||= result;
        const batch = result.rows || [];
        rows.push(...batch);
        offset += batch.length;
        if (!paged || !batch.length || offset >= result.total) break;
      }
      return { ...first, rows };
    });
    return () => store.cancel();
  }, [store, api, path, paged]);
  const current: SelectionSnapshot<any> =
    snapshot.key === path
      ? snapshot
      : { key: path || "", status: path ? "loading" : "idle" };
  useReadEvidence(path, current.data, current.status === "ready");
  return current;
}
function ErrorState({
  error,
  retry,
}: {
  error?: Error | null;
  retry?: () => void;
}) {
  return (
    <div role="alert" className="interp-alert">
      <p>{error?.message || "This evidence could not be loaded."}</p>
      {retry && <Button onClick={retry}>Try again</Button>}
    </div>
  );
}
function TaskAction({
  path,
  body,
  children,
  onQueued,
}: {
  path: string;
  body: unknown;
  children: any;
  onQueued?: () => void;
}) {
  const studio = useStudio(),
    [busy, setBusy] = useState(false);
  return (
    <Button
      loading={busy}
      onClick={async () => {
        setBusy(true);
        try {
          const result = await studio.api.post(path, body);
          studio.trackTask(result);
          onQueued?.();
        } catch (error: any) {
          if (error.name !== "AbortError") studio.toast(error.message, true);
        } finally {
          setBusy(false);
        }
      }}
    >
      {children}
    </Button>
  );
}
function ActionDialog({
  mode,
  onClose,
}: {
  mode: "capture" | "compare";
  onClose: () => void;
}) {
  const studio = useStudio(),
    jobs = useResource<any[]>("/api/jobs"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const experiments = (jobs.data || []).flatMap(
    ({ job, experiments: values }) =>
      (values || [])
        .filter((e: any) => e.state === "succeeded")
        .map((e: any) => ({
          id: `${job.name}/${e.number}`,
          checkpoints: e.checkpoints || [],
        })),
  );
  const choices = [
    ...new Set([
      ...experiments.flatMap((e) => [
        e.id,
        ...e.checkpoints.map((step: number) => `${e.id}@${step}`),
      ]),
      ...(studio.overview?.catalog || []).map((model: any) => model.id),
    ]),
  ];
  const datasets = (studio.overview?.tree?.datasets || []).filter(
      (d: any) => !d.missing,
    ),
    snapshots = datasets.flatMap((d: any) => d.snapshots || []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const fields = new FormData(event.currentTarget);
    try {
      const layers = parseLayers(String(fields.get("layers") || ""));
      let body: any;
      if (mode === "capture") {
        const source = String(fields.get("source") || ""),
          separator = source.indexOf(":");
        if (separator < 0) throw new Error("Choose a dataset or snapshot.");
        body = {
          model: fields.get("model"),
          layers,
          topk: Number(fields.get("topk")),
          name: fields.get("name") || null,
          [source.slice(0, separator)]: source.slice(separator + 1),
        };
      } else
        body = {
          experiment: fields.get("experiment"),
          dataset: fields.get("dataset"),
          layers,
          base: fields.get("base") || null,
        };
      const result = await studio.api.post(
        mode === "capture" ? "/api/interp/captures" : "/api/interp/diffs",
        body,
      );
      studio.trackTask(result);
      onClose();
    } catch (cause: any) {
      if (cause.name !== "AbortError") setError(cause.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      open
      title={
        mode === "capture"
          ? "Capture model activations"
          : "Compare base and experiment"
      }
      onClose={onClose}
      wide
    >
      <form onSubmit={submit}>
        <p className="muted">
          {mode === "capture"
            ? "Record the strongest positive, negative and gate-open tokens. Shared-prefix positions are excluded from the evidence."
            : "Run the same source through both models and inspect which neurons and tokens changed."}
        </p>
        <div className="interp-form-grid">
          {mode === "capture" ? (
            <>
              <Field label="Model">
                <input
                  name="model"
                  required
                  list="capture-models"
                  placeholder="job/1 or a Hub model ID"
                />
                <datalist id="capture-models">
                  {choices.map((id) => (
                    <option key={String(id)} value={String(id)} />
                  ))}
                </datalist>
              </Field>
              <Field label="Source">
                <select name="source" required>
                  {datasets.map((d: any) => (
                    <option key={`d${d.name}`} value={`dataset:${d.name}`}>
                      Dataset · {d.name} ({d.datapoints})
                    </option>
                  ))}
                  {snapshots.map((s: any) => (
                    <option key={`s${s.name}`} value={`snapshot:${s.name}`}>
                      Snapshot · {s.name}
                    </option>
                  ))}
                </select>
              </Field>
            </>
          ) : (
            <>
              <Field label="Experiment">
                <select name="experiment" required>
                  {experiments.map((e) => (
                    <option key={e.id}>{e.id}</option>
                  ))}
                </select>
              </Field>
              <Field label="Dataset">
                <select name="dataset" required>
                  {datasets.map((d: any) => (
                    <option key={d.name}>{d.name}</option>
                  ))}
                </select>
              </Field>
              <Field
                label="Compare against"
                hint="Leave empty to use the experiment's base weights."
              >
                <input name="base" list="comparison-models" />
                <datalist id="comparison-models">
                  {choices.map((id) => (
                    <option key={String(id)} value={String(id)} />
                  ))}
                </datalist>
              </Field>
            </>
          )}
          <Field label="Layers" hint="Comma-separated numbers or all.">
            <input name="layers" placeholder="all" />
          </Field>
          {mode === "capture" && (
            <>
              <Field label="Top tokens per neuron">
                <input
                  type="number"
                  name="topk"
                  defaultValue={40}
                  min={5}
                  max={500}
                  required
                />
              </Field>
              <Field label="Capture name (optional)">
                <input name="name" />
              </Field>
            </>
          )}
        </div>
        {error && <p role="alert">{error}</p>}
        {!datasets.length && (
          <p role="status">Create a dataset before starting this operation.</p>
        )}
        {mode === "compare" && !experiments.length && (
          <p role="status">
            A completed experiment is required for comparison.
          </p>
        )}
        <div className="actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            loading={busy}
            disabled={
              !datasets.length || (mode === "compare" && !experiments.length)
            }
          >
            {mode === "capture" ? "Capture" : "Compare"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function InterpretabilityPage() {
  const studio = useStudio(),
    resource = useResource<any>("/api/interp"),
    [dialog, setDialog] = useState<"capture" | "compare" | null>(null);
  const remove = async (kind: "captures" | "diffs", name: string) => {
    if (
      !(await studio.confirm(
        `Delete ${kind === "captures" ? "capture" : "comparison"} “${name}” and its saved evidence?`,
        "Delete interpretability artifact",
      ))
    )
      return;
    try {
      await studio.api.del(`/api/interp/${kind}/${encodeURIComponent(name)}`);
      resource.reload();
    } catch (error: any) {
      studio.toast(error.message, true);
    }
  };
  return (
    <Page
      className="interp-page"
      title="Interpretability"
      description="Follow model behavior from the layer overview to the tokens that explain it."
      actions={
        <>
          <Button onClick={() => setDialog("compare")}>Compare models</Button>
          <Button variant="primary" onClick={() => setDialog("capture")}>
            New capture
          </Button>
        </>
      }
    >
      <CollectionTemplate
        label="Interpretability artifacts"
        summary={
          resource.data ? (
            <div className="interp-collection-summary">
              <Badge>{resource.data.captures.length} captures</Badge>
              <Badge>{resource.data.comparisons.length} comparisons</Badge>
            </div>
          ) : null
        }
      >
        {resource.error && (
          <ErrorState error={resource.error} retry={resource.reload} />
        )}
        {resource.loading && (
          <div role="status" className="interp-loading">
            Loading model evidence…
          </div>
        )}
        {resource.data && (
          <>
            <Panel title="Activation captures">
              <div className="interp-artifact-table">
                <DataTable<any>
                  label="Activation captures"
                  rows={resource.data.captures}
                  getRowId={(capture) => capture.name}
                  scopeKey={studio.scopeKey}
                  empty=""
                  renderRow={(capture, { key, ...props }, cells) => (
                    <Evidence
                      {...props}
                      as="tr"
                      label="Activation capture"
                      data={capture}
                      key={key}
                    >
                      {cells}
                    </Evidence>
                  )}
                  columns={[
                    {
                      id: "name",
                      label: "Capture",
                      value: (capture) => capture.name,
                      render: (capture) => (
                        <Link
                          to={`/interp/c/${encodeURIComponent(capture.name)}`}
                        >
                          {capture.name}
                        </Link>
                      ),
                    },
                    {
                      id: "model",
                      label: "Model / source",
                      value: (capture) => capture.model_id,
                      render: (capture) => (
                        <>
                          {capture.model_id}
                          <small>{capture.source}</small>
                        </>
                      ),
                    },
                    {
                      id: "rows",
                      label: "Datapoints",
                      value: (capture) => capture.rows,
                      render: (capture) => capture.rows?.toLocaleString(),
                    },
                    {
                      id: "layers",
                      label: "Layers",
                      value: (capture) => capture.layers.length,
                    },
                    {
                      id: "evidence",
                      label: "Evidence",
                      value: (capture) =>
                        `${capture.explained || 0} explained · ${capture.tested || 0} tested`,
                    },
                    {
                      id: "actions",
                      label: "Actions",
                      value: () => null,
                      sortable: false,
                      filterable: false,
                      hideable: false,
                      render: (capture) => (
                        <div className="actions">
                          <Link
                            to={`/interp/c/${encodeURIComponent(capture.name)}`}
                          >
                            Explore neurons →
                          </Link>
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={() => remove("captures", capture.name)}
                          >
                            Delete
                          </Button>
                        </div>
                      ),
                    },
                  ]}
                />
              </div>
              {!resource.data.captures.length && (
                <EmptyState>
                  Capture a model on a dataset to begin exploring its neurons.
                </EmptyState>
              )}
            </Panel>
            <Panel title="Before / after training">
              <div className="interp-artifact-table">
                <DataTable<any>
                  label="Model comparisons"
                  rows={resource.data.comparisons}
                  getRowId={(comparison) => comparison.name}
                  scopeKey={studio.scopeKey}
                  empty=""
                  renderRow={(comparison, { key, ...props }, cells) => (
                    <Evidence
                      {...props}
                      as="tr"
                      label="Model comparison"
                      data={comparison}
                      key={key}
                    >
                      {cells}
                    </Evidence>
                  )}
                  columns={[
                    {
                      id: "name",
                      label: "Comparison",
                      value: (comparison) => comparison.name,
                      render: (comparison) => (
                        <Link
                          to={`/interp/d/${encodeURIComponent(comparison.name)}`}
                        >
                          {comparison.name}
                        </Link>
                      ),
                    },
                    {
                      id: "experiment",
                      label: "Experiment / base",
                      value: (comparison) => comparison.experiment,
                      render: (comparison) => (
                        <>
                          {comparison.experiment}
                          <small>vs {comparison.base}</small>
                        </>
                      ),
                    },
                    {
                      id: "source",
                      label: "Source",
                      value: (comparison) => comparison.source,
                    },
                    {
                      id: "rows",
                      label: "Datapoints",
                      value: (comparison) => comparison.rows,
                    },
                    {
                      id: "correlation",
                      label: "Layer correlation",
                      value: (comparison) =>
                        Object.entries(comparison.layer_summary || {})
                          .map(
                            ([layer, summary]: any) =>
                              `L${layer} r ${number(summary.median_r)}`,
                          )
                          .join(" · "),
                    },
                    {
                      id: "actions",
                      label: "Actions",
                      value: () => null,
                      sortable: false,
                      filterable: false,
                      hideable: false,
                      render: (comparison) => (
                        <div className="actions">
                          <Link
                            to={`/interp/d/${encodeURIComponent(comparison.name)}`}
                          >
                            Explore changes →
                          </Link>
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={() => remove("diffs", comparison.name)}
                          >
                            Delete
                          </Button>
                        </div>
                      ),
                    },
                  ]}
                />
              </div>
              {!resource.data.comparisons.length && (
                <EmptyState>
                  Compare a completed experiment with its base model to find the
                  neurons that changed.
                </EmptyState>
              )}
            </Panel>
          </>
        )}
      </CollectionTemplate>
      {dialog && <ActionDialog mode={dialog} onClose={() => setDialog(null)} />}
    </Page>
  );
}

function Metric({ label, value }: { label: string; value: any }) {
  return (
    <div className="neuron-metric">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}
function ActivationCard({ entry }: { entry: Activation }) {
  return (
    <Evidence
      as="article"
      className="activation-card"
      label="Neuron token activation"
      data={entry}
    >
      <div className="activation-meta">
        <span>
          Datapoint {entry.datapoint} · token {entry.position}
          {entry.first_token ? " · first after prefix" : ""}
        </span>
        <strong
          className={`activation-score ${entry.value < 0 ? "negative" : ""}`}
        >
          {number(entry.value)}
        </strong>
      </div>
      <p className="activation-prompt">
        {entry.user || "No user message saved for this context."}
      </p>
      <div className="activation-tags">
        {entry.labels.map((label) => (
          <span key={label} className="activation-tag">
            {label}
          </span>
        ))}
        {entry.bucket && <span className="activation-tag">{entry.bucket}</span>}
      </div>
      <details>
        <summary>
          Inspect token evidence ·{" "}
          <span className="active-token">
            {readableToken(entry.token) || "empty token"}
          </span>
        </summary>
        <div className="evidence-facts">
          <span>{REGIMES[entry.regime]?.label || entry.regime}</span>
          <span>Gate {number(entry.g, 3)}</span>
          <span>Value {number(entry.v, 3)}</span>
        </div>
        <div className="token-context" aria-label="Exact token context">
          {entry.before}
          <mark className="active-token">{entry.token || "␠"}</mark>
          {entry.after}
        </div>
        <p className="muted">
          Exact saved tokenizer context. The highlighted token produced this
          activation.
        </p>
      </details>
    </Evidence>
  );
}

function TestEvidence({ view, base }: { view: any; base: string }) {
  const explanation = view.explanation,
    test = view.test;
  return (
    <>
      <div className="actions">
        <TaskAction path={`${base}/explain`} body={{ neurons: [view.neuron] }}>
          {explanation ? "Explain again" : "Explain with helper model"}
        </TaskAction>
        <TaskAction path={`${base}/test`} body={{ neurons: [view.neuron] }}>
          Run blind / near-miss test
        </TaskAction>
      </div>
      <p className="muted">
        These actions run the configured helper model as background tasks.
        Reload evidence after completion.
      </p>
      {explanation ? (
        <Evidence label="Neuron explanation" data={explanation}>
          <h3>{explanation.explanation}</h3>
          <p>Negative pole: {explanation.negative_pole || "—"}</p>
          <p className="muted">
            Confidence {percent(explanation.confidence)} · {explanation.helper}
            {explanation.gate_never_open
              ? " · explained from the closed-gate echo"
              : ""}
          </p>
        </Evidence>
      ) : (
        <EmptyState>
          No saved explanation. Inspect the examples or ask the helper model to
          propose a rule.
        </EmptyState>
      )}
      {test && (
        <Evidence label="Neuron explanation test" data={test}>
          <div className="neuron-metrics">
            <Metric
              label="Blind maximum"
              value={`${number(test.blind_best)}×`}
            />
            <Metric
              label="Positive / strong"
              value={`${test.counts?.positives_strong ?? 0}/${test.positives?.length ?? 0}`}
            />
            <Metric
              label="Near misses / strong"
              value={`${test.counts?.near_misses_strong ?? 0}/${test.near_misses?.length ?? 0}`}
            />
            <Metric label="Verdict" value={test.verdict || "—"} />
          </div>
          <p className="muted">
            Reference: {test.reference || "corpus maximum"}{" "}
            {number(test.open_max ?? test.corpus_max)}. Strong means at least{" "}
            {percent(test.strong_ratio)} of that reference; gate-open
            activations are used when available.
          </p>
          {[
            ["Blind maximum", test.blind],
            ["Should fire", test.positives],
            ["Near misses", test.near_misses],
            ["Distant", test.distant],
          ].map(([title, entries]: any) => (
            <details className="test-exchanges" key={title}>
              <summary>
                {title} · {(entries || []).length} exchanges
              </summary>
              {(entries || []).map((entry: any, index: number) => (
                <Evidence
                  className="diff-datapoint"
                  label="Neuron test exchange"
                  data={entry}
                  key={index}
                >
                  <p>{entry.text}</p>
                  <div className="evidence-facts">
                    <b>{number(entry.ratio)}×</b>
                    <span>
                      {entry.strong ? "Strong" : "Below strong threshold"}
                    </span>
                    {entry.gate_open === false && <span>Closed-gate echo</span>}
                    <code>{entry.token}</code>
                  </div>
                </Evidence>
              ))}
            </details>
          ))}
        </Evidence>
      )}
    </>
  );
}

function DriftEvidence({
  meta,
  layer,
  neuron,
}: {
  meta: any;
  layer: number;
  neuron: number;
}) {
  const match = /^([^/@]+)\/(\d+)(?:@\d+)?$/.exec(meta.model_id || ""),
    [index, setIndex] = useState(0);
  const path = match
    ? `/api/interp/drift/${encodeURIComponent(match[1])}/${match[2]}?source=${encodeURIComponent(meta.source)}&layer=${layer}&neuron=${neuron}`
    : null;
  const result = useSelected(path),
    data = result.data;
  if (!match)
    return (
      <EmptyState>
        Checkpoint drift is available for captured training experiments.
      </EmptyState>
    );
  if (result.status === "loading")
    return <p role="status">Loading checkpoint evidence…</p>;
  if (result.status === "error")
    return <ErrorState error={result.error} retry={result.reload} />;
  if (!data) return null;
  const step = data.steps[Math.min(index, data.steps.length - 1)];
  return (
    <>
      <p className="muted">
        {data.list === "gate_open" ? "Gate-open" : "Overall positive"} top-
        {data.topk} datapoint sets. The first saved checkpoint is the baseline.
        Labels present initially: {data.core_labels.join(", ") || "none"}.
      </p>
      <div className="drift-steps" aria-label="Checkpoint selection">
        {data.steps.map((item: any, i: number) => (
          <button
            key={item.capture}
            aria-pressed={step === item}
            onClick={() => setIndex(i)}
          >
            {item.final ? "Final weights" : `Step ${item.step}`}
          </button>
        ))}
      </div>
      {step && (
        <Evidence label="Selected checkpoint drift" data={step}>
          <div className="neuron-metrics">
            <Metric label="Peak activation" value={number(step.max)} />
            <Metric label="Datapoints" value={step.datapoints} />
            <Metric
              label="Overlap / final"
              value={percent(step.jaccard_final)}
            />
            <Metric
              label="Overlap / previous"
              value={percent(step.jaccard_prev)}
            />
          </div>
          <LabelBars pairs={step.label_share} denominator={step.datapoints} />
          {step.new_labels.length > 0 && (
            <p className="evidence-note">
              Newly represented labels: {step.new_labels.join(", ")}. These are
              retained examples, not proof of a new causal feature.
            </p>
          )}
          <Link
            to={`/interp/c/${encodeURIComponent(step.capture)}?layer=${layer}&neuron=${neuron}`}
          >
            Open this checkpoint's capture →
          </Link>
          {(step.entries || [])
            .slice(0, 6)
            .map((entry: Activation, i: number) => (
              <ActivationCard
                key={`${entry.datapoint}:${entry.position}:${i}`}
                entry={entry}
              />
            ))}
        </Evidence>
      )}
    </>
  );
}

function NeuronEvidence({
  view,
  meta,
  layer,
  base,
  onBack,
  reload,
}: {
  view: any;
  meta: any;
  layer: number;
  base: string;
  onBack: () => void;
  reload: () => void;
}) {
  const [tab, setTab] = useState("examples"),
    [pole, setPole] = useState<"gate_open" | "positives" | "negatives">(
      view.gate_open.length ? "gate_open" : "positives",
    ),
    [regime, setRegime] = useState(""),
    [label, setLabel] = useState(""),
    [page, setPage] = useState(0),
    [pinnedToken, setPinnedToken] = useState<string | null>(null);
  const tabs = [
    ["examples", "Examples"],
    ["labels", "Labels & regimes"],
    ["tests", "Explanation & tests"],
    ["drift", "Checkpoints"],
  ];
  const entries = filterEntries(view[pole] || [], { regime, label }),
    start = page * 8,
    shown = entries.slice(start, start + 8),
    stats = view.stats;
  const tokenKey = (entry: Activation) =>
    `${entry.datapoint}:${entry.position}`;
  const pinned = entries.find(
    (entry: Activation) => tokenKey(entry) === pinnedToken,
  );
  const labels = labelSummary(view, pole);
  const tabKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const shift =
      event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!shift && !["Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : (index + shift + tabs.length) % tabs.length;
    setTab(tabs[next][0]);
    event.currentTarget.parentElement
      ?.querySelectorAll<HTMLButtonElement>("button")
      [next]?.focus();
  };
  return (
    <Panel className="selected-neuron">
      <div className="neuron-summary">
        <div className="neuron-back neuron-selection-return">
          <Button size="sm" variant="ghost" onClick={onBack}>
            ← Back to layer overview
          </Button>
        </div>
        <div className="neuron-eyebrow">Layer {layer} · selected evidence</div>
        <h2>Neuron {view.neuron}</h2>
        {view.explanation?.explanation && <p>{view.explanation.explanation}</p>}
        <div className="neuron-metrics">
          <Metric label="Peak activation" value={number(stats.max)} />
          <Metric
            label="Gate-open peak"
            value={
              stats.open_max == null ? "Never open" : number(stats.open_max)
            }
          />
          <Metric label="Negative peak" value={number(stats.min)} />
          <Metric label="Token density" value={percent(stats.density, 1)} />
        </div>
      </div>
      {stats.first_token_peak && (
        <p className="evidence-note">
          <b>First-token peak.</b> Strongest on the first token after the shared
          prefix. This may be an attention-sink artifact; inspect the examples
          before treating it as a learned feature.
        </p>
      )}
      {stats.echo_top && (
        <p className="evidence-note">
          <b>Closed-gate echo dominates.</b> Negative gate × negative value
          creates a positive activation. The gate-open peak is{" "}
          {stats.open_max == null
            ? "absent"
            : "less than half the overall peak"}
          ; inspect the negative pole too.
        </p>
      )}
      <div
        className="evidence-tabs"
        role="tablist"
        aria-label="Neuron evidence"
      >
        {tabs.map(([key, title], index) => (
          <button
            key={key}
            role="tab"
            aria-selected={key === tab}
            tabIndex={key === tab ? 0 : -1}
            aria-controls={`neuron-${view.neuron}-${key}`}
            id={`tab-${view.neuron}-${key}`}
            onKeyDown={(e) => tabKey(e, index)}
            onClick={() => setTab(key)}
          >
            {title}
          </button>
        ))}
      </div>
      <div
        className="neuron-evidence"
        role="tabpanel"
        tabIndex={0}
        id={`neuron-${view.neuron}-${tab}`}
        aria-labelledby={`tab-${view.neuron}-${tab}`}
      >
        {tab === "examples" && (
          <>
            <div className="pole-switch" aria-label="Activation pole">
              {[
                ["gate_open", "Gate open"],
                ["positives", "All positives"],
                ["negatives", "Negative pole"],
              ].map(([key, title]) => (
                <button
                  key={key}
                  aria-pressed={pole === key}
                  onClick={() => {
                    setPole(key as any);
                    setRegime("");
                    setPage(0);
                  }}
                >
                  {title}
                </button>
              ))}
            </div>
            <p className="interp-legend">
              {entries.length} matching returned examples · retained top-
              {view.topk} tokens per pole
              {(regime || label) && (
                <button
                  onClick={() => {
                    setRegime("");
                    setLabel("");
                    setPage(0);
                  }}
                >
                  Clear filters
                </button>
              )}
              . Warm values are negative, cool values positive.
            </p>
            <div
              className="interp-token-strip"
              role="group"
              aria-label="Returned token activations"
            >
              {shown.map((entry: Activation) => (
                <Button
                  size="sm"
                  key={tokenKey(entry)}
                  aria-pressed={pinnedToken === tokenKey(entry)}
                  aria-label={`Pin datapoint ${entry.datapoint} token ${entry.position}`}
                  onClick={() => setPinnedToken(tokenKey(entry))}
                >
                  <span>{readableToken(entry.token) || "empty token"}</span>
                  <small>
                    {number(entry.value)} · {entry.position}
                  </small>
                </Button>
              ))}
            </div>
            <div
              className={cx(
                "interp-example-workspace",
                pinned && "has-pinned-token",
              )}
            >
              <div className="interp-example-list">
                {shown.map((entry: Activation, index: number) => (
                  <ActivationCard
                    entry={entry}
                    key={`${entry.datapoint}:${entry.position}:${index}`}
                  />
                ))}
              </div>
              {pinned && (
                <aside
                  className="interp-pinned-evidence"
                  aria-label="Pinned token evidence"
                >
                  <div className="interp-pin-heading">
                    <h3>Pinned token · {pinned.position}</h3>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setPinnedToken(null)}
                    >
                      Clear pin
                    </Button>
                  </div>
                  <p className="interp-legend">
                    Datapoint {pinned.datapoint} · exact retained context
                  </p>
                  <div className="token-context">
                    {pinned.before}
                    <mark className="active-token">{pinned.token || "␠"}</mark>
                    {pinned.after}
                  </div>
                  <div className="evidence-facts">
                    <span>Activation {number(pinned.value)}</span>
                    <span>
                      {REGIMES[pinned.regime]?.label || pinned.regime}
                    </span>
                    <span>Gate {number(pinned.g, 3)}</span>
                    <span>Value {number(pinned.v, 3)}</span>
                  </div>
                </aside>
              )}
            </div>
            {!shown.length && (
              <EmptyState>
                No returned examples match these filters. Try another pole or
                clear the label/regime filter.
              </EmptyState>
            )}
            <div className="evidence-pagination">
              <Button
                size="sm"
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
              >
                Previous examples
              </Button>
              <span className="muted">
                {entries.length
                  ? `${start + 1}–${Math.min(start + 8, entries.length)} of ${entries.length}`
                  : "0 examples"}
              </span>
              <Button
                size="sm"
                disabled={start + 8 >= entries.length}
                onClick={() => setPage((p) => p + 1)}
              >
                Next examples
              </Button>
            </div>
            {view.topk > 200 && (
              <p className="muted">
                The API returns at most 200 examples per pole. Regime and label
                summaries still describe the complete retained top-{view.topk}{" "}
                list.
              </p>
            )}
          </>
        )}
        {tab === "labels" && (
          <>
            <h3>Gate / value regimes</h3>
            <p className="muted">
              Counts among{" "}
              {
                Object.values(view.regimes || {}).reduce(
                  (a: number, b: any) => a + Number(b),
                  0,
                ) as number
              }{" "}
              retained overall-positive tokens. Select a regime to inspect its
              examples.
            </p>
            <div className="regime-counts">
              {(Object.keys(REGIMES) as Regime[]).map((key) => (
                <button
                  key={key}
                  className="regime-count"
                  aria-pressed={regime === key}
                  onClick={() => {
                    setPole("positives");
                    setRegime(key);
                    setLabel("");
                    setPage(0);
                    setTab("examples");
                  }}
                >
                  <span>
                    {REGIMES[key].short} <small>({key})</small>
                  </span>
                  <b>{view.regimes?.[key] ?? 0}</b>
                </button>
              ))}
            </div>
            <h3>
              Labels in strongest{" "}
              {labels.pole === "gate_open" ? "gate-open" : "overall-positive"}{" "}
              examples
            </h3>
            {!view.gate_open.length && (
              <p className="muted">
                No gate-open examples; this summary uses the overall-positive
                pole.
              </p>
            )}
            <div className="pole-switch">
              {view.gate_open.length > 0 && (
                <button
                  aria-pressed={labels.pole === "gate_open"}
                  onClick={() => {
                    setPole("gate_open");
                    setLabel("");
                  }}
                >
                  Gate-open labels
                </button>
              )}
              <button
                aria-pressed={labels.pole === "positives"}
                onClick={() => {
                  setPole("positives");
                  setLabel("");
                }}
              >
                Overall-positive labels
              </button>
            </div>
            <LabelBars
              pairs={labels.pairs}
              denominator={labels.denominator}
              selected={label}
              onSelect={(value) => {
                setPole(labels.pole);
                setLabel(value);
                setRegime("");
                setPage(0);
                setTab("examples");
              }}
            />
          </>
        )}
        {tab === "tests" && <TestEvidence view={view} base={base} />}
        {tab === "drift" && (
          <DriftEvidence meta={meta} layer={layer} neuron={view.neuron} />
        )}
      </div>
      <div className="interp-reading">
        <Button size="sm" variant="ghost" onClick={reload}>
          Reload saved evidence
        </Button>
        <p>
          Token density is the share of content tokens above 10% of this
          neuron's maximum. Examples are retained extremes, not a random sample
          of the corpus.
        </p>
      </div>
    </Panel>
  );
}

export function CapturePage() {
  const { capture = "" } = useParams(),
    [params, setParams] = useSearchParams(),
    studio = useStudio();
  const meta = useResource<any>(
      `/api/interp/captures/${encodeURIComponent(capture)}`,
    ),
    layers = meta.data?.layers || [],
    requestedLayer = params.has("layer")
      ? Number(params.get("layer"))
      : layers[Math.floor(layers.length / 2)],
    layer = requestedLayer;
  const neuron = params.has("neuron") ? Number(params.get("neuron")) : null,
    sort = params.get("sort") || "max",
    filter = params.get("q") || "",
    [search, setSearch] = useState(filter),
    [jump, setJump] = useState("");
  useEffect(() => setSearch(filter), [filter]);
  const validLayer = layers.includes(layer),
    table = useLayerRows(
      validLayer
        ? `/api/interp/captures/${encodeURIComponent(capture)}/layers/${layer}?sort=${encodeURIComponent(sort)}`
        : null,
    ),
    rows = table.data?.rows || [];
  const selected = useSelected<any>(
    validLayer &&
      neuron != null &&
      Number.isInteger(neuron) &&
      neuron >= 0 &&
      neuron < (meta.data?.neurons_per_layer ?? 0)
      ? `/api/interp/captures/${encodeURIComponent(capture)}/layers/${layer}/neurons/${neuron}?limit=200`
      : null,
  );
  const [listPage, setListPage] = useState(0),
    detail = useRef<HTMLDivElement>(null);
  const filtered = rows.filter(
    (row: any) =>
      !filter ||
      String(row.neuron).includes(filter) ||
      (row.explanation || "").toLowerCase().includes(filter.toLowerCase()),
  );
  const navigate = (changes: Record<string, any>, replace = false) => {
    const next = new URLSearchParams(params);
    Object.entries(changes).forEach(([key, value]) =>
      value == null || value === ""
        ? next.delete(key)
        : next.set(key, String(value)),
    );
    setParams(next, { replace });
  };
  const select = (value: number) => {
    navigate({ layer, neuron: value }, true);
    if (matchMedia("(max-width:850px)").matches)
      requestAnimationFrame(() => detail.current?.focus());
  };
  const plotRows = useMemo(
    () => filtered.map((row: any) => ({ ...row, x: row.density, y: row.max })),
    [table.data, filter],
  );
  useComponentAction({
    id: "capture.layer",
    label: "Choose capture layer",
    kind: "select",
    state: String(layer ?? ""),
    options: layers.map((value: number) => ({
      value: String(value),
      label: `Layer ${value}`,
    })),
    disabled: !layers.length,
    run: (value) => {
      const next = Number(value);
      if (!layers.includes(next)) throw Error("Choose a saved layer");
      setListPage(0);
      navigate({ layer: next, neuron: null });
    },
  });
  useComponentAction({
    id: "capture.neuron",
    label: "Explore a captured neuron",
    kind: "select",
    state: neuron == null ? "" : String(neuron),
    options: filtered
      .slice(listPage * 40, listPage * 40 + 40)
      .map((row: any) => ({
        value: String(row.neuron),
        label: `Neuron ${row.neuron}`,
      })),
    disabled: !validLayer,
    run: (value) => {
      const next = Number(value);
      if (
        value == null ||
        !Number.isInteger(next) ||
        next < 0 ||
        next >= meta.data?.neurons_per_layer
      )
        throw Error("Enter a valid neuron number");
      select(next);
    },
  });
  useComponentAction({
    id: "capture.filter",
    label: "Find a neuron or explanation",
    kind: "filter",
    state: filter,
    run: (value) => {
      setSearch(value || "");
      setListPage(0);
      navigate({ q: value || "" }, true);
    },
  });
  const stats = meta.data?.layer_stats?.[String(layer)] || {};
  return (
    <Page
      className={cx(
        "interp-page",
        selected.status === "ready" && selected.data && "has-selected-neuron",
      )}
      title={capture}
      description={
        meta.data
          ? `${meta.data.model_id} · ${meta.data.source} · ${meta.data.rows.toLocaleString()} datapoints · ${meta.data.tokens?.toLocaleString()} tokens`
          : "Loading capture…"
      }
      actions={<Link to="/interp">All captures</Link>}
    >
      {meta.error && <ErrorState error={meta.error} retry={meta.reload} />}
      {meta.data && (
        <AnalysisTemplate
          label="Capture evidence"
          className={cx("interp-analysis", neuron != null && "has-selection")}
          toolbar={
            <div className="interp-toolbar">
              <Field label="Layer">
                <select
                  value={layer}
                  onChange={(e) => {
                    setListPage(0);
                    navigate({ layer: e.target.value, neuron: null });
                  }}
                >
                  {layers.map((l: number) => (
                    <option value={l} key={l}>
                      Layer {l}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Sort neurons">
                <select
                  value={sort}
                  onChange={(e) => {
                    setListPage(0);
                    navigate({ sort: e.target.value });
                  }}
                >
                  {[
                    ["max", "Highest peak"],
                    ["min", "Most negative"],
                    ["open_max", "Gate-open peak"],
                    ["echo_share", "Echo share"],
                    ["density", "Token density"],
                  ].map(([key, title]) => (
                    <option key={key} value={key}>
                      {title}
                    </option>
                  ))}
                </select>
              </Field>
              <form
                className="search-field"
                onSubmit={(e) => {
                  e.preventDefault();
                  setListPage(0);
                  navigate({ q: search });
                }}
              >
                <Field label="Find neuron or explanation">
                  <input
                    type="search"
                    value={search}
                    onChange={(e) => {
                      setSearch(e.target.value);
                      setListPage(0);
                      navigate({ q: e.target.value }, true);
                    }}
                    placeholder="Neuron ID or explanation"
                  />
                </Field>
              </form>
              <form
                className="neuron-quick-jump"
                onSubmit={(e) => {
                  e.preventDefault();
                  const value = Number(jump);
                  if (
                    Number.isInteger(value) &&
                    value >= 0 &&
                    value < meta.data.neurons_per_layer
                  )
                    select(value);
                  else studio.toast("Enter a valid neuron number.", true);
                }}
              >
                <Field label="Jump to neuron">
                  <input
                    type="number"
                    min={0}
                    max={meta.data.neurons_per_layer - 1}
                    value={jump}
                    onChange={(e) => setJump(e.target.value)}
                  />
                </Field>
                <Button type="submit" size="sm">
                  Explore
                </Button>
              </form>
            </div>
          }
          summary={
            validLayer ? (
              <p className="interp-legend">
                {meta.data.neurons_per_layer.toLocaleString()} neurons ·{" "}
                {meta.data.prefix_len || 0} shared-prefix tokens excluded ·
                median peak {number(stats.max_median)} · first-token peaks{" "}
                {stats.first_token_peaks ?? "—"} · never open{" "}
                {stats.never_open ?? "—"}
              </p>
            ) : null
          }
          inspector={
            validLayer ? (
              <div
                className="interp-detail"
                ref={detail}
                tabIndex={-1}
                aria-label="Selected neuron details"
              >
                {neuron == null ? (
                  <Panel>
                    <EmptyState>
                      Select a neuron to inspect its examples, labels,
                      explanation and test results.
                    </EmptyState>
                  </Panel>
                ) : selected.status === "loading" ? (
                  <Panel>
                    <p role="status" className="interp-loading">
                      Loading neuron {neuron}…
                    </p>
                  </Panel>
                ) : selected.error ? (
                  <ErrorState error={selected.error} retry={selected.reload} />
                ) : selected.data ? (
                  <NeuronEvidence
                    key={`${capture}:${layer}:${neuron}`}
                    view={selected.data}
                    meta={meta.data}
                    layer={layer}
                    base={`/api/interp/captures/${encodeURIComponent(capture)}/layers/${layer}`}
                    onBack={() => navigate({ neuron: null })}
                    reload={selected.reload}
                  />
                ) : (
                  <ErrorState
                    error={new Error("This neuron is outside the saved layer.")}
                  />
                )}
              </div>
            ) : null
          }
        >
          {!validLayer ? (
            <ErrorState
              error={
                new Error(
                  "This layer is not part of the capture. Choose a saved layer.",
                )
              }
            />
          ) : (
            <div className="interp-master">
              <Panel
                title="Layer overview"
                actions={<Badge>{filtered.length} neurons</Badge>}
              >
                {table.status === "loading" && (
                  <p role="status" className="interp-loading">
                    Loading the complete layer…
                  </p>
                )}
                {table.error && <ErrorState error={table.error} />}
                {table.data && (
                  <NeuronMap
                    rows={plotRows}
                    selected={neuron}
                    onSelect={select}
                    xLabel="Token density"
                    yLabel="Peak activation"
                    xFormat={(value) => percent(value, 1)}
                    label="Layer neuron activation map"
                  />
                )}
                <p className="interp-reading">
                  Select a point or row. Arrow keys move between points; Enter
                  opens the selected neuron.{" "}
                  {filtered.length !== rows.length &&
                    `Filtered to ${filtered.length} of ${rows.length} loaded neurons.`}
                </p>
              </Panel>
              <Panel title="Neuron evidence">
                <div className="neuron-list">
                  <SimpleTable<any>
                    label="Layer neurons"
                    rows={filtered.slice(listPage * 40, listPage * 40 + 40)}
                    getRowId={(row) => String(row.neuron)}
                    scopeKey={`${studio.scopeKey}:${capture}:${layer}`}
                    empty=""
                    rowProps={(row) => ({
                      className: row.neuron === neuron ? "selected" : "",
                    })}
                    renderRow={(row, { key, ...props }, cells) => (
                      <Evidence
                        {...props}
                        as="tr"
                        label="Neuron summary"
                        data={row}
                        key={key}
                      >
                        {cells}
                      </Evidence>
                    )}
                    columns={[
                      {
                        id: "neuron",
                        label: "Neuron",
                        value: (row) => row.neuron,
                        render: (row) => (
                          <button
                            aria-label={`Explore neuron ${row.neuron}`}
                            aria-current={
                              row.neuron === neuron ? "true" : undefined
                            }
                            onClick={() => select(row.neuron)}
                          >
                            <b>{row.neuron}</b>
                            {row.explanation && (
                              <div
                                className="explanation-cell"
                                title={row.explanation}
                              >
                                {row.explanation}
                              </div>
                            )}
                          </button>
                        ),
                      },
                      {
                        id: "peak",
                        label: "Peak",
                        value: (row) => row.max,
                        render: (row) => number(row.max),
                      },
                      {
                        id: "open",
                        label: "Open",
                        value: (row) => row.open_max,
                        render: (row) => number(row.open_max),
                      },
                      {
                        id: "density",
                        label: "Density",
                        value: (row) => row.density,
                        render: (row) => percent(row.density, 1),
                      },
                      {
                        id: "echo",
                        label: "Echo",
                        value: (row) => row.echo_share,
                        render: (row) => (
                          <>
                            {percent(row.echo_share)}
                            {row.echo_top ? "*" : ""}
                          </>
                        ),
                      },
                    ]}
                  />
                </div>
                {!filtered.length && table.status === "ready" && (
                  <EmptyState>
                    No matching neurons. Clear the search to return to this
                    layer.
                  </EmptyState>
                )}
                <div className="evidence-pagination">
                  <Button
                    size="sm"
                    disabled={listPage === 0}
                    onClick={() => setListPage((p) => p - 1)}
                  >
                    Previous neurons
                  </Button>
                  <small>
                    {filtered.length
                      ? `${listPage * 40 + 1}–${Math.min(listPage * 40 + 40, filtered.length)} of ${filtered.length}`
                      : "0 neurons"}
                  </small>
                  <Button
                    size="sm"
                    disabled={(listPage + 1) * 40 >= filtered.length}
                    onClick={() => setListPage((p) => p + 1)}
                  >
                    Next neurons
                  </Button>
                </div>
                <div className="interp-reading">
                  <TaskAction
                    path={`/api/interp/captures/${encodeURIComponent(capture)}/layers/${layer}/explain`}
                    body={{ top: 20, sort }}
                  >
                    Explain top 20
                  </TaskAction>
                  <p>
                    * Closed-gate echo dominates. Mean echo share:{" "}
                    {percent(stats.echo_share_mean)} of the retained top-
                    {meta.data.topk} positives.
                  </p>
                </div>
              </Panel>
            </div>
          )}
        </AnalysisTemplate>
      )}
    </Page>
  );
}

export function ComparisonPage() {
  const { comparison = "" } = useParams(),
    [params, setParams] = useSearchParams(),
    meta = useResource<any>(
      `/api/interp/diffs/${encodeURIComponent(comparison)}`,
    );
  const layers = meta.data?.layers || [],
    layer = params.has("layer")
      ? Number(params.get("layer"))
      : layers[layers.length - 1],
    sort = params.get("sort") || "change",
    neuron = params.has("neuron") ? Number(params.get("neuron")) : null;
  const data = useLayerRows(
    layers.includes(layer)
      ? `/api/interp/diffs/${encodeURIComponent(comparison)}/layers/${layer}?sort=${encodeURIComponent(sort)}`
      : null,
    false,
  );
  const selected = useSelected<any>(
    layers.includes(layer) &&
      neuron != null &&
      Number.isInteger(neuron) &&
      neuron >= 0 &&
      neuron < (meta.data?.neurons_per_layer ?? 0)
      ? `/api/interp/diffs/${encodeURIComponent(comparison)}/layers/${layer}/neurons/${neuron}`
      : null,
  );
  const captures = useResource<any>(selected.data ? "/api/interp" : null),
    [page, setPage] = useState(0),
    [maxR, setMaxR] = useState(""),
    summary = data.data?.summary || {};
  const rows = (data.data?.rows || []).filter(
    (row: any) => !maxR || (row.r != null && row.r <= Number(maxR)),
  );
  const plotRows = useMemo(
    () => rows.map((row: any) => ({ ...row, x: row.r, y: row.rel_change })),
    [data.data, maxR],
  );
  const navigate = (changes: Record<string, any>) => {
    const next = new URLSearchParams(params);
    Object.entries(changes).forEach(([key, value]) =>
      value == null ? next.delete(key) : next.set(key, String(value)),
    );
    setParams(next, { replace: true });
  };
  useComponentAction({
    id: "comparison.layer",
    label: "Choose comparison layer",
    kind: "select",
    state: String(layer ?? ""),
    options: layers.map((value: number) => ({
      value: String(value),
      label: `Layer ${value}`,
    })),
    disabled: !layers.length,
    run: (value) => {
      const next = Number(value);
      if (!layers.includes(next)) throw Error("Choose a saved layer");
      setPage(0);
      navigate({ layer: next, neuron: null });
    },
  });
  useComponentAction({
    id: "comparison.neuron",
    label: "Inspect a changed neuron",
    kind: "select",
    state: neuron == null ? "" : String(neuron),
    options: rows.slice(page * 40, page * 40 + 40).map((row: any) => ({
      value: String(row.neuron),
      label: `Neuron ${row.neuron}`,
    })),
    disabled: !layers.includes(layer),
    run: (value) => {
      const next = Number(value);
      if (
        value == null ||
        !Number.isInteger(next) ||
        next < 0 ||
        next >= meta.data?.neurons_per_layer
      )
        throw Error("Enter a valid neuron number");
      navigate({ neuron: next });
    },
  });
  const linked = captures.data?.captures?.find(
    (capture: any) =>
      capture.model_id === meta.data?.experiment &&
      capture.source === meta.data?.source &&
      capture.layers.includes(layer),
  );
  return (
    <Page
      className={cx(
        "interp-page",
        selected.status === "ready" && selected.data && "has-selected-neuron",
      )}
      title={
        meta.data
          ? `${meta.data.experiment} vs ${meta.data.base}`
          : "Model comparison"
      }
      description={
        meta.data
          ? `${meta.data.source} · ${meta.data.rows} datapoints · ${meta.data.prefix_len || 0} shared-prefix tokens excluded`
          : "Loading comparison…"
      }
      actions={<Link to="/interp">All comparisons</Link>}
    >
      {meta.error && <ErrorState error={meta.error} retry={meta.reload} />}
      {meta.data && (
        <AnalysisTemplate
          label="Model comparison evidence"
          className={cx("interp-analysis", neuron != null && "has-selection")}
          toolbar={
            <div className="interp-toolbar">
              <Field label="Layer">
                <select
                  value={layer}
                  onChange={(e) => {
                    setPage(0);
                    navigate({ layer: e.target.value, neuron: null });
                  }}
                >
                  {layers.map((l: number) => (
                    <option key={l} value={l}>
                      Layer {l}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Sort changed neurons">
                <select
                  value={sort}
                  onChange={(e) => {
                    setPage(0);
                    navigate({ sort: e.target.value });
                  }}
                >
                  {[
                    ["change", "Combined change"],
                    ["r", "Lowest correlation"],
                    ["rel_change", "Relative change"],
                  ].map(([key, title]) => (
                    <option key={key} value={key}>
                      {title}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Correlation at most">
                <select
                  value={maxR}
                  onChange={(e) => {
                    setMaxR(e.target.value);
                    setPage(0);
                  }}
                >
                  <option value="">All neurons</option>
                  <option value="0.9">r ≤ 0.9</option>
                  <option value="0.5">r ≤ 0.5</option>
                  <option value="0">r ≤ 0</option>
                </select>
              </Field>
            </div>
          }
          summary={
            <>
              <div className="neuron-metrics">
                <Metric
                  label="Median correlation"
                  value={number(summary.median_r, 3)}
                />
                <Metric label="r below 0.9" value={summary.below_0_9 ?? "—"} />
                <Metric label="r below 0.5" value={summary.below_0_5 ?? "—"} />
                <Metric
                  label="Constant on one side"
                  value={summary.constant ?? "—"}
                />
              </div>
              <p className="interp-reading">
                Correlation measures agreement over the same content tokens.
                Relative change is ‖after − before‖ / ‖before‖. A constant side
                has undefined correlation; those neurons remain in the list and
                are omitted from the scatterplot.
              </p>
            </>
          }
          inspector={
            <div className="interp-detail">
              {neuron != null && (
                <div className="neuron-back">
                  <Button size="sm" onClick={() => navigate({ neuron: null })}>
                    ← Back to changed neurons
                  </Button>
                </div>
              )}
              {selected.status === "loading" ? (
                <Panel>
                  <p role="status" className="interp-loading">
                    Loading neuron {neuron}…
                  </p>
                </Panel>
              ) : selected.error ? (
                <ErrorState error={selected.error} retry={selected.reload} />
              ) : selected.data ? (
                <Panel title={`Layer ${layer} · neuron ${neuron}`}>
                  <Evidence label="Selected neuron change" data={selected.data}>
                    <div className="neuron-summary">
                      <div className="neuron-metrics">
                        <Metric
                          label="Combined change"
                          value={number(selected.data.change)}
                        />
                        <Metric
                          label="Correlation r"
                          value={number(selected.data.r, 3)}
                        />
                        <Metric
                          label="Relative change"
                          value={number(selected.data.rel_change)}
                        />
                      </div>
                      <h3>Datapoints driving the change</h3>
                      {selected.data.datapoints.map((entry: any) => (
                        <Evidence
                          key={entry.datapoint}
                          className="diff-datapoint"
                          label="Changed datapoint"
                          data={entry}
                        >
                          <div className="activation-meta">
                            <span>Datapoint {entry.datapoint}</span>
                            <b>Σ change {number(entry.delta_sum)}</b>
                          </div>
                          <p>{entry.user}</p>
                          <small className="muted">
                            {entry.labels.join(", ")}
                          </small>
                        </Evidence>
                      ))}
                      <h3>Tokens with largest change</h3>
                      {selected.data.tokens.map((entry: any, index: number) => (
                        <Evidence
                          label="Changed token"
                          data={entry}
                          className="activation-card"
                          key={`${entry.datapoint}:${entry.position}:${index}`}
                        >
                          <div className="activation-meta">
                            <span>
                              Datapoint {entry.datapoint} · token{" "}
                              {entry.position}
                            </span>
                            <b>
                              {number(entry.before)} → {number(entry.after)}
                            </b>
                          </div>
                          <div className="token-context">{entry.context}</div>
                        </Evidence>
                      ))}
                      {linked ? (
                        <Link
                          to={`/interp/c/${encodeURIComponent(linked.name)}?layer=${layer}&neuron=${neuron}`}
                        >
                          Open this neuron's activation capture →
                        </Link>
                      ) : (
                        <p className="muted">
                          Capture this experiment on {meta.data.source} to
                          explore both poles.
                        </p>
                      )}
                    </div>
                  </Evidence>
                </Panel>
              ) : (
                <Panel>
                  <EmptyState>
                    Select a changed neuron from the map or list to inspect its
                    datapoints and tokens.
                  </EmptyState>
                </Panel>
              )}
            </div>
          }
        >
          <div className="interp-master">
            <Panel title="How neurons changed">
              {data.status === "loading" && (
                <p role="status" className="interp-loading">
                  Loading comparison evidence…
                </p>
              )}
              {data.error && <ErrorState error={data.error} />}
              <NeuronMap
                rows={plotRows}
                selected={neuron}
                onSelect={(n) => navigate({ neuron: n })}
                xLabel="Correlation r"
                yLabel="Relative change"
                label="Neuron change map"
              />
              <p className="interp-reading">
                {data.data?.rows.length || 0} of {meta.data.neurons_per_layer}{" "}
                neurons loaded
                {(data.data?.rows.length || 0) < meta.data.neurons_per_layer
                  ? " · leading subset, ordered by the selected metric"
                  : ""}
                .
              </p>
            </Panel>
            <Panel title="Changed neurons">
              <div className="neuron-list">
                <SimpleTable<any>
                  label="Changed neurons"
                  rows={rows.slice(page * 40, page * 40 + 40)}
                  getRowId={(row) => String(row.neuron)}
                  scopeKey={`${comparison}:${layer}`}
                  empty=""
                  rowProps={(row) => ({
                    className: neuron === row.neuron ? "selected" : "",
                  })}
                  renderRow={(row, { key, ...props }, cells) => (
                    <Evidence
                      {...props}
                      as="tr"
                      label="Neuron change"
                      data={row}
                      key={key}
                    >
                      {cells}
                    </Evidence>
                  )}
                  columns={[
                    {
                      id: "neuron",
                      label: "Neuron",
                      value: (row) => row.neuron,
                      render: (row) => (
                        <button
                          aria-label={`Explore changed neuron ${row.neuron}`}
                          onClick={() => navigate({ neuron: row.neuron })}
                        >
                          {row.neuron}
                        </button>
                      ),
                    },
                    {
                      id: "change",
                      label: "Change",
                      value: (row) => row.change,
                      render: (row) => number(row.change),
                    },
                    {
                      id: "r",
                      label: "r",
                      value: (row) => row.r,
                      render: (row) => number(row.r, 3),
                    },
                    {
                      id: "relative",
                      label: "Relative Δ",
                      value: (row) => row.rel_change,
                      render: (row) => number(row.rel_change),
                    },
                  ]}
                />
              </div>
              <div className="evidence-pagination">
                <Button
                  size="sm"
                  disabled={!page}
                  onClick={() => setPage((p) => p - 1)}
                >
                  Previous neurons
                </Button>
                <small>{rows.length} matching neurons</small>
                <Button
                  size="sm"
                  disabled={(page + 1) * 40 >= rows.length}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next neurons
                </Button>
              </div>
            </Panel>
          </div>
        </AnalysisTemplate>
      )}
    </Page>
  );
}
