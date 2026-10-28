import { useEffect, useRef, useState } from "react";
import { useStudio } from "../../app/StudioContext";
import { Input } from "../../components/boardui/base/input/input";
import { Button } from "../../components/studio/primitives";
import { useComponentAction } from "../assistant/actions";
import type { LabRun } from "./definition";
import { dataArtifacts } from "./evidence";
import { LabChoice } from "./inputs";
import {
  isOperationTerminal,
  waitForOperation,
  type LabOperation,
} from "./operations";

interface PromotionResult {
  dataset: { id: string; name: string };
  snapshot?: {
    id: string;
    name: string;
    training_count: number;
    validation_count: number;
  };
  promoted_rows: number;
  excluded_final_test_rows: number;
  excluded_truth_rows: number;
}
const chatRoles = new Set([
  "supervised_dataset",
  "chat_train",
  "chat_validation",
  "chat_dataset",
]);
export function DatasetPromotion(props: {
  run: LabRun;
  onOperationChanged: () => void;
}) {
  const studio = useStudio();
  return (
    <PromotionForm key={`${studio.scopeKey}:${props.run.id}`} {...props} />
  );
}
function PromotionForm({
  run,
  onOperationChanged,
}: {
  run: LabRun;
  onOperationChanged: () => void;
}) {
  const studio = useStudio();
  const candidates = dataArtifacts(run).all.filter(
    (item) => chatRoles.has(item.role ?? "") && item.visibility !== "truth",
  );
  const [selected, setSelected] = useState(candidates[0]?.id ?? "");
  const [name, setName] = useState("");
  const [snapshotName, setSnapshotName] = useState("");
  const [minimum, setMinimum] = useState("10");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [operation, setOperation] = useState<LabOperation | null>(null);
  const lifetime = useRef<AbortController | null>(null);
  const flight = useRef(false);
  const admission = useRef<{ intent: string; key: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);
  const completed = run.state === "succeeded" || run.state === "completed";
  const valid =
    completed &&
    candidates.some((item) => item.id === selected) &&
    !!name.trim() &&
    (!snapshotName.trim() ||
      (/^\d+$/.test(minimum) &&
        Number(minimum) >= 1 &&
        Number(minimum) <= 10000));
  const active = !!operation && !isOperationTerminal(operation.state);
  const succeeded = operation?.state === "succeeded";
  const submit = async (_value?: string, actionSignal?: AbortSignal) => {
    const signal = lifetime.current?.signal;
    if (
      !signal ||
      signal.aborted ||
      actionSignal?.aborted ||
      flight.current ||
      !valid ||
      succeeded
    )
      return;
    flight.current = true;
    setBusy(true);
    setError(null);
    // Cancel observation when an assistant action is revoked; durable server work
    // remains recoverable from the shared operation history.
    const observer = new AbortController();
    const abort = () => observer.abort();
    signal.addEventListener("abort", abort, { once: true });
    actionSignal?.addEventListener("abort", abort, { once: true });
    try {
      let current = operation;
      if (!current) {
        const intent = {
          run_id: run.id,
          artifact_id: selected,
          name: name.trim(),
          snapshot_name: snapshotName.trim() || null,
          min_training: snapshotName.trim() ? Number(minimum) : 10,
        };
        const serialized = JSON.stringify(intent);
        if (admission.current?.intent !== serialized)
          admission.current = { intent: serialized, key: crypto.randomUUID() };
        current = await studio.api.post<LabOperation>(
          "/api/lab/datasets/from-run",
          { ...intent, idempotency_key: admission.current.key },
          observer.signal,
        );
        if (observer.signal.aborted) return;
        setOperation(current);
        onOperationChanged();
      }
      await waitForOperation(studio.api, current, observer.signal, (next) => {
        if (!observer.signal.aborted) setOperation(next);
      });
    } catch (cause) {
      if (!observer.signal.aborted)
        setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      signal.removeEventListener("abort", abort);
      actionSignal?.removeEventListener("abort", abort);
      flight.current = false;
      if (!signal.aborted) {
        setBusy(false);
        onOperationChanged();
      }
    }
  };
  useComponentAction({
    id: `lab.dataset-promote.${run.id}`,
    label: "Promote selected retained chats to dataset",
    kind: "mutate",
    state: {
      run_id: run.id,
      artifact_id: selected,
      dataset_name: name,
      snapshot_name: snapshotName,
      operation_id: operation?.id,
    },
    disabled: !valid || busy || succeeded || (!!operation && !active),
    run: submit,
  });
  if (!completed) return null;
  const result = succeeded
    ? (operation.result as PromotionResult | undefined)
    : undefined;
  const locked = busy || !!operation;
  return (
    <section
      aria-label="Promote retained chats"
      className="flex flex-col gap-3"
    >
      <div>
        <h3 className="text-headline-medium text-text-primary">
          Create dataset from retained chats
        </h3>
        <p className="text-body-2-regular text-text-secondary">
          Only explicit training and validation chats are eligible. Final-test
          and truth rows remain excluded. Training starts separately.
        </p>
      </div>
      {!candidates.length ? (
        <p>
          No eligible retained chat artifacts. Numeric records cannot be
          promoted as chats.
        </p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <LabChoice
              label="Retained chat artifact"
              value={selected}
              onChange={setSelected}
              disabled={locked}
              options={candidates.map((item) => ({
                id: item.id,
                name: `${item.name} · ${item.role}`,
              }))}
            />
            <Input
              label="Dataset name"
              value={name}
              onChange={setName}
              isDisabled={locked}
            />
            <Input
              label="Snapshot name (optional)"
              value={snapshotName}
              onChange={setSnapshotName}
              isDisabled={locked}
              hint="Provide a name to freeze a training snapshot after promotion."
            />
            {snapshotName.trim() && (
              <Input
                label="Minimum training rows"
                type="number"
                hint="Between 1 and 10,000 rows"
                value={minimum}
                onChange={setMinimum}
                isDisabled={locked}
              />
            )}
          </div>
          {!succeeded && (!operation || active) && (
            <div>
              <Button disabled={!valid || busy} onClick={() => void submit()}>
                {busy
                  ? "Promoting…"
                  : active
                    ? "Reconnect promotion"
                    : "Create dataset"}
              </Button>
            </div>
          )}
        </>
      )}
      {operation && (
        <p role="status">
          Dataset promotion: {operation.state} · Operation {operation.id}
        </p>
      )}
      {error && (
        <p role="alert" className="text-text-error-primary">
          {error}
        </p>
      )}
      {operation && !succeeded && (
        <p className="text-body-2-regular text-text-secondary">
          This operation continues after leaving this view. Use Laboratory
          operation history to inspect, cancel, or retry it.
        </p>
      )}
      {result?.dataset && (
        <div className="flex flex-col gap-2">
          <p>
            {result.promoted_rows} chats promoted;{" "}
            {result.excluded_final_test_rows} final-test rows and{" "}
            {result.excluded_truth_rows} truth rows excluded.
          </p>
          <div className="flex flex-wrap gap-3">
            <a href={`#/datasets/${encodeURIComponent(result.dataset.name)}`}>
              Open dataset {result.dataset.name}
            </a>
            {result.snapshot && (
              <a href="#/finetuning">
                Open training · choose snapshot {result.snapshot.name}
              </a>
            )}
          </div>
          {result.snapshot && (
            <p>
              Snapshot {result.snapshot.name}: {result.snapshot.training_count}{" "}
              training, {result.snapshot.validation_count} validation. No
              training job has been queued.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
