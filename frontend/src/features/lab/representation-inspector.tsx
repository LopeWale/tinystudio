import { useResource } from "../../app/StudioContext";
import { AnalysisTemplate } from "../../components/studio/templates";
import { SimpleTable } from "../../components/studio/data-table";
import { EmptyState, Evidence } from "../../components/studio/primitives";
import { ErrorNotice } from "../data/common";
import { useComponentAction } from "../assistant/actions";

export type RetainedIdentity = {
  run_id: string;
  artifact_id: string;
  row_index: number;
  artifact_sha256?: string;
  observation_sha256?: string;
};
export type RepresentationInspection = {
  identity: RetainedIdentity;
  observation: Record<string, unknown>;
  observation_jsonl?: string;
  matches: {
    artifact_id: string;
    artifact_sha256: string;
    row_index: number;
    record: Record<string, any>;
  }[];
  legacy_unqualified_records: number;
  qualification: string;
};
export function representationPath(identity: RetainedIdentity) {
  return `/api/lab/runs/${encodeURIComponent(identity.run_id)}/representations?artifact_id=${encodeURIComponent(identity.artifact_id)}&row=${identity.row_index}`;
}
export function RepresentationInspector({
  identity,
}: {
  identity?: RetainedIdentity;
}) {
  const query = identity ? representationPath(identity) : null;
  const resource = useResource<RepresentationInspection>(query);
  const response = resource.data;
  const valid =
    response != null &&
    typeof response === "object" &&
    identity &&
    response.identity?.run_id === identity.run_id &&
    response.identity.artifact_id === identity.artifact_id &&
    response.identity.row_index === identity.row_index &&
    response.observation &&
    typeof response.observation === "object" &&
    !Array.isArray(response.observation) &&
    (response.observation_jsonl === undefined ||
      typeof response.observation_jsonl === "string") &&
    Array.isArray(response.matches) &&
    response.matches.every(
      (match) =>
        match &&
        typeof match.artifact_id === "string" &&
        Number.isSafeInteger(match.row_index) &&
        match.row_index >= 0 &&
        match.record &&
        typeof match.record === "object" &&
        [
          "representation_id",
          "representation_kind",
          "model_input_base64",
          "input_sha256",
          "untruncated_sha256",
          "specification_hash",
        ].every((key) => typeof match.record[key] === "string") &&
        (match.record.model_input_text === null ||
          typeof match.record.model_input_text === "string") &&
        Array.isArray(match.record.token_ids) &&
        match.record.token_ids.every(
          (token: unknown) => Number.isSafeInteger(token) && Number(token) >= 0,
        ),
    ) &&
    Number.isSafeInteger(response.legacy_unqualified_records) &&
    typeof response.qualification === "string";
  const data = valid ? response : undefined;
  const payloadError =
    response !== undefined && !valid
      ? "Representation response is invalid or does not match the selected retained row. Reload its evidence."
      : null;
  useComponentAction({
    id: "lab.representation-inspection",
    label: "Read selected retained representation evidence",
    kind: "open",
    state: {
      query,
      identity,
      loading: resource.loading,
      evidence: data,
      error: resource.error?.message,
    },
    disabled: !data,
    run: () => ({ query, ...data }),
  });
  if (!identity)
    return (
      <EmptyState>
        A retained record or representation artifact is required.
      </EmptyState>
    );
  if (resource.loading)
    return <p role="status">Loading exact retained representation evidence…</p>;
  if (resource.error || payloadError)
    return <ErrorNotice error={resource.error ?? payloadError} />;
  if (!data) return null;
  return (
    <Evidence
      label="Exact retained representation inspection"
      data={{ query, ...data }}
    >
      <AnalysisTemplate
        label="Representation inspector"
        summary={
          <p>
            Artifact {identity.artifact_id} · absolute row {identity.row_index}.{" "}
            {data.qualification}
          </p>
        }
      >
        <dl>
          <dt>Retained observation artifact SHA-256</dt>
          <dd>{data.identity.artifact_sha256 ?? "Not recorded"}</dd>
          <dt>Canonical observation SHA-256</dt>
          <dd>{data.identity.observation_sha256 ?? "Not recorded"}</dd>
        </dl>
        <details open>
          <summary>
            Exact numeric observation and original retained fields
          </summary>
          <pre>
            {data.observation_jsonl ??
              JSON.stringify(data.observation, null, 2)}
          </pre>
        </details>
        {!data.matches.length && (
          <EmptyState>
            No exact retained codec matches this observation.{" "}
            {data.legacy_unqualified_records} legacy rows lack a qualifying
            fingerprint. Original and derived observations are never joined by
            time.
          </EmptyState>
        )}
        <SimpleTable
          label="Matched retained codecs"
          rows={data.matches}
          getRowId={(row) => `${row.artifact_id}:${row.row_index}`}
          columns={[
            {
              id: "name",
              label: "Codec",
              value: (row) => row.record.representation_id,
            },
            {
              id: "kind",
              label: "Kind",
              value: (row) => row.record.representation_kind,
            },
            {
              id: "tokens",
              label: "Tokens / full",
              value: (row) =>
                `${row.record.token_count} / ${row.record.full_token_count}`,
            },
            {
              id: "truncated",
              label: "Truncated",
              value: (row) => row.record.loss?.truncated,
            },
          ]}
        />
        {data.matches.map((match) => (
          <details key={`${match.artifact_id}:${match.row_index}`} open>
            <summary>
              {match.record.representation_id} · {match.artifact_id} · row{" "}
              {match.row_index}
            </summary>
            <dl>
              <dt>Input text (exact stored text)</dt>
              <dd>
                <pre>
                  {match.record.model_input_text ??
                    "Binary input; no UTF-8 text recorded"}
                </pre>
              </dd>
              <dt>Input bytes (base64)</dt>
              <dd>
                <pre>{match.record.model_input_base64}</pre>
              </dd>
              <dt>Exact token IDs</dt>
              <dd>
                <pre>{JSON.stringify(match.record.token_ids)}</pre>
              </dd>
              <dt>Input SHA-256</dt>
              <dd>{match.record.input_sha256}</dd>
              <dt>Full input SHA-256</dt>
              <dd>{match.record.untruncated_sha256}</dd>
              <dt>Specification SHA-256</dt>
              <dd>{match.record.specification_hash}</dd>
              <dt>Loss, truncation and excluded fields</dt>
              <dd>
                <pre>{JSON.stringify(match.record.loss, null, 2)}</pre>
              </dd>
            </dl>
            <details>
              <summary>Full retained codec evidence</summary>
              <pre>{JSON.stringify(match, null, 2)}</pre>
            </details>
          </details>
        ))}
      </AnalysisTemplate>
    </Evidence>
  );
}
