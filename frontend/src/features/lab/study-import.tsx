import { useCallback, useEffect, useRef, useState } from "react";
import { Input } from "@/components/boardui/base/input/input";
import { useStudio } from "../../app/StudioContext";
import { ApiError, withScientificJson } from "../../api/client";
import { scientificJson } from "../../api/scientific-json";
import { Button, Evidence } from "../../components/studio/primitives";
import { SettingsSection } from "../../components/studio/settings-section";
import { DraftField } from "./data-preparation";

type JsonObject = Record<string, unknown>;
export type StudyObservationImportStudy = {
  id: string;
  revision: number;
  digest: string;
  actions?: {
    observation_import_preview: string;
    observation_import: string;
  };
};
type PreviewRequest = {
  artifact_id: string;
  revision: number;
  projection: JsonObject;
};
type ImportRequest = PreviewRequest & {
  preview_digest: string;
  idempotency_key: string;
};
type ImportPreview = {
  format: string;
  study_id: string;
  revision: number;
  protocol_digest: string;
  source: {
    id: string;
    sha256: string;
    size: number;
    role: string;
    visibility: string;
  };
  projection: JsonObject;
  summary: {
    count: number;
    rows_sha256: string;
    preview: unknown[];
    number_encoding: string;
    disclosure: unknown;
  };
  implementation_sha256: string;
  digest: string;
};
type ImportResult = {
  receipt: JsonObject & {
    id: string;
    study_id: string;
    revision: number;
    protocol_digest: string;
    kind: string;
    seq: number;
    data: {
      format: string;
      preview: Omit<ImportPreview, "summary"> & {
        summary: Omit<ImportPreview["summary"], "preview">;
      };
      count: number;
      first_sequence: number | null;
      last_sequence: number | null;
    };
  };
  count: number;
  first_sequence: number | null;
  last_sequence: number | null;
  replayed: boolean;
};
type Attempt = {
  path: string;
  body: ImportRequest;
  protocolDigest: string;
  sourceSha256: string;
  count: number;
  rowsSha256: string;
};
export type StudyImportPanelProps = {
  study: StudyObservationImportStudy;
  onImported: () => void;
  onPendingChange?: (pending: boolean) => void;
  onDirty?: (dirty: boolean) => void;
};
const json = (value: unknown) => scientificJson.stringify(value, 2);
const copy = <T,>(value: T): T =>
  scientificJson.parse(scientificJson.stringify(value)) as T;
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
const sha256 = (value: unknown) =>
  typeof value === "string" && /^[0-9a-f]{64}$/.test(value);

/** A response belongs to the project and study that started its request. */
export function StudyImportPanel(props: StudyImportPanelProps) {
  const { scopeKey } = useStudio();
  return (
    <ImportAuthor key={JSON.stringify([scopeKey, props.study.id])} {...props} />
  );
}
function ImportAuthor({
  study,
  onImported,
  onPendingChange,
  onDirty,
}: StudyImportPanelProps) {
  const { api } = useStudio();
  const [artifactId, setArtifactId] = useState("");
  const [projection, setProjection] = useState<JsonObject>({});
  const [projectionValid, setProjectionValid] = useState(true);
  const [preview, setPreview] = useState<{
    signature: string;
    request: PreviewRequest;
    data: ImportPreview;
  }>();
  const [previewBusy, setPreviewBusy] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [attempt, setAttempt] = useState<Attempt>();
  const [result, setResult] = useState<ImportResult>();
  const [error, setError] = useState("");
  const previewController = useRef<AbortController | null>(null);
  const importController = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const pending = useRef(attempt);
  pending.current = attempt;
  const callbacks = useRef({ onImported, onPendingChange, onDirty });
  callbacks.current = { onImported, onPendingChange, onDirty };
  const previewPath = study.actions?.observation_import_preview;
  const importPath = study.actions?.observation_import;
  const signature = scientificJson.stringify([
    study.id,
    study.revision,
    study.digest,
    previewPath,
    importPath,
    artifactId,
    projectionValid,
    projection,
  ]);
  const currentSignature = useRef(signature);
  currentSignature.current = signature;
  const currentPreview =
    preview?.signature === signature && projectionValid ? preview : undefined;
  const dirty =
    !!artifactId || !projectionValid || json(projection) !== "{}" || !!attempt;
  useEffect(() => callbacks.current.onDirty?.(dirty), [dirty]);
  useEffect(() => callbacks.current.onPendingChange?.(!!attempt), [attempt]);
  useEffect(
    () => () => {
      previewController.current?.abort();
      importController.current?.abort();
      callbacks.current.onPendingChange?.(false);
    },
    [],
  );

  const invalidatePreview = useCallback(() => {
    generation.current++;
    previewController.current?.abort();
    previewController.current = null;
    setPreview(undefined);
    setPreviewBusy(false);
    setError("");
  }, []);
  useEffect(() => {
    invalidatePreview();
  }, [
    study.revision,
    study.digest,
    previewPath,
    importPath,
    invalidatePreview,
  ]);
  const report = useCallback(
    (_field: string, valid: boolean) => {
      if (pending.current) return;
      setProjectionValid(valid);
      invalidatePreview();
    },
    [invalidatePreview],
  );
  const canPreview =
    !!previewPath &&
    !!importPath &&
    !!artifactId.trim() &&
    projectionValid &&
    !attempt;
  const review = async () => {
    if (!canPreview || previewController.current || !previewPath) return;
    const owner = new AbortController();
    previewController.current = owner;
    const submittedGeneration = ++generation.current;
    const submittedSignature = signature;
    const body: PreviewRequest = copy({
      artifact_id: artifactId,
      revision: study.revision,
      projection,
    });
    const current = () =>
      !owner.signal.aborted &&
      generation.current === submittedGeneration &&
      currentSignature.current === submittedSignature;
    setPreview(undefined);
    setPreviewBusy(true);
    setError("");
    try {
      const data = await api.post<ImportPreview>(
        previewPath,
        withScientificJson(body),
        owner.signal,
      );
      if (!current()) return;
      if (
        data.format !== "tinystudio.study-observation-import.v1" ||
        data.study_id !== study.id ||
        data.revision !== body.revision ||
        data.protocol_digest !== study.digest ||
        data.source?.id !== body.artifact_id ||
        !sha256(data.source?.sha256) ||
        !sha256(data.digest) ||
        !sha256(data.implementation_sha256) ||
        !Number.isSafeInteger(data.summary?.count) ||
        data.summary.count < 0 ||
        !sha256(data.summary.rows_sha256) ||
        !Array.isArray(data.summary.preview)
      )
        throw new Error(
          "Observation preview does not match this source, study and protocol revision.",
        );
      setPreview({ signature: submittedSignature, request: body, data });
    } catch (failure) {
      if (current()) setError(errorText(failure));
    } finally {
      if (previewController.current === owner) {
        previewController.current = null;
        setPreviewBusy(false);
      }
    }
  };
  const publish = async () => {
    if (importController.current) return;
    const retrying = !!pending.current;
    const submitted =
      pending.current ??
      (currentPreview && importPath
        ? {
            path: importPath,
            body: copy({
              ...currentPreview.request,
              preview_digest: currentPreview.data.digest,
              idempotency_key: crypto.randomUUID(),
            }),
            protocolDigest: currentPreview.data.protocol_digest,
            sourceSha256: currentPreview.data.source.sha256,
            count: currentPreview.data.summary.count,
            rowsSha256: currentPreview.data.summary.rows_sha256,
          }
        : undefined);
    if (!submitted) return;
    pending.current = submitted;
    setAttempt(submitted);
    const owner = new AbortController();
    importController.current = owner;
    setImportBusy(true);
    setError("");
    setResult(undefined);
    try {
      const imported = await api.post<ImportResult>(
        submitted.path,
        withScientificJson(submitted.body),
        owner.signal,
      );
      if (owner.signal.aborted) return;
      const receiptData = imported.receipt?.data;
      const retainedPreview = receiptData?.preview;
      const first = imported.first_sequence,
        last = imported.last_sequence;
      const sequencesMatch =
        imported.count === 0
          ? first === null && last === null
          : typeof first === "number" &&
            typeof last === "number" &&
            Number.isSafeInteger(first) &&
            Number.isSafeInteger(last) &&
            first >= 1 &&
            last - first + 1 === imported.count &&
            imported.receipt?.seq === last + 1;
      if (
        imported.receipt?.study_id !== study.id ||
        imported.receipt.revision !== submitted.body.revision ||
        imported.receipt.protocol_digest !== submitted.protocolDigest ||
        imported.receipt.kind !== "observation_import" ||
        !imported.receipt.id ||
        !Number.isSafeInteger(imported.receipt.seq) ||
        imported.receipt.seq < 1 ||
        imported.count !== submitted.count ||
        receiptData?.format !== "tinystudio.study-observation-import.v1" ||
        receiptData.count !== submitted.count ||
        receiptData.first_sequence !== imported.first_sequence ||
        receiptData.last_sequence !== imported.last_sequence ||
        retainedPreview?.digest !== submitted.body.preview_digest ||
        retainedPreview.source?.id !== submitted.body.artifact_id ||
        retainedPreview.source?.sha256 !== submitted.sourceSha256 ||
        retainedPreview.summary?.count !== submitted.count ||
        retainedPreview.summary?.rows_sha256 !== submitted.rowsSha256 ||
        !sequencesMatch
      )
        throw new Error(
          "Import receipt does not match the original preview, study and protocol revision. Retry the original request to resolve its outcome.",
        );
      setResult(imported);
      pending.current = undefined;
      setAttempt(undefined);
      callbacks.current.onImported();
    } catch (failure) {
      if (owner.signal.aborted) return;
      // An initial definite rejection rules out publication. A rejection on a
      // retry cannot resolve whether the original uncertain request committed.
      if (
        !retrying &&
        failure instanceof ApiError &&
        [400, 403, 404, 409, 422].includes(failure.status)
      ) {
        pending.current = undefined;
        setAttempt(undefined);
        invalidatePreview();
      }
      setError(errorText(failure));
    } finally {
      if (!owner.signal.aborted && importController.current === owner) {
        importController.current = null;
        setImportBusy(false);
      }
    }
  };

  return (
    <SettingsSection
      title="Import study observations"
      description="Project retained JSONL rows into observations using your own explicit mapping. Preview validates the source without writing ledger records or artifacts."
    >
      <p className="text-body-2-regular text-text-secondary">
        Selected protocol revision {study.revision}. The mapping defines row
        selection, unit IDs, conditions and outcomes; it makes no independence
        or sampling assumptions.
      </p>
      {!previewPath || !importPath ? (
        <p role="status">
          Observation import actions are unavailable for this study.
        </p>
      ) : null}
      <fieldset disabled={!!attempt} className="flex min-w-0 flex-col gap-4">
        <legend className="sr-only">
          Observation import source and projection
        </legend>
        <Input
          label="Observation source artifact ID"
          value={artifactId}
          onChange={(value) => {
            if (pending.current) return;
            invalidatePreview();
            setArtifactId(value);
          }}
        />
        <DraftField
          label="Observation projection (JSON)"
          fieldKey="observation-import:projection"
          kind="object"
          value={projection}
          jsonCodec={scientificJson}
          report={report}
          onChange={(value) => {
            if (!pending.current) setProjection(value as JsonObject);
          }}
        />
      </fieldset>
      <details>
        <summary>Projection mapping reference</summary>
        <div className="flex flex-col gap-2 text-body-2-regular text-text-secondary">
          <p>
            Supply selection as {'{"all":true}'} or {'{"lines":[1,3]}'}; line
            numbers identify physical JSONL lines starting at 1. Each selected
            nonblank row must be a JSON object.
          </p>
          <p>
            Supply unit_id, condition, and each named data outcome as a
            selector: {'{"pointer":"/path"}'} uses an RFC 6901 JSON pointer;{" "}
            {'{"literal":"your value"}'} supplies your authored value.
            observed_at is optional. kind accepts your own record vocabulary and
            defaults to {'"observation"'} when omitted.
          </p>
          <p>
            Choose number_encoding explicitly: {'"decimal_strings"'} retains
            exact source numeric tokens as strings; {'"json"'} uses JSON numbers
            and binary64 floating-point values may round. Literal selector
            values retain their authored types. No values are coerced or
            aggregated.
          </p>
        </div>
      </details>
      <p className="text-body-2-regular text-text-secondary">
        Repeats are retained. Row counts are bookkeeping counts, not an
        independent sample size. Source line identity and raw line hashes remain
        available in the preview and imported provenance.
      </p>
      {error && <p role="alert">{error}</p>}
      {previewBusy && <p role="status">Validating selected source rows…</p>}
      <Button
        disabled={!canPreview || previewBusy}
        onClick={() => void review()}
      >
        Preview observation import
      </Button>
      {currentPreview && (
        <Evidence label="Observation import preview" data={currentPreview.data}>
          <p>
            Selected observation rows: {currentPreview.data.summary.count} ·
            Protocol revision {currentPreview.data.revision}
          </p>
          <p className="break-all">
            Source SHA-256: {currentPreview.data.source.sha256}
          </p>
          <p>
            Source: {currentPreview.data.source.id} ·{" "}
            {currentPreview.data.source.size} bytes ·{" "}
            {currentPreview.data.source.role} ·{" "}
            {currentPreview.data.source.visibility}
          </p>
          <p className="break-all">
            Projected rows SHA-256: {currentPreview.data.summary.rows_sha256}
          </p>
          <p>
            At most five rows below are for display only; the count and digest
            cover every selected row.
          </p>
          <details>
            <summary>First five projected rows and source identities</summary>
            <pre dir="ltr">
              {json(currentPreview.data.summary.preview.slice(0, 5))}
            </pre>
          </details>
          <details>
            <summary>Normalized mapping and numeric disclosure</summary>
            <pre dir="ltr">
              {json({
                projection: currentPreview.data.projection,
                number_encoding: currentPreview.data.summary.number_encoding,
                disclosure: currentPreview.data.summary.disclosure,
              })}
            </pre>
          </details>
          <Button disabled={!!attempt} onClick={() => void publish()}>
            Import {currentPreview.data.summary.count} observation rows
          </Button>
        </Evidence>
      )}
      {attempt && (
        <div className="flex flex-col gap-2" role="status">
          <p>
            The original import is pinned to protocol revision{" "}
            {attempt.body.revision}. Its outcome remains unresolved. Source and
            mapping edits and study navigation stay locked until the original
            request returns a matching receipt. A rejection of a retry does not
            establish the original outcome.
          </p>
          <p>
            Retry sends the exact original payload and idempotency key; it does
            not create a second copy of a successful import. Before leaving this
            view, retain the original request below, including its idempotency
            key, to resolve an uncertain outcome.
          </p>
          <details>
            <summary>Original observation import request</summary>
            <pre dir="ltr">{json(attempt.body)}</pre>
          </details>
          <Button disabled={importBusy} onClick={() => void publish()}>
            {importBusy
              ? "Import request in progress…"
              : "Retry original observation import"}
          </Button>
        </div>
      )}
      {result && (
        <Evidence label="Observation import receipt" data={result}>
          <p role="status">
            Imported {result.count} observation rows · Protocol revision{" "}
            {result.receipt.revision} · Observation sequences{" "}
            {result.first_sequence ?? "none"}–{result.last_sequence ?? "none"} ·
            Receipt sequence {result.receipt.seq}
            {result.replayed ? " · Replayed original receipt" : ""}
          </p>
          <details>
            <summary>Retained import receipt</summary>
            <pre dir="ltr">{json(result)}</pre>
          </details>
        </Evidence>
      )}
    </SettingsSection>
  );
}
