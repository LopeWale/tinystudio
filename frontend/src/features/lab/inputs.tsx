import { useEffect, useRef, useState } from "react";
import { useResource, useStudio } from "../../app/StudioContext";
import { FileUploadField } from "../../components/studio/uploads";
import { Button, EmptyState } from "../../components/studio/primitives";
import {
  defaultVisibility,
  inputExpectations,
  type ExperimentDefinition,
  type InputBinding,
} from "./definition";
import { RepositoryInput } from "./repository-input";
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
  "resume",
  "demonstrations",
  "examples",
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
}
export function InputArtifacts({
  definition,
  onChange,
  signal,
}: {
  definition: ExperimentDefinition;
  onChange: (inputs: InputBinding[]) => void;
  signal: () => AbortSignal | undefined;
}) {
  const { api } = useStudio();
  const inputs = useResource<LabInput[]>("/api/lab/inputs");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
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
      })),
  ];
  const latest = useRef({ chosen, onChange, role, visibility });
  latest.current = { chosen, onChange, role, visibility };
  const uploadAbort = useRef<AbortController | null>(null);
  useEffect(() => () => uploadAbort.current?.abort(), []);
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
    <section className="lab-inputs">
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
        <div className="lab-repository-source">
          <RepositoryInput
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
          onChange={setFiles}
          disabled={busy}
        />
        <Button
          disabled={busy || !files.length}
          onClick={async () => {
            const scopeSignal = signal();
            if (!scopeSignal || scopeSignal.aborted) return;
            const controller = new AbortController();
            uploadAbort.current = controller;
            const abort = controller.signal;
            const cancel = () => controller.abort();
            scopeSignal.addEventListener("abort", cancel, { once: true });
            setBusy(true);
            setError("");
            try {
              const body = new FormData();
              body.append("file", files[0]);
              const row = await api.upload<LabInput>("/api/lab/inputs", body, {
                signal: abort,
              });
              if (!abort.aborted) {
                attach(row);
                setRecent((current) => [
                  ...current.filter((item) => item.id !== row.id),
                  row,
                ]);
                setFiles([]);
                inputs.reload();
              }
            } catch (e: any) {
              if (!abort.aborted && e.name !== "AbortError")
                setError(e.message);
            } finally {
              scopeSignal.removeEventListener("abort", cancel);
              if (!abort.aborted) setBusy(false);
            }
          }}
        >
          {busy ? "Importing input…" : "Import and attach input"}
        </Button>
        <p className="lab-input-note">
          Evaluation and truth inputs stay outside model readers.
        </p>
      </div>
      <div className="lab-input-library">
        <header className="lab-input-heading">
          <h3>
            Input library <span>{chosen.length} attached</span>
          </h3>
          <p>Reuse retained inputs and manage their role in this experiment.</p>
        </header>
        {inputs.loading && <p role="status">Loading input artifacts…</p>}
        {libraryRows.map((row) => (
          <div className="lab-input-row" key={row.id}>
            <div>
              <strong>{row.name}</strong>
              <small>
                {row.size == null
                  ? "Metadata unavailable"
                  : `${row.size.toLocaleString()} bytes`}{" "}
                · {row.media_type}
              </small>
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
                    chosen.find((item) => item.artifact_id === row.id)?.role ??
                    "source"
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
              aria-pressed={chosen.some((item) => item.artifact_id === row.id)}
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
                : "Attach"}{" "}
              {row.name}
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
      </div>
    </section>
  );
}
