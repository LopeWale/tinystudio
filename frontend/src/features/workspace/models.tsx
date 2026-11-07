import { ModelIdentity } from "./model-identity";
import { CollectionToolbar } from "../../components/studio/collection-toolbar";
import { AdvancedTable, DataTable } from "../../components/studio/data-table";
import { CollectionTemplate } from "../../components/studio/templates";
import { useComponentAction } from "../assistant/actions";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useResource, useStudio } from "../../app/StudioContext";
import {
  Badge,
  Button,
  EmptyState,
  Evidence,
  Field,
  Modal,
  Page,
} from "../../components/studio/primitives";
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
import { cx } from "../../components/boardui/utils/cx";
import {
  Input as HubInput,
  TextField,
  InputBase,
} from "../../components/boardui/base/input/input";
import {
  Select,
  SelectItem,
} from "../../components/boardui/base/select/select";
import { Label, Tabs, TabList, Tab, TabPanel } from "react-aria-components";
import {
  RiSearchLine,
  RiDownloadLine,
  RiKey2Line,
  RiPulseLine,
} from "@remixicon/react";
import {
  RecordsInspector as DetailInspector,
  RecordComparison,
  RecordsScroll,
  useRetainedResource,
} from "../data/records-workspace";
import "../data/records-workspace.css";
import "./serving-records.css";
import "./model-hub.css";
import "./model-inventory.css";
type HubPane = "discover" | "downloaded" | "connection" | "activity";
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
/** Serving is the same operation from a collection row or its selected context. */
function ModelServingActions({
  model,
  busy,
  onToggle,
}: {
  model: Model;
  busy: string;
  onToggle: (model: Model) => void;
}) {
  return (
    <div className="actions" role="group" aria-label={`Serving ${model.alias}`}>
      <Button
        size="sm"
        disabled={!!busy}
        loading={busy === model.id}
        onClick={() => onToggle(model)}
      >
        {model.status === "up" ? "Unload" : "Load"}
      </Button>
      <a href={`#/playground?model=${enc(model.id)}`}>Test</a>
    </div>
  );
}

export function ModelsPage() {
  const studio = useStudio(),
    models = useRetainedResource<Model[]>("/api/models"),
    hub = useResource<HubInfo>("/api/hub/models");
  const [hubOpen, setHubOpen] = useState(false);
  const [hubPane, setHubPane] = useState<HubPane>("discover");
  const [compactHub, setCompactHub] = useState(
    () => window.matchMedia?.("(max-width: 850px)").matches ?? false,
  );
  useEffect(() => {
    const media = window.matchMedia?.("(max-width: 850px)");
    if (!media) return;
    const update = () => setCompactHub(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const [apiOpen, setApiOpen] = useState(false);
  const [inventoryQuery, setInventoryQuery] = useState(""),
    [inspectedId, inspect] = useState<string | null>(null),
    [checked, setChecked] = useState<Model[]>([]),
    [comparing, compare] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null),
    compareFocus = useRef<HTMLElement | null>(null);
  const restoreInspectorFocus = useRef(false);
  const closeInspector = () => {
    restoreInspectorFocus.current = true;
    inspect(null);
  };
  useLayoutEffect(() => {
    if (inspectedId !== null || !restoreInspectorFocus.current) return;
    restoreInspectorFocus.current = false;
    returnFocus.current?.focus();
  }, [inspectedId]);
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
    [pollError, setPollError] = useState(""),
    [upload, setUpload] = useState<Model | null>(null),
    [tasks, setTasks] = useState<any[]>([]);
  const searchOwner = useRef<AbortController | null>(null),
    mounted = useRef(true);
  const scopeOwner = useRef({ key: studio.scopeKey, generation: 0 });
  if (scopeOwner.current.key !== studio.scopeKey) {
    scopeOwner.current = {
      key: studio.scopeKey,
      generation: scopeOwner.current.generation + 1,
    };
  }
  useEffect(() => {
    searchOwner.current?.abort();
    searchOwner.current = null;
    setHubPane("discover");
    setToken("");
    setQuery("");
    setTask("text-generation");
    setMaximum("2");
    setResults(null);
    setSearching(false);
    setBusy("");
    setError("");
    setPollError("");
    setTasks([]);
    setUpload(null);
  }, [studio.scopeKey]);
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
      setHubPane("discover");
      setQuery(value || "");
    },
  });
  async function action(
    name: string,
    operation: (isCurrent: () => boolean) => Promise<void>,
  ) {
    if (busy) return;
    const scope = scopeOwner.current;
    const isCurrent = () => mounted.current && scopeOwner.current === scope;
    setBusy(name);
    setError("");
    try {
      await operation(isCurrent);
    } catch (e: any) {
      if (isCurrent() && e.name !== "AbortError") setError(e.message);
    } finally {
      if (isCurrent()) setBusy("");
    }
  }
  function toggleServing(model: Model) {
    void action(model.id, async (isCurrent) => {
      await studio.api.post(
        `/api/models/${modelSlug(model.id)}/${model.status === "up" ? "down" : "up"}`,
        {},
      );
      if (isCurrent()) reload();
    });
  }
  function track(result: any) {
    const accepted = result.task ?? result;
    setTasks((rows) => [accepted, ...rows.filter((t) => t.id !== accepted.id)]);
    studio.trackTask(accepted);
    studio.toast(
      "Background operation accepted. Open Hub Activity to follow its status.",
    );
  }
  useEffect(() => {
    // A final task may resolve through Stop rather than the polling loop.
    // Once none remain active, an earlier polling failure is no longer current.
    if (!tasks.some((t) => ["queued", "running"].includes(t.state))) {
      setPollError("");
      return;
    }
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
          setPollError("");
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
          setPollError(e.message);
          setTasks((rows) => [...rows]);
        }
      }
    }, 2500);
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [tasks, studio.api, studio.scopeKey]);
  async function search(event: FormEvent) {
    event.preventDefault();
    searchOwner.current?.abort();
    const controller = new AbortController();
    searchOwner.current = controller;
    const scope = scopeOwner.current;
    const isCurrent = () =>
      !controller.signal.aborted && scopeOwner.current === scope;
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
      if (isCurrent()) setResults(rows);
    } catch (e: any) {
      if (isCurrent()) setError(e.message);
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
        {!hubOpen && (error || models.error || hub.error) && (
          <HubErrorNotice
            message={
              error ||
              models.error?.message ||
              hub.error?.message ||
              "Request failed."
            }
          />
        )}

        {!hubOpen && pollError && <HubErrorNotice message={pollError} />}
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
                    width: 48,
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
                          aria-label={`Inspect model ${model.alias}`}
                          onClick={(event) => {
                            returnFocus.current = event.currentTarget;
                            inspect(model.id);
                          }}
                        >
                          <ModelIdentity id={model.id} alias={model.alias} />
                        </Button>
                      </>
                    ),
                  },
                  {
                    id: "kind",
                    width: 100,
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
                    width: 104,
                    label: "Parameters",
                    value: (model) => model.parameters,
                    render: (model) => <>{parameters(model.parameters)}</>,
                  },
                  {
                    id: "status",
                    width: 80,
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
                    width: 136,
                    label: "Actions",
                    value: (model) => model.id,
                    sortable: false,
                    filterable: false,
                    hideable: false,
                    render: (model) => (
                      <>
                        <ModelServingActions
                          model={model}
                          busy={busy}
                          onToggle={toggleServing}
                        />
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
                <ModelServingActions
                  model={selected}
                  busy={busy}
                  onToggle={toggleServing}
                />
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
                {selected.kind === "fine-tuned" && (
                  <div className="actions">
                    <a
                      aria-label={`Download ${selected.alias}`}
                      href={`/api/p/${enc(studio.project)}/jobs/${enc(selected.id.split("/")[0])}/experiments/${enc(selected.id.split("/")[1])}/download`}
                    >
                      Download
                    </a>
                    <Button size="sm" onClick={() => setUpload(selected)}>
                      Upload to Hub
                    </Button>
                    <a href={`#/finetuning/${enc(selected.id.split("/")[0])}`}>
                      Training & checkpoints
                    </a>
                  </div>
                )}
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
          className="model-hub-modal"
          wide
          title="Discover & manage Hub models"
          onClose={() => setHubOpen(false)}
        >
          <Tabs
            className="model-hub-workspace"
            orientation={compactHub ? "horizontal" : "vertical"}
            selectedKey={hubPane}
            onSelectionChange={(key) => setHubPane(key as HubPane)}
          >
            <aside className="model-hub-rail">
              <span className="model-hub-rail-label">HUGGING FACE HUB</span>
              <TabList aria-label="Hub sections" className="model-hub-nav">
                <Tab id="discover">
                  <RiSearchLine aria-hidden="true" />
                  Discover
                </Tab>
                <Tab id="downloaded">
                  <RiDownloadLine aria-hidden="true" />
                  Downloaded
                  {hub.data && (
                    <span className="model-hub-count">
                      {hub.data.models.length}
                    </span>
                  )}
                </Tab>
                <Tab id="connection">
                  <RiKey2Line aria-hidden="true" />
                  Connection
                </Tab>
                <Tab id="activity">
                  <RiPulseLine aria-hidden="true" />
                  Activity
                  {tasks.length > 0 && (
                    <span className="model-hub-count">{tasks.length}</span>
                  )}
                </Tab>
              </TabList>
              <p className="model-hub-storage">
                {hub.data?.free_disk_gb != null
                  ? `${hub.data.free_disk_gb.toFixed(1)} GB free on server`
                  : "Models are stored on your server."}
              </p>
            </aside>
            <div className="model-hub-main">
              {(error || hub.error) && (
                <HubErrorNotice
                  message={error || hub.error?.message || "Hub request failed."}
                />
              )}
              {pollError && <HubErrorNotice message={pollError} />}
              <TabPanel id="discover" className="model-hub-pane">
                <header className="model-hub-heading">
                  <h3>Discover models</h3>
                  <p>Find compatible models from the Hugging Face community.</p>
                </header>
                <form className="model-hub-search" onSubmit={search}>
                  <HubInput
                    label="Search the Hugging Face Hub"
                    size="small"
                    value={query}
                    onChange={setQuery}
                    placeholder="Model or repository…"
                    leadingIcon={RiSearchLine}
                  />
                  <div className="model-hub-task-field">
                    <span className="model-hub-field-label">Task</span>
                    <Select
                      aria-label="Task"
                      size="sm"
                      selectedKey={task}
                      onSelectionChange={(key) =>
                        key != null && setTask(String(key))
                      }
                    >
                      <SelectItem id="text-generation">
                        Text generation
                      </SelectItem>
                      <SelectItem id="embeddings">Embeddings (RAG)</SelectItem>
                      <SelectItem id="sentence-similarity">
                        Sentence similarity
                      </SelectItem>
                      <SelectItem id="any">Any</SelectItem>
                    </Select>
                  </div>
                  <TextField size="small" value={maximum} onChange={setMaximum}>
                    <Label className="model-hub-field-label">
                      Max parameters (B)
                    </Label>
                    <InputBase
                      type="number"
                      min={0}
                      step={0.1}
                      placeholder="No limit"
                    />
                  </TextField>
                  <Button type="submit" variant="primary" loading={searching}>
                    Search
                  </Button>
                </form>
                <p className="model-hub-search-hint">
                  Parameter sizes are in billions. Clear the limit to search all
                  sizes.
                </p>
                {results !== null ? (
                  <section
                    className="model-hub-collection"
                    aria-label="Hub search results"
                    aria-live="polite"
                  >
                    {!results.length ? (
                      <EmptyState>
                        No matching models. Try another query, task or parameter
                        limit.
                      </EmptyState>
                    ) : (
                      <DataTable
                        layout="fill"
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
                                  <ModelIdentity id={result.id} />
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
                            render: (result) => (
                              <>{result.license || "Unknown"}</>
                            ),
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
                              <div className="actions model-hub-row-actions">
                                {result.transformers_loadable ? (
                                  <Button
                                    size="sm"
                                    disabled={!!busy}
                                    onClick={() =>
                                      void action(
                                        result.id,
                                        async (isCurrent) => {
                                          const accepted =
                                            await studio.api.post(
                                              "/api/hub/download",
                                              { repo_id: result.id },
                                            );
                                          if (isCurrent()) track(accepted);
                                        },
                                      )
                                    }
                                  >
                                    Download
                                  </Button>
                                ) : (
                                  <span className="muted">
                                    Not loadable here
                                  </span>
                                )}
                              </div>
                            ),
                          },
                        ]}
                      />
                    )}
                  </section>
                ) : (
                  <EmptyState className="model-hub-empty">
                    <RiSearchLine aria-hidden="true" />
                    <h4>Find your next model</h4>
                    <p>
                      Search by repository or model family, then narrow by task
                      and parameter size.
                    </p>
                    <small>Discover public models without a token.</small>
                  </EmptyState>
                )}
              </TabPanel>
              <TabPanel id="downloaded" className="model-hub-pane">
                <header className="model-hub-heading">
                  <h3>Downloaded models</h3>
                  <p>
                    Manage local copies and open compatible models in
                    Playground.
                  </p>
                </header>
                {hub.loading ? (
                  <p role="status">Loading cache…</p>
                ) : hub.data?.models.length ? (
                  <DataTable
                    layout="fill"
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
                        render: (model) => <ModelIdentity id={model.id} />,
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
                              ? new Date(
                                  model.last_accessed * 1000,
                                ).toLocaleString()
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
                          <div className="actions model-hub-row-actions">
                            {model.transformers_loadable && (
                              <a href={`#/playground?model=${enc(model.id)}`}>
                                Test
                              </a>
                            )}
                            {studio.user?.role === "admin" && (
                              <Button
                                size="sm"
                                variant="danger"
                                disabled={!!busy}
                                onClick={() =>
                                  void action("remove", async (isCurrent) => {
                                    if (
                                      await studio.confirm(
                                        `Delete the local copy of ${model.id}? It can be downloaded again.`,
                                        "Remove cached model",
                                      )
                                    ) {
                                      if (!isCurrent()) return;
                                      await studio.api.del(
                                        `/api/hub/models/${model.id.split("/").map(enc).join("/")}`,
                                      );
                                      if (isCurrent()) reload();
                                    }
                                  })
                                }
                              >
                                Remove
                              </Button>
                            )}
                          </div>
                        ),
                      },
                    ]}
                  />
                ) : hub.error || !hub.data ? (
                  <EmptyState>
                    Downloaded models are unavailable. Refresh to check the
                    server cache.
                    <Button onClick={() => hub.reload()}>Refresh cache</Button>
                  </EmptyState>
                ) : (
                  <EmptyState>No downloaded models yet.</EmptyState>
                )}
              </TabPanel>
              <TabPanel id="connection" className="model-hub-pane">
                <header className="model-hub-heading">
                  <h3>Hub connection</h3>
                  <p>
                    Configure access to gated repositories and checkpoint
                    uploads.
                  </p>
                </header>
                <RecordsScroll label="Hub connection settings">
                  <div className="model-hub-connection-status">
                    <Badge>
                      {!hub.data
                        ? hub.loading
                          ? "Checking connection…"
                          : "Connection status unavailable"
                        : hub.data.token.configured
                          ? "Token configured"
                          : "No token configured"}
                    </Badge>
                    <p>
                      {!hub.data
                        ? "Server token status could not yet be confirmed."
                        : hub.data.token.configured
                          ? hub.data.token.masked ||
                            "A server token is available."
                          : "Public models are available without a token."}
                    </p>
                  </div>
                  {studio.user?.role === "admin" ? (
                    <form
                      className="model-hub-token-form"
                      onSubmit={(event) => {
                        event.preventDefault();
                        void action("token", async (isCurrent) => {
                          const result = await studio.api.post<any>(
                            "/api/hub/token",
                            { token },
                          );
                          if (!isCurrent()) return;
                          setToken("");
                          studio.toast(`Hub token saved for ${result.user}`);
                          hub.reload();
                        });
                      }}
                    >
                      <HubInput
                        label="Hub access token"
                        hint="Needed for gated models and uploads. Stored on the server."
                        type="password"
                        value={token}
                        autoComplete="off"
                        onChange={setToken}
                        placeholder="hf_…"
                        isRequired
                      />
                      <Button
                        type="submit"
                        variant="primary"
                        disabled={!token.trim() || !!busy}
                        loading={busy === "token"}
                      >
                        Save token
                      </Button>
                    </form>
                  ) : (
                    <p>
                      Ask an administrator to configure or update the server's
                      Hub token.
                    </p>
                  )}
                </RecordsScroll>
              </TabPanel>
              <TabPanel id="activity" className="model-hub-pane">
                <header className="model-hub-heading">
                  <h3>Hub activity</h3>
                  <p>
                    Follow background downloads and uploads. Tasks continue when
                    this dialog closes.
                  </p>
                </header>
                <RecordsScroll label="Hub activity tasks">
                  {" "}
                  {tasks.length ? (
                    <div className="model-hub-activity">
                      {tasks.map((t) => (
                        <article key={t.id}>
                          <strong>{t.kind}</strong> <Badge>{t.state}</Badge>
                          <pre>{(t.log_tail ?? []).slice(-8).join("\n")}</pre>
                          {t.error && <p role="alert">{t.error}</p>}
                          {["queued", "running"].includes(t.state) && (
                            <Button
                              size="sm"
                              disabled={!!busy}
                              loading={busy === "stop"}
                              onClick={() =>
                                void action("stop", async (isCurrent) => {
                                  await studio.api.post(
                                    `/api/tasks/${enc(t.id)}/stop`,
                                    {},
                                  );
                                  if (!isCurrent()) return;
                                  const next = await studio.api.get(
                                    `/api/tasks/${enc(t.id)}`,
                                  );
                                  if (!isCurrent()) return;
                                  setTasks((rows) =>
                                    rows.map((row) =>
                                      row.id === t.id ? next : row,
                                    ),
                                  );
                                })
                              }
                            >
                              Stop task
                            </Button>
                          )}
                        </article>
                      ))}
                    </div>
                  ) : (
                    <EmptyState>
                      No Hub activity yet. Download a model or upload a trained
                      checkpoint to start a background task.
                    </EmptyState>
                  )}
                </RecordsScroll>
              </TabPanel>
            </div>
          </Tabs>
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
function HubErrorNotice({ message }: { message: string }) {
  const offline =
    /OfflineModeIsEnabled|offline mode is enabled|HF_HUB_OFFLINE/i.test(
      message,
    );
  return (
    <div className="model-hub-error">
      <p role="alert">
        {offline
          ? "Hub search is unavailable while the server is offline."
          : message}
      </p>
      {offline && (
        <details>
          <summary>Technical details</summary>
          <pre>{message}</pre>
        </details>
      )}
    </div>
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
