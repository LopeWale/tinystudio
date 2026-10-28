import { CollectionToolbar } from "../../components/studio/collection-toolbar";
import { AdvancedTable, DataTable } from "../../components/studio/data-table";
import { CollectionTemplate } from "../../components/studio/templates";
import { useComponentAction } from "../assistant/actions";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useResource, useStudio } from "../../app/StudioContext";
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
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
import { cx } from "../../components/boardui/utils/cx";
import { Input } from "../data/common";
import {
  RecordsInspector as DetailInspector,
  RecordComparison,
  RecordsScroll,
  useRetainedResource,
} from "../data/records-workspace";
import "../data/records-workspace.css";
import "./serving-records.css";
interface Model {
  id: string;
  alias: string;
  kind: string;
  base_model: string;
  parameters?: number;
  status: string;
  created?: string;
  notes?: string;
  training_type?: string;
}
interface Cached {
  id: string;
  size_gb: number;
  transformers_loadable: boolean;
  mlx?: boolean;
  last_accessed?: number;
}
interface SearchResult {
  id: string;
  url: string;
  params_b?: number;
  license?: string;
  downloads?: number;
  gated?: boolean;
  transformers_loadable: boolean;
  format_tags?: string[];
  library?: string;
}
interface HubInfo {
  models: Cached[];
  free_disk_gb: number;
  token: { configured: boolean; masked?: string };
}
const enc = encodeURIComponent;
const modelSlug = (id: string) => enc(id.replaceAll("/", "~"));
const parameters = (count?: number) =>
  count == null
    ? "—"
    : count >= 1e9
      ? `${(count / 1e9).toFixed(2)} B`
      : count >= 1e6
        ? `${(count / 1e6).toFixed(1)} M`
        : count.toLocaleString();
export function ModelsPage() {
  const studio = useStudio(),
    models = useRetainedResource<Model[]>("/api/models"),
    hub = useResource<HubInfo>("/api/hub/models");
  const [hubOpen, setHubOpen] = useState(false);
  const [apiOpen, setApiOpen] = useState(false);
  const [inventoryQuery, setInventoryQuery] = useState(""),
    [inspectedId, inspect] = useState<string | null>(null),
    [checked, setChecked] = useState<Model[]>([]),
    [comparing, compare] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null),
    compareFocus = useRef<HTMLElement | null>(null);
  const closeInspector = () => {
    inspect(null);
    returnFocus.current?.focus();
  };
  const visibleModels = (models.data || []).filter((model) =>
    `${model.alias} ${model.id} ${model.base_model}`
      .toLowerCase()
      .includes(inventoryQuery.trim().toLowerCase()),
  );
  const selected = models.data?.find((model) => model.id === inspectedId);
  const toggleModel = (model: Model) =>
    setChecked((previous) =>
      previous.some((item) => item.id === model.id)
        ? previous.filter((item) => item.id !== model.id)
        : [...previous, model],
    );
  useEffect(() => {
    setInventoryQuery("");
    setHubOpen(false);
    setApiOpen(false);
    inspect(null);
    setChecked([]);
    compare(false);
  }, [studio.scopeKey]);
  useEffect(() => {
    if (!models.data) return;
    setChecked((previous) =>
      previous.map(
        (record) =>
          models.data!.find((item) => item.id === record.id) || record,
      ),
    );
  }, [models.data]);
  const [token, setToken] = useState(""),
    [query, setQuery] = useState(""),
    [task, setTask] = useState("text-generation"),
    [maximum, setMaximum] = useState("2"),
    [results, setResults] = useState<SearchResult[] | null>(null),
    [searching, setSearching] = useState(false),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [upload, setUpload] = useState<Model | null>(null),
    [tasks, setTasks] = useState<any[]>([]);
  const searchOwner = useRef<AbortController | null>(null),
    mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      searchOwner.current?.abort();
    };
  }, []);
  const reload = () => {
    models.reload();
    hub.reload();
    void studio.refreshOverview().catch(() => {});
  };
  useComponentAction({
    id: "models.refresh",
    label: "Refresh model inventory",
    kind: "open",
    run: reload,
  });
  useComponentAction({
    id: "models.hub",
    label: "Open model discovery",
    kind: "open",
    state: { open: hubOpen },
    run: () => setHubOpen(true),
  });
  useComponentAction({
    id: "models.query",
    label: "Set model search query",
    kind: "filter",
    state: { query },
    run: (value) => {
      setHubOpen(true);
      setQuery(value || "");
    },
  });
  async function action(name: string, operation: () => Promise<void>) {
    if (busy) return;
    setBusy(name);
    setError("");
    try {
      await operation();
    } catch (e: any) {
      if (mounted.current && e.name !== "AbortError") setError(e.message);
    } finally {
      if (mounted.current) setBusy("");
    }
  }
  function track(result: any) {
    const accepted = result.task ?? result;
    setTasks((rows) => [accepted, ...rows.filter((t) => t.id !== accepted.id)]);
    studio.trackTask(accepted);
    studio.toast("Background operation accepted. Its status is shown below.");
  }
  useEffect(() => {
    if (!tasks.some((t) => ["queued", "running"].includes(t.state))) return;
    const controller = new AbortController();
    let active = true;
    const timer = setTimeout(async () => {
      try {
        const next = await Promise.all(
          tasks.map((t) =>
            ["queued", "running"].includes(t.state)
              ? studio.api.get(
                  `/api/tasks/${enc(t.id)}?include_result=false`,
                  controller.signal,
                )
              : t,
          ),
        );
        if (active) {
          setTasks(next);
          if (
            next.some(
              (t, i) =>
                t.state !== tasks[i].state &&
                !["queued", "running"].includes(t.state),
            )
          )
            reload();
        }
      } catch (e: any) {
        if (active && e.name !== "AbortError") {
          setError(e.message);
          setTasks((rows) => [...rows]);
        }
      }
    }, 2500);
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [tasks, studio.api]);
  async function search(event: FormEvent) {
    event.preventDefault();
    searchOwner.current?.abort();
    const controller = new AbortController();
    searchOwner.current = controller;
    setSearching(true);
    setError("");
    try {
      const params = new URLSearchParams({
        q: query.trim(),
        limit: "15",
        task,
      });
      if (maximum.trim()) params.set("max_params_b", maximum);
      else params.set("max_params_b", "0");
      const rows = await studio.api.get<SearchResult[]>(
        "/api/hub/search?" + params,
        controller.signal,
      );
      if (!controller.signal.aborted) setResults(rows);
    } catch (e: any) {
      if (!controller.signal.aborted) setError(e.message);
    } finally {
      if (searchOwner.current === controller) setSearching(false);
    }
  }
  return (
    <Page
      className="models-workspace data-workspace records-page serving-records-page collection-page"
      title="Models"
      description="Manage trained checkpoints, serve a model, and find compatible models on the Hugging Face Hub."
      actions={
        <div className="data-actions">
          <Button onClick={() => setHubOpen(true)}>
            Discover & manage Hub models
          </Button>
          <Button onClick={() => setApiOpen(true)}>
            Use the inference API
          </Button>
          <Button onClick={reload}>Refresh</Button>
        </div>
      }
    >
      <CollectionTemplate
        className="data-template"
        label="Model inventory workspace"
      >
        {(error || models.error || hub.error) && (
          <p role="alert">
            {error || models.error?.message || hub.error?.message}
          </p>
        )}

        {comparing && (
          <RecordComparison
            records={checked.map((model) => ({ ...model, name: model.alias }))}
            fields={[
              { label: "Model ID", value: (model) => model.id },
              { label: "Kind", value: (model) => model.kind },
              {
                label: "Base model",
                value: (model) => model.base_model || "—",
              },
              {
                label: "Parameters",
                value: (model) => parameters(model.parameters),
              },
              { label: "Status", value: (model) => model.status },
              {
                label: "Training type",
                value: (model) => model.training_type || "—",
              },
            ]}
            onRemove={toggleModel}
            onClose={() => {
              compare(false);
              compareFocus.current?.focus();
            }}
          />
        )}
        <div
          hidden={comparing}
          className={cx(
            "records-workbench",
            selected && "records-workbench-inspecting",
          )}
          onKeyDown={(event) => {
            if (event.key === "Escape") closeInspector();
          }}
        >
          <div className="records-table-host">
            {models.loading && !models.data ? (
              <p role="status">Loading models…</p>
            ) : (
              <AdvancedTable
                layout="fill"
                toolbarContent={
                  <CollectionToolbar
                    label="Find an inventory model"
                    query={inventoryQuery}
                    onQueryChange={setInventoryQuery}
                    selectedCount={checked.length}
                    onCompare={(event) => {
                      compareFocus.current = event.currentTarget;
                      compare(true);
                    }}
                    onClearSelection={() => setChecked([])}
                  />
                }
                label="Model inventory"
                rows={visibleModels}
                getRowId={(model) => model.id}
                scopeKey={studio.scopeKey}
                rowProps={(model) => ({
                  className: cx(
                    inspectedId === model.id && "records-inspected",
                  ),
                })}
                renderRow={(model, { key, ...props }, cells) => (
                  <Evidence
                    key={key}
                    as="tr"
                    {...props}
                    label="Model"
                    data={model}
                  >
                    {cells}
                  </Evidence>
                )}
                columns={[
                  {
                    id: "select",
                    label: "Select",
                    value: (model) => model.id,
                    sortable: false,
                    filterable: false,
                    hideable: false,
                    render: (model) => (
                      <>
                        <Checkbox
                          aria-label={`Select model ${model.alias}`}
                          isSelected={checked.some(
                            (item) => item.id === model.id,
                          )}
                          onChange={() => toggleModel(model)}
                        />
                      </>
                    ),
                  },
                  {
                    id: "model",
                    label: "Model",
                    value: (model) => model.alias,
                    hideable: false,
                    render: (model) => (
                      <>
                        <Button
                          variant="ghost"
                          aria-pressed={inspectedId === model.id}
                          onClick={(event) => {
                            returnFocus.current = event.currentTarget;
                            inspect(model.id);
                          }}
                        >
                          {model.alias}
                        </Button>
                        <small className="muted" style={{ display: "block" }}>
                          {model.notes || model.id}
                        </small>
                        {model.created && (
                          <small>
                            {new Date(model.created).toLocaleString()}
                          </small>
                        )}
                      </>
                    ),
                  },
                  {
                    id: "kind",
                    label: "Kind",
                    value: (model) =>
                      `${model.kind} ${model.training_type || ""}`,
                    render: (model) => (
                      <>
                        {model.kind}
                        {model.training_type && ` · ${model.training_type}`}
                      </>
                    ),
                  },
                  {
                    id: "base",
                    label: "Base model",
                    value: (model) => model.base_model,
                    render: (model) => <>{model.base_model}</>,
                  },
                  {
                    id: "parameters",
                    label: "Parameters",
                    value: (model) => model.parameters,
                    render: (model) => <>{parameters(model.parameters)}</>,
                  },
                  {
                    id: "status",
                    label: "Status",
                    value: (model) => model.status,
                    render: (model) => (
                      <>
                        <Badge>{model.status}</Badge>
                      </>
                    ),
                  },
                  {
                    id: "actions",
                    label: "Actions",
                    value: (model) => model.id,
                    sortable: false,
                    filterable: false,
                    hideable: false,
                    render: (model) => (
                      <>
                        <div className="actions">
                          <Button
                            size="sm"
                            disabled={!!busy}
                            loading={busy === model.id}
                            onClick={() =>
                              void action(model.id, async () => {
                                await studio.api.post(
                                  `/api/models/${modelSlug(model.id)}/${model.status === "up" ? "down" : "up"}`,
                                  {},
                                );
                                reload();
                              })
                            }
                          >
                            {model.status === "up" ? "Unload" : "Load"}
                          </Button>
                          <a href={`#/playground?model=${enc(model.id)}`}>
                            Test
                          </a>
                          {model.kind === "fine-tuned" && (
                            <>
                              <a
                                aria-label={`Download ${model.alias}`}
                                href={`/api/p/${enc(studio.project)}/jobs/${enc(model.id.split("/")[0])}/experiments/${enc(model.id.split("/")[1])}/download`}
                              >
                                Download
                              </a>
                              <Button
                                size="sm"
                                onClick={() => setUpload(model)}
                              >
                                Upload to Hub
                              </Button>
                              <a
                                href={`#/finetuning/${enc(model.id.split("/")[0])}`}
                              >
                                Training & checkpoints
                              </a>
                            </>
                          )}
                        </div>
                      </>
                    ),
                  },
                ]}
              />
            )}
          </div>
          {selected && (
            <DetailInspector title={selected.alias} onClose={closeInspector}>
              <Evidence label="Inspected model" data={selected}>
                <dl className="records-facts">
                  <dt>Model ID</dt>
                  <dd>{selected.id}</dd>
                  <dt>Kind</dt>
                  <dd>{selected.kind}</dd>
                  <dt>Base model</dt>
                  <dd>{selected.base_model}</dd>
                  <dt>Parameters</dt>
                  <dd>{parameters(selected.parameters)}</dd>
                  <dt>Status</dt>
                  <dd>{selected.status}</dd>
                  <dt>Created</dt>
                  <dd>
                    {selected.created
                      ? new Date(selected.created).toLocaleString()
                      : "—"}
                  </dd>
                  <dt>Training type</dt>
                  <dd>{selected.training_type || "—"}</dd>
                </dl>
                {selected.notes && <p>{selected.notes}</p>}
                <a href={`#/playground?model=${enc(selected.id)}`}>
                  Test {selected.alias} in Playground →
                </a>
              </Evidence>
            </DetailInspector>
          )}
        </div>
        {models.isStale && (
          <p role="status">
            {models.loading ? "Refreshing model inventory…" : "Refresh failed."}{" "}
            Previous results remain visible.
          </p>
        )}
        <Modal
          open={hubOpen}
          wide
          title="Discover & manage Hub models"
          onClose={() => setHubOpen(false)}
        >
          <p>
            {hub.data?.models.length ?? 0} models cached · Token:{" "}
            {hub.data?.token.configured
              ? hub.data.token.masked || "configured"
              : "not configured"}
          </p>
          {studio.user?.role === "admin" && (
            <form
              className="row"
              onSubmit={(event) => {
                event.preventDefault();
                void action("token", async () => {
                  const result = await studio.api.post<any>("/api/hub/token", {
                    token,
                  });
                  setToken("");
                  studio.toast(`Hub token saved for ${result.user}`);
                  hub.reload();
                });
              }}
            >
              <Field
                label="Hub access token"
                hint="Needed for gated models and uploads. Stored on the server."
              >
                <input
                  type="password"
                  value={token}
                  autoComplete="off"
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="hf_…"
                  required
                />
              </Field>
              <Button
                type="submit"
                disabled={!token.trim() || !!busy}
                loading={busy === "token"}
              >
                Save token
              </Button>
            </form>
          )}
          <form className="row" onSubmit={search}>
            <Field label="Search the Hugging Face Hub">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="SmolLM2, Qwen, all-MiniLM…"
              />
            </Field>
            <Field label="Task">
              <select value={task} onChange={(e) => setTask(e.target.value)}>
                <option value="text-generation">Text generation</option>
                <option value="embeddings">Embeddings (RAG)</option>
                <option value="sentence-similarity">Sentence similarity</option>
                <option value="any">Any</option>
              </select>
            </Field>
            <Field
              label="Maximum parameters (billions)"
              hint="Clear for no parameter limit."
            >
              <input
                type="number"
                min="0"
                step="0.1"
                value={maximum}
                onChange={(e) => setMaximum(e.target.value)}
              />
            </Field>
            <Button type="submit" variant="primary" loading={searching}>
              Search
            </Button>
          </form>
          {results && (
            <section aria-label="Hub search results" aria-live="polite">
              {!results.length ? (
                <EmptyState>
                  No matching models. Try another query, task or parameter
                  limit.
                </EmptyState>
              ) : (
                <DataTable
                  label="Hub model search results"
                  rows={results}
                  getRowId={(result) => result.id}
                  scopeKey={studio.scopeKey}
                  renderRow={(result, { key, ...props }, cells) => (
                    <Evidence
                      key={key}
                      as="tr"
                      {...props}
                      label="Hub model result"
                      data={result}
                    >
                      {cells}
                    </Evidence>
                  )}
                  columns={[
                    {
                      id: "model",
                      label: "Repository",
                      value: (result) => result.id,
                      hideable: false,
                      render: (result) => (
                        <>
                          <a
                            href={`https://huggingface.co/${result.id.split("/").map(enc).join("/")}`}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            {result.id}
                          </a>
                          {result.gated && <Badge>Gated</Badge>}
                        </>
                      ),
                    },
                    {
                      id: "parameters",
                      label: "Parameters",
                      value: (result) => result.params_b,
                      render: (result) => (
                        <>
                          {result.params_b == null
                            ? "Unknown"
                            : `${result.params_b} B`}
                        </>
                      ),
                    },
                    {
                      id: "license",
                      label: "License",
                      value: (result) => result.license,
                      render: (result) => <>{result.license || "Unknown"}</>,
                    },
                    {
                      id: "downloads",
                      label: "Downloads",
                      value: (result) => result.downloads,
                      render: (result) => (
                        <>{result.downloads?.toLocaleString() ?? "—"}</>
                      ),
                    },
                    {
                      id: "format",
                      label: "Format",
                      value: (result) =>
                        result.transformers_loadable
                          ? "Transformers"
                          : result.format_tags?.join(", ") ||
                            result.library ||
                            "Other",
                      render: (result) => (
                        <>
                          {result.transformers_loadable
                            ? "Transformers"
                            : result.format_tags?.join(", ") ||
                              result.library ||
                              "Other"}
                        </>
                      ),
                    },
                    {
                      id: "actions",
                      label: "Action",
                      value: (result) => result.id,
                      sortable: false,
                      filterable: false,
                      hideable: false,
                      render: (result) => (
                        <>
                          {result.transformers_loadable ? (
                            <Button
                              size="sm"
                              disabled={!!busy}
                              onClick={() =>
                                void action(result.id, async () =>
                                  track(
                                    await studio.api.post("/api/hub/download", {
                                      repo_id: result.id,
                                    }),
                                  ),
                                )
                              }
                            >
                              Download
                            </Button>
                          ) : (
                            <span className="muted">Not loadable here</span>
                          )}
                        </>
                      ),
                    },
                  ]}
                />
              )}
            </section>
          )}
          <h3>Downloaded models</h3>
          {hub.loading ? (
            <p role="status">Loading cache…</p>
          ) : hub.data?.models.length ? (
            <DataTable
              label="Downloaded models"
              rows={hub.data.models}
              getRowId={(model) => model.id}
              scopeKey={studio.scopeKey}
              renderRow={(model, { key, ...props }, cells) => (
                <Evidence
                  key={key}
                  as="tr"
                  {...props}
                  label="Cached model"
                  data={model}
                >
                  {cells}
                </Evidence>
              )}
              columns={[
                {
                  id: "model",
                  label: "Repository",
                  value: (model) => model.id,
                  hideable: false,
                  render: (model) => <>{model.id}</>,
                },
                {
                  id: "size",
                  label: "Size",
                  value: (model) => model.size_gb,
                  render: (model) => <>{model.size_gb} GB</>,
                },
                {
                  id: "format",
                  label: "Format",
                  value: (model) =>
                    model.transformers_loadable
                      ? "Transformers"
                      : model.mlx
                        ? "MLX"
                        : "Other",
                  render: (model) => (
                    <>
                      {model.transformers_loadable
                        ? "Transformers"
                        : model.mlx
                          ? "MLX · helper LLM only"
                          : "Other"}
                    </>
                  ),
                },
                {
                  id: "lastused",
                  label: "Last used",
                  value: (model) => model.last_accessed,
                  render: (model) => (
                    <>
                      {model.last_accessed
                        ? new Date(model.last_accessed * 1000).toLocaleString()
                        : "—"}
                    </>
                  ),
                },
                {
                  id: "actions",
                  label: "Actions",
                  value: (model) => model.id,
                  sortable: false,
                  filterable: false,
                  hideable: false,
                  render: (model) => (
                    <>
                      {model.transformers_loadable && (
                        <a href={`#/playground?model=${enc(model.id)}`}>Test</a>
                      )}
                      {studio.user?.role === "admin" && (
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={!!busy}
                          onClick={() =>
                            void action("remove", async () => {
                              if (
                                await studio.confirm(
                                  `Delete the local copy of ${model.id}? It can be downloaded again.`,
                                  "Remove cached model",
                                )
                              ) {
                                await studio.api.del(
                                  `/api/hub/models/${model.id.split("/").map(enc).join("/")}`,
                                );
                                reload();
                              }
                            })
                          }
                        >
                          Remove
                        </Button>
                      )}
                    </>
                  ),
                },
              ]}
            />
          ) : (
            <EmptyState>No downloaded models yet.</EmptyState>
          )}
          {!!tasks.length && (
            <Panel title="Hub activity">
              {tasks.map((t) => (
                <article key={t.id}>
                  <strong>{t.kind}</strong> <Badge>{t.state}</Badge>
                  <pre>{(t.log_tail ?? []).slice(-8).join("\n")}</pre>
                  {t.error && <p role="alert">{t.error}</p>}
                  {["queued", "running"].includes(t.state) && (
                    <Button
                      size="sm"
                      onClick={() =>
                        void action("stop", async () => {
                          await studio.api.post(
                            `/api/tasks/${enc(t.id)}/stop`,
                            {},
                          );
                          const next = await studio.api.get(
                            `/api/tasks/${enc(t.id)}`,
                          );
                          setTasks((rows) =>
                            rows.map((row) => (row.id === t.id ? next : row)),
                          );
                        })
                      }
                    >
                      Stop task
                    </Button>
                  )}
                </article>
              ))}
            </Panel>
          )}
        </Modal>
        <Modal
          open={apiOpen}
          title="Use the inference API"
          onClose={() => setApiOpen(false)}
        >
          <p>
            OpenAI-compatible endpoint:{" "}
            <code>{window.location.origin}/v1/chat/completions</code>.
            Authenticate with an API key and pass{" "}
            <code>{studio.project}/&lt;job&gt;/&lt;number&gt;</code> or a model
            alias. Downloaded Transformers models can be used as fine-tuning
            base models.
          </p>
        </Modal>
        {upload && (
          <HubUpload
            model={upload}
            onClose={() => setUpload(null)}
            onComplete={track}
          />
        )}
      </CollectionTemplate>
    </Page>
  );
}
function HubUpload({
  model,
  onClose,
  onComplete,
}: {
  model: Model;
  onClose: () => void;
  onComplete: (task: any) => void;
}) {
  const { api, project } = useStudio(),
    [repo, setRepo] = useState(""),
    [privateRepo, setPrivate] = useState(true),
    [merge, setMerge] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const owner = useRef(new AbortController());
  useEffect(() => {
    owner.current = new AbortController();
    return () => owner.current.abort();
  }, []);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const task = await api.post(
        "/api/hub/upload",
        {
          project,
          kind: "experiment",
          name: model.id,
          repo_id: repo.trim(),
          private: privateRepo,
          merge,
        },
        owner.current.signal,
      );
      onComplete(task);
      onClose();
    } catch (e: any) {
      if (e.name !== "AbortError") setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal open title={`Upload ${model.alias} to the Hub`} onClose={onClose}>
      <form onSubmit={submit}>
        <Field label="Repository ID (user/name)">
          <input
            required
            pattern="[^/ ]+/[^/ ]+"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="you/my-tiny-model"
          />
        </Field>
        <Field label="Private repository">
          <input
            type="checkbox"
            checked={privateRepo}
            onChange={(e) => setPrivate(e.target.checked)}
          />
        </Field>
        <Field
          label="Merge LoRA into base model"
          hint="Uploads full weights instead of an adapter."
        >
          <input
            type="checkbox"
            checked={merge}
            onChange={(e) => setMerge(e.target.checked)}
          />
        </Field>
        {error && <p role="alert">{error}</p>}
        <div className="actions">
          <Button onClick={onClose} type="button">
            Cancel
          </Button>
          <Button variant="primary" type="submit" loading={busy}>
            Upload
          </Button>
        </div>
      </form>
    </Modal>
  );
}
