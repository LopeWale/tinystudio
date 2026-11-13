import { useEffect, useRef, useState } from "react";
import { Input } from "@/components/boardui/base/input/input";
import { Textarea } from "@/components/boardui/base/textarea/textarea";
import { useResource, useStudio } from "../../app/StudioContext";
import { ApiError, withScientificJson } from "../../api/client";
import { scientificJson } from "../../api/scientific-json";
import {
  Button,
  EmptyState,
  Evidence,
  Panel,
} from "../../components/studio/primitives";
import { SettingsSection } from "../../components/studio/settings-section";
import { SimpleTable } from "../../components/studio/data-table";
import { DraftField } from "./data-preparation";
import { StudyAnalysisPanel } from "./study-analysis";
import {
  StudyImportPanel,
  type StudyObservationImportStudy,
} from "./study-import";

type JsonObject = Record<string, unknown>;
type StudyDocument = JsonObject & {
  schema_version: number;
  name: string;
  description: string;
  hypothesis: string;
  independent_unit: string;
  status: string;
  conditions: unknown[];
  outcomes: unknown[];
  sampling_plan: JsonObject;
  stopping_rules: JsonObject;
  metadata: JsonObject;
};
type Study = StudyObservationImportStudy & {
  id: string;
  revision: number;
  head_revision: number;
  study: StudyDocument;
  digest: string;
  author_id: string;
  recorded_at: string;
  summary: { records: number; declared_unit_ids: number };
  last_sequence: number;
};
type StudySummary = {
  id: string;
  name: string;
  status: string;
  revision: number;
  record_count: number;
};
type StudyRevision = {
  revision: number;
  digest: string;
  author_id: string;
  recorded_at: string;
  name: string;
  status: string;
};
type CollectionRecord = JsonObject & {
  id: string;
  study_id: string;
  seq: number;
  revision: number;
  kind: string;
  protocol_digest: string;
  digest: string;
  observed_at: string | null;
  recorded_at: string;
  author_id: string;
  run_references: unknown[];
  artifact_references: unknown[];
};
type RecordPage = {
  records: CollectionRecord[];
  next_after: number;
  latest_seq: number;
  has_more: boolean;
};
type ExportManifest = {
  format: string;
  study_id: string;
  revision: number;
  through_seq: number;
  protocol_digest: string;
  external_evidence_bytes: boolean;
  export_path: string;
};
const enc = encodeURIComponent;
const json = (value: unknown) => scientificJson.stringify(value, 2);
const copy = <T,>(value: T): T =>
  scientificJson.parse(scientificJson.stringify(value)) as T;
const studyPath = (id: string) => `/api/lab/studies/${enc(id)}`;
const isObject = (value: unknown): value is JsonObject =>
  !!value && typeof value === "object" && !Array.isArray(value);
const finiteJson = (value: unknown): boolean =>
  typeof value === "number"
    ? Number.isFinite(value)
    : Array.isArray(value)
      ? value.every(finiteJson)
      : isObject(value)
        ? Object.values(value).every(finiteJson)
        : true;
const blankStudy = (): StudyDocument => ({
  schema_version: 1,
  name: "",
  description: "",
  hypothesis: "",
  independent_unit: "",
  conditions: [],
  outcomes: [],
  sampling_plan: {},
  stopping_rules: {},
  status: "draft",
  metadata: {},
});
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

/** Mutations may outlive transport cancellation; their responses never outlive this owner. */
function useStudyAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const perform = async <T,>(
    request: (signal: AbortSignal) => Promise<T>,
    accept: (result: T) => void,
    reject?: (error: unknown) => void,
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
      if (!owner.signal.aborted) {
        setError(errorText(failure));
        reject?.(failure);
      }
    } finally {
      if (!owner.signal.aborted) {
        controller.current = null;
        setBusy(false);
      }
    }
  };
  return { busy, error, clearError: () => setError(""), perform };
}
function useDraftValidity() {
  const version = useRef(0);
  const [invalid, setInvalid] = useState<Set<string>>(() => new Set());
  const [report] = useState(() => (key: string, valid: boolean) => {
    version.current++;
    setInvalid((previous) => {
      if (previous.has(key) === !valid) return previous;
      const next = new Set(previous);
      if (valid) next.delete(key);
      else next.add(key);
      return next;
    });
  });
  return { version, invalid, report };
}

/** Project changes synchronously remove both drafts and responses from the old scope. */
export function Studies() {
  const { scopeKey } = useStudio();
  return <StudyDirectory key={scopeKey} />;
}
function StudyDirectory() {
  const [offset, setOffset] = useState(0);
  const list = useResource<StudySummary[]>(
    offset ? `/api/lab/studies?offset=${offset}&limit=100` : "/api/lab/studies",
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [selectionGeneration, setSelectionGeneration] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [importPending, setImportPending] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const open = (id: string) => {
    setSelectionGeneration((value) => value + 1);
    setSelected(id);
    setPending(null);
    setDirty(false);
  };
  const select = (id: string) => {
    if (importPending) return;
    if (dirty) setPending(id);
    else open(id);
  };
  const rows = Array.isArray(list.data) ? list.data : [];
  return (
    <section aria-label="Study tracking" className="flex flex-col gap-4">
      <Panel title="Project studies">
        <p className="text-body-2-regular text-text-secondary">
          Author a protocol and retain collection records independently of any
          experiment engine. Statuses, assumptions and stopping rules are yours
          to define.
        </p>
        <Button disabled={importPending} onClick={() => select("new")}>
          New study
        </Button>
        {importPending && (
          <p role="status">
            Resolve the original observation import before changing studies.
          </p>
        )}
        {list.loading && <p role="status">Loading studies…</p>}
        {list.error && (
          <>
            <p role="alert">{list.error.message}</p>
            <Button onClick={list.reload}>Retry study list</Button>
          </>
        )}
        <SimpleTable<StudySummary>
          label="Project study directory"
          rows={rows}
          getRowId={(row) => row.id}
          columns={[
            {
              id: "name",
              label: "Study",
              value: (row) => row.name,
              render: (row) => (
                <Button
                  disabled={importPending}
                  onClick={() => select(row.id)}
                  aria-label={`Open study ${row.name}`}
                >
                  {row.name}
                </Button>
              ),
            },
            {
              id: "status",
              label: "Researcher status",
              value: (row) => row.status,
            },
            {
              id: "revision",
              label: "Head revision",
              value: (row) => row.revision,
            },
            {
              id: "records",
              label: "Ledger records",
              value: (row) => row.record_count,
            },
          ]}
        />
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={list.loading || offset === 0}
            onClick={() => setOffset((value) => Math.max(0, value - 100))}
          >
            Previous study page
          </Button>
          <Button
            disabled={list.loading || !!list.error || rows.length < 100}
            onClick={() => setOffset((value) => value + 100)}
          >
            Next study page
          </Button>
          <p>Study directory page {offset / 100 + 1}</p>
        </div>
      </Panel>
      {pending && (
        <div role="alert">
          <p>
            Your current protocol, collection or analysis work has unsaved
            changes. Drafts and local analysis context remain available until
            you choose to discard them.
          </p>
          <Button onClick={() => setPending(null)}>
            Keep current study draft
          </Button>
          <Button disabled={importPending} onClick={() => open(pending)}>
            Discard draft and open study
          </Button>
        </div>
      )}
      {selected ? (
        <StudySession
          key={`${selected}:${selectionGeneration}`}
          id={selected === "new" ? null : selected}
          onChanged={list.reload}
          onDirty={setDirty}
          onImportPending={setImportPending}
        />
      ) : (
        <EmptyState>
          Select a study or create a new protocol. Unknown assumptions can
          remain blank.
        </EmptyState>
      )}
    </section>
  );
}
function StudySession({
  id,
  onChanged,
  onDirty,
  onImportPending,
}: {
  id: string | null;
  onChanged: () => void;
  onDirty: (dirty: boolean) => void;
  onImportPending: (pending: boolean) => void;
}) {
  const [selectedProtocol, setSelectedProtocol] = useState<{
    id: string;
    revision: number;
  } | null>(null);
  const activeId = selectedProtocol?.id ?? id;
  const revision = selectedProtocol?.revision ?? null;
  const [dirty, setDirty] = useState(false);
  const [importPending, setImportPending] = useState(false);
  const [pendingRevision, setPendingRevision] = useState<{
    id: string;
    revision: number;
  } | null>(null);
  const resource = useResource<Study>(
    activeId
      ? `${studyPath(activeId)}${revision ? `?revision=${revision}` : ""}`
      : null,
  );
  const reportDirty = (value: boolean) => {
    setDirty(value);
    onDirty(value);
  };
  const openRevision = (target: { id: string; revision: number }) => {
    setSelectedProtocol(target);
    resource.reload();
  };
  const chooseRevision = (studyId: string, next: number) => {
    if (importPending) return;
    const target = { id: studyId, revision: next };
    if (dirty) setPendingRevision(target);
    else openRevision(target);
  };
  const data = resource.data;
  return (
    <>
      {activeId && resource.loading && (
        <p role="status">Loading selected study protocol…</p>
      )}
      {resource.error && (
        <>
          <p role="alert">{resource.error.message}</p>
          <Button onClick={resource.reload}>Retry selected study</Button>
        </>
      )}
      {data &&
      (data.id !== activeId ||
        (revision !== null && data.revision !== revision)) ? (
        <p role="alert">
          The study response does not match the selected protocol.
        </p>
      ) : (
        (!activeId || data) && (
          <StudyEditor
            key={`${activeId ?? "new"}:${revision ?? "head"}`}
            initial={data ?? null}
            onChanged={onChanged}
            onDirty={reportDirty}
            onImportPending={(pending) => {
              setImportPending(pending);
              onImportPending(pending);
            }}
            onRevision={chooseRevision}
          />
        )
      )}
      {pendingRevision !== null && (
        <div role="alert">
          <p>
            Opening another protocol revision would discard your current drafts.
          </p>
          <Button onClick={() => setPendingRevision(null)}>
            Keep current protocol draft
          </Button>
          <Button
            disabled={importPending}
            onClick={() => {
              openRevision(pendingRevision);
              setPendingRevision(null);
              reportDirty(false);
            }}
          >
            Discard draft and open protocol revision
          </Button>
        </div>
      )}
    </>
  );
}
function StudyEditor({
  initial,
  onChanged,
  onDirty,
  onRevision,
  onImportPending,
}: {
  initial: Study | null;
  onChanged: () => void;
  onDirty: (dirty: boolean) => void;
  onRevision: (id: string, revision: number) => void;
  onImportPending: (pending: boolean) => void;
}) {
  const { api } = useStudio();
  const [base, setBase] = useState(initial);
  const [doc, setDoc] = useState<StudyDocument>(() =>
    copy(initial?.study ?? blankStudy()),
  );
  const [expectedHead, setExpectedHead] = useState(initial?.head_revision ?? 0);
  const [createKey, setCreateKey] = useState(() => crypto.randomUUID());
  const [createAttempt, setCreateAttempt] = useState<{
    body: { study: StudyDocument; idempotency_key: string };
    version: number;
  } | null>(null);
  const [conflict, setConflict] = useState(false);
  const [latest, setLatest] = useState<Study | null>(null);
  const [recordDirty, setRecordDirty] = useState(false);
  const [analysisDirty, setAnalysisDirty] = useState(false);
  const [importDirty, setImportDirty] = useState(false);
  const [importPending, setImportPending] = useState(false);
  const [recordRefresh, setRecordRefresh] = useState(0);
  const [exported, setExported] = useState<ExportManifest | null>(null);
  const draft = useDraftValidity();
  const action = useStudyAction();
  const header = useResource<Study>(base ? studyPath(base.id) : null);
  const history = useResource<StudyRevision[]>(
    base ? `${studyPath(base.id)}/revisions` : null,
  );
  const dirty =
    !!draft.invalid.size ||
    json(doc) !== json(base?.study ?? blankStudy()) ||
    !!createAttempt ||
    recordDirty ||
    analysisDirty ||
    importDirty;
  const dirtyCallback = useRef(onDirty);
  dirtyCallback.current = onDirty;
  useEffect(() => dirtyCallback.current(dirty), [dirty]);
  const patch = (key: string, value: unknown) => {
    draft.version.current++;
    setDoc((old) => ({ ...old, [key]: value }));
  };
  const valid = !!doc.name.trim() && !draft.invalid.size && finiteJson(doc);
  const save = () => {
    if (action.busy) return;
    const attempt = createAttempt ?? {
      body: { study: copy(doc), idempotency_key: createKey },
      version: draft.version.current,
    };
    if (!base) setCreateAttempt(attempt);
    const submittedVersion = base ? draft.version.current : attempt.version;
    void action.perform(
      (signal) =>
        base
          ? api.put<Study>(
              studyPath(base.id),
              withScientificJson({
                study: copy(doc),
                expected_revision: expectedHead,
              }),
              signal,
            )
          : api.post<Study>(
              "/api/lab/studies",
              withScientificJson(attempt.body),
              signal,
            ),
      (saved) => {
        if (!saved.id || (base && saved.id !== base.id))
          throw new Error("Saved study does not match this editor.");
        setBase(saved);
        setExpectedHead(saved.head_revision);
        if (draft.version.current === submittedVersion)
          setDoc(copy(saved.study));
        setCreateAttempt(null);
        setConflict(false);
        setLatest(null);
        history.reload();
        header.reload();
        onChanged();
      },
      (failure) => {
        if (failure instanceof ApiError && failure.status === 422)
          setCreateAttempt(null);
        if (base && failure instanceof ApiError && failure.status === 409)
          setConflict(true);
      },
    );
  };
  const exportTracking = () =>
    base &&
    void action.perform(
      async (signal) => {
        const manifest = await api.get<ExportManifest>(
          `${studyPath(base.id)}/manifest`,
          signal,
        );
        if (signal.aborted)
          throw new DOMException("Export cancelled", "AbortError");
        if (
          manifest.format !== "tinystudio.study-tracking.v1" ||
          manifest.study_id !== base.id ||
          manifest.external_evidence_bytes !== false ||
          manifest.export_path.split("?")[0] !==
            `${studyPath(base.id)}/export` ||
          manifest.export_path.includes("#")
        )
          throw new Error(
            "Tracking export metadata does not match this study.",
          );
        const blob = await api.download(manifest.export_path, signal);
        return { manifest, blob };
      },
      ({ manifest, blob }) => {
        const url = URL.createObjectURL(blob);
        try {
          const link = document.createElement("a");
          link.href = url;
          link.download = `${base.id}-tracking-r${manifest.revision}-s${manifest.through_seq}.jsonl`;
          link.click();
        } finally {
          URL.revokeObjectURL(url);
        }
        setExported(manifest);
      },
    );
  const counts = header.error
    ? undefined
    : header.data?.id === base?.id
      ? header.data?.summary
      : base?.summary;
  return (
    <div className="flex flex-col gap-4">
      {base && (
        <Evidence label="Pinned study protocol" data={base}>
          <p>
            Pinned protocol revision {base.revision} · head revision{" "}
            {base.head_revision}
          </p>
          <p className="text-caption-1-regular text-text-secondary">
            Protocol digest: {base.digest} · Author: {base.author_id} · Server
            recorded at: {base.recorded_at}
          </p>
        </Evidence>
      )}
      <SettingsSection
        title="Study protocol"
        description="Only the name is required. Define your own conditions, outcomes, independent unit, sampling plan and stopping rules. Saving a revision preserves earlier protocols and records."
      >
        <div className="lab-fields">
          <Input
            label="Study name"
            value={doc.name}
            onChange={(value) => patch("name", value)}
          />
          <Input
            label="Status"
            value={doc.status}
            onChange={(value) => patch("status", value)}
          />
          <Textarea
            label="Description"
            value={doc.description}
            onChange={(value) => patch("description", value)}
          />
          <Textarea
            label="Hypothesis"
            value={doc.hypothesis}
            onChange={(value) => patch("hypothesis", value)}
          />
          <Textarea
            label="Independent unit"
            value={doc.independent_unit}
            onChange={(value) => patch("independent_unit", value)}
          />
          {(
            [
              ["conditions", "Conditions (JSON)", "array"],
              ["outcomes", "Outcomes (JSON)", "array"],
              ["sampling_plan", "Sampling plan (JSON)", "object"],
              ["stopping_rules", "Stopping rules (JSON)", "object"],
              ["metadata", "Protocol metadata (JSON)", "object"],
            ] as const
          ).map(([key, label, kind]) => (
            <DraftField
              jsonCodec={scientificJson}
              key={key}
              fieldKey={`protocol:${key}`}
              label={label}
              kind={kind}
              value={doc[key]}
              onChange={(value) => patch(key, value)}
              report={draft.report}
            />
          ))}
        </div>
        {!finiteJson(doc) && (
          <p role="alert">Protocol JSON must contain finite numbers.</p>
        )}
        {action.error && (
          <p role="alert">{action.error} Your draft is preserved.</p>
        )}
        {action.busy && <p role="status">Study request in progress…</p>}
        <Button
          disabled={action.busy || importPending || (!createAttempt && !valid)}
          onClick={save}
        >
          {createAttempt && !action.busy
            ? "Retry original study save"
            : base
              ? "Save as new protocol revision"
              : "Save study"}
        </Button>
        {base && (
          <p className="text-caption-1-regular text-text-secondary">
            This saves a new revision above expected head revision{" "}
            {expectedHead}. Existing collection records keep their original
            protocol.
          </p>
        )}
        {createAttempt && !action.busy && (
          <div>
            <p>
              The earlier save may have succeeded. Retry sends its original
              payload and key. Check the study directory before starting another
              attempt, which could create a second study.
            </p>
            <details>
              <summary>Original study save payload</summary>
              <pre>{json(createAttempt.body)}</pre>
            </details>
            <Button
              onClick={() => {
                setCreateAttempt(null);
                setCreateKey(crypto.randomUUID());
                action.clearError();
              }}
            >
              Start new study save attempt
            </Button>
          </div>
        )}
        {base && conflict && (
          <Button
            disabled={action.busy}
            onClick={() =>
              void action.perform(
                (signal) => api.get<Study>(studyPath(base.id), signal),
                setLatest,
              )
            }
          >
            Inspect latest protocol
          </Button>
        )}
        {latest && (
          <div>
            <details open>
              <summary>
                Latest retained protocol revision {latest.revision}
              </summary>
              <pre>{json(latest)}</pre>
            </details>
            <Button
              onClick={() => {
                setExpectedHead(latest.head_revision);
                setLatest(null);
                setConflict(false);
                action.clearError();
              }}
            >
              Use head revision {latest.head_revision} for next save
            </Button>
          </div>
        )}
      </SettingsSection>
      {base && (
        <>
          <SettingsSection
            title="Protocol history"
            description="Opening a historical protocol also makes that revision available for new record associations."
          >
            {history.loading && <p role="status">Loading protocol history…</p>}
            {history.error && (
              <>
                <p role="alert">{history.error.message}</p>
                <Button onClick={history.reload}>Retry protocol history</Button>
              </>
            )}
            {(Array.isArray(history.data) ? history.data : []).map((row) => (
              <div key={row.revision}>
                <Button
                  disabled={importPending}
                  onClick={() => onRevision(base.id, row.revision)}
                >
                  Open protocol revision {row.revision}
                </Button>
                <p className="text-caption-1-regular text-text-secondary">
                  {row.name} · {row.status} · {row.author_id} ·{" "}
                  {row.recorded_at} · Digest: {row.digest}
                </p>
              </div>
            ))}
          </SettingsSection>
          <p>
            Ledger records: {counts?.records ?? "unavailable"} · Declared unit
            IDs: {counts?.declared_unit_ids ?? "unavailable"}. These are
            bookkeeping counts, not independent sample sizes or a success
            judgment.
          </p>
          {header.loading && (
            <p role="status">Refreshing bookkeeping totals…</p>
          )}
          {header.error && (
            <div>
              <p role="alert">
                Study totals unavailable: {header.error.message}
              </p>
              <Button onClick={header.reload}>Retry study totals</Button>
            </div>
          )}
          <CollectionRecords
            study={base}
            refreshVersion={recordRefresh}
            onChanged={() => {
              header.reload();
              onChanged();
            }}
            onDirty={setRecordDirty}
          />
          <StudyImportPanel
            study={base}
            onDirty={setImportDirty}
            onPendingChange={(pending) => {
              setImportPending(pending);
              onImportPending(pending);
            }}
            onImported={() => {
              setRecordRefresh((value) => value + 1);
              header.reload();
              onChanged();
            }}
          />
          <StudyAnalysisPanel
            key={base.id}
            study={base}
            onDirty={setAnalysisDirty}
            currentHead={{
              revision:
                header.data?.id === base.id
                  ? header.data.head_revision
                  : base.head_revision,
              last_sequence:
                header.data?.id === base.id
                  ? header.data.last_sequence
                  : base.last_sequence,
            }}
            onResultLinked={() => {
              header.reload();
              onChanged();
            }}
          />
          <SettingsSection
            title="Tracking export"
            description="Exports retained protocol revisions, collection records and frozen evidence identities. Unsaved drafts are not included. External artifact bytes are not included; use existing artifact and run exports to obtain them."
          >
            <Button disabled={action.busy} onClick={exportTracking}>
              Export tracking manifest
            </Button>
            {exported && (
              <Evidence label="Study tracking export snapshot" data={exported}>
                <p>
                  Export snapshot: revision {exported.revision}, through record
                  sequence {exported.through_seq}.
                </p>
                <details>
                  <summary>Tracking export metadata</summary>
                  <pre>{json(exported)}</pre>
                </details>
              </Evidence>
            )}
          </SettingsSection>
        </>
      )}
    </div>
  );
}

type RecordDraft = {
  kind: string;
  unit_id: string;
  condition: string;
  observed_at: string;
  data: JsonObject;
  run_ids: unknown[];
  artifact_ids: unknown[];
  supersedes: string;
};
const blankRecord = (): RecordDraft => ({
  kind: "observation",
  unit_id: "",
  condition: "",
  observed_at: "",
  data: {},
  run_ids: [],
  artifact_ids: [],
  supersedes: "",
});
function CollectionRecords({
  study,
  refreshVersion,
  onChanged,
  onDirty,
}: {
  study: Study;
  refreshVersion: number;
  onChanged: () => void;
  onDirty: (dirty: boolean) => void;
}) {
  const { api } = useStudio();
  const [form, setForm] = useState(blankRecord);
  const [pin, setPin] = useState({
    revision: study.revision,
    digest: study.digest,
  });
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [generation, setGeneration] = useState(0);
  const [attempt, setAttempt] = useState<{
    body: JsonObject;
    version: number;
  } | null>(null);
  const [last, setLast] = useState<CollectionRecord | null>(null);
  const [pages, setPages] = useState([0]);
  const after = pages.at(-1)!;
  const records = useResource<RecordPage>(
    `${studyPath(study.id)}/records?after=${after}&limit=100`,
  );
  const refreshRecords = records.reload;
  useEffect(() => {
    if (refreshVersion) refreshRecords();
  }, [refreshVersion, refreshRecords]);
  const draft = useDraftValidity();
  const action = useStudyAction();
  const dirty =
    !!attempt || !!draft.invalid.size || json(form) !== json(blankRecord());
  const callback = useRef(onDirty);
  callback.current = onDirty;
  useEffect(() => callback.current(dirty), [dirty]);
  useEffect(() => {
    if (!dirty) setPin({ revision: study.revision, digest: study.digest });
  }, [study.revision, study.digest, dirty]);
  const patch = (name: string, value: unknown) => {
    draft.version.current++;
    setForm((old) => ({ ...old, [name]: value }));
  };
  const ids = (values: unknown[]) =>
    values.every((id) => typeof id === "string" && !!id.trim());
  const validTime =
    !form.observed_at.trim() ||
    (/^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:?\d{2})$/.test(
      form.observed_at,
    ) &&
      Number.isFinite(
        Date.parse(form.observed_at.replace(/[t ]/, "T").replace(/z$/, "Z")),
      ));
  const valid =
    !!form.kind.trim() &&
    !draft.invalid.size &&
    finiteJson(form.data) &&
    ids(form.run_ids) &&
    ids(form.artifact_ids) &&
    validTime;
  const append = () => {
    if (action.busy) return;
    const submitted = attempt ?? {
      body: {
        ...copy(form),
        revision: pin.revision,
        idempotency_key: key,
        observed_at: form.observed_at.trim() ? form.observed_at : null,
        supersedes: form.supersedes.trim() || null,
      },
      version: draft.version.current,
    };
    setAttempt(submitted);
    void action.perform(
      (signal) =>
        api.post<CollectionRecord>(
          `${studyPath(study.id)}/records`,
          withScientificJson(submitted.body),
          signal,
        ),
      (record) => {
        if (
          record.study_id !== study.id ||
          record.revision !== submitted.body.revision
        )
          throw new Error(
            "Appended record does not match this study and protocol revision.",
          );
        setLast(record);
        setAttempt(null);
        setKey(crypto.randomUUID());
        if (draft.version.current === submitted.version) {
          setForm(blankRecord());
          setGeneration((value) => value + 1);
        }
        records.reload();
        onChanged();
      },
      (failure) => {
        if (failure instanceof ApiError && failure.status === 422)
          setAttempt(null);
      },
    );
  };
  return (
    <>
      <SettingsSection
        title="Append collection record"
        description="Record your observations or notes, including negative, missing or custom outcomes. Linking existing evidence freezes its metadata at association time; it does not verify a scientific claim. Corrections append a new record and retain the original."
      >
        <p>
          Record draft pins protocol revision {pin.revision} · Digest:{" "}
          {pin.digest}
        </p>
        {pin.revision !== study.revision && (
          <div>
            <p>
              This draft still refers to its earlier protocol; the editor now
              displays revision {study.revision}.
            </p>
            <Button
              disabled={!!attempt || action.busy}
              onClick={() =>
                setPin({ revision: study.revision, digest: study.digest })
              }
            >
              Retarget record draft to displayed protocol revision{" "}
              {study.revision}
            </Button>
          </div>
        )}
        <div className="lab-fields">
          <Input
            label="Record kind"
            value={form.kind}
            onChange={(value) => patch("kind", value)}
          />
          <Input
            label="Declared unit ID"
            value={form.unit_id}
            onChange={(value) => patch("unit_id", value)}
          />
          <Input
            label="Condition"
            value={form.condition}
            onChange={(value) => patch("condition", value)}
          />
          <Input
            label="Observed at"
            hint="Optional ISO 8601 time with Z or a timezone offset. Server recording time is retained separately."
            value={form.observed_at}
            onChange={(value) => patch("observed_at", value)}
          />
          <Input
            label="Supersedes record ID"
            value={form.supersedes}
            onChange={(value) => patch("supersedes", value)}
          />
          <DraftField
            jsonCodec={scientificJson}
            key={`data:${generation}`}
            fieldKey="record:data"
            label="Record data (JSON)"
            kind="object"
            value={form.data}
            onChange={(value) => patch("data", value)}
            report={draft.report}
          />
          <DraftField
            jsonCodec={scientificJson}
            key={`runs:${generation}`}
            fieldKey="record:runs"
            label="Run IDs (JSON)"
            kind="array"
            value={form.run_ids}
            onChange={(value) => patch("run_ids", value)}
            report={draft.report}
          />
          <DraftField
            jsonCodec={scientificJson}
            key={`artifacts:${generation}`}
            fieldKey="record:artifacts"
            label="Artifact IDs (JSON)"
            kind="array"
            value={form.artifact_ids}
            onChange={(value) => patch("artifact_ids", value)}
            report={draft.report}
          />
        </div>
        {!validTime && (
          <p role="alert">
            Observed at must include an explicit timezone; leave it blank if
            unknown.
          </p>
        )}
        {(!ids(form.run_ids) || !ids(form.artifact_ids)) && (
          <p role="alert">
            Run and artifact IDs must be arrays of nonempty strings.
          </p>
        )}
        {!finiteJson(form.data) && (
          <p role="alert">Record JSON must contain finite numbers.</p>
        )}
        {action.error && (
          <p role="alert">{action.error} Your collection draft is preserved.</p>
        )}
        {action.busy && <p role="status">Appending collection record…</p>}
        <Button disabled={action.busy || (!attempt && !valid)} onClick={append}>
          {attempt && !action.busy
            ? "Retry original record append"
            : "Append collection record"}
        </Button>
        {attempt && !action.busy && (
          <div>
            <p>
              The earlier append may have succeeded. Retry sends the same
              original payload and key. Starting another attempt can create an
              additional record; inspect the ledger first.
            </p>
            <details>
              <summary>Original record append payload</summary>
              <pre>{json(attempt.body)}</pre>
            </details>
            <Button
              onClick={() => {
                setAttempt(null);
                setKey(crypto.randomUUID());
                action.clearError();
              }}
            >
              Start new record append attempt
            </Button>
          </div>
        )}
        {last && (
          <p role="status">
            Recorded collection record #{last.seq} · {last.id} · protocol
            revision {last.revision}.
          </p>
        )}
      </SettingsSection>
      <SettingsSection
        title="Collection ledger"
        description="All protocol revisions are included. Sequence, declared IDs and correction links are descriptive bookkeeping, not independent sample accrual or analytical exclusions."
      >
        {records.loading && <p role="status">Loading collection records…</p>}
        {records.error && (
          <>
            <p role="alert">{records.error.message}</p>
            <Button onClick={records.reload}>Retry collection records</Button>
          </>
        )}
        {records.data && (
          <p>Latest ledger sequence: {records.data.latest_seq}</p>
        )}
        {(records.data?.records ?? []).map((record) => (
          <Evidence
            key={record.id}
            label={`Study collection record ${record.seq}`}
            data={record}
          >
            <details>
              <summary>
                Record #{record.seq} · {record.kind}
              </summary>
              <p>
                Protocol revision {record.revision} · Digest:{" "}
                {record.protocol_digest}
              </p>
              <p>
                Observed at: {record.observed_at ?? "unknown"} · Server recorded
                at: {record.recorded_at} · Author: {record.author_id}
              </p>
              <p>Record digest: {record.digest}</p>
              <pre>{json(record)}</pre>
            </details>
          </Evidence>
        ))}
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={records.loading || pages.length < 2}
            onClick={() => setPages((old) => old.slice(0, -1))}
          >
            Previous record page
          </Button>
          <Button
            disabled={records.loading || !records.data?.has_more}
            onClick={() => {
              if (records.data)
                setPages((old) => [...old, records.data!.next_after]);
            }}
          >
            Next record page
          </Button>
        </div>
      </SettingsSection>
    </>
  );
}
