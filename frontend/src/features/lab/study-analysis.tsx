import { useEffect, useRef, useState } from "react";
import { Input } from "@/components/boardui/base/input/input";
import { Textarea } from "@/components/boardui/base/textarea/textarea";
import { useResource, useStudio } from "../../app/StudioContext";
import { scientificJson } from "../../api/scientific-json";
import {
  Button,
  EmptyState,
  Evidence,
  Panel,
} from "../../components/studio/primitives";
import { SettingsSection } from "../../components/studio/settings-section";
import { SimpleTable } from "../../components/studio/data-table";
import { isTerminal, type LabRun, type SavedDefinition } from "./definition";
import { dataArtifacts, type RetainedArtifact } from "./evidence";
import { LabChoice, type LabInput } from "./inputs";
import { isOperationTerminal } from "./operations";
import {
  appendStudyAnalysisResult,
  checkStudyAnalysis,
  loadStudyAnalysisReport,
  retainAnalysisSnapshot,
  saveStudyAnalysis,
  studyAnalysisPaths,
  submitStudyAnalysis,
  type AnalysisCompatibility,
  type AnalysisDefinitionRequest,
  type AnalysisMethodCatalog,
  type AnalysisReport,
  type AnalysisSnapshot,
  type StudyAnalysisHead,
  type StudyAnalysisStudy,
} from "./study-analysis-api";
import {
  createAnalysisDraft,
  parseAnalysisDraft,
  parseSnapshotBoundary,
  type AnalysisDraft,
} from "./study-analysis-definition";

const json = (value: unknown) => scientificJson.stringify(value, 2);
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
const policyOptions = [
  { id: "error", name: "Stop and require a decision" },
  { id: "omit", name: "Omit and retain diagnostics" },
];

/** Transport cancellation does not let responses escape the component that started them. */
function useAnalysisAction() {
  const controller = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  useEffect(() => () => controller.current?.abort(), []);
  const perform = async <T,>(
    request: (signal: AbortSignal) => Promise<T>,
    accept: (result: T) => void,
  ) => {
    if (controller.current) return;
    const owner = new AbortController();
    controller.current = owner;
    setBusy(true);
    setError("");
    try {
      const result = await request(owner.signal);
      if (!owner.signal.aborted) accept(result);
    } catch (failure) {
      if (!owner.signal.aborted) setError(errorText(failure));
    } finally {
      if (!owner.signal.aborted) {
        controller.current = null;
        setBusy(false);
      }
    }
  };
  return { busy, error, perform };
}
export interface StudyAnalysisPanelProps {
  study: StudyAnalysisStudy;
  currentHead?: StudyAnalysisHead;
  onDirty?: (dirty: boolean) => void;
  onRunCreated?: (run: LabRun) => void;
  onResultLinked?: () => void;
}

/** A project or study change synchronously removes the previous owner's draft and requests. */
export function StudyAnalysisPanel(props: StudyAnalysisPanelProps) {
  const { scopeKey } = useStudio();
  return (
    <AnalysisAuthor
      key={JSON.stringify([scopeKey, props.study.id])}
      {...props}
    />
  );
}
function AnalysisAuthor({
  study,
  currentHead,
  onDirty,
  onRunCreated,
  onResultLinked,
}: StudyAnalysisPanelProps) {
  const { api } = useStudio();
  const availableHead = currentHead ?? study;
  const head = useRef<StudyAnalysisHead>(availableHead);
  // Protocols and the ledger are append-only. Resource reloads can temporarily
  // omit the fresh header, so retain the largest known limits already seen.
  head.current = {
    revision: Math.max(head.current.revision, availableHead.revision),
    last_sequence: Math.max(
      head.current.last_sequence,
      availableHead.last_sequence,
    ),
  };
  const retainedHead = head.current;
  const catalog = useResource<AnalysisMethodCatalog>(
    studyAnalysisPaths.methods,
  );
  const targets = useResource<
    { id: string; name: string; enabled?: boolean }[]
  >(studyAnalysisPaths.targets);
  const inputs = useResource<LabInput[]>(studyAnalysisPaths.inputs);
  const [storedDraft, setDraft] = useState(() => createAnalysisDraft(study));
  const pristineDraft = useRef(storedDraft);
  const [boundarySelected, setBoundarySelected] = useState(false);
  // Fresh collection/protocol props can arrive after this panel first mounts.
  // Only an untouched boundary follows them; every explicit choice stays pinned.
  const draft = boundarySelected
    ? storedDraft
    : {
        ...storedDraft,
        revision: String(retainedHead.revision),
        throughSeq: String(retainedHead.last_sequence),
      };
  const [target, setTarget] = useState("");
  const [snapshot, setSnapshot] = useState<{
    boundary: string;
    data: AnalysisSnapshot;
  }>();
  const [snapshotState, setSnapshotState] = useState("");
  const [saved, setSaved] = useState<{
    signature: string;
    data: SavedDefinition;
  }>();
  const [compatibility, setCompatibility] = useState<{
    signature: string;
    target: string;
    report: AnalysisCompatibility;
  }>();
  const [run, setRun] = useState<LabRun>();
  const [runError, setRunError] = useState("");
  const action = useAnalysisAction();
  // Saved definitions do not restore this panel's snapshot/target/run context.
  // Protect that local work as well as invalid or unfinished authored choices.
  const dirty =
    Object.entries(storedDraft).some(
      ([key, value]) =>
        key !== "revision" &&
        key !== "throughSeq" &&
        value !== pristineDraft.current[key as keyof AnalysisDraft],
    ) ||
    boundarySelected ||
    !!target ||
    !!snapshot ||
    !!saved ||
    !!compatibility ||
    !!run ||
    action.busy;
  const dirtyCallback = useRef(onDirty);
  dirtyCallback.current = onDirty;
  useEffect(() => dirtyCallback.current?.(dirty), [dirty]);
  const keys = useRef(new Map<string, string>());
  const keyFor = (key: string) => {
    if (!keys.current.has(key)) keys.current.set(key, crypto.randomUUID());
    return keys.current.get(key)!;
  };
  const patch = (key: keyof AnalysisDraft, value: string) => {
    setDraft((previous) => ({
      ...previous,
      revision: draft.revision,
      throughSeq: draft.throughSeq,
      [key]: value,
    }));
    if (key === "revision" || key === "throughSeq") setBoundarySelected(true);
  };
  const selectBoundary = (revision: string, throughSeq: string) => {
    setBoundarySelected(true);
    setDraft((previous) => ({ ...previous, revision, throughSeq }));
  };
  const atCurrentBoundary =
    draft.revision === String(retainedHead.revision) &&
    draft.throughSeq === String(retainedHead.last_sequence);
  let boundary: ReturnType<typeof parseSnapshotBoundary> | undefined,
    boundaryError = "";
  try {
    boundary = parseSnapshotBoundary(draft);
    if (
      boundary.revision > retainedHead.revision ||
      boundary.through_seq > retainedHead.last_sequence
    )
      throw new Error(
        "Choose a boundary within the available retained study history.",
      );
  } catch (failure) {
    boundaryError = errorText(failure);
    boundary = undefined;
  }
  const boundarySignature = boundary ? scientificJson.stringify(boundary) : "";
  const currentSnapshot =
    snapshot?.boundary === boundarySignature ? snapshot.data : undefined;
  let authored: ReturnType<typeof parseAnalysisDraft> | undefined,
    draftError = "";
  try {
    authored = parseAnalysisDraft(draft);
  } catch (failure) {
    draftError = errorText(failure);
  }
  const methods =
    catalog.data?.format === "tinystudio.analysis-methods.v1" &&
    Array.isArray(catalog.data.methods)
      ? catalog.data.methods.filter(
          (method) =>
            ["means.welch", "means.paired", "custom.python"].includes(
              method.id,
            ) &&
            typeof method.label === "string" &&
            Array.isArray(method.assumptions) &&
            Array.isArray(method.references),
        )
      : [];
  const method = methods.find((item) => item.id === draft.method);
  const catalogValid =
    !catalog.loading &&
    !catalog.error &&
    catalog.data?.format === "tinystudio.analysis-methods.v1";
  const methodValid =
    draft.method === "custom.python" ? catalogValid : catalogValid && !!method;
  const body: AnalysisDefinitionRequest | undefined =
    authored && currentSnapshot && methodValid
      ? { ...authored, snapshot_artifact_id: currentSnapshot.artifact.id }
      : undefined;
  const signature = body ? scientificJson.stringify(body) : "";
  const savedCurrent = !!saved && !!signature && saved.signature === signature;
  const currentCompatibility =
    savedCurrent &&
    compatibility?.signature === signature &&
    compatibility.target === target
      ? compatibility.report
      : undefined;
  const custom = draft.method === "custom.python";

  useEffect(() => {
    if (!run || isTerminal(run.state)) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api
        .get<LabRun>(studyAnalysisPaths.run(run.id), controller.signal)
        .then((next) => {
          if (!controller.signal.aborted) {
            setRun(next);
            setRunError("");
          }
        })
        .catch((failure) => {
          if (!controller.signal.aborted) setRunError(errorText(failure));
        });
    }, 1500);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [api, run]);

  const textField = (
    key: keyof AnalysisDraft,
    label: string,
    hint?: string,
  ) => (
    <div>
      <Input
        label={label}
        value={draft[key]}
        onChange={(value) => patch(key, value)}
        isDisabled={action.busy}
      />
      {hint && (
        <p className="text-caption-1-regular text-text-secondary">{hint}</p>
      )}
    </div>
  );
  const textArea = (key: keyof AnalysisDraft, label: string, rows = 3) => (
    <Textarea
      label={label}
      value={draft[key]}
      onChange={(value) => patch(key, value)}
      rows={rows}
      isDisabled={action.busy}
    />
  );
  const choose = (
    key: keyof AnalysisDraft,
    label: string,
    options: { id: string; name: string }[],
  ) => (
    <LabChoice
      label={label}
      value={draft[key]}
      onChange={(value) => patch(key, value)}
      options={options}
      disabled={action.busy}
      placeholder="Choose explicitly"
    />
  );

  return (
    <Panel
      title="Analyze retained study observations"
      aria-label="Study analysis"
    >
      <p className="text-body-2-regular text-text-secondary">
        Choose the protocol and ledger boundary, observations and inference
        units for this analysis. Ledger corrections remain bookkeeping; original
        and correcting records are never automatically replaced. Counts do not
        establish independent experimental units.
      </p>
      <SettingsSection
        title="1. Freeze the study boundary"
        description="The snapshot retains the protocol history and ledger through your chosen sequence."
      >
        <div className="grid gap-4 md:grid-cols-2">
          {textField("revision", "Protocol revision for analysis")}
          {textField("throughSeq", "Last included ledger sequence")}
        </div>
        <p>
          Viewed protocol revision {study.revision}. Current retained boundary:
          protocol revision {retainedHead.revision}, ledger sequence{" "}
          {retainedHead.last_sequence}.
        </p>
        {boundary && boundary.revision !== study.revision && (
          <p role="status">
            The analysis snapshot includes protocol history through revision{" "}
            {boundary.revision}; the editor displays revision {study.revision}.
            Review that retained history when mapping observations and recording
            assumptions.
          </p>
        )}
        <p className="text-caption-1-regular text-text-secondary">
          {boundarySelected
            ? "Your selected boundary stays pinned as study history changes. Choosing the current boundary preserves your analysis choices and requires a matching retained snapshot."
            : "Until you edit or capture it, this boundary follows the current retained study head."}
        </p>
        <Button
          disabled={action.busy || atCurrentBoundary}
          onClick={() =>
            selectBoundary(
              String(retainedHead.revision),
              String(retainedHead.last_sequence),
            )
          }
        >
          Use current retained boundary
        </Button>
        {boundaryError && <p role="alert">{boundaryError}</p>}
        <Button
          disabled={action.busy || !boundary}
          onClick={() => {
            const selected = boundary!,
              ownerBoundary = boundarySignature;
            selectBoundary(draft.revision, draft.throughSeq);
            void action.perform(
              (signal) =>
                retainAnalysisSnapshot(
                  api,
                  study.id,
                  selected,
                  keyFor(`snapshot:${ownerBoundary}`),
                  signal,
                  (operation) => {
                    if (!signal.aborted) {
                      setSnapshotState(operation.state);
                      if (
                        isOperationTerminal(operation.state) &&
                        operation.state !== "succeeded"
                      )
                        keys.current.delete(`snapshot:${ownerBoundary}`);
                    }
                  },
                ),
              (data) => setSnapshot({ boundary: ownerBoundary, data }),
            );
          }}
        >
          Retain analysis snapshot
        </Button>
        {snapshotState && !isOperationTerminal(snapshotState) && (
          <p role="status">Snapshot operation: {snapshotState}</p>
        )}
        {currentSnapshot && (
          <Evidence
            label="Retained study analysis snapshot"
            data={currentSnapshot}
          >
            <p role="status">
              Snapshot retained · protocol revision{" "}
              {currentSnapshot.source.revision}, ledger sequence{" "}
              {currentSnapshot.source.through_seq} ·{" "}
              {currentSnapshot.artifact.name}
            </p>
          </Evidence>
        )}
        {snapshot && !currentSnapshot && (
          <p role="status">
            The boundary changed. Retain a snapshot for the current boundary.
          </p>
        )}
      </SettingsSection>
      <SettingsSection
        title="2. Choose the analysis and observations"
        description="Every inclusion choice is retained with the resulting calculation."
      >
        {textField("name", "Analysis name")}
        {catalog.loading && (
          <p role="status">Loading observed-data analysis methods…</p>
        )}
        {catalog.error && (
          <>
            <p role="alert">{catalog.error.message}</p>
            <Button onClick={catalog.reload}>Retry analysis methods</Button>
          </>
        )}
        {!catalog.loading && !catalog.error && !catalogValid && (
          <p role="alert">
            The analysis-method catalog has an unsupported format.
          </p>
        )}
        {choose("method", "Analysis method", [
          ...methods
            .filter((item) => item.id !== "custom.python")
            .map((item) => ({ id: item.id, name: item.label })),
          ...(catalogValid
            ? [{ id: "custom.python", name: "Custom Python analysis" }]
            : []),
        ])}
        {method && (
          <div>
            <p>{method.description}</p>
            <p className="text-caption-1-regular text-text-secondary">
              Method version {method.version} · dependencies:{" "}
              {method.dependencies?.join(", ") || "None declared"}
            </p>
            <ul>
              {method.assumptions.map((assumption, index) => (
                <li key={index}>{assumption}</li>
              ))}
            </ul>
          </div>
        )}
        {choose("selectionMode", "Record inclusion", [
          { id: "all", name: "All records through the chosen boundary" },
          { id: "ids", name: "Only specified record IDs" },
        ])}
        {draft.selectionMode === "ids" &&
          textArea("recordIds", "Included record IDs (one per line)")}
        {textArea("excludeIds", "Excluded record IDs (one per line)")}
        {textArea("kinds", "Included record kinds (one per line, optional)")}
        <p className="text-caption-1-regular text-text-secondary">
          A blank record-kind filter includes all kinds. Exclusions apply to
          either inclusion mode. Review correcting records and their originals
          explicitly.
        </p>
      </SettingsSection>
      {draft.method && (
        <SettingsSection
          title={
            custom
              ? "3. Describe the custom method"
              : "3. Map observations to inference units"
          }
          description={
            custom
              ? "Supply your own method contract and retain the Python source as an immutable input."
              : "Paths are JSON Pointers into each retained ledger record. A mean contrast is condition A minus condition B."
          }
        >
          {custom ? (
            <>
              {textField("customName", "Custom method name")}
              {textField("customVersion", "Custom method version")}
              <LabChoice
                label="Python source-code artifact"
                value={draft.customCodeArtifactId}
                onChange={(value) => patch("customCodeArtifactId", value)}
                options={(Array.isArray(inputs.data) ? inputs.data : []).map(
                  (item) => ({ id: item.id, name: item.name }),
                )}
                disabled={action.busy || inputs.loading}
                placeholder="Choose retained Python source"
              />
              {inputs.error && (
                <>
                  <p role="alert">{inputs.error.message}</p>
                  <Button onClick={inputs.reload}>
                    Retry source-code artifacts
                  </Button>
                </>
              )}
              {textField(
                "customCodeArtifactId",
                "Python source-code artifact ID (advanced)",
              )}
              {textArea("customOptions", "Custom method options (JSON)", 5)}
              {textArea(
                "extraArtifactIds",
                "Additional custom input artifact IDs (one per line)",
              )}
              <p>
                Custom Python requires an operator native execution grant for
                statistics.analyze and a target advertising custom-analysis
                support. An author cannot grant execution through these
                settings. Compatibility and execution check the selected target.
              </p>
            </>
          ) : (
            <>
              {study.study.independent_unit && (
                <p>
                  Viewed protocol independent unit (revision {study.revision}):{" "}
                  {study.study.independent_unit}
                </p>
              )}
              {draft.method === "means.paired" ? (
                <p>
                  Pairing uses the same declared unit identifier in both
                  conditions. Review the independent-unit path and justify
                  independence between pairs; your mapping is retained when
                  switching methods.
                </p>
              ) : (
                <p>
                  Independent groups require a researcher justification of
                  independence within and between groups; record counts and
                  distinct identifiers do not establish it.
                </p>
              )}
              <div className="grid gap-4 md:grid-cols-2">
                {textField(
                  "outcomePath",
                  "Outcome path",
                  "For example, /values/temperature. Choose a numeric outcome in your own records.",
                )}
                {textField(
                  "conditionPath",
                  "Condition path",
                  "For example, /condition. Values must match your selected A and B conditions.",
                )}
                {textField(
                  "unitPath",
                  "Independent-unit path",
                  "For example, /unit_id. Choose the experimental unit, not an observation counter.",
                )}
              </div>
              {choose("conditionMode", "Condition value format", [
                { id: "text", name: "Text condition values" },
                { id: "json", name: "JSON values (advanced)" },
              ])}
              <div className="grid gap-4 md:grid-cols-2">
                {textField(
                  "groupA",
                  draft.conditionMode === "json"
                    ? "Condition A value (JSON)"
                    : "Condition A value",
                )}
                {textField(
                  "groupB",
                  draft.conditionMode === "json"
                    ? "Condition B value (JSON)"
                    : "Condition B value",
                )}
              </div>
              {choose("measurementMode", "Outcome scale", [
                { id: "named", name: "Named measurement unit" },
                { id: "dimensionless", name: "Dimensionless values" },
              ])}
              {draft.measurementMode === "named" &&
                textField("measurementUnit", "Outcome measurement unit")}
              {textField(
                "confidence",
                "Confidence probability",
                "Enter the probability you chose between zero and one.",
              )}
              {choose("alternative", "Interval alternative", [
                { id: "two-sided", name: "Two-sided" },
                { id: "less", name: "Less: upper confidence limit" },
                { id: "greater", name: "Greater: lower confidence limit" },
              ])}
              {choose(
                "repeated",
                "Repeated measurements per unit and condition",
                [
                  { id: "error", name: "Stop and require a decision" },
                  {
                    id: "mean",
                    name: "Reduce to the mean per unit and condition",
                  },
                  {
                    id: "median",
                    name: "Reduce to the median per unit and condition",
                  },
                  {
                    id: "first",
                    name: "Use the first ledger observation per unit and condition",
                  },
                  {
                    id: "last",
                    name: "Use the last ledger observation per unit and condition",
                  },
                ],
              )}
              {choose(
                "missing",
                "Missing outcomes or unit identifiers",
                policyOptions,
              )}
              {draft.method === "means.paired" &&
                choose("incompletePairs", "Incomplete pairs", [
                  { id: "error", name: "Stop and require a decision" },
                  {
                    id: "omit",
                    name: "Omit incomplete pairs and retain diagnostics",
                  },
                ])}
            </>
          )}
        </SettingsSection>
      )}
      <SettingsSection
        title="4. Record the interpretation"
        description="Document why these observations and units address your question."
      >
        {textArea("notes", "Analysis notes")}
        {textArea("assumptions", "Researcher assumptions (one per line)")}
        <details>
          <summary>Input sources and execution settings</summary>
          {textArea("inputSources", "Analysis input sources (JSON)", 5)}
          {textArea("execution", "Analysis execution settings (JSON)", 5)}
        </details>
      </SettingsSection>
      {draftError && (
        <p role="status">Complete the analysis choices: {draftError}</p>
      )}
      {!currentSnapshot && (
        <p role="status">
          Retain the chosen study snapshot before saving the analysis.
        </p>
      )}
      {action.error && <p role="alert">{action.error}</p>}
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={action.busy || !body}
          onClick={() => {
            const ownerSignature = signature;
            void action.perform(
              (signal) => saveStudyAnalysis(api, body!, signal),
              (data) => setSaved({ signature: ownerSignature, data }),
            );
          }}
        >
          Save analysis
        </Button>
      </div>
      {saved && (
        <p role="status">
          Saved analysis revision {saved.data.revision} · {saved.data.name}
          {!savedCurrent
            ? ". The draft changed; save the current choices before checking or running."
            : ""}
        </p>
      )}
      <LabChoice
        label="Analysis execution target"
        value={target}
        onChange={setTarget}
        options={(Array.isArray(targets.data) ? targets.data : []).map(
          (item) => ({
            id: item.id,
            name: item.name,
            disabled: item.enabled === false,
          }),
        )}
        disabled={action.busy || targets.loading}
        placeholder="Choose execution target"
      />
      {targets.error && (
        <>
          <p role="alert">{targets.error.message}</p>
          <Button onClick={targets.reload}>Retry analysis targets</Button>
        </>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={action.busy || !savedCurrent || !target}
          onClick={() => {
            const ownerSignature = signature,
              ownerTarget = target;
            void action.perform(
              (signal) =>
                checkStudyAnalysis(api, saved!.data, ownerTarget, signal),
              (report) =>
                setCompatibility({
                  signature: ownerSignature,
                  target: ownerTarget,
                  report,
                }),
            );
          }}
        >
          Check analysis compatibility
        </Button>
        <Button
          disabled={
            action.busy ||
            !savedCurrent ||
            !target ||
            !currentCompatibility?.compatible
          }
          onClick={() => {
            const requestKey = `run:${saved!.data.id}:${saved!.data.revision}:${target}`;
            void action.perform(
              (signal) =>
                submitStudyAnalysis(
                  api,
                  saved!.data,
                  target,
                  keyFor(requestKey),
                  signal,
                ),
              (created) => {
                keys.current.delete(requestKey);
                setRun(created);
                onRunCreated?.(created);
              },
            );
          }}
        >
          {run ? "Run another saved analysis" : "Run saved analysis"}
        </Button>
      </div>
      {currentCompatibility && (
        <Evidence
          label="Study analysis compatibility"
          data={currentCompatibility}
        >
          <div role="status">
            <p>
              {currentCompatibility.compatible
                ? "Selected target passed compatibility checks."
                : "Resolve the reported target compatibility issues before running."}
            </p>
            <ul>
              {currentCompatibility.checks.map((check, index) => (
                <li key={index}>{check.message}</li>
              ))}
            </ul>
            {currentCompatibility.limitations?.map((limitation, index) => (
              <p key={index}>{limitation}</p>
            ))}
          </div>
        </Evidence>
      )}
      {compatibility && !currentCompatibility && (
        <p role="status">
          Compatibility check is stale. Check the current saved analysis and
          target.
        </p>
      )}
      {run && (
        <div className="flex flex-col gap-4">
          <p role="status">
            Analysis run {run.id}: {run.state}
          </p>
          {runError && <p role="alert">{runError}</p>}
          <Button
            disabled={action.busy}
            onClick={() =>
              void action.perform(
                (signal) =>
                  api.get<LabRun>(studyAnalysisPaths.run(run.id), signal),
                (next) => {
                  setRun(next);
                  setRunError("");
                },
              )
            }
          >
            Refresh analysis run
          </Button>
          <StudyAnalysisResult run={run} onResultLinked={onResultLinked} />
        </div>
      )}
    </Panel>
  );
}

export function StudyAnalysisResult({
  run,
  onResultLinked,
}: {
  run: LabRun;
  onResultLinked?: () => void;
}) {
  const { scopeKey } = useStudio();
  const artifact = dataArtifacts(run).all.find(
    (item) => item.role === "statistical_analysis",
  );
  if (!artifact)
    return (
      <EmptyState>
        No retained statistical analysis is available for this run yet.
      </EmptyState>
    );
  return (
    <RetainedAnalysis
      key={JSON.stringify([scopeKey, run.id, artifact.id, artifact.sha256])}
      run={run}
      artifact={artifact}
      onResultLinked={onResultLinked}
    />
  );
}
function RetainedAnalysis({
  run,
  artifact,
  onResultLinked,
}: {
  run: LabRun;
  artifact: RetainedArtifact;
  onResultLinked?: () => void;
}) {
  const { api } = useStudio();
  const [state, setState] = useState<{
    report?: AnalysisReport;
    error?: string;
  }>({});
  const [retry, setRetry] = useState(0),
    [notes, setNotes] = useState("");
  const [linked, setLinked] = useState<{ id: string; seq: number }>();
  const [linkKey] = useState(() => crypto.randomUUID());
  const action = useAnalysisAction();
  useEffect(() => {
    const controller = new AbortController();
    setState({});
    void loadStudyAnalysisReport(api, artifact, controller.signal)
      .then((report) => {
        if (!controller.signal.aborted) setState({ report });
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setState({ error: errorText(failure) });
      });
    return () => controller.abort();
  }, [api, artifact.id, artifact.sha256, retry]);
  if (state.error)
    return (
      <>
        <p role="alert">Statistical analysis unavailable: {state.error}</p>
        <Button onClick={() => setRetry((value) => value + 1)}>
          Retry retained study analysis
        </Button>
      </>
    );
  if (!state.report)
    return <p role="status">Loading retained study analysis…</p>;
  const report = state.report,
    result = report.result,
    custom = report.method.id === "custom.python";
  const number = (value: number | null | undefined) =>
    value === null || value === undefined
      ? "Undefined"
      : scientificJson.stringify(value);
  const limit = (value: number | null, unbounded: boolean) =>
    unbounded ? "Unbounded" : number(value);
  const selectionCounts = Object.entries(report.selection.counts).map(
    ([name, count]) => ({ name, count }),
  );
  const resultCounts = Object.entries(result.counts).map(([name, count]) => ({
    name,
    count,
  }));
  return (
    <Evidence
      label="Retained study statistical analysis"
      data={{ run_id: run.id, artifact, report }}
    >
      <section
        aria-label="Retained study analysis result"
        className="flex flex-col gap-4"
      >
        <p className="text-caption-1-regular text-text-secondary">
          Retained report: {artifact.name} · SHA-256 verified
        </p>
        <h3 className="text-title-3-semibold">
          {report.method.label ?? report.method.name} · {report.method.version}
        </h3>
        <p>
          Protocol revision {report.source.revision} · ledger sequence{" "}
          {report.source.through_seq} · study {report.source.study_id}
        </p>
        {custom ? (
          <>
            <p>
              Researcher-supplied custom result. The retained method, source
              code, inputs and runtime define its interpretation.
            </p>
            <pre aria-label="Custom method retained output">
              {json(result.custom_output)}
            </pre>
          </>
        ) : (
          <>
            <dl className="grid gap-2 md:grid-cols-2">
              <div>
                <dt>Point estimate (A minus B)</dt>
                <dd>{number(result.estimate)}</dd>
              </div>
              <div>
                <dt>Standard error</dt>
                <dd>{number(result.standard_error)}</dd>
              </div>
              <div>
                <dt>Degrees of freedom</dt>
                <dd>{number(result.degrees_of_freedom)}</dd>
              </div>
            </dl>
            <div>
              <p>
                Confidence probability: {number(result.interval!.confidence)}
              </p>
              <p>
                Lower limit:{" "}
                {limit(
                  result.interval!.lower,
                  result.interval!.lower_unbounded,
                )}
              </p>
              <p>
                Upper limit:{" "}
                {limit(
                  result.interval!.upper,
                  result.interval!.upper_unbounded,
                )}
              </p>
            </div>
          </>
        )}
        <SimpleTable
          label="Retained analysis counts"
          rows={resultCounts}
          getRowId={(row) => row.name}
          columns={[
            {
              id: "name",
              label: "Analysis count",
              value: (row) => row.name.replaceAll("_", " "),
            },
            { id: "count", label: "Count", value: (row) => row.count },
          ]}
        />
        <SimpleTable
          label="Retained observation selection counts"
          rows={selectionCounts}
          getRowId={(row) => row.name}
          columns={[
            {
              id: "name",
              label: "Selection count",
              value: (row) => row.name.replaceAll("_", " "),
            },
            { id: "count", label: "Count", value: (row) => row.count },
          ]}
        />
        <p className="text-caption-1-regular text-text-secondary">
          Counts describe the retained selection and reduction. They do not
          establish independence or validate a population or causal
          interpretation.
        </p>
        <div>
          <h4 className="text-headline-medium">Diagnostics</h4>
          {[...report.selection.diagnostics, ...(result.diagnostics ?? [])].map(
            (diagnostic, index) => (
              <p key={index}>{diagnostic.message}</p>
            ),
          )}
        </div>
        <div>
          <h4 className="text-headline-medium">Method assumptions</h4>
          <ul>
            {report.method.assumptions.map((assumption, index) => (
              <li key={index}>{assumption}</li>
            ))}
          </ul>
        </div>
        <details>
          <summary>Retained request, qualification and provenance</summary>
          <pre>
            {json({
              run_id: run.id,
              spec_digest: run.spec_digest,
              artifact,
              report,
            })}
          </pre>
        </details>
        <Textarea
          label="Result reference notes"
          value={notes}
          onChange={setNotes}
          rows={3}
          isDisabled={action.busy || !!linked}
        />
        {run.state !== "succeeded" && (
          <p role="status">
            A result reference can be appended after the corresponding analysis
            run completes successfully.
          </p>
        )}
        {action.error && <p role="alert">{action.error}</p>}
        <Button
          disabled={action.busy || !!linked || run.state !== "succeeded"}
          onClick={() =>
            void action.perform(
              (signal) =>
                appendStudyAnalysisResult(
                  api,
                  report.source.study_id,
                  run.id,
                  linkKey,
                  notes,
                  signal,
                ),
              (record) => {
                setLinked(record);
                onResultLinked?.();
              },
            )
          }
        >
          Append completed result reference to study
        </Button>
        {linked && (
          <p role="status">
            Result reference appended to the study at ledger sequence{" "}
            {linked.seq}.
          </p>
        )}
      </section>
    </Evidence>
  );
}
