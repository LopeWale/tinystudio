import { SimpleTable, DataTable } from "../../components/studio/data-table";
import {
  CollectionTemplate,
  RunTemplate,
  SetupTemplate,
} from "../../components/studio/templates";
import { useAnalyticalResource } from "./analytical-resource";
import { TrainingChart } from "./training-chart";
import "./analytical-workspace.css";
import { useComponentAction } from "../assistant/actions";
import { Activity, useState, useEffect, useRef, type ReactNode } from "react";
import { SegmentedControl } from "../../components/affectui/controls";
import { ExperimentFields } from "./experiment-fields";
import { validateExperimentDraft } from "./experiment-validation";
import "./training-job.css";
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
import { EvaluationDialog } from "./evaluations";
import {
  enc,
  useAction,
  ErrorNotice,
  Input,
  Text,
  Select,
  Chips,
  Check,
  track,
  scopedDownload,
} from "./common";
export function defaultExperiment(base_model = "") {
  return {
    base_model,
    training_type: "lora",
    epochs: 3,
    learning_rate: 0.0002,
    batch_size: 8,
    gradient_accumulation_steps: 2,
    max_length: 512,
    lora_r: 16,
    lora_alpha: 32,
    assistant_only_loss: false,
    refinetune_from: null,
    checkpoint_every_steps: 0,
    keep_checkpoints: 0,
  };
}
type JobDialogProps = {
  onClose: () => void;
  snapshot?: string;
};
export function JobDialog(props: JobDialogProps) {
  const { scopeKey } = useStudio();
  // Retire the form and pending action state when account/project identity changes.
  return <JobDialogForm key={scopeKey} {...props} />;
}
function JobDialogForm({ onClose, snapshot = "" }: JobDialogProps) {
  const s = useStudio(),
    a = useAction(),
    resource = useResource<any[]>("/api/snapshots"),
    snaps = resource.data || [];
  const [name, N] = useState(""),
    [selection, setSelection] = useState<string | undefined>(
      snapshot || undefined,
    ),
    [reasoning, R] = useState(false);
  const requested = selection ?? snaps[0]?.name ?? "",
    snap = snaps.some((item) => item.name === requested) ? requested : "",
    ready = !resource.loading && !resource.error && !!snap;
  return (
    <Modal
      open
      title="New finetuning job"
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          loading={a.busy}
          disabled={!ready || !name.trim()}
          onClick={() =>
            a.run(async () => {
              if (!name.trim()) throw Error("Give the job a name");
              if (!ready) throw Error("Choose an available snapshot first");
              const job = await s.api.post<any>("/api/jobs", {
                name: name.trim(),
                snapshot: snap,
                reasoning,
              });
              onClose();
              s.navigate(`#/finetuning/${enc(job.name)}`);
            })
          }
        >
          Create job
        </Button>
      }
    >
      <SetupTemplate
        className="data-template data-setup"
        label="Training job setup"
      >
        <ErrorNotice error={a.error || resource.error} />
        {resource.loading && <p role="status">Loading snapshots…</p>}
        {resource.error && (
          <Button onClick={resource.reload}>Retry snapshots</Button>
        )}
        {!resource.loading && !resource.error && !snaps.length && (
          <p>Create a snapshot before creating a training job.</p>
        )}
        <Input label="Name" value={name} onChange={N} />
        <Select
          label="Snapshot"
          value={snap}
          disabled={resource.loading || !!resource.error || !snaps.length}
          onChange={setSelection}
          options={[
            { value: "", label: "Choose a snapshot" },
            ...snaps.map((x) => ({
              value: x.name,
              label: `${x.name} — ${x.training_count}/${x.validation_count}`,
            })),
          ]}
        />
        <Check
          label="Reasoning model (requires thinking support and longer sequences)"
          checked={reasoning}
          onChange={R}
        />
        <p>
          Non reasoning models give direct answers for formats, extraction,
          classification and domain Q&A.
        </p>
      </SetupTemplate>
    </Modal>
  );
}
function AutopilotDialog({
  preset,
  onClose,
}: {
  preset: any;
  onClose: () => void;
}) {
  const s = useStudio(),
    a = useAction();
  const ds = (s.overview?.tree?.datasets || []).filter((x: any) => !x.missing),
    metrics = useResource<any[]>("/api/metrics");
  const [v, V] = useState<any>({
      name: preset.name || "",
      dataset: preset.dataset || ds[0]?.name || "",
      base_model: s.overview?.catalog?.[0]?.id || "",
      training_type: "lora",
      epochs: 2,
      min_trace_score: "",
      preference: false,
      clean: true,
      eval_limit: "",
    }),
    [chosen, C] = useState<string[]>([]);
  const field = (k: string) => (x: any) =>
    V((p: any) => ({
      ...p,
      [k]: x,
      ...(k === "training_type" && x === "dpo" ? { preference: true } : {}),
    }));
  return (
    <Modal
      open
      wide
      title={
        preset.name
          ? `Autopilot · next cycle of ${preset.name}`
          : "Autopilot cycle"
      }
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          loading={a.busy}
          onClick={() =>
            a.run(async () => {
              if (!v.name.trim()) throw Error("Give the autopilot a name");
              if (!v.dataset) throw Error("Create a dataset first");
              track(
                s,
                await s.api.post("/api/autopilot", {
                  ...v,
                  name: v.name.trim(),
                  base_model: v.base_model.trim() || null,
                  epochs: Number(v.epochs),
                  min_trace_score:
                    v.min_trace_score === "" ? null : Number(v.min_trace_score),
                  eval_limit: v.eval_limit === "" ? null : Number(v.eval_limit),
                  metrics: chosen,
                }),
              );
              onClose();
            })
          }
        >
          Run cycle
        </Button>
      }
    >
      <SetupTemplate
        className="data-template data-setup"
        label="Autopilot setup"
      >
        <ErrorNotice error={a.error || metrics.error} />
        <p>
          Promote traces → clean → split → snapshot → refine the previous winner
          → evaluate → record winner.
        </p>
        <div className="data-grid">
          <Input
            label="Autopilot name"
            value={v.name}
            readOnly={!!preset.name}
            onChange={field("name")}
          />
          <Select
            label="Dataset"
            value={v.dataset}
            onChange={field("dataset")}
            options={ds.map((x: any) => x.name)}
          />
          <Input
            label="Base model (first cycle only)"
            value={v.base_model}
            onChange={field("base_model")}
          />
          <Select
            label="Training type"
            value={v.training_type}
            onChange={field("training_type")}
            options={["lora", "full", "dpo"]}
          />
          <Input
            label="Epochs"
            type="number"
            min={0.5}
            step={0.5}
            value={v.epochs}
            onChange={field("epochs")}
          />
          <Input
            label="Promote traces scored ≥ (blank = none)"
            type="number"
            min={0}
            max={1}
            step={0.1}
            value={v.min_trace_score}
            onChange={field("min_trace_score")}
          />
          <Input
            label="Evaluation row limit (blank = all)"
            type="number"
            min={1}
            value={v.eval_limit}
            onChange={field("eval_limit")}
          />
        </div>
        <p>Metrics: first selected decides the winner.</p>
        <Chips
          items={(metrics.data || []).map((x) => x.name)}
          chosen={chosen}
          onChange={C}
        />
        <Check
          label="Promote corrected traces as DPO preference pairs"
          checked={v.preference}
          onChange={field("preference")}
        />
        <Check
          label="Clean first (dedupe, drop empty, trim whitespace)"
          checked={v.clean}
          onChange={field("clean")}
        />
      </SetupTemplate>
    </Modal>
  );
}
function jobState(experiments: any[]) {
  if (experiments.some((e) => e.state === "running")) return "Running";
  if (!experiments.length || experiments.every((e) => e.state === "pending"))
    return "Ready to start";
  if (experiments.every((e) => e.state === "succeeded")) return "Completed";
  if (experiments.some((e) => e.state === "failed")) return "Failed";
  return "Partially run";
}
function recordedLoss(experiment: any, kind: "loss" | "eval_loss") {
  const direct =
    kind === "loss" ? experiment.train_loss : experiment.metrics?.eval_loss;
  if (typeof direct === "number" && Number.isFinite(direct)) return direct;
  const point = (experiment.loss || [])
    .filter((p: any) => typeof p[kind] === "number" && Number.isFinite(p[kind]))
    .at(-1);
  return point?.[kind] ?? null;
}
function TrainingInspector({
  label,
  title,
  closeLabel,
  onClose,
  children,
}: {
  label: string;
  title: ReactNode;
  closeLabel: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null),
    [depth, setDepth] = useState(0);
  useEffect(() => {
    if (window.matchMedia?.("(max-width: 760px)").matches) ref.current?.focus();
  }, []);
  return (
    <aside
      ref={ref}
      tabIndex={-1}
      className="training-directory-inspector"
      aria-label={label}
    >
      <header className="training-inspector-heading">
        <div>{title}</div>
        <Button size="sm" variant="ghost" onClick={onClose}>
          {closeLabel}
        </Button>
      </header>
      <div className="training-inspector-scroll-wrap">
        <div className="training-scroll-fade" aria-hidden="true">
          <span style={{ opacity: depth }} />
          <span style={{ opacity: depth }} />
          <span style={{ opacity: depth }} />
        </div>
        <div
          className="training-inspector-scroll"
          onScroll={(event) =>
            setDepth(Math.min(1, event.currentTarget.scrollTop / 24))
          }
        >
          {children}
        </div>
      </div>
    </aside>
  );
}
export function FinetuningPage() {
  const r = useResource<any[]>("/api/jobs"),
    pilots = useResource<any[]>("/api/autopilot");
  const s = useStudio(),
    a = useAction();
  const [open, O] = useState(""),
    [preset, P] = useState<any>({}),
    [history, H] = useState<any[] | null>(null),
    [query, setQuery] = useState(""),
    [state, setState] = useState(""),
    [inspected, inspect] = useState<string | null>(null),
    [checked, selectJobs] = useState<string[]>([]),
    [comparison, compare] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null),
    searchInput = useRef<HTMLInputElement | null>(null);
  const inspectedResource = useResource<any>(
    inspected ? `/api/jobs/${enc(inspected)}` : null,
  );
  const selectedJob = (r.data || []).find(
    (item) => item.job.name === inspected,
  );
  const selectedData = inspectedResource.data || selectedJob;
  const comparedJobs = (r.data || []).filter((item) =>
    checked.includes(item.job.name),
  );
  const matching = (r.data || []).filter(
    ({ job, experiments }) =>
      job.name.toLowerCase().includes(query.trim().toLowerCase()) &&
      (!state || experiments.some((e: any) => e.state === state)),
  );
  const closeInspector = () => {
    inspect(null);
    compare(false);
    requestAnimationFrame(() => {
      if (returnFocus.current?.isConnected) returnFocus.current.focus();
      else searchInput.current?.focus();
    });
  };
  const inspectJob = (name: string, trigger: HTMLElement) => {
    returnFocus.current = trigger;
    inspect(name);
    compare(false);
  };
  useEffect(() => {
    inspect(null);
    selectJobs([]);
    compare(false);
  }, [s.scopeKey]);
  useEffect(() => {
    if ((!inspected && !comparison) || open || history) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeInspector();
      }
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [inspected, comparison, open, history]);

  return (
    <Page
      className="data-workspace analytical-workspace collection-page analytical-collection-page training-collection-page"
      title="Finetuning"
      description="Turn snapshots into experiments and compare what improves."
      actions={
        <div className="data-actions">
          <Button
            onClick={() => {
              a.clearError();
              P({});
              O("autopilot");
            }}
          >
            Autopilot cycle
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              a.clearError();
              O("job");
            }}
          >
            Create finetuning job
          </Button>
        </div>
      }
    >
      <CollectionTemplate
        className="data-template"
        label="Training job collection workspace"
        summary={
          <div className="analytical-scope">
            <strong>{r.data?.length ?? "—"} jobs</strong>
            <span>
              {(r.data || []).reduce((n, j) => n + j.experiments.length, 0)}{" "}
              experiments
            </span>
            <span>Inspect a row or select jobs to compare.</span>
            <a href="#/evaluations">Review evaluations →</a>
          </div>
        }
      >
        <ErrorNotice error={r.error || pilots.error || a.error} />
        {!!pilots.data?.length && (
          <details className="analytical-autopilot">
            <summary>Autopilot ({pilots.data.length})</summary>
            <div className="analytical-autopilot-table">
              <SimpleTable
                scopeKey={s.scopeKey + ":" + s.route}
                label="Autopilot"
                layout="fill"
                rows={pilots.data}
                getRowId={(p: any) => String(p.name)}
                pagination="none"
                columns={[
                  {
                    id: "col-0",
                    label: "Name",
                    value: (p: any) => p.name,
                    render: (p: any) => <>{p.name}</>,
                  },
                  {
                    id: "col-1",
                    label: "Dataset",
                    value: (p: any) => p.dataset,
                    render: (p: any) => <>{p.dataset}</>,
                  },
                  {
                    id: "col-2",
                    label: "Cycles",
                    value: (p: any) => p.cycles,
                    render: (p: any) => <>{p.cycles}</>,
                  },
                  {
                    id: "col-3",
                    label: "Winner",
                    value: (p: any) => p.winner,
                    render: (p: any) => <>{p.winner || "—"}</>,
                  },
                  {
                    id: "col-4",
                    label: "Actions",
                    value: (p: any) => p.name,
                    sortable: false,
                    filterable: false,
                    hideable: false,
                    render: (p: any) => (
                      <>
                        <Button
                          onClick={() =>
                            a.run(async () => {
                              H(
                                await s.api.get<any[]>(
                                  `/api/autopilot/${enc(p.name)}`,
                                ),
                              );
                              P(p);
                            })
                          }
                        >
                          History
                        </Button>
                        <Button
                          onClick={() => {
                            a.clearError();
                            P(p);
                            O("autopilot");
                          }}
                        >
                          Next cycle
                        </Button>
                      </>
                    ),
                  },
                ]}
                renderRow={(p: any, { key, ...props }, cells) => (
                  <Evidence
                    as="tr"
                    {...props}
                    key={key}
                    label="Autopilot"
                    data={p}
                  >
                    {cells}
                  </Evidence>
                )}
              />
            </div>
          </details>
        )}
        <div
          className={`training-directory ${inspected || comparison ? "has-inspector" : ""}`}
        >
          <div className="training-directory-master">
            <DataTable
              scopeKey={s.scopeKey + ":" + s.route}
              label="Finetuning jobs"
              layout="fill"
              toolbarContent={
                <div className="analytical-toolbar">
                  <Input
                    ref={searchInput}
                    label="Find a training job"
                    type="search"
                    value={query}
                    onChange={setQuery}
                  />
                  <Select
                    label="Run state"
                    value={state}
                    onChange={setState}
                    options={[
                      { value: "", label: "All jobs" },
                      { value: "running", label: "Running" },
                      { value: "failed", label: "Has failures" },
                      { value: "succeeded", label: "Has completed runs" },
                    ]}
                  />
                  <Button
                    size="sm"
                    disabled={comparedJobs.length < 2}
                    onClick={(event) => {
                      returnFocus.current = event.currentTarget;
                      compare(true);
                    }}
                  >
                    Compare selected ({comparedJobs.length})
                  </Button>
                  {!!checked.length && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        selectJobs([]);
                        compare(false);
                      }}
                    >
                      Clear selection
                    </Button>
                  )}
                </div>
              }
              empty={
                r.loading ? (
                  "Loading jobs…"
                ) : r.data?.length ? (
                  "No jobs match these filters."
                ) : (
                  <>
                    No finetuning jobs yet.{" "}
                    <a href="#/datasets">Create a dataset snapshot.</a>
                  </>
                )
              }
              rows={matching}
              getRowId={(item: any) => item.job.name}
              columns={[
                {
                  id: "Select",
                  label: "Select",
                  value: ({ job, experiments }: any) =>
                    checked.includes(job.name),
                  sortable: false,
                  filterable: false,
                  hideable: false,
                  render: ({ job, experiments }: any) => (
                    <>
                      <Check
                        label={`Select ${job.name} for comparison`}
                        checked={checked.includes(job.name)}
                        onChange={(on: boolean) =>
                          selectJobs(
                            on
                              ? [...checked, job.name]
                              : checked.filter((name) => name !== job.name),
                          )
                        }
                      />
                    </>
                  ),
                },
                {
                  id: "Job",
                  label: "Job",
                  value: ({ job, experiments }: any) => job.name,
                  render: ({ job, experiments }: any) => (
                    <>
                      <a
                        className="data-record-link"
                        href={`#/finetuning/${enc(job.name)}`}
                      >
                        {job.name}
                      </a>
                    </>
                  ),
                },
                {
                  id: "Snapshot",
                  label: "Snapshot",
                  value: ({ job, experiments }: any) => job.snapshot,
                  render: ({ job, experiments }: any) => <>{job.snapshot}</>,
                },
                {
                  id: "Experiments",
                  label: "Experiments",
                  value: ({ job, experiments }: any) => experiments.length,
                  render: ({ job, experiments }: any) => (
                    <>{experiments.length}</>
                  ),
                },
                {
                  id: "Reasoning",
                  label: "Reasoning",
                  value: ({ job, experiments }: any) => !!job.reasoning,
                  render: ({ job, experiments }: any) => (
                    <>{job.reasoning ? "Enabled" : "—"}</>
                  ),
                },
                {
                  id: "State",
                  label: "State",
                  value: ({ job, experiments }: any) => jobState(experiments),
                  render: ({ job, experiments }: any) => (
                    <>
                      <Badge>{jobState(experiments)}</Badge>
                    </>
                  ),
                },
                {
                  id: "Inspect",
                  label: "Inspect",
                  value: ({ job, experiments }: any) => job.name,
                  sortable: false,
                  filterable: false,
                  hideable: false,
                  render: ({ job, experiments }: any) => (
                    <>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`Inspect ${job.name}`}
                        onClick={(event) =>
                          inspectJob(job.name, event.currentTarget)
                        }
                      >
                        Inspect
                      </Button>
                    </>
                  ),
                },
              ]}
              rowProps={({ job, experiments }: any) => ({
                tabIndex: 0,
                "aria-label": `Training job ${job.name}`,
                "aria-selected": inspected === job.name,
                className: "training-inspectable-row",
                onClick: (event: any) => {
                  if (
                    (event.target as Element).closest(
                      "a,button,input,select,label",
                    )
                  )
                    return;
                  inspectJob(job.name, event.currentTarget);
                },
                onKeyDown: (event: any) => {
                  if (event.target !== event.currentTarget) return;
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    inspectJob(job.name, event.currentTarget);
                  }
                },
              })}
              renderRow={(item: any, { key, ...props }, cells) => (
                <Evidence
                  as="tr"
                  {...props}
                  key={key}
                  label="Finetuning job"
                  data={item}
                >
                  {cells}
                </Evidence>
              )}
            />
          </div>
          {comparison ? (
            <TrainingInspector
              key="comparison"
              label="Compare selected jobs"
              title={<h2>Compare {comparedJobs.length} jobs</h2>}
              closeLabel="Close comparison"
              onClose={closeInspector}
            >
              <p className="data-muted">
                Aligned recorded settings and losses.{" "}
                {new Set(comparedJobs.map((item) => item.job.snapshot)).size > 1
                  ? "Snapshots differ; loss values are not directly comparable across datasets."
                  : "Jobs share a snapshot; compare matching training objectives before interpreting loss."}
              </p>
              <Evidence label="Selected job comparison" data={comparedJobs}>
                <SimpleTable
                  label="Selected job comparison"
                  scopeKey={s.scopeKey}
                  getRowId={(field: any) => field.label}
                  rows={[
                    {
                      label: "Snapshot",
                      value: (item: any) => item.job.snapshot,
                    },
                    {
                      label: "State",
                      value: (item: any) => jobState(item.experiments),
                    },
                    {
                      label: "Experiments",
                      value: (item: any) => item.experiments.length,
                    },
                    {
                      label: "Base models",
                      value: (item: any) =>
                        [
                          ...new Set(
                            item.experiments.map(
                              (e: any) => e.config?.base_model,
                            ),
                          ),
                        ]
                          .filter(Boolean)
                          .join(", ") || "—",
                    },
                    {
                      label: "Training types",
                      value: (item: any) =>
                        [
                          ...new Set(
                            item.experiments.map(
                              (e: any) => e.config?.training_type,
                            ),
                          ),
                        ]
                          .filter(Boolean)
                          .join(", ") || "—",
                    },
                    {
                      label: "Recorded evaluation loss",
                      value: (item: any) =>
                        item.experiments
                          .map(
                            (e: any) =>
                              `Experiment ${e.number}: ${recordedLoss(e, "eval_loss") ?? "Not recorded"}`,
                          )
                          .join("; ") || "No experiments",
                    },
                    { label: "Open", value: (item: any) => item.job.name },
                  ]}
                  columns={[
                    {
                      id: "field",
                      label: "Field",
                      value: (field: any) => field.label,
                    },
                    ...comparedJobs.map((item) => ({
                      id: item.job.name,
                      label: item.job.name,
                      value: (field: any) => field.value(item),
                      render: (field: any) =>
                        field.label === "Open" ? (
                          <a href={`#/finetuning/${enc(item.job.name)}`}>
                            Open {item.job.name}
                          </a>
                        ) : field.label === "Recorded evaluation loss" ? (
                          item.experiments.length ? (
                            item.experiments.map((experiment: any) => (
                              <div key={experiment.number}>
                                Experiment {experiment.number}:{" "}
                                {recordedLoss(experiment, "eval_loss") ??
                                  "Not recorded"}
                              </div>
                            ))
                          ) : (
                            "No experiments"
                          )
                        ) : (
                          field.value(item)
                        ),
                    })),
                  ]}
                />
              </Evidence>
            </TrainingInspector>
          ) : selectedData && inspected ? (
            <TrainingInspector
              key={inspected}
              label={`Job inspector ${inspected}`}
              title={
                <>
                  <h2>{selectedData.job.name}</h2>
                  <Badge>{jobState(selectedData.experiments)}</Badge>
                </>
              }
              closeLabel="Close inspector"
              onClose={closeInspector}
            >
              <p className="data-muted">Snapshot {selectedData.job.snapshot}</p>
              <p>{selectedData.job.goal || "No job goal recorded."}</p>
              <a
                className="training-open-workspace"
                href={`#/finetuning/${enc(inspected)}`}
              >
                Open run workspace →
              </a>
              <ErrorNotice error={inspectedResource.error} />
              {inspectedResource.loading && (
                <p role="status">Loading recorded experiment details…</p>
              )}
              {!selectedData.experiments.length && (
                <EmptyState>
                  No experiments configured yet. Open the run workspace to add
                  one.
                </EmptyState>
              )}
              {selectedData.experiments.map((experiment: any) => (
                <Evidence
                  key={experiment.number}
                  label="Inspected experiment"
                  data={experiment}
                >
                  <details className="training-experiment-disclosure">
                    <summary>
                      Experiment {experiment.number} · {experiment.state}
                    </summary>
                    <dl>
                      <dt>Base model</dt>
                      <dd>{experiment.config?.base_model || "—"}</dd>
                      <dt>Training</dt>
                      <dd>{experiment.config?.training_type || "—"}</dd>
                      <dt>Epochs</dt>
                      <dd>{experiment.config?.epochs ?? "—"}</dd>
                      <dt>Latest training loss</dt>
                      <dd>
                        {recordedLoss(experiment, "loss") ?? "Not recorded"}
                      </dd>
                      <dt>Latest evaluation loss</dt>
                      <dd>
                        {recordedLoss(experiment, "eval_loss") ??
                          "Not recorded"}
                      </dd>
                      <dt>Saved checkpoints</dt>
                      <dd>{experiment.checkpoints?.length ?? 0}</dd>
                    </dl>
                    {experiment.error && (
                      <ErrorNotice error={experiment.error} />
                    )}
                    <h3>Configuration</h3>
                    <pre className="data-prose">
                      {JSON.stringify(experiment.config || {}, null, 2)}
                    </pre>
                    <a href={`#/finetuning/${enc(inspected)}`}>
                      Open this job workspace →
                    </a>
                  </details>
                </Evidence>
              ))}
            </TrainingInspector>
          ) : null}
        </div>
        {open === "job" && <JobDialog onClose={() => O("")} />}{" "}
        {open === "autopilot" && (
          <AutopilotDialog
            preset={preset}
            onClose={() => {
              O("");
              pilots.reload();
            }}
          />
        )}
        {history && (
          <Modal
            open
            wide
            title={`Autopilot · ${preset.name}`}
            onClose={() => H(null)}
          >
            <DataTable
              scopeKey={s.scopeKey + ":" + s.route}
              label="Autopilot history"
              rows={history}
              getRowId={(x: any) => String(x.cycle)}
              pagination="client"
              columns={[
                {
                  id: "col-0",
                  label: "Cycle",
                  value: (x: any) => x.cycle,
                  render: (x: any) => <>{x.cycle}</>,
                },
                {
                  id: "col-1",
                  label: "Snapshot",
                  value: (x: any) => x.snapshot?.name,
                  render: (x: any) => (
                    <>
                      {x.snapshot?.name}
                      <p>
                        {x.snapshot?.training} / {x.snapshot?.validation}
                      </p>
                    </>
                  ),
                },
                {
                  id: "col-2",
                  label: "Experiment",
                  value: (x: any) => x.experiment?.model,
                  render: (x: any) => (
                    <>
                      {x.experiment?.model} · {x.experiment?.state}
                      <p>
                        From {x.parent || "base"} · +{x.promoted_traces || 0}{" "}
                        traces
                      </p>
                    </>
                  ),
                },
                {
                  id: "col-3",
                  label: "Eval loss",
                  value: (x: any) => x.experiment?.eval_loss,
                  render: (x: any) => <>{x.experiment?.eval_loss ?? "—"}</>,
                },
                {
                  id: "col-4",
                  label: "Evaluation",
                  value: (x: any) => x.evaluation?.primary_metric,
                  render: (x: any) => (
                    <>
                      {x.evaluation
                        ? `${x.evaluation.primary_metric}: ${x.evaluation.candidate} vs ${x.evaluation.baseline}`
                        : "No metrics"}
                    </>
                  ),
                },
                {
                  id: "col-5",
                  label: "Winner",
                  value: (x: any) => x.winner,
                  render: (x: any) => (
                    <>
                      {x.winner}
                      <p>{x.decision || x.experiment?.error}</p>
                    </>
                  ),
                },
              ]}
              renderRow={(x: any, { key, ...props }, cells) => (
                <Evidence
                  as="tr"
                  {...props}
                  key={key}
                  label="Autopilot cycle"
                  data={x}
                >
                  {cells}
                </Evidence>
              )}
            />
          </Modal>
        )}
      </CollectionTemplate>
    </Page>
  );
}
function TrainingPaneScroll({ children }: { children: ReactNode }) {
  const [depth, setDepth] = useState(0);
  return (
    <div className="training-job-scroll">
      <div className="training-scroll-fade" aria-hidden="true">
        <span style={{ opacity: depth }} />
        <span style={{ opacity: depth }} />
        <span style={{ opacity: depth }} />
      </div>
      <div
        className="training-job-scroll-body"
        onScroll={(event) =>
          setDepth(Math.min(1, event.currentTarget.scrollTop / 24))
        }
      >
        {children}
      </div>
    </div>
  );
}
function ExperimentSelectionAction({
  experiments,
  selected,
  onSelect,
}: {
  experiments: any[];
  selected: number | null;
  onSelect: (number: number) => void;
}) {
  useComponentAction({
    id: "job.experiment",
    label: "Inspect an experiment",
    kind: "select",
    state: selected == null ? "" : String(selected),
    options: experiments.map((item) => ({
      value: String(item.number),
      label: `Experiment ${item.number}`,
    })),
    disabled: !experiments.length,
    run: (value) => {
      const item = experiments.find((e) => String(e.number) === value);
      if (!item) throw Error("Choose an available experiment");
      onSelect(item.number);
    },
  });
  return null;
}
export function JobPage() {
  const params = useParams(),
    s = useStudio();
  const name = params.job || params.name || "";
  return <TrainingJobPage key={`${s.scopeKey}:${name}`} name={name} />;
}
function TrainingJobPage({ name }: { name: string }) {
  const s = useStudio(),
    a = useAction(),
    r = useAnalyticalResource<any>(`/api/jobs/${enc(name)}`),
    models = useResource<any[]>("/api/models"),
    hub = useResource<any>("/api/hub/models");
  const active = useRef(true);
  const dialogGeneration = useRef(0);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const [pane, setPane] = useState("experiments"),
    [setup, setSetup] = useState(false),
    [admitted, setAdmitted] = useState(false),
    [review, setReview] = useState(false),
    [draftIndex, setDraftIndex] = useState(0),
    [validation, setValidation] = useState(""),
    [drafts, D] = useState<any[]>([]),
    [evaluate, E] = useState(false),
    [capture, C] = useState<any>(null),
    [captureDs, CD] = useState(""),
    [layers, L] = useState(""),
    [experimentNumber, chooseExperiment] = useState<number | null>(null);
  useEffect(() => {
    D([]);
    E(false);
    C(null);
    CD("");
    L("");
    chooseExperiment(null);
  }, [name, s.project, s.user?.id]);
  const running = r.data?.experiments?.some((e: any) => e.state === "running");
  const pendingTask = r.data?.experiments?.some(
    (e: any) => e.state === "pending" && e.task_id,
  );
  useEffect(() => {
    if (
      r.data?.experiments?.length &&
      !r.data.experiments.some(
        (e: any) => e.state === "pending" || e.state === "running",
      )
    )
      setAdmitted(false);
  }, [r.data]);
  useEffect(() => {
    if (!running && !pendingTask && !admitted) return;
    const timer = setInterval(r.reload, 4000);
    return () => clearInterval(timer);
  }, [running, pendingTask, admitted, r.reload]);
  const detail = r.data;
  if (!detail)
    return (
      <Page title={name}>
        <ErrorNotice error={r.error} />
        <p role="status">
          {r.error ? "Job evidence could not be loaded." : "Loading job…"}
        </p>
        {r.error && <Button onClick={r.reload}>Retry loading job</Button>}
      </Page>
    );
  const { job, experiments, analysis } = detail;
  const inspectedExperiment =
    experiments.find((item: any) => item.number === experimentNumber) ||
    experiments[0];
  const configuring =
    !experiments.length || experiments.every((x: any) => x.state === "pending");
  const catalog = [
    ...(s.overview?.catalog || []),
    ...(hub.data?.models || []).filter((x: any) => x.transformers_loadable),
  ];
  const draftLocked = a.busy || running || pendingTask || admitted;
  const add = (option?: any) => {
    if (draftLocked) return;
    D((current) => [
      ...current,
      option
        ? {
            ...defaultExperiment(option.base_model),
            training_type: option.training_type,
            ...option.hyperparameters,
          }
        : defaultExperiment(catalog[0]?.id || ""),
    ]);
    setDraftIndex(drafts.length);
    setReview(false);
    setValidation("");
    setSetup(true);
  };
  const validate = () => {
    const invalid = drafts.map(validateExperimentDraft).findIndex(Boolean);
    if (invalid !== -1) {
      setValidation(
        `Draft experiment ${invalid + 1}: ${validateExperimentDraft(drafts[invalid])}`,
      );
      setDraftIndex(invalid);
      setReview(false);
      return false;
    }
    setValidation("");
    return true;
  };
  const submit = (launch: boolean) => {
    const ownDialog = dialogGeneration.current;
    return a.run(async () => {
      if (running || pendingTask || admitted)
        throw Error("A training task is already submitted for this job");
      if (!validate()) return;
      if (drafts.length) {
        await s.api.post(`/api/jobs/${enc(name)}/experiments`, {
          experiments: drafts,
        });
        if (!active.current) return;
        D([]);
        r.reload();
      }
      if (launch) {
        const task = await s.api.post(`/api/jobs/${enc(name)}/start`, {});
        if (!active.current) return;
        setAdmitted(true);
        track(s, task);
        s.toast("Training task submitted");
        r.reload();
      } else s.toast("Experiments saved as pending");
      if (ownDialog === dialogGeneration.current) {
        setSetup(false);
        setReview(false);
      }
    });
  };
  return (
    <Page
      className="data-workspace analytical-workspace collection-page training-job"
      title={name}
      description={`Snapshot ${job.snapshot} · ${detail.snapshot?.training_count ?? "?"} training / ${detail.snapshot?.validation_count ?? "?"} validation`}
      actions={
        <div className="data-actions">
          {!configuring && !running && (
            <Button
              onClick={() => {
                a.clearError();
                E(true);
              }}
            >
              Start evaluation
            </Button>
          )}
          <Button
            variant="danger"
            onClick={() =>
              a.run(async () => {
                if (
                  await s.confirm(
                    `Delete job "${name}" and all its experiments and weights?`,
                  )
                ) {
                  if (!active.current) return;
                  await s.api.del(`/api/jobs/${enc(name)}`);
                  if (!active.current) return;
                  s.navigate("#/finetuning");
                }
              })
            }
          >
            Delete job
          </Button>
        </div>
      }
    >
      <RunTemplate
        className="data-template"
        label="Training run workspace"
        summary={
          <div className="analytical-scope">
            <span>
              {r.stale
                ? r.error
                  ? "Refresh failed · showing last received evidence"
                  : "Refreshing · showing last received evidence"
                : running
                  ? "Running · refresh every 4 seconds"
                  : "Recorded training evidence"}
            </span>
            {r.updated && <span>Received {r.updated}</span>}
            <Button size="sm" onClick={r.reload}>
              Refresh evidence
            </Button>
          </div>
        }
      >
        <ErrorNotice error={r.error || (!setup && a.error)} />
        <div className="training-job-identity">
          <div>
            <Badge>
              {running
                ? "Running"
                : pendingTask || admitted
                  ? "Task submitted"
                  : !experiments.length
                    ? "Ready to configure"
                    : configuring
                      ? "Pending experiments"
                      : experiments.every((e: any) => e.state === "succeeded")
                        ? "Completed"
                        : "Partially run"}
            </Badge>
            <a href={`#/datasets/${enc(detail.snapshot?.dataset || "")}`}>
              Dataset {detail.snapshot?.dataset || "missing snapshot"}
            </a>
            <span>{experiments.length} saved experiments</span>
          </div>
          {!running && !pendingTask && !admitted && (
            <div className="data-actions">
              <Button
                onClick={() => {
                  setSetup(true);
                  setReview(false);
                }}
              >
                Configure experiments
                {drafts.length ? ` · ${drafts.length} drafts` : ""}
              </Button>
              {experiments.some(
                (e: any) => e.state === "pending" && !e.task_id,
              ) && (
                <Button
                  variant="primary"
                  loading={a.busy}
                  onClick={() => {
                    setSetup(true);
                    setReview(true);
                  }}
                >
                  Review & start
                </Button>
              )}
            </div>
          )}
        </div>
        <SegmentedControl
          kind="tabs"
          variant="underline"
          aria-label="Training job tasks"
          value={pane}
          onChange={setPane}
          options={[
            {
              value: "experiments",
              label: "Experiments",
              controls: "training-experiments",
            },
            {
              value: "curves",
              label: "Training curves",
              controls: "training-curves",
            },
            {
              value: "analysis",
              label: "Dataset analysis",
              controls: "training-analysis",
            },
          ]}
        />
        <Activity mode={pane === "curves" ? "visible" : "hidden"}>
          <section
            id="training-curves"
            role="tabpanel"
            aria-label="Training curves"
          >
            <TrainingPaneScroll>
              <TrainingChart
                experiments={experiments}
                onInspect={(number) => {
                  chooseExperiment(number);
                  setPane("experiments");
                }}
              />
            </TrainingPaneScroll>
          </section>
        </Activity>
        <Activity mode={pane === "experiments" ? "visible" : "hidden"}>
          <section
            id="training-experiments"
            role="tabpanel"
            aria-label="Experiments"
          >
            <ExperimentSelectionAction
              experiments={experiments}
              selected={experimentNumber}
              onSelect={chooseExperiment}
            />
            {!experiments.length && (
              <EmptyState>
                <h3>No saved experiments</h3>
                <p>
                  Configure a draft, review its settings, then save or start
                  training.
                </p>
                <Button disabled={draftLocked} onClick={() => add()}>
                  Add experiment
                </Button>
              </EmptyState>
            )}
            <div
              className={`data-master-detail data-job-workspace ${experimentNumber != null ? "has-selection" : ""}`}
            >
              <div className="data-master">
                <DataTable
                  layout="fill"
                  scopeKey={s.scopeKey + ":" + s.route}
                  label="Experiments"
                  rows={experiments}
                  getRowId={(item: any) => String(item.number)}
                  pagination="client"
                  columns={[
                    {
                      id: "col-0",
                      label: "Experiment",
                      value: (item: any) => item.number,
                      render: (item: any) => (
                        <>
                          <Button
                            variant="ghost"
                            aria-pressed={
                              inspectedExperiment?.number === item.number
                            }
                            onClick={() => chooseExperiment(item.number)}
                          >
                            Experiment {item.number}
                          </Button>
                        </>
                      ),
                    },
                    {
                      id: "col-1",
                      label: "State",
                      value: (item: any) => item.state,
                      render: (item: any) => (
                        <>
                          <Badge>{item.state}</Badge>
                        </>
                      ),
                    },
                    {
                      id: "col-2",
                      label: "Base model",
                      value: (item: any) => item.config.base_model,
                      render: (item: any) => <>{item.config.base_model}</>,
                    },
                    {
                      id: "col-3",
                      label: "Training",
                      value: (item: any) => item.config.training_type,
                      render: (item: any) => (
                        <>
                          {item.config.training_type} · {item.config.epochs}{" "}
                          epochs
                        </>
                      ),
                    },
                    {
                      id: "col-4",
                      label: "Train loss",
                      value: (item: any) => item.train_loss,
                      render: (item: any) => <>{item.train_loss ?? "—"}</>,
                    },
                    {
                      id: "col-5",
                      label: "Eval loss",
                      value: (item: any) => item.metrics?.eval_loss,
                      render: (item: any) => (
                        <>{item.metrics?.eval_loss ?? "—"}</>
                      ),
                    },
                  ]}
                  renderRow={(item: any, { key, ...props }, cells) => (
                    <Evidence
                      as="tr"
                      {...props}
                      key={key}
                      label="Experiment summary"
                      data={item}
                    >
                      {cells}
                    </Evidence>
                  )}
                />
              </div>
              <div className="data-job-detail">
                <TrainingPaneScroll>
                  <div className="data-mobile-back">
                    <Button
                      variant="ghost"
                      onClick={() => chooseExperiment(null)}
                    >
                      Back to experiments
                    </Button>
                  </div>
                  {experiments
                    .filter(
                      (item: any) =>
                        item.number === inspectedExperiment?.number,
                    )
                    .map((e: any) => (
                      <Evidence
                        key={e.number}
                        label="Experiment configuration"
                        data={e}
                      >
                        <Panel
                          title={`Experiment ${e.number} · ${e.state}`}
                          actions={
                            e.state !== "running" && (
                              <Button
                                variant="danger"
                                onClick={() =>
                                  a.run(async () => {
                                    if (
                                      await s.confirm(
                                        `Delete experiment ${e.number} and its weights?`,
                                      )
                                    ) {
                                      await s.api.del(
                                        `/api/jobs/${enc(name)}/experiments/${e.number}`,
                                      );
                                      r.reload();
                                    }
                                  })
                                }
                              >
                                Delete
                              </Button>
                            )
                          }
                        >
                          <p>
                            {e.config.base_model} · {e.config.training_type} ·{" "}
                            {e.config.epochs} epochs · lr{" "}
                            {e.config.learning_rate}
                          </p>
                          <details>
                            <summary>Full configuration</summary>
                            <pre className="data-prose">
                              {JSON.stringify(e.config, null, 2)}
                            </pre>
                          </details>
                          {e.error && <ErrorNotice error={e.error} />}
                          <p>
                            Train loss {e.train_loss ?? "—"} · Eval loss{" "}
                            {e.metrics?.eval_loss ?? "—"} · {e.seconds ?? "—"}s
                          </p>
                          {e.state === "succeeded" && (
                            <div className="data-actions">
                              <a
                                href={`#/playground?model=${enc(`${name}/${e.number}`)}`}
                              >
                                Test model
                              </a>
                              <a
                                href={scopedDownload(
                                  s.project,
                                  `/api/jobs/${enc(name)}/experiments/${e.number}/download`,
                                )}
                              >
                                Download weights
                              </a>
                              <Button
                                onClick={() =>
                                  a.run(async () => {
                                    await navigator.clipboard.writeText(
                                      e.alias,
                                    );
                                    s.toast("Alias copied");
                                  })
                                }
                              >
                                Copy {e.alias}
                              </Button>
                              {e.checkpoints?.length > 0 && (
                                <Button
                                  onClick={() => {
                                    a.clearError();
                                    C(e);
                                    CD(
                                      s.overview?.tree?.datasets?.[0]?.name ||
                                        "",
                                    );
                                  }}
                                >
                                  {e.checkpoints.length} checkpoints · capture
                                  for drift
                                </Button>
                              )}
                            </div>
                          )}
                        </Panel>
                      </Evidence>
                    ))}
                </TrainingPaneScroll>
              </div>
            </div>
          </section>
        </Activity>
        <Activity mode={pane === "analysis" ? "visible" : "hidden"}>
          <section
            id="training-analysis"
            role="tabpanel"
            aria-label="Dataset analysis"
          >
            <TrainingPaneScroll>
              <Panel
                title="Dataset analysis"
                actions={
                  <Button
                    onClick={() =>
                      a.run(async () => {
                        track(
                          s,
                          await s.api.post(
                            `/api/jobs/${enc(name)}/analyze`,
                            {},
                          ),
                        );
                        s.toast("Analysis started");
                      })
                    }
                  >
                    {analysis?.analysis
                      ? "Re-analyze"
                      : "Analyze with helper LLM"}
                  </Button>
                }
              >
                {analysis?.analysis ? (
                  <Evidence label="Dataset analysis" data={analysis}>
                    {Object.entries(analysis.analysis).map(([k, v]) => (
                      <details key={k}>
                        <summary>{k.replaceAll("_", " ")}</summary>
                        <pre className="data-prose">
                          {Array.isArray(v)
                            ? v.join("\n")
                            : typeof v === "object"
                              ? JSON.stringify(v, null, 2)
                              : String(v)}
                        </pre>
                      </details>
                    ))}
                    {analysis.recommendation && (
                      <pre className="data-prose">
                        {JSON.stringify(analysis.recommendation, null, 2)}
                      </pre>
                    )}
                  </Evidence>
                ) : (
                  <p>
                    Analyze the snapshot for task summary, success criteria,
                    edge cases, and model recommendation. Requires helper LLM.
                  </p>
                )}
              </Panel>
            </TrainingPaneScroll>
          </section>
        </Activity>
        {setup && (
          <Modal
            open
            wide
            className="training-setup"
            title={review ? "Review experiments" : "Configure experiments"}
            onClose={() => {
              dialogGeneration.current++;
              setSetup(false);
            }}
            footer={
              <div className="training-setup-footer">
                <span>
                  {experiments.filter((e: any) => e.state === "pending").length}{" "}
                  saved pending · {drafts.length} unsaved drafts
                </span>
                {review ? (
                  <div className="data-actions">
                    <Button disabled={a.busy} onClick={() => setReview(false)}>
                      Back to configuration
                    </Button>
                    {!!drafts.length && (
                      <Button disabled={a.busy} onClick={() => submit(false)}>
                        Save experiments
                      </Button>
                    )}
                    <Button
                      variant="primary"
                      loading={a.busy}
                      disabled={
                        running ||
                        pendingTask ||
                        admitted ||
                        (!drafts.length &&
                          !experiments.some((e: any) => e.state === "pending"))
                      }
                      onClick={() => submit(true)}
                    >
                      {drafts.length
                        ? "Save & start experiments"
                        : "Start saved experiments"}
                    </Button>
                  </div>
                ) : (
                  <Button
                    variant="primary"
                    disabled={a.busy || (!drafts.length && !experiments.length)}
                    onClick={() => {
                      if (validate()) setReview(true);
                    }}
                  >
                    Review experiments
                  </Button>
                )}
              </div>
            }
          >
            <TrainingPaneScroll>
              <ErrorNotice error={validation || a.error} />
              {review ? (
                <div className="training-review">
                  <p>
                    Saving creates pending experiments. Starting submits a
                    background training task for all saved pending experiments
                    in this job.
                  </p>
                  {experiments
                    .filter((e: any) => e.state === "pending")
                    .map((e: any) => (
                      <article key={`saved-${e.number}`}>
                        <h3>Saved experiment {e.number} · pending</h3>
                        <p>
                          {e.config.base_model} · {e.config.training_type} ·{" "}
                          {e.config.epochs} epochs
                        </p>
                      </article>
                    ))}
                  {drafts.map((draft, i) => (
                    <article key={i}>
                      <h3>Draft experiment {i + 1}</h3>
                      <p>
                        {draft.base_model} · {draft.training_type} ·{" "}
                        {draft.epochs} epochs · learning rate{" "}
                        {draft.learning_rate}
                      </p>
                      <details>
                        <summary>All settings</summary>
                        <pre className="data-prose">
                          {JSON.stringify(draft, null, 2)}
                        </pre>
                      </details>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="training-setup-layout">
                  <aside
                    className="training-drafts"
                    aria-label="Draft experiments"
                  >
                    <p>Unsaved drafts</p>
                    {drafts.map((draft, i) => (
                      <Button
                        key={i}
                        disabled={a.busy}
                        aria-pressed={draftIndex === i}
                        onClick={() => setDraftIndex(i)}
                      >
                        Draft {i + 1}
                        <span>{draft.base_model || "Choose a model"}</span>
                      </Button>
                    ))}
                    <Button disabled={draftLocked} onClick={() => add()}>
                      Add experiment
                    </Button>
                    {analysis?.recommendation?.recommended && (
                      <Button
                        disabled={draftLocked}
                        onClick={() => add(analysis.recommendation.recommended)}
                      >
                        Add recommended experiment
                      </Button>
                    )}
                  </aside>
                  <div className="training-editor">
                    {drafts[draftIndex] ? (
                      <>
                        <header>
                          <h3>Draft experiment {draftIndex + 1}</h3>
                          <Button
                            disabled={a.busy}
                            onClick={() => {
                              D(drafts.filter((_, i) => i !== draftIndex));
                              setDraftIndex(Math.max(0, draftIndex - 1));
                              setValidation("");
                            }}
                          >
                            Remove draft
                          </Button>
                        </header>
                        <ExperimentFields
                          value={drafts[draftIndex]}
                          onChange={(value) => {
                            D(
                              drafts.map((draft, i) =>
                                i === draftIndex ? value : draft,
                              ),
                            );
                            setValidation("");
                          }}
                          catalog={catalog}
                          models={models.data || []}
                          disabled={a.busy}
                        />
                      </>
                    ) : (
                      <EmptyState>
                        <h3>Choose the experiments to train</h3>
                        <p>
                          Add a draft or review the job’s saved pending
                          experiments.
                        </p>
                      </EmptyState>
                    )}
                  </div>
                </div>
              )}
            </TrainingPaneScroll>
          </Modal>
        )}
        {evaluate && (
          <EvaluationDialog
            snapshot={job.snapshot}
            models={experiments
              .filter((e: any) => e.state === "succeeded")
              .map((e: any) => `${name}/${e.number}`)}
            onClose={() => E(false)}
          />
        )}{" "}
        {capture && (
          <Modal
            open
            title={`Capture checkpoints of ${name}/${capture.number}`}
            onClose={() => C(null)}
            footer={
              <Button
                variant="primary"
                loading={a.busy}
                onClick={() =>
                  a.run(async () => {
                    const parsed =
                      layers.trim() && layers.trim() !== "all"
                        ? layers.split(",").map((x) => Number(x.trim()))
                        : [];
                    if (
                      parsed &&
                      parsed.some((x) => !Number.isInteger(x) || x < 0)
                    )
                      throw Error(
                        "Layers must be comma-separated nonnegative integers, or all",
                      );
                    if (!captureDs) throw Error("Choose a dataset");
                    track(
                      s,
                      await s.api.post("/api/interp/checkpoint-captures", {
                        job: name,
                        number: capture.number,
                        dataset: captureDs,
                        layers: parsed,
                      }),
                    );
                    C(null);
                  })
                }
              >
                Capture
              </Button>
            }
          >
            <p>
              Capture every saved checkpoint plus final weights for neuron
              drift.
            </p>
            <Select
              label="Dataset"
              value={captureDs}
              onChange={CD}
              options={(s.overview?.tree?.datasets || [])
                .filter((d: any) => !d.missing)
                .map((d: any) => d.name)}
            />
            <Input
              label="Layers (e.g. 8,12,16 or all)"
              value={layers}
              onChange={L}
            />
            <ErrorNotice error={a.error} />
          </Modal>
        )}
      </RunTemplate>
    </Page>
  );
}
