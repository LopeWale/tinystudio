import { useEffect, useRef, useState } from "react";
import { useStudio } from "../../app/StudioContext";
import { scientificJson } from "../../api/scientific-json";
import { SettingsSection } from "../../components/studio/settings-section";
import { Button, Evidence } from "../../components/studio/primitives";
import { SimpleTable } from "../../components/studio/data-table";
import type { LabRun } from "./definition";
import {
  downloadRecoveredFile,
  loadRecoveredReceipt,
  loadRecoveredReferences,
  verifyRecoveredArtifact,
  type RecoveredArtifact,
  type RecoveredFile,
  type RecoveredReceipt,
  type RecoveredReference,
} from "./recovered-evidence-api";

const errorText = (value: unknown) =>
  value instanceof Error ? value.message : String(value);
const json = (value: unknown) => scientificJson.stringify(value, 2);

/** Recovery is a separate association; this panel never changes a run or result. */
export function RecoveredEvidencePanel({ run }: { run: LabRun }) {
  const { scopeKey } = useStudio();
  return (
    <RecoveryReferences key={JSON.stringify([scopeKey, run.id])} run={run} />
  );
}

function RecoveryReferences({ run }: { run: LabRun }) {
  const { api } = useStudio();
  const [refresh, setRefresh] = useState(0);
  const [state, setState] = useState<{
    references?: RecoveredReference[];
    error?: string;
  }>({});
  useEffect(() => {
    const controller = new AbortController();
    setState({});
    void loadRecoveredReferences(api, run.id, controller.signal)
      .then((references) => {
        if (!controller.signal.aborted) setState({ references });
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setState({ error: errorText(cause) });
      });
    return () => controller.abort();
  }, [api, run.id, run.state, refresh]);
  return (
    <SettingsSection
      title="Operator-recovered evidence"
      description="Operator-supplied snapshots are attached separately from the execution result. Byte and existing server identity checks imply no scientific truth, runtime attestation or physical stop."
    >
      <p>Current run status: {run.state}</p>
      <p>
        This evidence does not change the run status or restart an experiment.
        Inspect file identities and downloads, then use the existing Inputs and
        data preparation workflow for explicit curation.
      </p>
      <Button onClick={() => setRefresh((value) => value + 1)}>
        Refresh recovered evidence
      </Button>
      {state.error && (
        <p role="alert">
          Recovered evidence references unavailable: {state.error}
        </p>
      )}
      {!state.error && !state.references && (
        <p role="status">Loading recovered evidence references…</p>
      )}
      {state.references?.length === 0 && (
        <p>No operator-recovered evidence is attached to this run.</p>
      )}
      {state.references?.map((reference) => (
        <Receipt
          key={JSON.stringify([
            reference.receipt.id,
            reference.receipt.sha256,
            reference.receipt.size,
          ])}
          reference={reference}
          run={run}
        />
      ))}
    </SettingsSection>
  );
}

function Receipt({
  reference,
  run,
}: {
  reference: RecoveredReference;
  run: LabRun;
}) {
  const { api, scopeKey } = useStudio();
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<{
    value?: RecoveredReceipt;
    blob?: Blob;
    error?: string;
  }>({});
  const [downloadError, setDownloadError] = useState("");
  const [downloading, setDownloading] = useState<string | null>(null);
  const downloads = useRef(new Set<AbortController>());
  useEffect(() => {
    const controller = new AbortController();
    setState({});
    setDownloadError("");
    void loadRecoveredReceipt(api, reference, run, controller.signal)
      .then((retained) => {
        if (!controller.signal.aborted) setState(retained);
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setState({ error: errorText(cause) });
      });
    const active = downloads.current;
    return () => {
      controller.abort();
      active.forEach((download) => download.abort());
      active.clear();
    };
  }, [api, reference, run.id, run.spec_digest, run.target_id, retry]);

  const download = async (
    identity: RecoveredArtifact,
    cached?: Blob,
    file?: RecoveredFile,
  ) => {
    const controller = new AbortController();
    downloads.current.add(controller);
    setDownloading(identity.id);
    setDownloadError("");
    let url: string | undefined;
    try {
      const blob =
        cached ??
        (file
          ? await downloadRecoveredFile(api, file, controller.signal)
          : await verifyRecoveredArtifact(api, identity, controller.signal));
      if (controller.signal.aborted) return;
      url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = identity.name;
      link.click();
    } catch (cause) {
      if (!controller.signal.aborted) setDownloadError(errorText(cause));
    } finally {
      if (url) URL.revokeObjectURL(url);
      downloads.current.delete(controller);
      if (!controller.signal.aborted) setDownloading(null);
    }
  };
  if (state.error)
    return (
      <section aria-label={`Recovery receipt ${reference.receipt.id}`}>
        <p>
          Receipt identity: {reference.receipt.id} · {reference.receipt.size}{" "}
          bytes · SHA-256: {reference.receipt.sha256}
        </p>
        <p role="alert">
          Recovered evidence receipt unavailable: {state.error}
        </p>
        <Button onClick={() => setRetry((value) => value + 1)}>
          Retry recovery receipt {reference.receipt.id}
        </Button>
        <Button
          disabled={downloading !== null}
          onClick={() => void download(reference.receipt)}
        >
          Download recovery receipt bytes {reference.receipt.id}
        </Button>
        <p>
          An uninterpreted receipt can be downloaded after an independent size
          and SHA-256 check. No receipt claims are accepted by this view.
        </p>
        {downloadError && (
          <p role="alert">
            Recovered evidence download unavailable: {downloadError}
          </p>
        )}
      </section>
    );
  if (!state.value)
    return (
      <p role="status">Loading recovery receipt {reference.receipt.id}…</p>
    );
  const receipt = state.value;
  return (
    <Evidence
      label="Operator-recovered bytes and identity only"
      data={{ run_id: run.id, reference, receipt }}
    >
      <section
        aria-label={`Recovery receipt ${reference.receipt.id}`}
        className="flex flex-col gap-4"
      >
        <h4 className="text-headline-medium">Receipt bytes verified</h4>
        <p>
          Receipt identity: {reference.receipt.id} · {reference.receipt.size}{" "}
          bytes · SHA-256: {reference.receipt.sha256}
        </p>
        <p>
          Attached by: {receipt.author_id} · Recovery key:{" "}
          {reference.idempotency_key}
        </p>
        <p>
          Source attempt: {receipt.source.attempt_id} · Target:{" "}
          {receipt.source.target_id}
        </p>
        <p>
          Receipt source run status: {receipt.source_states.run} · Source
          attempt status: {receipt.source_states.attempt} · Observed:{" "}
          {receipt.source_states_observed_at}
        </p>
        <p>
          Attachment source run status: {reference.source_states.run} · Source
          attempt status: {reference.source_states.attempt}
        </p>
        <p>
          These source states were observed during recovery; they do not replace
          the current run status. Producer cleanup and outcome statements remain
          unverified claims.
        </p>
        <Button
          disabled={downloading !== null}
          onClick={() => void download(reference.receipt, state.blob)}
        >
          Download recovery receipt {reference.receipt.id}
        </Button>
        <SimpleTable
          label="Recovered file byte identities"
          rows={receipt.files}
          getRowId={(file) => JSON.stringify([file.name, file.artifact.id])}
          scopeKey={JSON.stringify([scopeKey, run.id, reference.receipt.id])}
          empty="No recovered file identities are attached."
          columns={[
            { id: "name", label: "Source file", value: (file) => file.name },
            {
              id: "identity",
              label: "Artifact ID",
              value: (file) => file.artifact.id,
            },
            {
              id: "size",
              label: "Byte size",
              value: (file) => `${file.verified_bytes.size} bytes`,
            },
            {
              id: "hash",
              label: "SHA-256",
              value: (file) => file.artifact.sha256,
            },
            {
              id: "newlines",
              label: "Newline byte count",
              value: (file) =>
                file.verified_bytes.jsonl?.newline_count ?? "Not reported",
            },
            {
              id: "tail",
              label: "Tail bytes",
              value: (file) =>
                file.verified_bytes.jsonl
                  ? `${file.verified_bytes.jsonl.tail_bytes} bytes`
                  : "Not reported",
            },
            {
              id: "newline",
              label: "Ends with newline",
              value: (file) =>
                file.verified_bytes.jsonl
                  ? String(file.verified_bytes.jsonl.ends_with_newline)
                  : "Not reported",
            },
            {
              id: "download",
              label: "Verified download",
              value: (file) => file.name,
              render: (file) => (
                <Button
                  disabled={downloading !== null}
                  onClick={() => void download(file.artifact, undefined, file)}
                >
                  Download recovered file {file.name}
                </Button>
              ),
            },
          ]}
        />
        <p>
          File byte facts were verified by the server when attached. Each file
          download checks size and SHA-256 again. Newline and tail counts
          describe bytes; semantic observation validation was not performed.
        </p>
        <p>
          Source manifest: {receipt.manifest.id} · {receipt.manifest.size} bytes
          · SHA-256: {receipt.manifest.sha256}
        </p>
        <Button
          disabled={downloading !== null}
          onClick={() => void download(receipt.manifest)}
        >
          Download source manifest {receipt.manifest.id}
        </Button>
        {downloading && (
          <p role="status">Verifying recovered evidence download…</p>
        )}
        {downloadError && (
          <p role="alert">
            Recovered evidence download unavailable: {downloadError}
          </p>
        )}
        <details>
          <summary>Source and recovery qualification</summary>
          <pre>
            {json({
              source: receipt.source,
              qualification: receipt.qualification,
              reference,
            })}
          </pre>
        </details>
        <details>
          <summary>Original producer manifest · unverified claims</summary>
          <pre>{json(receipt.producer_manifest)}</pre>
        </details>
        <details>
          <summary>Verified recovery receipt JSON</summary>
          <pre>{json(receipt)}</pre>
        </details>
      </section>
    </Evidence>
  );
}
