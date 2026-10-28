import { useEffect, useRef, useState } from "react";
import { useResource, useStudio } from "../../app/StudioContext";
import { Button } from "../../components/studio/primitives";
import { WorkspaceDisclosure } from "../../components/studio/workspace";
import { useComponentAction } from "../assistant/actions";
import type { InputBinding } from "./definition";
import { LabChoice } from "./inputs";
import {
  isOperationTerminal,
  waitForOperation,
  type LabOperation,
} from "./operations";

interface CaptureResult {
  inputs: InputBinding[];
  corpus?: { id?: string; sha256?: string };
  vectors?: { id?: string; sha256?: string };
  manifest?: { id?: string; sha256?: string };
  qualification?: unknown;
}
interface Props {
  inputs: InputBinding[];
  onChange: (inputs: InputBinding[]) => void;
  onCaptured: () => void;
  signal: () => AbortSignal | undefined;
}

export function RepositoryInput(props: Props) {
  const studio = useStudio();
  return <RepositoryCapture key={studio.scopeKey} {...props} />;
}
function RepositoryCapture({ inputs, onChange, onCaptured, signal }: Props) {
  const studio = useStudio();
  const repositories = useResource<{ name: string }[]>("/api/repositories");
  const rows = Array.isArray(repositories.data) ? repositories.data : [];
  const [selected, setSelected] = useState("");
  const [operation, setOperation] = useState<LabOperation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const latest = useRef({ inputs, onChange, onCaptured, signal });
  latest.current = { inputs, onChange, onCaptured, signal };
  const lifetime = useRef<AbortController | null>(null);
  const flight = useRef(false);
  const admission = useRef<{ repository: string; key: string } | null>(null);
  const attached = useRef(new Set<string>());
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);
  const valid = !!selected && rows.some((item) => item.name === selected);
  const terminal = !!operation && isOperationTerminal(operation.state);
  const succeeded = operation?.state === "succeeded";
  const submit = async (_value?: string, actionSignal?: AbortSignal) => {
    const parentSignal = latest.current.signal();
    const localSignal = lifetime.current?.signal;
    if (
      !localSignal ||
      localSignal.aborted ||
      !parentSignal ||
      parentSignal.aborted ||
      actionSignal?.aborted ||
      flight.current ||
      !valid ||
      terminal
    )
      return;
    flight.current = true;
    setBusy(true);
    setError("");
    const observer = new AbortController();
    const cancel = () => observer.abort();
    localSignal.addEventListener("abort", cancel, { once: true });
    parentSignal.addEventListener("abort", cancel, { once: true });
    actionSignal?.addEventListener("abort", cancel, { once: true });
    try {
      let current = operation;
      if (!current) {
        if (admission.current?.repository !== selected)
          admission.current = {
            repository: selected,
            key: crypto.randomUUID(),
          };
        current = await studio.api.post<LabOperation>(
          "/api/lab/inputs/from-repository",
          { repository: selected, idempotency_key: admission.current.key },
          observer.signal,
        );
        if (observer.signal.aborted) return;
        setOperation(current);
      }
      const completed = await waitForOperation(
        studio.api,
        current,
        observer.signal,
        (next) => {
          if (!observer.signal.aborted) setOperation(next);
        },
      );
      if (observer.signal.aborted || attached.current.has(completed.id)) return;
      const result = completed.result as CaptureResult | undefined;
      if (
        !result ||
        !Array.isArray(result.inputs) ||
        result.inputs.length === 0 ||
        result.inputs.some(
          (ref) =>
            !ref ||
            typeof ref.artifact_id !== "string" ||
            !ref.artifact_id ||
            typeof ref.sha256 !== "string" ||
            !/^[a-f0-9]{64}$/.test(ref.sha256) ||
            ref.role !== "corpus" ||
            !["model", "evaluation", "truth"].includes(ref.visibility ?? ""),
        )
      )
        throw new Error(
          "Capture returned invalid corpus input bindings; inspect the retained operation before attaching.",
        );
      const merged = [...latest.current.inputs];
      for (const ref of result.inputs) {
        if (
          !merged.some(
            (existing) =>
              existing.artifact_id === ref.artifact_id &&
              existing.role === ref.role,
          )
        )
          merged.push({ ...ref });
      }
      attached.current.add(completed.id);
      latest.current.onChange(merged);
      latest.current.onCaptured();
    } catch (cause) {
      if (!observer.signal.aborted)
        setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      localSignal.removeEventListener("abort", cancel);
      parentSignal.removeEventListener("abort", cancel);
      actionSignal?.removeEventListener("abort", cancel);
      flight.current = false;
      if (!localSignal.aborted && !parentSignal.aborted) setBusy(false);
    }
  };
  useComponentAction({
    id: "lab.repository-capture",
    label: operation
      ? "Reconnect selected repository capture"
      : "Capture repository and attach corpus",
    kind: "mutate",
    state: {
      repository: selected,
      operation_id: operation?.id,
      operation_state: operation?.state,
    },
    disabled: !valid || busy || terminal,
    run: submit,
  });
  return (
    <WorkspaceDisclosure
      title="Capture an existing repository"
      summary="Retain corpus, chunk vectors and capture manifest"
    >
      <p>
        Capture an existing scoped repository as immutable evidence. Only the
        captured corpus input is attached; vectors and the manifest remain
        retained evidence. Capture does not start embedding or training.
      </p>
      <LabChoice
        label="Repository to capture"
        value={selected}
        options={rows.map((item) => ({
          id: item.name,
          name: item.name,
        }))}
        onChange={(value) => {
          setSelected(value);
          admission.current = null;
        }}
        disabled={busy || !!operation}
      />
      {repositories.loading && <p role="status">Loading repositories…</p>}
      {repositories.error && (
        <>
          <p role="alert">{repositories.error.message}</p>
          <Button onClick={repositories.reload}>Reload repositories</Button>
        </>
      )}
      {!repositories.loading && !repositories.error && !rows.length && (
        <p>No existing repositories in this scope.</p>
      )}
      <div className="lab-actions">
        <Button
          onClick={() => void submit()}
          disabled={!valid || busy || terminal}
        >
          {busy
            ? "Observing repository capture…"
            : operation
              ? "Reconnect capture"
              : "Capture and attach repository"}
        </Button>
        {terminal && (
          <Button
            onClick={() => {
              setOperation(null);
              setError("");
              admission.current = null;
            }}
          >
            Start another capture
          </Button>
        )}
      </div>
      {operation && (
        <p role="status">
          Repository capture: {operation.state} · {operation.id}. Durable server
          work remains recoverable in operation history.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {succeeded && !error && attached.current.has(operation.id) && (
        <p>
          Corpus capture completed. This is an optimistically validated capture;
          encoder revision is unknown. Atomic snapshot consistency and semantic
          reproducibility are unqualified.
        </p>
      )}
    </WorkspaceDisclosure>
  );
}
