import { useEffect, useMemo, useRef, useState } from "react";
import { useResource, useStudio } from "../../app/StudioContext";
import { SegmentedControl } from "../../components/affectui/controls";
import { ScrollPane } from "../../components/studio/scroll-pane";
import { FileUploadField, UploadStatus } from "../../components/studio/uploads";
import { Button, EmptyState } from "../../components/studio/primitives";
import {
  defaultVisibility,
  inputExpectations,
  type ExperimentDefinition,
  type InputBinding,
} from "./definition";
import { RepositoryInput } from "./repository-input";
import {
  ArtifactTransferClient,
  type SavedTransfer,
  type TransferProgress,
} from "./artifact-transfers";
import "./inputs-desktop.css";
import { Select, SelectItem } from "@/components/boardui/base/select/select";
export function LabChoice({
  label,
  value,
  onChange,
  options,
  disabled = false,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { id: string; name: string; disabled?: boolean }[];
  disabled?: boolean;
  placeholder?: string;
}) {
  const choices =
    options.some((o) => o.id === value) || !value
      ? options
      : [{ id: value, name: `${value} · retained selection` }, ...options];
  return (
    <div className="lab-choice">
      <span className="text-caption-1-medium text-text-secondary">{label}</span>
      <Select
        className="min-w-0 w-full max-w-full"
        triggerClassName="min-w-0 max-w-full"
        size="sm"
        placeholder={placeholder}
        aria-label={label}
        selectedKey={value || null}
        onSelectionChange={(key) => {
          if (key !== null) onChange(String(key));
        }}
        isDisabled={disabled}
      >
        {choices.map((option) => (
          <SelectItem
            id={option.id}
            key={option.id}
            isDisabled={option.disabled}
          >
            {option.name}
          </SelectItem>
        ))}
      </Select>
    </div>
  );
}
export const artifactRoles = [
  "source",
  "corpus",
  "model",
  "checkpoint",
  "snapshot",
  "pretrained_model",
  "embedding_model",
  "retrieval_index",
  "resume",
  "initial_weights",
  "teacher",
  "validation",
  "demonstrations",
  "examples",
  "cases",
  "candidate",
  "baseline",
  "provenance",
  "truth",
  "ground_truth",
  "final_test",
  "firmware",
  "diagram",
  "platform",
  "weather",
  "network",
  "parameters",
  "fmu",
  "world",
];
const visibilityOptions = [
  { id: "model", name: "Model-visible" },
  { id: "evaluation", name: "Evaluation only" },
  { id: "truth", name: "Truth only" },
];
export interface LabInput {
  id: string;
  name: string;
  sha256: string;
  size: number;
  media_type: string;
  origin?: { run_id: string; revision: number; definition_name: string };
}
export function InputArtifacts({
  definition,
  onChange,
  onPendingChange,
  active = true,
  signal,
}: {
  definition: ExperimentDefinition;
  onChange: (inputs: InputBinding[]) => void;
  onPendingChange?: (pending: boolean) => void;
  active?: boolean;
  signal: () => AbortSignal | undefined;
}) {
  const { api, project, scopeKey } = useStudio();
  const inputs = useResource<LabInput[]>("/api/lab/inputs");
  const [inputView, setInputView] = useState("library");
  const [mobile, setMobile] = useState(
    () => window.matchMedia?.("(max-width: 760px)").matches ?? false,
  );
  useEffect(() => {
    const media = window.matchMedia?.("(max-width: 760px)");
    if (!media) return;
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const [files, setFiles] = useState<File[]>([]);
  useEffect(() => {
    onPendingChange?.(files.length > 0);
  }, [files, onPendingChange]);
  useEffect(() => () => onPendingChange?.(false), [onPendingChange]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [progress, setProgress] = useState<TransferProgress | null>(null);
  const [terminal, setTerminal] = useState<"failed" | "paused" | null>(null);
  const [saved, setSaved] = useState<SavedTransfer[]>([]);
  const transfers = useMemo(
    () =>
      project
        ? new ArtifactTransferClient(api, {
            origin: window.location.origin,
            project,
          })
        : null,
    [api, project, scopeKey],
  );
  const expectations = inputExpectations(definition);
  const [role, setRole] = useState(expectations[0]?.role ?? "source");
  const [visibility, setVisibility] = useState<InputBinding["visibility"]>(
    defaultVisibility(expectations[0]?.role ?? "source"),
  );
  const [recent, setRecent] = useState<LabInput[]>([]);
  const chosen = definition.inputs;
  const rows = [
    ...(inputs.data ?? []),
    ...recent.filter((row) => !inputs.data?.some((item) => item.id === row.id)),
  ];
  // Retained bindings remain editable even if their artifact is absent from the list.
  const libraryRows = [
    ...rows,
    ...chosen
      .filter((binding) => !rows.some((row) => row.id === binding.artifact_id))
      .map((binding) => ({
        id: binding.artifact_id,
        name: binding.artifact_id,
        sha256: binding.sha256,
        size: null,
        media_type: "Retained input",
        origin: undefined,
      })),
  ];
  const latest = useRef({ chosen, onChange, role, visibility });
  latest.current = { chosen, onChange, role, visibility };
  const uploadAbort = useRef<AbortController | null>(null);
  const transferScope = useRef(transfers);
  transferScope.current = transfers;
  const refreshSaved = () => {
    if (transferScope.current !== transfers) return;
    if (!transfers) {
      setSaved([]);
      return;
    }
    try {
      setSaved(transfers.pending());
    } catch {
      setError(
        "Saved transfer metadata is unavailable. Existing sessions were left unchanged.",
      );
    }
  };
  useEffect(() => {
    setFiles([]);
    setProgress(null);
    setTerminal(null);
    setError("");
    setNotice("");
    setBusy(false);
    refreshSaved();
    return () => {
      uploadAbort.current?.abort();
      uploadAbort.current = null;
    };
  }, [transfers]);
  const attach = (row: Pick<LabInput, "id" | "sha256">) =>
    latest.current.onChange([
      ...latest.current.chosen.filter((item) => item.artifact_id !== row.id),
      {
        artifact_id: row.id,
        sha256: row.sha256,
        role: latest.current.role,
        visibility: latest.current.visibility,
      },
    ]);
  return (
    <section className="lab-inputs" data-input-view={inputView}>
      <div className="lab-input-view">
        <SegmentedControl
          aria-label="Input source"
          value={inputView}
          onChange={setInputView}
          options={[
            { value: "library", label: "Input library" },
            { value: "import", label: "Import input" },
          ]}
        />
      </div>
      <div className="lab-input-sources">
        <header className="lab-input-heading">
          <h3>Add input</h3>
          <p>
            Import a file or capture a repository. Set its role before attaching
            it to this experiment.
          </p>
        </header>
        {expectations.length > 0 && (
          <ul className="lab-input-contract">
            {expectations.map((item) => (
              <li key={item.role}>
                <code>{item.role}</code> ·{" "}
                {item.required ? "Required" : "Optional"} · {item.description}
              </li>
            ))}
          </ul>
        )}
        <div className="lab-fields">
          <LabChoice
            label="Input role"
            value={role}
            disabled={busy}
            onChange={(next) => {
              setRole(next);
              setVisibility(defaultVisibility(next));
            }}
            options={[
              ...new Set([
                ...expectations.map((e) => e.role),
                ...artifactRoles,
              ]),
            ].map((id) => ({ id, name: id }))}
          />
          <LabChoice
            label="Input visibility"
            value={visibility ?? "model"}
            disabled={busy}
            onChange={(next) =>
              setVisibility(next as InputBinding["visibility"])
            }
            options={visibilityOptions.map((option) => ({
              ...option,
              disabled:
                option.id === "model" && defaultVisibility(role) === "truth",
            }))}
          />
        </div>
        {defaultVisibility(role) === "truth" && (
          <p>Truth and final-test roles stay outside model-visible evidence.</p>
        )}
        {(error || inputs.error) && (
          <p role="alert">{error || inputs.error?.message}</p>
        )}
        <div className="lab-input-import">
          <div className="lab-repository-source">
            <RepositoryInput
              active={active && (!mobile || inputView === "import")}
              inputs={chosen}
              onChange={onChange}
              onCaptured={inputs.reload}
              signal={signal}
            />
          </div>
          <FileUploadField
            presentation="compact"
            label="Import laboratory input"
            files={files}
            onChange={(selected) => {
              setFiles(selected);
              setError("");
              setNotice("");
              setProgress(null);
              setTerminal(null);
            }}
            disabled={busy}
          />
          <Button
            disabled={busy || !files.length || !transfers}
            onClick={async () => {
              if (!transfers) return;
              const scopeSignal = signal();
              if (!scopeSignal || scopeSignal.aborted) return;
              const controller = new AbortController();
              uploadAbort.current = controller;
              const abort = controller.signal;
              const cancel = () => controller.abort();
              scopeSignal.addEventListener("abort", cancel, { once: true });
              setBusy(true);
              setError("");
              setNotice("");
              setTerminal(null);
              try {
                const row = await transfers.upload(
                  files[0],
                  { role, visibility: visibility ?? "model" },
                  {
                    signal: abort,
                    onProgress: (value) => {
                      if (transferScope.current !== transfers) return;
                      setProgress(value);
                      if (
                        value.phase === "admitting" ||
                        value.phase === "verifying"
                      )
                        refreshSaved();
                    },
                  },
                );
                if (!abort.aborted && transferScope.current === transfers) {
                  attach(row);
                  setRecent((current) => [
                    ...current.filter((item) => item.id !== row.id),
                    row,
                  ]);
                  setFiles([]);
                  inputs.reload();
                }
              } catch (e: any) {
                if (transferScope.current !== transfers) return;
                if (e.name === "AbortError") {
                  setTerminal("paused");
                  setNotice(
                    "Import paused. Retry with the same file; any saved transfer is retained.",
                  );
                } else if (!abort.aborted) {
                  setTerminal("failed");
                  setError(e.message);
                }
              } finally {
                scopeSignal.removeEventListener("abort", cancel);
                if (uploadAbort.current === controller) {
                  uploadAbort.current = null;
                  setBusy(false);
                  refreshSaved();
                }
              }
            }}
          >
            {busy
              ? "Importing input…"
              : error && files.length
                ? "Resume / retry import"
                : "Import and attach input"}
          </Button>
          {(progress || terminal) && (
            <UploadStatus
              phase={
                terminal
                  ? terminal === "failed"
                    ? "Import failed"
                    : "Import paused"
                  : {
                      hashing: "Hashing full-file SHA-256",
                      admitting: "Preparing resumable transfer",
                      uploading: "uploading",
                      verifying: "Server verification · not yet attached",
                      verified: "Input verified",
                    }[progress!.phase]
              }
              progress={
                progress
                  ? { loaded: progress.loaded, total: progress.total }
                  : undefined
              }
            />
          )}
          {busy && uploadAbort.current && (
            <Button type="button" onClick={() => uploadAbort.current?.abort()}>
              Pause import
            </Button>
          )}
          {notice && <p role="status">{notice}</p>}
          {saved.length > 0 && (
            <div className="grid gap-2" aria-label="Saved input transfers">
              <p className="text-body-2-regular text-text-secondary">
                Saved transfers remain available after interruption. Reselect
                the same file and choices to resume; attachment waits for server
                verification.
              </p>
              {saved.map((record) => (
                <div key={record.idempotency_key} className="grid gap-2">
                  <p className="text-caption-1-regular text-text-secondary">
                    <bdi>{record.intent.name}</bdi> ·{" "}
                    {record.intent.size.toLocaleString()} bytes ·{" "}
                    {record.intent.role} · {record.intent.visibility} ·{" "}
                    {record.state ?? "admission unconfirmed"}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        setRole(record.intent.role);
                        setVisibility(record.intent.visibility);
                        setFiles([]);
                        setError("");
                        setProgress(null);
                        setTerminal(null);
                        setNotice(
                          `Reselect ${record.intent.name} to resume its saved transfer.`,
                        );
                      }}
                    >
                      Use saved choices for {record.intent.name}
                    </Button>
                    {record.session_id && (
                      <Button
                        type="button"
                        disabled={busy}
                        onClick={async () => {
                          if (!transfers) return;
                          const scopeSignal = signal();
                          if (!scopeSignal || scopeSignal.aborted) return;
                          setBusy(true);
                          setError("");
                          setProgress(null);
                          setTerminal(null);
                          setNotice("Aborting transfer…");
                          try {
                            await transfers.abort(record, {
                              signal: scopeSignal,
                            });
                            if (
                              !scopeSignal.aborted &&
                              transferScope.current === transfers
                            ) {
                              setFiles([]);
                              setProgress(null);
                              setNotice(
                                "Transfer aborted. No input was attached.",
                              );
                            }
                          } catch (cause) {
                            if (
                              !scopeSignal.aborted &&
                              transferScope.current === transfers
                            ) {
                              setTerminal("failed");
                              setNotice("");
                              setError(
                                cause instanceof Error
                                  ? cause.message
                                  : "Transfer abort is unconfirmed.",
                              );
                            }
                          } finally {
                            if (
                              !scopeSignal.aborted &&
                              transferScope.current === transfers
                            ) {
                              setBusy(false);
                              refreshSaved();
                            }
                          }
                        }}
                      >
                        Abort transfer for {record.intent.name}
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
          <p className="lab-input-note">
            Evaluation and truth inputs stay outside model readers.
          </p>
        </div>
      </div>
      <div className="lab-input-library">
        <header className="lab-input-heading">
          <h3>
            Input library <span>{chosen.length} attached</span>
          </h3>
          <p>Reuse retained inputs and manage their role in this experiment.</p>
        </header>
        <ScrollPane label="Input library artifacts">
          {inputs.loading && <p role="status">Loading input artifacts…</p>}
          {libraryRows.map((row) => (
            <div className="lab-input-row" key={row.id}>
              <div>
                <strong title={row.name}>{row.name}</strong>
                <small>
                  {row.size == null
                    ? "Metadata unavailable"
                    : `${row.size.toLocaleString()} bytes`}{" "}
                  · {row.media_type}
                </small>
                {row.origin && (
                  <small className="break-words">
                    {row.origin.definition_name} · r{row.origin.revision} ·{" "}
                    {row.origin.run_id}
                  </small>
                )}
                <details className="lab-input-checksum">
                  <summary>Checksum</summary>
                  <code>{row.sha256}</code>
                </details>
              </div>
              {chosen.some((item) => item.artifact_id === row.id) && (
                <div className="lab-input-binding">
                  <LabChoice
                    label={`Role for ${row.name}`}
                    value={
                      chosen.find((item) => item.artifact_id === row.id)
                        ?.role ?? "source"
                    }
                    onChange={(next) =>
                      onChange(
                        chosen.map((item) =>
                          item.artifact_id === row.id
                            ? {
                                ...item,
                                role: next,
                                visibility:
                                  defaultVisibility(next) === "truth"
                                    ? "truth"
                                    : item.visibility === "truth"
                                      ? "truth"
                                      : defaultVisibility(next) === "evaluation"
                                        ? "evaluation"
                                        : (item.visibility ?? "model"),
                              }
                            : item,
                        ),
                      )
                    }
                    options={artifactRoles.map((id) => ({ id, name: id }))}
                  />
                  <LabChoice
                    label={`Visibility for ${row.name}`}
                    value={
                      chosen.find((item) => item.artifact_id === row.id)
                        ?.visibility ?? "model"
                    }
                    onChange={(next) =>
                      onChange(
                        chosen.map((item) =>
                          item.artifact_id === row.id
                            ? {
                                ...item,
                                visibility: next as InputBinding["visibility"],
                              }
                            : item,
                        ),
                      )
                    }
                    options={visibilityOptions.map((option) => ({
                      ...option,
                      disabled:
                        option.id === "model" &&
                        defaultVisibility(
                          chosen.find((item) => item.artifact_id === row.id)
                            ?.role ?? "source",
                        ) === "truth",
                    }))}
                  />
                </div>
              )}
              <Button
                aria-label={`${chosen.some((item) => item.artifact_id === row.id) ? "Detach" : "Attach"} ${row.name}${row.origin ? ` from ${row.origin.definition_name} revision ${row.origin.revision} run ${row.origin.run_id}` : ""}`}
                aria-pressed={chosen.some(
                  (item) => item.artifact_id === row.id,
                )}
                onClick={() =>
                  chosen.some((item) => item.artifact_id === row.id)
                    ? onChange(
                        chosen.filter((item) => item.artifact_id !== row.id),
                      )
                    : attach(row)
                }
              >
                {chosen.some((item) => item.artifact_id === row.id)
                  ? "Detach"
                  : "Attach"}
              </Button>
            </div>
          ))}
          {!inputs.loading && !libraryRows.length && (
            <EmptyState>No imported inputs in this project.</EmptyState>
          )}
          {!!chosen.length && (
            <details>
              <summary>Bound input checksums ({chosen.length})</summary>
              <pre>{JSON.stringify(chosen, null, 2)}</pre>
            </details>
          )}
        </ScrollPane>
      </div>
    </section>
  );
}
