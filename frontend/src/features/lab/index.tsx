import { SegmentedControl } from "../../components/affectui/controls";
import { DataTable, SimpleTable } from "../../components/studio/data-table";
import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { useResource, useStudio } from "../../app/StudioContext";
import { useRetainedResource } from "../data/records-workspace";
import {
  Page,
  Panel,
  Button,
  Badge,
  EmptyState,
  Evidence,
  Modal,
} from "../../components/studio/primitives";
import { SettingsSection } from "../../components/studio/settings-section";
import { SetupTemplate, RunTemplate } from "../../components/studio/templates";
import { Input } from "../../components/boardui/base/input/input";
import { Textarea } from "../../components/boardui/base/textarea/textarea";
import {
  Select,
  SelectItem,
} from "../../components/boardui/base/select/select";
import { Label } from "react-aria-components";
import {
  makeDefinition,
  parseDefinition,
  evaluationParameters,
  templates,
  isTerminal,
  type ExperimentDefinition,
  type SavedDefinition,
  type LabRun,
} from "./definition";
import "./lab.css";
import "./runs-workspace.css";
import "../data/records-workspace.css";
import "./experiment-dialog.css";
import { InputArtifacts, LabChoice as Choice } from "./inputs";
import { DataPreparation, DraftField } from "./data-preparation";
import { LanguageLearning } from "./language-learning";
import { ControlLearning } from "./control-learning";
import { NumericLearning } from "./numeric-learning";
import { pretrainedAxes, editPretrainedAxes } from "./pretrained-learning";
import { MethodMatrixEditor } from "./method-matrix";
import { Studies } from "./studies";
import { StudyAnalysisResult } from "./study-analysis";
import { RuntimeProbeResult } from "./runtime-probe";
import { RuntimeProbeFields } from "./runtime-probe-fields";
import {
  HardwareDiscoveryFields,
  HardwareDiscoveryResult,
} from "./hardware-discovery";
import { RecoveredEvidencePanel } from "./recovered-evidence";
import {
  StatisticsPlanning,
  RetainedStatisticsPlanning,
} from "./statistics-planning";
import { DefinitionGraph } from "./graph";
import { ReplayViews, type ReplayRecordSource } from "./replay";
import { dataArtifacts } from "./evidence";
import { DatasetPromotion } from "./dataset-promotion";
import { FileUploadField } from "../../components/studio/uploads";
import {
  waitForOperation,
  downloadOperation,
  isOperationTerminal,
  type LabOperation,
} from "./operations";

const enc = encodeURIComponent;
const json = (value: unknown) => JSON.stringify(value, null, 2);
function EvidenceValue({ value }: { value: unknown }) {
  if (value == null) return <span className="text-text-secondary">—</span>;
  if (typeof value !== "object") return <code>{String(value)}</code>;
  return (
    <details className="lab-record-fields">
      <summary>
        {Array.isArray(value)
          ? `${value.length} items`
          : `${Object.keys(value).length} fields`}
      </summary>
      <pre>{json(value)}</pre>
    </details>
  );
}
function Fault({ error }: { error: unknown }) {
  return error ? (
    <p role="alert" className="text-text-error-primary">
      {error instanceof Error ? error.message : String(error)}
    </p>
  ) : null;
}
// A temporarily empty name is an editing state, not a reason to remove the
// controls. Save and preflight continue to use the strict definition parser.
function readEditorDefinition(text: string): ExperimentDefinition {
  const value = JSON.parse(text);
  if (value && typeof value.name === "string" && !value.name.trim()) {
    return {
      ...parseDefinition(json({ ...value, name: "Untitled experiment" })),
      name: value.name,
    };
  }
  return parseDefinition(text);
}
// Keep one editor implementation for inline existing work and modal creation.
function ExperimentSurface({
  creating,
  open,
  active,
  onClose,
  headingRef,
  children,
}: {
  creating: boolean;
  open: boolean;
  active: boolean;
  onClose: () => void;
  headingRef: RefObject<HTMLHeadingElement | null>;
  children: ReactNode;
}) {
  if (creating)
    return (
      <Modal
        open={open}
        keepMounted
        fallbackFocusRef={headingRef}
        wide
        title="New experiment"
        onClose={onClose}
        className="lab-experiment-dialog"
      >
        {children}
      </Modal>
    );
  return (
    <section
      id="lab-area-experiment"
      role="tabpanel"
      aria-label="Experiment"
      hidden={!active}
      className="lab-experiment-dialog lab-existing-experiment"
    >
      {children}
    </section>
  );
}
export function LabPage() {
  const studio = useStudio();
  return <LabWorkspace key={studio.scopeKey} />;
}
function LabWorkspace() {
  const studio = useStudio();
  const catalog = useResource<any>("/api/lab/catalog");
  const definitions = useResource<SavedDefinition[]>("/api/lab/definitions");
  const runs = useResource<LabRun[]>("/api/lab/runs");
  const targets = useResource<any[]>("/api/lab/targets");
  const operations = useRetainedResource<LabOperation[]>("/api/lab/operations");
  const [inspectedOperation, setInspectedOperation] =
    useState<LabOperation | null>(null);
  const [draft, setDraft] = useState(() => json(makeDefinition()));
  const [authoringGeneration, setAuthoringGeneration] = useState(0);
  const [dataPreparationValid, setDataPreparationValid] = useState(true);
  const [languageLearningValid, setLanguageLearningValid] = useState(true);
  const [controlLearningValid, setControlLearningValid] = useState(true);
  const [numericLearningValid, setNumericLearningValid] = useState(true);
  const [hardwareDiscoveryValid, setHardwareDiscoveryValid] = useState(true);
  const [parameterDrafts, setParameterDrafts] = useState<Set<string>>(
    () => new Set(),
  );
  const [reportParameter] = useState(
    () => (key: string, valid: boolean) =>
      setParameterDrafts((current) => {
        if (current.has(key) === !valid) return current;
        const next = new Set(current);
        if (valid) next.delete(key);
        else next.add(key);
        return next;
      }),
  );
  const [statisticsPlanningValid, setStatisticsPlanningValid] = useState(false);
  const [pendingInputWork, setPendingInputWork] = useState(false);
  const [saved, setSaved] = useState<SavedDefinition | null>(null);
  const [baselineText, setBaselineText] = useState(draft);
  const [area, setArea] = useState("runs");
  const [studiesOpened, setStudiesOpened] = useState(false);
  const [experimentOpen, setExperimentOpen] = useState(false);
  const [creatingExperiment, setCreatingExperiment] = useState(false);
  const [pendingDefinition, setPendingDefinition] = useState<
    SavedDefinition | "new" | null
  >(null);
  const laboratoryHeading = useRef<HTMLHeadingElement>(null);
  const [authoringStarted, setAuthoringStarted] = useState(false);
  const authoringScroll = useRef<HTMLDivElement>(null);
  const [scrollFade, setScrollFade] = useState(0);
  const [transferOpen, setTransferOpen] = useState(false);
  const [transferView, setTransferView] = useState("import");
  const [view, setView] = useState("parameters");
  useEffect(() => {
    if (authoringScroll.current) authoringScroll.current.scrollTop = 0;
    setScrollFade(0);
  }, [experimentOpen, view]);
  const [graphVisited, setGraphVisited] = useState(false);
  const [target, setTarget] = useState("local");
  const [notBefore, setNotBefore] = useState("");
  const [selectedRun, selectRun] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [conflict, setConflict] = useState(false);
  const [bundleFiles, setBundleFiles] = useState<File[]>([]);
  const [importOperation, setImportOperation] = useState<LabOperation | null>(
    null,
  );
  const [preflight, setPreflight] = useState<{
    draft: string;
    target: string;
    result: unknown;
  } | null>(null);
  const lifetime = useRef<AbortController | null>(null);
  const historyPoll = useRef<AbortController | null>(null);
  const transferDismissal = useRef(0);
  const dismissTransfer = () => {
    transferDismissal.current += 1;
    setTransferOpen(false);
  };
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);
  let definition: ExperimentDefinition | null = null;
  let parseError = "";
  try {
    definition = readEditorDefinition(draft);
    parseDefinition(draft);
  } catch (e) {
    parseError = (e as Error).message;
  }
  useEffect(() => setPreflight(null), [draft, target]);
  const change = (patch: Partial<ExperimentDefinition>) => {
    setDraft((current) => {
      try {
        return json({ ...readEditorDefinition(current), ...patch });
      } catch {
        return current;
      }
    });
  };
  const load = (row: SavedDefinition) => {
    setAuthoringGeneration((generation) => generation + 1);
    setSaved(row);
    setDraft(json(row.definition));
    setBaselineText(json(row.definition));
    setConflict(false);
    setError(null);
    setView("parameters");
    setAuthoringStarted(true);
    setCreatingExperiment(false);
    setExperimentOpen(false);
    setArea("experiment");
    setPendingDefinition(null);
  };
  const observeHistory = (operation: LabOperation, signal: AbortSignal) => {
    historyPoll.current?.abort();
    const controller = new AbortController();
    historyPoll.current = controller;
    const cancel = () => controller.abort();
    if (signal.aborted) cancel();
    else signal.addEventListener("abort", cancel, { once: true });
    void waitForOperation(
      studio.api,
      operation,
      controller.signal,
      setInspectedOperation,
      false,
    )
      .catch((e: any) => {
        if (!controller.signal.aborted && e.name !== "AbortError") setError(e);
      })
      .finally(() => {
        signal.removeEventListener("abort", cancel);
        if (!controller.signal.aborted) operations.reload();
      });
  };
  const historyCommand = async (
    operation: LabOperation,
    command: "cancel" | "retry",
  ) => {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted || historyBusy) return;
    setHistoryBusy(true);
    setError(null);
    try {
      const updated = await studio.api.post<LabOperation>(
        `/api/lab/operations/${enc(operation.id)}/${command}`,
        {},
        signal,
      );
      if (!signal.aborted) {
        operations.reload();
        observeHistory(updated, signal);
      }
    } catch (e: any) {
      if (!signal.aborted && e.name !== "AbortError") setError(e);
    } finally {
      if (!signal.aborted) setHistoryBusy(false);
    }
  };
  const action = async (fn: (signal: AbortSignal) => Promise<void>) => {
    const signal = lifetime.current?.signal;
    if (!signal || signal.aborted || busy) return;
    setBusy(true);
    setError(null);
    try {
      await fn(signal);
    } catch (e: any) {
      if (!signal.aborted && e.name !== "AbortError") {
        setError(e);
        if (e.status === 409) setConflict(true);
      }
    } finally {
      if (!signal.aborted) setBusy(false);
    }
  };
  const save = () =>
    action(async (signal) => {
      const submittedDraft = draft;
      const next = parseDefinition(submittedDraft);
      const row = saved
        ? await studio.api.put<SavedDefinition>(
            `/api/lab/definitions/${enc(saved.id)}`,
            { expected_revision: saved.revision, definition: next },
            signal,
          )
        : await studio.api.post<SavedDefinition>(
            "/api/lab/definitions",
            { name: next.name, definition: next },
            signal,
          );
      if (signal.aborted) return;
      setSaved(row);
      setDraft((current) =>
        current === submittedDraft ? json(row.definition) : current,
      );
      setBaselineText(json(row.definition));
      setConflict(false);
      definitions.reload();
      studio.toast(`Saved revision ${row.revision}`);
    });
  // Invalid structured text and unimported files belong to the current editor
  // even when they cannot yet be serialized into its portable definition.
  const hasAuthoringEdits =
    draft !== baselineText ||
    !dataPreparationValid ||
    !languageLearningValid ||
    !controlLearningValid ||
    !numericLearningValid ||
    !!parameterDrafts.size ||
    (definition?.engine === "statistics.plan" && !statisticsPlanningValid) ||
    (definition?.engine === "hardware.discover" && !hardwareDiscoveryValid) ||
    pendingInputWork;
  const dirty = !saved || hasAuthoringEdits;
  const structuredEngine = [
    "data.pipeline",
    "learning.language",
    "learning.control_rl",
    "learning.timeseries",
    "learning.multimodal",
    "statistics.plan",
    "runtime.qualify",
    "hardware.discover",
  ].includes(definition?.engine ?? "");
  const authoringValid =
    !parameterDrafts.size &&
    (definition?.engine === "data.pipeline"
      ? dataPreparationValid
      : definition?.engine === "learning.language"
        ? languageLearningValid
        : definition?.engine === "learning.control_rl"
          ? controlLearningValid
          : ["learning.timeseries", "learning.multimodal"].includes(
                definition?.engine ?? "",
              )
            ? numericLearningValid
            : definition?.engine === "statistics.plan"
              ? statisticsPlanningValid
              : definition?.engine === "hardware.discover"
                ? hardwareDiscoveryValid
                : true);
  const draftParameterEngine = [
    "learning.pretrained",
    "learning.language_rl",
    "learning.tool_rl",
    "retrieval.index",
    "evaluation.compare",
  ].includes(definition?.engine ?? "");
  const engineCatalog = [
    ...(catalog.data?.adapters ?? []),
    ...(catalog.data?.methods ?? []),
  ];
  const engineOptions = [
    ...new Map(
      [...templates, ...engineCatalog].map((item) => [
        item.id,
        { id: item.id, name: item.name ?? item.id },
      ]),
    ).values(),
  ];
  const capability = engineCatalog.find(
    (item) => item.id === definition?.engine,
  );
  const template = templates.find((item) => item.id === definition?.engine);
  const selectedTarget = targets.data?.find((item) => item.id === target);
  const startNewExperiment = () => {
    if (busy) return;
    setPendingDefinition(null);
    setCreatingExperiment(true);
    setAuthoringStarted(true);
    setExperimentOpen(true);
    setView("parameters");
    setGraphVisited(false);
    setSaved(null);
    const fresh = json(makeDefinition());
    setBaselineText(fresh);
    setAuthoringGeneration((generation) => generation + 1);
    setDraft(fresh);
    setConflict(false);
    setError(null);
    setTarget("local");
    setNotBefore("");
  };
  const savedExperimentChoice = (
    <Choice
      label="Saved experiment"
      placeholder="Open saved experiment"
      value={saved?.id ?? ""}
      disabled={busy || definitions.loading}
      options={(definitions.data ?? []).map((row) => ({
        id: row.id,
        name: `${row.name} · r${row.revision}`,
      }))}
      onChange={(id) => {
        const row = definitions.data?.find((item) => item.id === id);
        if (row) {
          if (row.id === saved?.id) return;
          if (hasAuthoringEdits) setPendingDefinition(row);
          else load(row);
        }
      }}
    />
  );
  const runSelector =
    runs.loading || !!runs.data?.length ? (
      <div className="lab-runs-header">
        <div className="lab-run-selector">
          <span className="text-caption-1-medium text-text-secondary">
            Retained run
          </span>
          <Select
            size="sm"
            aria-label="Retained run"
            placeholder="Select a retained run"
            selectedKey={selectedRun}
            isDisabled={runs.loading}
            onSelectionChange={(key) => {
              if (key !== null) selectRun(String(key));
            }}
          >
            {(runs.data ?? []).map((run) => (
              <SelectItem
                id={run.id}
                key={run.id}
                textValue={runSelectorLabel(run, runs.data ?? [])}
              >
                {runSelectorLabel(run, runs.data ?? [])}
              </SelectItem>
            ))}
          </Select>
        </div>
        {runs.loading && <p role="status">Loading runs…</p>}
      </div>
    ) : null;
  return (
    <Page
      title="Laboratory"
      headingRef={laboratoryHeading}
      className="lab-page"
      description="Author portable experiments, execute a saved revision, and inspect retained evidence."
      actions={
        <>
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => {
              if (busy) return;
              if (authoringStarted && dirty) {
                setPendingDefinition("new");
                if (creatingExperiment) setExperimentOpen(true);
                else setArea("experiment");
              } else startNewExperiment();
            }}
          >
            New experiment
          </Button>
          <Button
            disabled={busy}
            onClick={() => {
              setAuthoringStarted(true);
              if (creatingExperiment) setExperimentOpen(true);
              else setArea("experiment");
            }}
          >
            {saved
              ? "Edit experiment"
              : authoringStarted
                ? "Resume draft"
                : "Open experiment"}
          </Button>
          <Button onClick={() => setTransferOpen(true)}>Import / export</Button>
          <Button
            onClick={() => {
              catalog.reload();
              definitions.reload();
              runs.reload();
              targets.reload();
              operations.reload();
            }}
          >
            Refresh laboratory
          </Button>
        </>
      }
    >
      <Fault
        error={
          (!transferOpen &&
            !experimentOpen &&
            area !== "experiment" &&
            error) ||
          catalog.error ||
          definitions.error ||
          runs.error ||
          targets.error ||
          (!transferOpen && operations.error)
        }
      />
      <Modal
        open={transferOpen}
        title="Import / export"
        onClose={dismissTransfer}
        className="lab-transfer-dialog"
      >
        <SegmentedControl
          kind="tabs"
          variant="underline"
          size="sm"
          aria-label="Bundle transfer task"
          value={transferView}
          onChange={setTransferView}
          options={[
            {
              value: "import",
              label: "Import",
              controls: "lab-transfer-import",
            },
            {
              value: "history",
              label: "History",
              controls: "lab-transfer-history",
            },
          ]}
        />
        <Fault error={error || operations.error} />
        <div className="lab-transfer-viewport" key={transferView}>
          <div
            className="lab-transfer-scroll"
            onScroll={(event) => {
              event.currentTarget.parentElement?.style.setProperty(
                "--lab-transfer-fade",
                String(Math.min(event.currentTarget.scrollTop / 24, 1)),
              );
            }}
          >
            <div className="lab-transfer-layout">
              <section
                id="lab-transfer-import"
                role="tabpanel"
                aria-label="Import"
                hidden={transferView !== "import"}
              >
                <Panel title="Import bundle" className="lab-bundle-panel">
                  <FileUploadField
                    label="Portable laboratory bundle"
                    presentation="compact"
                    accept=".zip"
                    files={bundleFiles}
                    onChange={setBundleFiles}
                    disabled={busy}
                  />
                  <Button
                    disabled={busy || !bundleFiles.length}
                    onClick={() =>
                      action(async (signal) => {
                        const dismissal = transferDismissal.current;
                        setImportOperation(null);
                        const form = new FormData();
                        form.append("file", bundleFiles[0]);
                        const artifact = await studio.api.upload<{
                          id: string;
                        }>("/api/lab/inputs", form, { signal });
                        if (signal.aborted) return;
                        if (
                          !artifact ||
                          typeof artifact.id !== "string" ||
                          !artifact.id
                        )
                          throw new Error(
                            "Upload did not return a retained artifact. Try uploading the complete bundle again.",
                          );
                        const submitted = await studio.api.post<LabOperation>(
                          "/api/lab/bundle-imports",
                          {
                            artifact_id: artifact.id,
                            idempotency_key: crypto.randomUUID(),
                          },
                          signal,
                        );
                        operations.reload();
                        let operation: LabOperation;
                        try {
                          operation = await waitForOperation(
                            studio.api,
                            submitted,
                            signal,
                            setImportOperation,
                          );
                        } finally {
                          if (!signal.aborted) operations.reload();
                        }
                        if (signal.aborted) return;
                        const result = operation.result as {
                          definition: SavedDefinition;
                        };
                        definitions.reload();
                        setBundleFiles([]);
                        if (dismissal !== transferDismissal.current) {
                          studio.toast(
                            "Import completed. Load the experiment from Import / export → History.",
                          );
                          return;
                        }
                        setTransferOpen(false);
                        load(result.definition);
                        selectRun(null);
                        studio.toast("Imported portable experiment");
                      })
                    }
                  >
                    Import portable bundle
                  </Button>
                  <p>
                    Queued imports continue on the server if you leave this
                    page.
                  </p>
                  <div className="lab-export-guide">
                    <h3>Export a run</h3>
                    <p>
                      Portable exports include a run and its retained evidence.
                    </p>
                    <Button
                      onClick={() => {
                        dismissTransfer();
                        setArea("runs");
                      }}
                    >
                      Choose run to export
                    </Button>
                  </div>
                  {importOperation && (
                    <Evidence
                      label="Laboratory bundle import"
                      data={importOperation}
                    >
                      <p role="status">Import: {importOperation.state}</p>
                      <p>Operation: {importOperation.id}</p>
                      {importOperation.result != null && (
                        <details>
                          <summary>
                            Imported evidence and artifact mapping
                          </summary>
                          <pre>{json(importOperation.result)}</pre>
                        </details>
                      )}
                    </Evidence>
                  )}
                </Panel>
              </section>
              <section
                id="lab-transfer-history"
                role="tabpanel"
                aria-label="History"
                hidden={transferView !== "history"}
              >
                <Panel
                  title="Operation history"
                  className="lab-operation-history"
                  actions={
                    <Button onClick={operations.reload}>
                      Refresh operations
                    </Button>
                  }
                >
                  {operations.loading && (
                    <p role="status">Loading operations…</p>
                  )}
                  {(Array.isArray(operations.data) ? operations.data : []).map(
                    (operation) => (
                      <div className="lab-input-row" key={operation.id}>
                        <p>
                          {operation.kind} · {operation.id} ·{" "}
                          <Badge>{operation.state}</Badge>
                        </p>
                        <div className="lab-actions">
                          <Button
                            disabled={busy}
                            onClick={() =>
                              action(async (signal) => {
                                const full = await studio.api.get<LabOperation>(
                                  `/api/lab/operations/${enc(operation.id)}`,
                                  signal,
                                );
                                observeHistory(full, signal);
                              })
                            }
                          >
                            Inspect {operation.id}
                          </Button>
                          <Button
                            disabled={
                              historyBusy ||
                              isOperationTerminal(operation.state)
                            }
                            onClick={() => historyCommand(operation, "cancel")}
                          >
                            Cancel {operation.id}
                          </Button>
                          <Button
                            disabled={
                              historyBusy ||
                              !["failed", "lost", "cancelled"].includes(
                                operation.state,
                              )
                            }
                            onClick={() => historyCommand(operation, "retry")}
                          >
                            Retry {operation.id}
                          </Button>
                          {operation.kind === "export" &&
                            operation.state === "succeeded" && (
                              <Button
                                disabled={busy}
                                onClick={() =>
                                  action((signal) =>
                                    downloadOperation(
                                      studio.api,
                                      operation.id,
                                      signal,
                                      `laboratory-${operation.id}.zip`,
                                    ),
                                  )
                                }
                              >
                                Download {operation.id}
                              </Button>
                            )}
                          {operation.kind === "import" &&
                            operation.state === "succeeded" && (
                              <Button
                                disabled={busy}
                                onClick={() =>
                                  action(async (signal) => {
                                    const dismissal = transferDismissal.current;
                                    const full =
                                      await studio.api.get<LabOperation>(
                                        `/api/lab/operations/${enc(operation.id)}`,
                                        signal,
                                      );
                                    if (signal.aborted) return;
                                    setInspectedOperation(full);
                                    const result = full.result as
                                      | { definition?: SavedDefinition }
                                      | undefined;
                                    if (
                                      full.state !== "succeeded" ||
                                      !result?.definition
                                    )
                                      throw new Error(
                                        "This import has no completed saved definition.",
                                      );
                                    if (dismissal !== transferDismissal.current)
                                      return;
                                    setTransferOpen(false);
                                    load(result.definition);
                                    definitions.reload();
                                  })
                                }
                              >
                                Load imported definition {operation.id}
                              </Button>
                            )}
                        </div>
                      </div>
                    ),
                  )}
                  {!operations.loading && !operations.data?.length && (
                    <EmptyState>
                      No durable operations in this project.
                    </EmptyState>
                  )}
                  {inspectedOperation && (
                    <Evidence
                      label="Laboratory operation history"
                      data={inspectedOperation}
                    >
                      <p role="status">
                        Operation {inspectedOperation.id}:{" "}
                        {inspectedOperation.state}
                      </p>
                      <details open>
                        <summary>Retained operation evidence</summary>
                        <pre>{json(inspectedOperation)}</pre>
                      </details>
                    </Evidence>
                  )}
                </Panel>
              </section>
            </div>
          </div>
          <div className="lab-transfer-fade" aria-hidden="true">
            <i />
            <i />
            <i />
          </div>
        </div>
      </Modal>
      <SetupTemplate
        label="Laboratory workspace"
        className="lab-template"
        toolbar={
          <SegmentedControl
            kind="tabs"
            variant="underline"
            size="sm"
            aria-label="Laboratory work area"
            value={area}
            onChange={(next) => {
              if (next === "studies") setStudiesOpened(true);
              if (
                next === "experiment" &&
                creatingExperiment &&
                hasAuthoringEdits
              ) {
                setExperimentOpen(true);
                return;
              }
              if (next === "experiment") setCreatingExperiment(false);
              setArea(next);
            }}
            options={[
              {
                value: "experiment",
                label: "Experiment",
                controls: "lab-area-experiment",
              },
              { value: "runs", label: "Runs", controls: "lab-area-runs" },
              {
                value: "studies",
                label: "Studies",
                controls: "lab-area-studies",
              },
              {
                value: "capabilities",
                label: "Capabilities",
                controls: "lab-area-capabilities",
              },
            ]}
          />
        }
      >
        <div className="lab-main">
          <ExperimentSurface
            creating={creatingExperiment}
            open={experimentOpen}
            active={area === "experiment"}
            headingRef={laboratoryHeading}
            onClose={() => {
              setExperimentOpen(false);
              // Moving children from the creation modal into the saved page
              // remounts their local numeric/JSON drafts and selected files.
              // Promote only a clean saved revision; reopen edits in place.
              if (saved && !busy && !hasAuthoringEdits) {
                setCreatingExperiment(false);
                setArea("experiment");
              }
            }}
          >
            <div className="lab-experiment-meta">
              {!creatingExperiment && savedExperimentChoice}
              <Badge>
                {saved
                  ? `r${saved.revision} · ${dirty ? "Edited draft" : "Saved"}`
                  : "Unsaved draft"}
              </Badge>
            </div>
            {pendingDefinition && (
              <div role="alert" className="lab-switch-confirmation">
                <p>
                  {pendingDefinition === "new"
                    ? "Save or discard your edits before creating a new experiment."
                    : `Save or discard your edits before opening ${pendingDefinition.name}.`}
                </p>
                <Button
                  disabled={busy}
                  onClick={() => setPendingDefinition(null)}
                >
                  Keep editing
                </Button>
                <Button
                  disabled={busy}
                  onClick={() => {
                    if (busy) return;
                    if (pendingDefinition === "new") startNewExperiment();
                    else load(pendingDefinition);
                  }}
                >
                  {pendingDefinition === "new"
                    ? "Discard edits and create new"
                    : "Discard edits and open"}
                </Button>
              </div>
            )}
            <SegmentedControl
              kind="tabs"
              variant="tray"
              size="sm"
              aria-label="Experiment authoring view"
              value={view}
              onChange={(next) => {
                setView(next);
                if (next === "graph") setGraphVisited(true);
              }}
              options={[
                {
                  value: "parameters",
                  label: "Parameters",
                  controls: "lab-view-parameters",
                },
                {
                  value: "inputs",
                  label: "Inputs",
                  controls: "lab-view-inputs",
                },
                {
                  value: "execution",
                  label: "Execution",
                  controls: "lab-view-execution",
                },
                {
                  value: "graph",
                  label: "Visual topology",
                  controls: "lab-view-graph",
                },
                {
                  value: "json",
                  label: "Definition JSON",
                  controls: "lab-view-json",
                },
              ]}
            />
            <div className="lab-experiment-scroll-wrap">
              <div className="lab-experiment-fade" aria-hidden="true">
                <span style={{ opacity: scrollFade }} />
                <span style={{ opacity: scrollFade }} />
                <span style={{ opacity: scrollFade }} />
              </div>
              <div
                ref={authoringScroll}
                className="lab-experiment-scroll"
                onScroll={(event) =>
                  setScrollFade(Math.min(event.currentTarget.scrollTop / 24, 1))
                }
              >
                <fieldset
                  disabled={busy}
                  inert={busy}
                  className="lab-experiment-fields"
                >
                  <div className="lab-authoring-workspace">
                    <div
                      id="lab-view-graph"
                      role="tabpanel"
                      aria-label="Visual topology"
                      hidden={view !== "graph"}
                    >
                      {definition && graphVisited && (
                        <div className="lab-canvas-pane">
                          <DefinitionGraph
                            key={`canvas:${authoringGeneration}:${definition.engine}`}
                            definition={definition}
                            onChange={change}
                          />
                        </div>
                      )}
                    </div>
                    <div className="lab-configuration-pane">
                      <div
                        id="lab-view-parameters"
                        role="tabpanel"
                        aria-label="Parameters"
                        hidden={view !== "parameters"}
                      >
                        <SettingsSection
                          title="Engine"
                          description="Choose the adapter that powers this experiment."
                        >
                          <div className="lab-engine-bar">
                            <Choice
                              label="Experiment engine"
                              value={definition?.engine ?? ""}
                              options={engineOptions}
                              disabled={busy}
                              onChange={(engine) => {
                                if (busy) return;
                                const next = makeDefinition(engine);
                                if (definition)
                                  next.execution = { ...definition.execution };
                                const manifest = engineCatalog.find(
                                  (item) => item.id === engine,
                                );
                                if (manifest?.name) next.name = manifest.name;
                                if (manifest?.domain)
                                  next.domain = manifest.domain;
                                setDraft(json(next));
                                setAuthoringGeneration(
                                  (generation) => generation + 1,
                                );
                                setSaved(null);
                                setBaselineText("");
                              }}
                            />
                            <div className="lab-engine-status" role="status">
                              <strong>
                                {definition?.engine ?? "Invalid definition"}
                              </strong>
                              <p>
                                {capability
                                  ? `${capability.implemented ? "Implemented" : "Not implemented"} · ${capability.available ? "Available on catalog host" : "Prerequisite unavailable on catalog host"}`
                                  : "Capability not reported for this engine."}
                              </p>
                              <details>
                                <summary>Engine requirements</summary>
                                {capability?.reason && (
                                  <p>{capability.reason}</p>
                                )}
                                {template?.hint && <p>{template.hint}</p>}
                                <small>
                                  Execution target:{" "}
                                  {selectedTarget?.name ?? target}. Catalog host
                                  availability does not establish target
                                  qualification.
                                </small>
                              </details>
                            </div>
                          </div>
                        </SettingsSection>
                        {definition && (
                          <SettingsSection
                            title="Identity"
                            description="Name and reproducibility settings for the saved definition."
                          >
                            <div className="lab-fields lab-identity-fields">
                              <Input
                                label="Experiment name"
                                value={definition.name}
                                onChange={(name) => change({ name })}
                              />
                              <Input
                                label="Seed"
                                type="number"
                                value={String(definition.seed)}
                                onChange={(value) =>
                                  change({ seed: Number(value) })
                                }
                              />
                              <Input
                                label="Domain"
                                value={definition.domain}
                                onChange={(domain) => change({ domain })}
                              />
                              {definition.engine !== "data.pipeline" && (
                                <Input
                                  label="Mode"
                                  value={definition.mode}
                                  onChange={(mode) => change({ mode })}
                                />
                              )}
                            </div>
                          </SettingsSection>
                        )}
                        {definition && !structuredEngine && (
                          <SettingsSection
                            title="Engine parameters"
                            className="lab-engine-parameters"
                            description="Configure the adapter for this experiment."
                          >
                            <div className="lab-fields">
                              {definition.engine === "evaluation.compare" && (
                                <>
                                  <Choice
                                    label="Evaluation operation"
                                    value={String(
                                      definition.parameters.operation ??
                                        "precomputed",
                                    )}
                                    options={[
                                      {
                                        id: "precomputed",
                                        name: "Score retained predictions",
                                      },
                                      {
                                        id: "generate",
                                        name: "Generate and evaluate",
                                      },
                                      {
                                        id: "matrix",
                                        name: "Compare methods on shared cases",
                                      },
                                    ]}
                                    onChange={(operation) =>
                                      change({
                                        parameters: evaluationParameters(
                                          definition.parameters,
                                          operation,
                                        ),
                                      })
                                    }
                                  />
                                  {definition.parameters.operation ===
                                    "matrix" && (
                                    <MethodMatrixEditor
                                      definition={definition}
                                      onChange={(parameters) =>
                                        change({ parameters })
                                      }
                                    />
                                  )}
                                  {definition.parameters.operation ===
                                    "generate" && (
                                    <>
                                      <Choice
                                        label="Retrieval comparison"
                                        value={
                                          definition.parameters
                                            .compare_retrieval === true
                                            ? "paired"
                                            : "single"
                                        }
                                        options={[
                                          {
                                            id: "single",
                                            name: "Selected model and optional retrieval",
                                          },
                                          {
                                            id: "paired",
                                            name: "Same model with and without retrieval",
                                          },
                                        ]}
                                        onChange={(value) =>
                                          change({
                                            parameters: {
                                              ...definition.parameters,
                                              compare_retrieval:
                                                value === "paired",
                                            },
                                          })
                                        }
                                      />
                                      <p className="muted">
                                        Attach a frozen model and held-out cases
                                        in Inputs. Numeric scoring uses the
                                        entire completion; malformed outputs
                                        remain failed cases. Context overflow
                                        fails instead of dropping evidence. A
                                        retrieval index requires its original
                                        encoder for semantic or hybrid modes.
                                      </p>
                                    </>
                                  )}
                                </>
                              )}
                              {definition?.engine === "learning.pretrained" && (
                                <>
                                  <Choice
                                    label="Learning objective"
                                    value={pretrainedAxes(definition).objective}
                                    options={[
                                      {
                                        id: "sft",
                                        name: "Supervised conversations",
                                      },
                                      {
                                        id: "causal_lm",
                                        name: "Continued pretraining · raw text",
                                      },
                                      {
                                        id: "dpo",
                                        name: "Preference learning · DPO",
                                      },
                                    ]}
                                    onChange={(objective) =>
                                      change(
                                        editPretrainedAxes(definition, {
                                          objective,
                                        }),
                                      )
                                    }
                                  />
                                  <Choice
                                    label="Weight updates"
                                    value={
                                      pretrainedAxes(definition).update_strategy
                                    }
                                    options={["full", "lora", "qlora"].map(
                                      (id) => ({ id, name: id }),
                                    )}
                                    onChange={(update_strategy) =>
                                      change(
                                        editPretrainedAxes(definition, {
                                          update_strategy,
                                        }),
                                      )
                                    }
                                  />
                                  <p className="text-text-secondary">
                                    {pretrainedAxes(definition).objective ===
                                    "causal_lm"
                                      ? "Snapshot rows use exact text strings. Tokenization appends EOS if absent, then truncates on the right; no chat template is applied."
                                      : pretrainedAxes(definition).objective ===
                                          "dpo"
                                        ? "Every row needs a prompt, a final assistant answer and a distinct rejected answer. Full updates use a separate frozen reference model."
                                        : "Snapshot rows use conversational messages. The retained model tokenizer supplies the chat template."}
                                    {
                                      " Use a runtime qualification probe to measure NF4 training and reload on the selected target."
                                    }
                                  </p>
                                </>
                              )}
                              {[
                                "learning.language_rl",
                                "learning.control_rl",
                                "learning.tool_rl",
                              ].includes(definition?.engine ?? "") &&
                                definition && (
                                  <Choice
                                    label="Learning objective"
                                    value={String(
                                      definition.learning?.objective ?? "ppo",
                                    )}
                                    options={(definition.engine ===
                                    "learning.language_rl"
                                      ? ["ppo", "grpo"]
                                      : definition.engine ===
                                          "learning.control_rl"
                                        ? ["ppo", "dqn", "behavioral_cloning"]
                                        : [
                                            "grpo_tool_bandit",
                                            "ppo_tool_bandit",
                                          ]
                                    ).map((id) => ({ id, name: id }))}
                                    onChange={(objective) =>
                                      change({
                                        learning: {
                                          ...definition.learning,
                                          objective,
                                        },
                                      })
                                    }
                                  />
                                )}
                              {definition?.engine === "retrieval.index" && (
                                <>
                                  <Choice
                                    label="Retrieval operation"
                                    value={String(
                                      definition.parameters.operation ??
                                        "build",
                                    )}
                                    options={[
                                      { id: "build", name: "Build an index" },
                                      {
                                        id: "query",
                                        name: "Query a retained index",
                                      },
                                    ]}
                                    onChange={(operation) =>
                                      change({
                                        parameters: {
                                          ...definition.parameters,
                                          operation,
                                        },
                                      })
                                    }
                                  />
                                  <Choice
                                    label="Retrieval mode"
                                    value={String(
                                      definition.parameters.mode ??
                                        definition.retrieval?.mode ??
                                        (definition.parameters.operation ===
                                        "query"
                                          ? "inherited"
                                          : "lexical"),
                                    )}
                                    options={[
                                      ...(definition.parameters.operation ===
                                      "query"
                                        ? [
                                            {
                                              id: "inherited",
                                              name: "From retained index",
                                            },
                                          ]
                                        : []),
                                      ...[
                                        "lexical",
                                        "structured",
                                        "semantic",
                                        "hybrid",
                                        "episode",
                                      ].map((id) => ({ id, name: id })),
                                    ]}
                                    onChange={(mode) => {
                                      const parameters = {
                                        ...definition.parameters,
                                      };
                                      const retrieval = {
                                        ...definition.retrieval,
                                      };
                                      if (mode === "inherited") {
                                        delete parameters.mode;
                                        delete retrieval.mode;
                                      } else {
                                        parameters.mode = mode;
                                        if ("mode" in retrieval)
                                          retrieval.mode = mode;
                                      }
                                      change({
                                        parameters,
                                        ...(definition.retrieval
                                          ? { retrieval }
                                          : {}),
                                      });
                                    }}
                                  />
                                  {definition.parameters.operation ===
                                    "query" && (
                                    <p className="muted">
                                      Attach a retained index in Inputs. Its
                                      corpus stays unchanged. Semantic and
                                      hybrid indexes also require their original
                                      embedding model. Remove fresh corpus
                                      inputs before checking compatibility.
                                    </p>
                                  )}
                                </>
                              )}
                              {Object.entries(
                                definition.engine === "data.pipeline"
                                  ? {}
                                  : definition.parameters,
                              )
                                .filter(
                                  ([key]) =>
                                    (definition.engine !== "retrieval.index" ||
                                      !["mode", "operation"].includes(key)) &&
                                    (definition.engine !==
                                      "evaluation.compare" ||
                                      ![
                                        "operation",
                                        "compare_retrieval",
                                        "methods",
                                        "baseline_method",
                                      ].includes(key)),
                                )
                                .map(([key, value]) =>
                                  draftParameterEngine &&
                                  (typeof value === "number" ||
                                    (value !== null &&
                                      typeof value === "object")) ? (
                                    <DraftField
                                      key={`${authoringGeneration}:${definition.engine}:${key}`}
                                      fieldKey={`${authoringGeneration}:${definition.engine}:${key}`}
                                      label={key.replaceAll("_", " ")}
                                      value={value}
                                      kind={
                                        typeof value === "number"
                                          ? "number"
                                          : Array.isArray(value)
                                            ? "array"
                                            : "object"
                                      }
                                      report={reportParameter}
                                      onChange={(next) =>
                                        change({
                                          parameters: {
                                            ...definition.parameters,
                                            [key]: next,
                                          },
                                        })
                                      }
                                    />
                                  ) : draftParameterEngine &&
                                    typeof value === "boolean" ? (
                                    <Choice
                                      key={key}
                                      label={key.replaceAll("_", " ")}
                                      value={String(value)}
                                      options={[
                                        { id: "true", name: "Enabled" },
                                        { id: "false", name: "Disabled" },
                                      ]}
                                      onChange={(next) =>
                                        change({
                                          parameters: {
                                            ...definition.parameters,
                                            [key]: next === "true",
                                          },
                                        })
                                      }
                                    />
                                  ) : (
                                    <Input
                                      key={key}
                                      label={key.replaceAll("_", " ")}
                                      value={
                                        typeof value === "object"
                                          ? JSON.stringify(value)
                                          : String(value ?? "")
                                      }
                                      onChange={(next) => {
                                        let parsed: unknown = next;
                                        if (typeof value === "number")
                                          parsed = Number(next);
                                        else if (
                                          typeof value === "object" ||
                                          typeof value === "boolean"
                                        ) {
                                          try {
                                            parsed = JSON.parse(next);
                                          } catch {
                                            return;
                                          }
                                        }
                                        change({
                                          parameters: {
                                            ...definition!.parameters,
                                            [key]: parsed,
                                          },
                                        });
                                      }}
                                    />
                                  ),
                                )}
                              <p className="lab-wide text-text-secondary">
                                Use Definition JSON for advanced adapter
                                settings.
                              </p>
                            </div>
                          </SettingsSection>
                        )}
                        {definition?.engine === "learning.language" && (
                          <LanguageLearning
                            key={`${authoringGeneration}:language`}
                            definition={definition}
                            onChange={change}
                            onValidityChange={setLanguageLearningValid}
                          />
                        )}
                        {definition?.engine === "learning.control_rl" && (
                          <ControlLearning
                            key={`${authoringGeneration}:control`}
                            definition={definition}
                            onChange={change}
                            onValidityChange={setControlLearningValid}
                          />
                        )}
                        {definition &&
                          [
                            "learning.timeseries",
                            "learning.multimodal",
                          ].includes(definition.engine) && (
                            <NumericLearning
                              key={`${authoringGeneration}:numeric:${definition.engine}`}
                              definition={definition}
                              onChange={change}
                              onValidityChange={setNumericLearningValid}
                            />
                          )}
                        {definition?.engine === "statistics.plan" && (
                          <StatisticsPlanning
                            key={`${authoringGeneration}:statistics`}
                            definition={definition}
                            onChange={change}
                            onValidityChange={setStatisticsPlanningValid}
                          />
                        )}
                        {definition?.engine === "runtime.qualify" && (
                          <RuntimeProbeFields
                            definition={definition}
                            onChange={change}
                            methods={catalog.data?.runtime_methods?.methods}
                          />
                        )}
                        {definition?.engine === "hardware.discover" && (
                          <HardwareDiscoveryFields
                            key={`${authoringGeneration}:hardware-discovery`}
                            definition={definition}
                            onChange={change}
                            onValidityChange={setHardwareDiscoveryValid}
                            methods={
                              catalog.data?.hardware_discovery_methods?.methods
                            }
                          />
                        )}
                        {definition?.engine === "data.pipeline" && (
                          <DataPreparation
                            key={`${authoringGeneration}:data`}
                            definition={definition}
                            onChange={change}
                            onValidityChange={setDataPreparationValid}
                          />
                        )}
                      </div>
                      <div
                        id="lab-view-json"
                        role="tabpanel"
                        aria-label="Definition JSON"
                        hidden={view !== "json"}
                      >
                        <Textarea
                          label="Experiment definition JSON"
                          value={draft}
                          onChange={setDraft}
                          rows={20}
                        />
                      </div>
                      {definition && (
                        <section
                          id="lab-view-execution"
                          role="tabpanel"
                          aria-label="Execution"
                          hidden={view !== "execution"}
                          className="lab-execution"
                        >
                          <SettingsSection
                            title="Destination"
                            description="Choose where and when to run this revision."
                          >
                            <div className="lab-fields">
                              <Choice
                                label="Execution target"
                                value={target}
                                onChange={setTarget}
                                options={(targets.data ?? []).map((t) => ({
                                  id: t.id,
                                  name: t.name ?? t.id,
                                }))}
                              />
                              <Input
                                label="Start after"
                                type="datetime-local"
                                value={notBefore}
                                onChange={setNotBefore}
                                hint="Optional. Leave empty to run immediately. Uses your local time zone."
                              />
                            </div>
                          </SettingsSection>
                          <SettingsSection
                            title="Runtime"
                            description="Select the execution profile and backend."
                          >
                            <div className="lab-fields">
                              <Choice
                                label="Execution profile"
                                value={definition.execution.profile}
                                options={[
                                  {
                                    id: "trusted_native",
                                    name: "Trusted native",
                                  },
                                  { id: "isolated", name: "Isolated" },
                                ]}
                                onChange={(profile) =>
                                  change({
                                    execution: {
                                      ...definition.execution,
                                      profile,
                                    },
                                  })
                                }
                              />
                              <Choice
                                label="Execution backend"
                                value={definition.execution.backend ?? "native"}
                                options={[
                                  "native",
                                  "container",
                                  "kubernetes",
                                  "rq",
                                ].map((id) => ({ id, name: id }))}
                                onChange={(backend) =>
                                  change({
                                    execution: {
                                      ...definition.execution,
                                      backend,
                                    },
                                  })
                                }
                              />
                            </div>
                          </SettingsSection>
                          <SettingsSection
                            title="Limits"
                            description="Bound execution time and retained output size."
                          >
                            <div className="lab-fields">
                              <Input
                                label="Maximum execution seconds"
                                type="number"
                                value={String(
                                  definition.execution.max_seconds ?? 300,
                                )}
                                onChange={(value) => {
                                  if (
                                    Number.isInteger(Number(value)) &&
                                    Number(value) > 0
                                  )
                                    change({
                                      execution: {
                                        ...definition.execution,
                                        max_seconds: Number(value),
                                      },
                                    });
                                }}
                              />
                              <Input
                                label="Maximum output bytes"
                                type="number"
                                value={String(
                                  definition.execution.max_output_bytes ??
                                    536870912,
                                )}
                                onChange={(value) => {
                                  if (
                                    Number.isInteger(Number(value)) &&
                                    Number(value) > 0
                                  )
                                    change({
                                      execution: {
                                        ...definition.execution,
                                        max_output_bytes: Number(value),
                                      },
                                    });
                                }}
                              />
                            </div>
                          </SettingsSection>
                          <p>
                            The chosen profile/backend is saved without
                            fallback. Runner-owned capabilities and native
                            grants are checked on the selected target.
                          </p>
                          <details>
                            <summary>
                              Selected target profile capabilities
                            </summary>
                            <pre>
                              {json(
                                selectedTarget?.profile ??
                                  selectedTarget?.capabilities ?? {
                                    status: "not_reported",
                                    target_id: target,
                                  },
                              )}
                            </pre>
                          </details>
                        </section>
                      )}
                      {definition && (
                        <section
                          id="lab-view-inputs"
                          role="tabpanel"
                          aria-label="Inputs"
                          hidden={view !== "inputs"}
                        >
                          <InputArtifacts
                            active={
                              view === "inputs" &&
                              (creatingExperiment
                                ? experimentOpen
                                : area === "experiment")
                            }
                            key={`${authoringGeneration}:${definition.engine}`}
                            definition={definition}
                            onChange={(inputs) => change({ inputs })}
                            onPendingChange={setPendingInputWork}
                            signal={() => lifetime.current?.signal}
                          />
                        </section>
                      )}
                    </div>
                  </div>
                  {parseError && <Fault error={parseError} />}
                  {conflict && (
                    <div role="alert">
                      <p>
                        The saved revision changed. Your draft is preserved.
                        Reload the latest revision to compare before saving
                        again.
                      </p>
                      <Button
                        disabled={busy}
                        onClick={() =>
                          action(async (signal) => {
                            const rows = await studio.api.get<
                              SavedDefinition[]
                            >("/api/lab/definitions", signal);
                            if (signal.aborted) return;
                            const latest = rows.find(
                              (row) => row.id === saved?.id,
                            );
                            if (!latest)
                              throw new Error(
                                "This definition no longer exists. Save your draft as a new experiment.",
                              );
                            setAuthoringGeneration(
                              (generation) => generation + 1,
                            );
                            setSaved(latest);
                            setDraft(json(latest.definition));
                            setBaselineText(json(latest.definition));
                            setConflict(false);
                            definitions.reload();
                          })
                        }
                      >
                        Load latest saved revision
                      </Button>
                      <Button
                        onClick={() => {
                          setSaved(null);
                          setConflict(false);
                        }}
                      >
                        Save draft as new experiment
                      </Button>
                    </div>
                  )}
                  <Fault
                    error={
                      error ||
                      catalog.error ||
                      definitions.error ||
                      targets.error
                    }
                  />
                  {preflight != null &&
                    preflight.draft === draft &&
                    preflight.target === target && (
                      <Evidence
                        label="Laboratory compatibility check"
                        data={preflight.result}
                      >
                        <details open>
                          <summary>Compatibility result</summary>
                          <pre>{json(preflight.result)}</pre>
                        </details>
                      </Evidence>
                    )}
                </fieldset>
              </div>
            </div>
            <div className="lab-actions lab-primary-actions">
              <Button
                variant="primary"
                disabled={busy || !!parseError || !authoringValid}
                onClick={save}
              >
                {saved ? "Save revision" : "Save experiment"}
              </Button>
              <Button
                disabled={busy || !!parseError || !authoringValid || !target}
                onClick={() =>
                  action(async (signal) => {
                    const result = await studio.api.post(
                      "/api/lab/preflight",
                      {
                        definition: parseDefinition(draft),
                        target_id: target,
                      },
                      signal,
                    );
                    if (!signal.aborted)
                      setPreflight({ draft, target, result });
                  })
                }
              >
                Check compatibility
              </Button>
              <Button
                disabled={busy || dirty || !saved || !target || !authoringValid}
                onClick={() =>
                  action(async (signal) => {
                    const run = await studio.api.post<LabRun>(
                      "/api/lab/runs",
                      {
                        definition_id: saved!.id,
                        revision: saved!.revision,
                        target_id: target,
                        idempotency_key: crypto.randomUUID(),
                        ...(notBefore
                          ? { not_before: new Date(notBefore).toISOString() }
                          : {}),
                      },
                      signal,
                    );
                    if (!signal.aborted) {
                      selectRun(run.id);
                      setArea("runs");
                      setExperimentOpen(false);
                      runs.reload();
                    }
                  })
                }
              >
                Run saved revision
              </Button>
            </div>
            <p className="lab-run-readiness" role="status">
              {busy
                ? "Laboratory request in progress."
                : !saved
                  ? "Save the experiment before running."
                  : dirty
                    ? "Save your edited revision before running."
                    : !target
                      ? "Choose an execution target before running."
                      : "Saved revision ready to submit to the selected target."}
              {preflight &&
                (preflight.draft !== draft || preflight.target !== target) &&
                " Compatibility check is stale. Recheck the current draft and target."}
            </p>
          </ExperimentSurface>
          <section
            id="lab-area-runs"
            role="tabpanel"
            aria-label="Runs"
            hidden={area !== "runs"}
          >
            <div className="lab-runs-layout">
              {!selectedRun && runSelector}
              <div className="lab-run-detail">
                {!selectedRun && (
                  <div className="lab-runs-empty">
                    <h2>
                      {runs.data?.length
                        ? "Select a run"
                        : "Run your first experiment"}
                    </h2>
                    <p>
                      {runs.data?.length
                        ? "Choose a retained run to inspect its evidence or export a portable bundle."
                        : "Configure an experiment, save a revision, and run it to collect evidence."}
                    </p>
                    <Button
                      onClick={() => {
                        if (creatingExperiment && hasAuthoringEdits) {
                          setExperimentOpen(true);
                          return;
                        }
                        setCreatingExperiment(false);
                        setArea("experiment");
                      }}
                    >
                      Configure experiment
                    </Button>
                  </div>
                )}
                {selectedRun && (
                  <RunEvidence
                    key={selectedRun}
                    id={selectedRun}
                    active={area === "runs"}
                    selector={runSelector}
                    onChanged={runs.reload}
                    onOperationChanged={operations.reload}
                  />
                )}
              </div>
            </div>
          </section>
          <section
            id="lab-area-studies"
            role="tabpanel"
            aria-label="Studies"
            hidden={area !== "studies"}
          >
            {studiesOpened && <Studies />}
          </section>
          <section
            id="lab-area-capabilities"
            role="tabpanel"
            aria-label="Capabilities"
            hidden={area !== "capabilities"}
          >
            <Panel title="Capability catalog" className="lab-catalog-panel">
              <p className="text-text-secondary">
                Portfolio capabilities remain visible even when an adapter
                cannot run on this target.
              </p>
              {catalog.loading && <p role="status">Loading capabilities…</p>}
              <DataTable<any>
                label="Engine capabilities"
                assistantActionsEnabled={area === "capabilities"}
                rows={catalog.data?.adapters ?? []}
                getRowId={(adapter) => adapter.id}
                scopeKey={studio.scopeKey}
                columns={[
                  {
                    id: "adapter",
                    label: "Adapter",
                    value: (adapter) => adapter.name ?? adapter.id,
                    render: (adapter) => (
                      <>
                        {adapter.name ?? adapter.id}
                        <small>{adapter.id}</small>
                      </>
                    ),
                  },
                  {
                    id: "domain",
                    label: "Domain",
                    value: (adapter) => adapter.domain,
                  },
                  {
                    id: "status",
                    label: "Status",
                    value: (adapter) =>
                      !adapter.implemented
                        ? "Planned"
                        : adapter.available
                          ? "Available"
                          : "Unavailable",
                    render: (adapter) => (
                      <>
                        {!adapter.implemented
                          ? "Planned"
                          : adapter.available
                            ? "Available"
                            : "Unavailable"}
                        {adapter.reason && <small>{adapter.reason}</small>}
                      </>
                    ),
                  },
                  {
                    id: "modes",
                    label: "Modes / version",
                    value: (adapter) => adapter.modes?.join(", "),
                    render: (adapter) => (
                      <>
                        {adapter.modes?.join(", ")}
                        <small>{adapter.version}</small>
                      </>
                    ),
                  },
                ]}
              />
              <details>
                <summary>Domains and methods</summary>
                <pre>
                  {json({
                    domains: catalog.data?.domains,
                    methods: catalog.data?.methods,
                  })}
                </pre>
              </details>
            </Panel>
          </section>
        </div>
      </SetupTemplate>
    </Page>
  );
}
export function runSelectorLabel(run: LabRun, runs: LabRun[]): string {
  const name = (run.spec as any)?.definition?.name;
  let length = run.id.length <= 24 ? run.id.length : 8;
  while (
    length < run.id.length &&
    runs.some(
      (other) =>
        other.id !== run.id &&
        other.id.slice(-length) === run.id.slice(-length),
    )
  )
    length++;
  const identity =
    length < run.id.length ? `…${run.id.slice(-length)}` : run.id;
  return `${typeof name === "string" && name.trim() ? `${name} · ` : ""}${identity} · ${run.state}`;
}

function RunEvidence({
  selector,
  id,
  active,
  onChanged,
  onOperationChanged,
}: {
  selector?: ReactNode;
  id: string;
  active: boolean;
  onChanged: () => void;
  onOperationChanged: () => void;
}) {
  const studio = useStudio();
  const [run, setRun] = useState<LabRun | null>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [requestedArtifact, setArtifact] = useState("processed.jsonl");
  const [runView, setRunView] = useState("replay");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const retained = run ? dataArtifacts(run) : undefined;
  const statisticsRun =
    (run?.spec as any)?.definition?.engine === "statistics.plan" ||
    retained?.all.some((item) => item.role === "statistical_plan");
  const analysisRun =
    (run?.spec as any)?.definition?.engine === "statistics.analyze" ||
    retained?.all.some((item) => item.role === "statistical_analysis");
  const runtimeProbeRun =
    (run?.spec as any)?.definition?.engine === "runtime.qualify" ||
    retained?.all.some((item) => item.role === "runtime_probe");
  const hardwareDiscoveryRun =
    (run?.spec as any)?.definition?.engine === "hardware.discover" ||
    retained?.all.some((item) => item.role === "device_discovery");
  const recordArtifacts =
    analysisRun || runtimeProbeRun || hardwareDiscoveryRun
      ? []
      : (retained?.all.filter((item) => item.name.endsWith(".jsonl")) ?? []);
  const artifact = recordArtifacts.some(
    (item) => item.name === requestedArtifact,
  )
    ? requestedArtifact
    : (retained?.derived?.name ??
      retained?.original?.name ??
      recordArtifacts[0]?.name ??
      requestedArtifact);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [pollError, setPollError] = useState<unknown>(null);
  const [recovery, setRecovery] = useState<unknown>(null);
  const cursor = useRef(0);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [exportOperation, setExportOperation] = useState<LabOperation | null>(
    null,
  );
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    let timer: ReturnType<typeof setTimeout>;
    let after = cursor.current;
    let failures = 0;
    setPollError(null);
    const poll = async () => {
      try {
        // A terminal status must precede the event snapshot; concurrent reads can
        // pair a pre-completion timeline with post-completion status and stop early.
        const row = await studio.api.get<LabRun>(
          `/api/lab/runs/${enc(id)}`,
          abort.signal,
        );
        if (abort.signal.aborted) return;
        const timeline = await studio.api.get<any>(
          `/api/lab/runs/${enc(id)}/events?after=${after}`,
          abort.signal,
        );
        if (abort.signal.aborted) return;
        setRun(row);
        setEvents((old) => [
          ...old,
          ...timeline.events.filter(
            (e: any) => !old.some((item) => item.seq === e.seq),
          ),
        ]);
        const next = timeline.next_after ?? after;
        const hasMore =
          timeline.has_more === true ||
          (typeof timeline.latest_seq === "number" &&
            timeline.latest_seq > next);
        if (hasMore && next <= after)
          throw new Error(
            "Event history did not advance. Refresh evidence to reconnect.",
          );
        after = next;
        cursor.current = after;
        failures = 0;
        setPollError(null);
        if (hasMore) timer = setTimeout(poll, 0);
        else if (!isTerminal(row.state)) timer = setTimeout(poll, 1200);
        else onChanged();
      } catch (e: any) {
        if (abort.signal.aborted || e.name === "AbortError") return;
        const retryable =
          e instanceof TypeError ||
          e.name === "NetworkError" ||
          e.status === 429 ||
          e.status >= 500;
        if (retryable && failures < 5) {
          setPollError(
            new Error(
              `Evidence connection interrupted; reconnecting (${failures + 1}/5). ${e.message}`,
            ),
          );
          timer = setTimeout(poll, Math.min(1200 * 2 ** failures, 15000));
          failures++;
        } else
          setPollError(
            new Error(
              `${e.message}${retryable ? " Automatic reconnect stopped. Refresh evidence to try again." : ""}`,
            ),
          );
      }
    };
    void poll();
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [studio.api, id, refresh, onChanged]);
  // Each artifact owns its retrieval state: a missing counterpart must not
  // erase successfully retained observations or independent run evidence.
  const recordsAvailable =
    !!run &&
    !analysisRun &&
    !runtimeProbeRun &&
    !hardwareDiscoveryRun &&
    isTerminal(run.state);
  const recordPath = (name: string, start: number, limit: number) =>
    recordsAvailable
      ? `/api/lab/runs/${enc(id)}/records?offset=${start}&limit=${limit}&artifact=${enc(name)}`
      : null;
  type RecordWindow = {
    records: any[];
    next_offset?: number | null;
    artifact?: { id: string; name: string };
  };
  const page = useResource<RecordWindow>(recordPath(artifact, offset, 100));
  const processedName = retained?.derived?.name ?? "processed.jsonl";
  const originalName = retained?.original?.name ?? "raw.jsonl";
  const processed = useResource<RecordWindow>(
    recordPath(processedName, 0, 1000),
  );
  const original = useResource<RecordWindow>(recordPath(originalName, 0, 1000));
  useEffect(() => {
    if (!refresh) return;
    page.reload();
    processed.reload();
    original.reload();
  }, [refresh, page.reload, processed.reload, original.reload]);
  const recordSource = (
    label: string,
    name: string,
    resource: typeof processed,
  ): ReplayRecordSource => {
    if (!recordsAvailable) return { label, status: "waiting" };
    if (resource.loading) return { label, status: "loading" };
    if (resource.error)
      return { label, status: "error", error: resource.error };
    const response = resource.data;
    if (!Array.isArray(response?.records))
      return {
        label,
        status: "error",
        error: new Error(
          "The record response did not contain a records array.",
        ),
      };
    const declared = retained?.all.find((item) => item.name === name);
    if (
      response.artifact &&
      (response.artifact.name !== name ||
        typeof response.artifact.id !== "string" ||
        (declared && response.artifact.id !== declared.id))
    )
      return {
        label,
        status: "error",
        error: new Error(
          "The record response belongs to a different retained artifact.",
        ),
      };
    // The service returns an empty, unqualified window when the name is absent.
    // Older retained responses may omit identity; accept those only when the run
    // manifest establishes it or the response carries actual observations.
    if (!response.artifact && !declared && !response.records.length)
      return { label, status: "missing" };
    return { label, status: "ready" };
  };
  const pageSource = recordSource("Retained records", artifact, page);
  const processedSource = recordSource(
    "Processed records",
    processedName,
    processed,
  );
  const originalSource = recordSource(
    "Original records",
    originalName,
    original,
  );
  const pageError = pageSource.error;
  const records = pageSource.status === "ready" ? page.data!.records : [];
  const nextOffset =
    pageSource.status === "ready" ? (page.data?.next_offset ?? null) : null;
  const chartRecords =
    processedSource.status === "ready" ? processed.data!.records : [];
  const rawRecords =
    originalSource.status === "ready" ? original.data!.records : [];
  const command = async (operation: "cancel" | "retry" | "recover") => {
    const signal = controller.current?.signal;
    if (!signal || signal.aborted || busy) return;
    setBusy(true);
    setError(null);
    try {
      const response = await studio.api.post(
        operation === "recover"
          ? `/api/lab/targets/${enc(run!.target_id!)}/recover`
          : `/api/lab/runs/${enc(id)}/${operation}`,
        {},
        signal,
      );
      if (!signal.aborted) {
        if (operation === "recover") setRecovery(response);
        setRefresh((v) => v + 1);
        onChanged();
      }
    } catch (e: any) {
      if (!signal.aborted && e.name !== "AbortError") setError(e);
    } finally {
      if (!signal.aborted) setBusy(false);
    }
  };
  return (
    <Panel className="lab-run-evidence-panel" aria-label="Run evidence">
      <RunTemplate label="Retained run evidence">
        <Fault error={error} />
        <Fault error={pollError} />
        <div className="lab-run-toolbar">
          {selector}
          <Badge>{run?.state ?? "Loading"}</Badge>
          <Button size="sm" onClick={() => setDetailsOpen(true)}>
            Run details
          </Button>
          <details className="records-command-menu">
            <summary>Run actions</summary>
            <div className="records-command-options">
              <Button
                disabled={busy || !run || isTerminal(run.state)}
                onClick={() => command("cancel")}
              >
                Cancel run
              </Button>
              <Button
                disabled={
                  busy ||
                  !run ||
                  !["failed", "cancelled", "canceled", "lost"].includes(
                    run.state,
                  )
                }
                onClick={() => command("retry")}
              >
                Retry run
              </Button>
              <Button
                disabled={busy || run?.state !== "running" || !run?.target_id}
                onClick={() => command("recover")}
              >
                Recover expired runner
              </Button>
              <Button disabled={busy} onClick={() => setRefresh((v) => v + 1)}>
                Refresh evidence
              </Button>
              {run && (
                <Button
                  disabled={busy || !isTerminal(run.state)}
                  onClick={async () => {
                    const signal = controller.current?.signal;
                    if (!signal || signal.aborted || busy) return;
                    setBusy(true);
                    setError(null);
                    try {
                      const submitted = await studio.api.post<LabOperation>(
                        `/api/lab/runs/${enc(id)}/exports`,
                        { idempotency_key: crypto.randomUUID() },
                        signal,
                      );
                      onOperationChanged();
                      try {
                        const operation = await waitForOperation(
                          studio.api,
                          submitted,
                          signal,
                          setExportOperation,
                        );
                        await downloadOperation(
                          studio.api,
                          operation.id,
                          signal,
                          `laboratory-${id}.zip`,
                        );
                      } finally {
                        if (!signal.aborted) onOperationChanged();
                      }
                    } catch (e: any) {
                      if (!signal.aborted && e.name !== "AbortError")
                        setError(e);
                    } finally {
                      if (!signal.aborted) setBusy(false);
                    }
                  }}
                >
                  Export manifest bundle
                </Button>
              )}
            </div>
          </details>
        </div>
        <Modal
          open={detailsOpen}
          title="Run details"
          onClose={() => setDetailsOpen(false)}
          className="lab-run-details-dialog"
        >
          <div className="lab-transfer-viewport">
            <div
              className="lab-transfer-scroll"
              onScroll={(event) =>
                event.currentTarget.parentElement?.style.setProperty(
                  "--lab-transfer-fade",
                  String(Math.min(1, event.currentTarget.scrollTop / 24)),
                )
              }
            >
              <div className="lab-run-summary">
                <Badge>{run?.state ?? "Loading"}</Badge>
                <span>
                  <strong>Run:</strong> {id}
                </span>
                <span>
                  <strong>Start after:</strong>{" "}
                  {run?.spec?.schedule?.not_before ?? "Immediately"}
                </span>
                <details>
                  <summary>Specification digest</summary>
                  <code>
                    {run?.spec_digest ?? "Awaiting retained specification"}
                  </code>
                </details>
              </div>
              <dl>
                <dt>Definition ID</dt>
                <dd>{run?.definition_id ?? "Loading"}</dd>
                <dt>Target ID</dt>
                <dd>{run?.target_id ?? "Not recorded"}</dd>
                <dt>Created</dt>
                <dd>{run?.created ?? "Not recorded"}</dd>
              </dl>
              <h3>Result and attempts</h3>
              <pre>
                {json({ result: run?.result, attempts: run?.attempts })}
              </pre>
            </div>
            <div className="lab-transfer-fade" aria-hidden="true">
              <i />
              <i />
              <i />
            </div>
          </div>
        </Modal>
        {busy && <p role="status">Run operation in progress…</p>}
        {recovery != null && (
          <Evidence label="Laboratory runner recovery" data={recovery}>
            <details open>
              <summary>Runner recovery result</summary>
              <pre>{json(recovery)}</pre>
            </details>
          </Evidence>
        )}
        {exportOperation && (
          <Evidence label="Laboratory export operation" data={exportOperation}>
            <p role="status">Export: {exportOperation.state}</p>
          </Evidence>
        )}
        {run?.error != null && <pre role="alert">{json(run.error)}</pre>}
        {run && <RecoveredEvidencePanel run={run} />}
        <SegmentedControl
          kind="tabs"
          variant="tray"
          size="sm"
          aria-label="Run evidence view"
          value={runView}
          onChange={setRunView}
          options={[
            {
              value: "replay",
              label: statisticsRun
                ? "Planning"
                : analysisRun
                  ? "Analysis"
                  : runtimeProbeRun
                    ? "Runtime probe"
                    : hardwareDiscoveryRun
                      ? "Discovery"
                      : "Replay",
              controls: "lab-run-view-replay",
            },
            {
              value: "records",
              label: "Records",
              controls: "lab-run-view-records",
            },
            {
              value: "events",
              label: "Events",
              controls: "lab-run-view-events",
            },
            {
              value: "dataset",
              label: "Dataset",
              controls: "lab-run-view-dataset",
            },
          ]}
        />
        <section
          id="lab-run-view-events"
          role="tabpanel"
          aria-label="Events"
          hidden={runView !== "events"}
        >
          <Evidence
            label="Laboratory run and event lineage"
            data={{ run, events }}
          >
            <details>
              <summary>Result and attempts</summary>
              <pre>
                {json({ result: run?.result, attempts: run?.attempts })}
              </pre>
            </details>
            <h3>Event timeline</h3>
            <SimpleTable<any>
              assistantActionsEnabled={active && runView === "events"}
              label="Run event timeline"
              rows={events}
              getRowId={(event) => String(event.seq)}
              scopeKey={id}
              columns={[
                { id: "seq", label: "Sequence", value: (event) => event.seq },
                { id: "time", label: "Time", value: (event) => event.time },
                { id: "kind", label: "Event", value: (event) => event.kind },
                {
                  id: "payload",
                  label: "Evidence",
                  value: (event) => json(event.payload),
                  render: (event) => <EvidenceValue value={event.payload} />,
                },
              ]}
            />
          </Evidence>
        </section>
        <section
          id="lab-run-view-dataset"
          role="tabpanel"
          aria-label="Dataset"
          hidden={runView !== "dataset"}
        >
          {run && (
            <DatasetPromotion
              run={run}
              onOperationChanged={onOperationChanged}
            />
          )}
        </section>
        <section
          id="lab-run-view-replay"
          role="tabpanel"
          aria-label={
            statisticsRun
              ? "Planning"
              : analysisRun
                ? "Analysis"
                : runtimeProbeRun
                  ? "Runtime probe"
                  : hardwareDiscoveryRun
                    ? "Discovery"
                    : "Replay"
          }
          hidden={runView !== "replay"}
        >
          {run &&
            (statisticsRun ? (
              <RetainedStatisticsPlanning
                artifacts={retained?.all ?? []}
                runId={id}
                scopeKey={studio.scopeKey}
              />
            ) : analysisRun ? (
              <StudyAnalysisResult run={run} />
            ) : runtimeProbeRun ? (
              <RuntimeProbeResult run={run} />
            ) : hardwareDiscoveryRun ? (
              <HardwareDiscoveryResult run={run} />
            ) : (
              <ReplayViews
                records={chartRecords}
                rawRecords={rawRecords}
                recordSources={[processedSource, originalSource]}
                onRetryRecords={() => {
                  if (processedSource.status === "error") processed.reload();
                  if (originalSource.status === "error") original.reload();
                }}
                events={events}
                run={run}
                active={active && runView === "replay"}
              />
            ))}
        </section>
        <section
          id="lab-run-view-records"
          role="tabpanel"
          aria-label="Records"
          hidden={runView !== "records"}
        >
          {analysisRun || runtimeProbeRun || hardwareDiscoveryRun ? (
            <>
              <EmptyState
                heading="Results use reports and supporting artifacts"
                actions={
                  <Button onClick={() => setRunView("replay")}>
                    {runtimeProbeRun
                      ? "Open runtime probe report"
                      : hardwareDiscoveryRun
                        ? "Open discovery report"
                        : "Open analysis report"}
                  </Button>
                }
              >
                <p>
                  {runtimeProbeRun
                    ? "Runtime qualification retains a compatibility report, measurement stages and diagnostic artifacts. Inspect the report in Runtime probe."
                    : hardwareDiscoveryRun
                      ? "Hardware discovery retains structural metadata, model-address chains and wire trace artifacts. Inspect the retained status and source identity in Discovery. These artifacts are not numeric observation records."
                      : "Study analysis retains its statistical report, selected observations and inference-unit artifacts. Inspect estimates, diagnostics and provenance in Analysis."}
                </p>
                <p>Use Run actions to export the retained manifest bundle.</p>
              </EmptyState>
              <SimpleTable
                label="Report and supporting artifact identities"
                rows={retained?.all ?? []}
                getRowId={(row) => row.id}
                scopeKey={id}
                empty="No report or supporting artifact identities are retained for this run."
                columns={[
                  { id: "name", label: "Artifact", value: (row) => row.name },
                  {
                    id: "role",
                    label: "Role",
                    value: (row) => row.role ?? "Not recorded",
                  },
                  {
                    id: "identity",
                    label: "Artifact identity",
                    value: (row) => row.id,
                  },
                  {
                    id: "checksum",
                    label: "SHA-256",
                    value: (row) => row.sha256 ?? "Not recorded",
                  },
                ]}
              />
            </>
          ) : (
            <>
              <Choice
                label="Record artifact"
                value={artifact}
                options={
                  recordArtifacts.length
                    ? recordArtifacts.map((item) => ({
                        id: item.name,
                        name: `${item.role ?? "Evidence"} · ${item.name}`,
                      }))
                    : [
                        {
                          id: "processed.jsonl",
                          name: "Derived / processed records",
                        },
                        { id: "raw.jsonl", name: "Original / raw records" },
                      ]
                }
                onChange={(value) => {
                  setArtifact(value);
                  setOffset(0);
                }}
              />
              {!recordsAvailable ? (
                <EmptyState
                  heading="Records are not available yet"
                  role="status"
                >
                  Retained records can be retrieved after this run finishes.
                </EmptyState>
              ) : page.loading ? (
                <EmptyState
                  heading="Loading retained records"
                  role="status"
                  aria-busy="true"
                >
                  Retrieving {artifact} for the selected record window.
                </EmptyState>
              ) : pageSource.status === "missing" ? (
                <EmptyState heading="No retained record artifact">
                  This run has no retained {artifact} artifact. Choose another
                  artifact or inspect the other run evidence.
                </EmptyState>
              ) : pageError ? (
                <EmptyState
                  heading="Retained records unavailable"
                  role="alert"
                  actions={<Button onClick={page.reload}>Retry records</Button>}
                >
                  {pageError instanceof Error
                    ? pageError.message
                    : String(pageError)}
                </EmptyState>
              ) : (
                <Evidence
                  label="Laboratory records"
                  data={{ run_id: id, artifact, offset, records }}
                >
                  <p className="lab-context-hint">
                    Filters and sorting apply to this loaded window of{" "}
                    {records.length} records. Use Previous records and Next
                    records to load another window.
                  </p>
                  <DataTable<{ record: any; position: number }>
                    assistantActionsEnabled={active && runView === "records"}
                    label="Retained records"
                    rows={records.map((record, index) => ({
                      record,
                      position: offset + index + 1,
                    }))}
                    getRowId={(row) => `${id}:${artifact}:${row.position}`}
                    scopeKey={`${id}:${artifact}`}
                    empty=""
                    columns={[
                      {
                        id: "record",
                        label: "Record",
                        value: (row) => row.position,
                      },
                      {
                        id: "original",
                        label: "Original fields",
                        value: ({ record }) =>
                          json(
                            record.original ??
                              record.raw ??
                              (artifact ===
                              (retained?.original?.name ?? "raw.jsonl")
                                ? record
                                : null),
                          ),
                        render: ({ record }) => (
                          <EvidenceValue
                            value={
                              record.original ??
                              record.raw ??
                              (artifact ===
                              (retained?.original?.name ?? "raw.jsonl")
                                ? record
                                : null)
                            }
                          />
                        ),
                      },
                      {
                        id: "derived",
                        label: "Derived fields",
                        value: ({ record }) =>
                          json(
                            record.derived ??
                              record.processed ??
                              (artifact ===
                              (retained?.derived?.name ?? "processed.jsonl")
                                ? record
                                : null),
                          ),
                        render: ({ record }) => (
                          <EvidenceValue
                            value={
                              record.derived ??
                              record.processed ??
                              (artifact ===
                              (retained?.derived?.name ?? "processed.jsonl")
                                ? record
                                : null)
                            }
                          />
                        ),
                      },
                      {
                        id: "lineage",
                        label: "Lineage and full record",
                        value: ({ record }) => json(record),
                        render: ({ record }) => (
                          <details>
                            <summary>Inspect record</summary>
                            <pre>{json(record)}</pre>
                          </details>
                        ),
                      },
                    ]}
                  />
                  {!records.length && (
                    <EmptyState heading="No retained records in this window">
                      {artifact} returned no records at offset {offset}. Choose
                      another artifact or record window to inspect other
                      retained evidence.
                    </EmptyState>
                  )}
                </Evidence>
              )}
              <div className="lab-actions">
                <Button
                  disabled={!recordsAvailable || page.loading || !offset}
                  onClick={() => setOffset(Math.max(0, offset - 100))}
                >
                  Previous records
                </Button>
                <Button
                  disabled={
                    !recordsAvailable ||
                    page.loading ||
                    !!pageError ||
                    nextOffset == null
                  }
                  onClick={() => setOffset(nextOffset!)}
                >
                  Next records
                </Button>
              </div>
            </>
          )}
        </section>
      </RunTemplate>
    </Panel>
  );
}
