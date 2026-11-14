import { useEffect, useRef, useState } from "react";
import { Input } from "@/components/boardui/base/input/input";
import { useStudio } from "../../app/StudioContext";
import { scientificJson } from "../../api/scientific-json";
import { SettingsSection } from "../../components/studio/settings-section";
import {
  Button,
  EmptyState,
  Evidence,
} from "../../components/studio/primitives";
import { SimpleTable } from "../../components/studio/data-table";
import type { ExperimentDefinition, LabRun } from "./definition";
import { dataArtifacts, type RetainedArtifact } from "./evidence";
import { DraftField } from "./data-preparation";
import { LabChoice } from "./inputs";
import type { RetainedJson } from "./retained-json";
import {
  discoveryParameterProblems,
  HARDWARE_DISCOVERY_ARTIFACT_ROLE,
  HARDWARE_DISCOVERY_WIRE_ROLE,
  loadHardwareDiscoveryArtifact,
  loadHardwareDiscoveryReport,
  type HardwareDiscoveryMethod,
  type HardwareDiscoveryReport,
} from "./hardware-discovery-api";

type JsonObject = Record<string, unknown>;
const object = (value: unknown): JsonObject =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
const selected = (value: unknown) => (typeof value === "string" ? value : "");
const json = (value: unknown) => scientificJson.stringify(value, 2);
const display = (value: unknown) =>
  value === undefined
    ? "Not reported"
    : typeof value === "string"
      ? value
      : scientificJson.stringify(value);
const failureText = (failure: unknown) =>
  failure instanceof Error ? failure.message : String(failure);

export interface HardwareDiscoveryFieldsProps {
  definition: ExperimentDefinition;
  onChange: (patch: Partial<ExperimentDefinition>) => void;
  methods?: readonly HardwareDiscoveryMethod[];
  onValidityChange?: (valid: boolean) => void;
}

/** Catalog inspection and authoring never open devices or request discovery. */
export function HardwareDiscoveryFields({
  definition,
  onChange,
  methods,
  onValidityChange,
}: HardwareDiscoveryFieldsProps) {
  const p = definition.parameters;
  const catalog = (methods ?? []).filter(
    (method) => method && typeof method.id === "string" && !!method.id,
  );
  const method = catalog.find((item) => item.id === p.method);
  const advanced = Object.fromEntries(
    Object.entries(p).filter(
      ([key]) => !["method", "binding_id"].includes(key),
    ),
  );
  const [invalidDraft, setInvalidDraft] = useState(false);
  const [reportDraft] = useState(
    () => (_key: string, valid: boolean) => setInvalidDraft(!valid),
  );
  const schema = method?.schema ?? {
    type: "object",
    required: method?.required ?? ["method", "binding_id"],
    properties: method?.parameters ?? {},
    additionalProperties: false,
  };
  const problems = method
    ? discoveryParameterProblems(p, schema)
    : [
        "Choose an advertised hardware discovery method explicitly. Existing values remain retained.",
      ];
  const valid =
    !invalidDraft &&
    !problems.length &&
    definition.operating_mode === "acquisition";
  const validity = useRef(onValidityChange);
  validity.current = onValidityChange;
  useEffect(() => {
    validity.current?.(valid);
  }, [valid]);
  const patch = (key: string, value: unknown) =>
    onChange({ parameters: { ...p, [key]: value } });
  return (
    <SettingsSection
      title="Hardware discovery"
      description="Author a structural discovery selection for an exact operator binding. Hardware is inspected only when the experiment executes on its selected target."
    >
      <div className="grid gap-4 md:grid-cols-2">
        <LabChoice
          label="Hardware discovery method"
          value={selected(p.method)}
          options={catalog.map((item) => ({
            id: item.id,
            name: item.label || item.id,
          }))}
          onChange={(value) => patch("method", value)}
          disabled={!catalog.length}
          placeholder="Choose explicitly"
        />
        <Input
          label="Operator discovery binding"
          value={selected(p.binding_id)}
          onChange={(value) => patch("binding_id", value)}
          hint="Enter the exact execution-host binding identity. The authored definition does not grant hardware authority."
        />
        <LabChoice
          label="Discovery operating mode"
          value={definition.operating_mode ?? ""}
          options={[{ id: "acquisition", name: "Acquisition" }]}
          onChange={(operating_mode) => onChange({ operating_mode })}
          placeholder="Choose explicitly"
        />
      </div>
      {!catalog.length && (
        <p>
          No discovery methods are advertised by this catalog. Existing
          parameter values remain retained.
        </p>
      )}
      {method?.description && <p>{method.description}</p>}
      <p className="text-body-2-regular text-text-secondary">
        Serial discovery reads port metadata. CAN discovery queries only
        authored providers; providers may send discovery traffic. SunSpec
        discovery reads the authored marker and model-length chain. These
        operations do not write actuator values or establish physical
        qualification.
      </p>
      <DraftField
        label="Advanced discovery parameters JSON"
        fieldKey="hardware-discovery-advanced"
        kind="object"
        value={advanced}
        report={reportDraft}
        jsonCodec={scientificJson}
        onChange={(value) =>
          onChange({
            parameters: {
              ...Object.fromEntries(
                Object.entries(p).filter(([key]) =>
                  ["method", "binding_id"].includes(key),
                ),
              ),
              ...object(value),
            },
          })
        }
      />
      <p className="text-body-2-regular text-text-secondary">
        Author CAN interfaces as an explicit interfaces array. For SunSpec,
        author scan.base_addresses as zero-based protocol addresses and
        scan.device_id explicitly. Optional scan settings include register_kind,
        max_models and read_model_data; timeout and max_frame_bytes are resource
        settings. Omitted values follow the advertised backend policy. No
        addresses, bindings, model limits, point meanings or units are inferred.
      </p>
      <p className="text-body-2-regular text-text-secondary">
        Method changes retain all advanced fields. Correct unsupported fields
        explicitly in the JSON editor before saving. Physical endpoints belong
        to the execution-host binding and are recorded in the retained report.
      </p>
      {!invalidDraft && problems.length > 0 && (
        <div role="alert">
          <p>Discovery selection requires correction:</p>
          <ul>
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      )}
      {definition.operating_mode !== "acquisition" && (
        <p role="alert">
          Live discovery requires acquisition operating mode. Retained reports
          can be inspected without executing discovery.
        </p>
      )}
      <details>
        <summary>Current authored discovery parameters</summary>
        <pre>{json(p)}</pre>
      </details>
      <details>
        <summary>Advertised method schema</summary>
        <pre>{json(method?.schema ?? method?.parameters ?? {})}</pre>
      </details>
    </SettingsSection>
  );
}

export function HardwareDiscoveryResult({ run }: { run: LabRun }) {
  const { scopeKey } = useStudio();
  const artifacts = dataArtifacts(run).all;
  const artifact = artifacts.find(
    (item) => item.role === HARDWARE_DISCOVERY_ARTIFACT_ROLE,
  );
  if (!artifact)
    return (
      <EmptyState>
        No retained hardware discovery report is available for this run.
        Physical qualification has not been established.
      </EmptyState>
    );
  return (
    <RetainedHardwareDiscovery
      key={JSON.stringify([scopeKey, run.id, artifact.id, artifact.sha256])}
      run={run}
      artifact={artifact}
      artifacts={artifacts}
    />
  );
}

function RetainedHardwareDiscovery({
  run,
  artifact,
  artifacts,
}: {
  run: LabRun;
  artifact: RetainedArtifact;
  artifacts: RetainedArtifact[];
}) {
  const { api } = useStudio();
  const [state, setState] = useState<{
    retained?: RetainedJson<HardwareDiscoveryReport>;
    error?: string;
  }>({});
  const [retry, setRetry] = useState(0);
  const [downloadError, setDownloadError] = useState("");
  const [downloading, setDownloading] = useState<string | null>(null);
  const downloads = useRef(new Set<AbortController>());
  useEffect(() => {
    const controller = new AbortController();
    setState({});
    setDownloadError("");
    void loadHardwareDiscoveryReport(api, artifact, controller.signal)
      .then((retained) => {
        if (!controller.signal.aborted) setState({ retained });
      })
      .catch((failure) => {
        if (!controller.signal.aborted)
          setState({ error: failureText(failure) });
      });
    const activeDownloads = downloads.current;
    return () => {
      controller.abort();
      activeDownloads.forEach((active) => active.abort());
      activeDownloads.clear();
    };
  }, [api, artifact.id, artifact.sha256, retry]);
  if (state.error)
    return (
      <>
        <p role="alert">Hardware discovery report unavailable: {state.error}</p>
        <Button onClick={() => setRetry((value) => value + 1)}>
          Retry hardware discovery report
        </Button>
      </>
    );
  if (!state.retained)
    return <p role="status">Loading retained hardware discovery report…</p>;
  const { value: report, blob } = state.retained;
  const discovery = report.discovery;
  const endpoint = object(report.source.endpoint);
  const models = (discovery?.trials ?? []).flatMap((trial) =>
    trial.models.map((model) => ({
      ...model,
      base_address: trial.base_address,
      device_id: trial.device_id,
      register_kind: trial.register_kind,
    })),
  );
  const wireArtifacts = artifacts.filter(
    (item) => item.role === HARDWARE_DISCOVERY_WIRE_ROLE,
  );
  const download = async (item: RetainedArtifact, verifiedBlob?: Blob) => {
    const controller = new AbortController();
    downloads.current.add(controller);
    setDownloading(item.id);
    setDownloadError("");
    let url: string | undefined;
    try {
      const bytes =
        verifiedBlob ??
        (await loadHardwareDiscoveryArtifact(api, item, controller.signal));
      if (controller.signal.aborted) return;
      url = URL.createObjectURL(bytes);
      const link = document.createElement("a");
      link.href = url;
      link.download = item.name;
      link.click();
    } catch (failure) {
      if (!controller.signal.aborted) setDownloadError(failureText(failure));
    } finally {
      if (url) URL.revokeObjectURL(url);
      downloads.current.delete(controller);
      if (!controller.signal.aborted) setDownloading(null);
    }
  };
  return (
    <Evidence
      label="Retained hardware discovery evidence"
      data={{
        run_id: run.id,
        spec_digest: run.spec_digest,
        artifact,
        artifacts,
        report,
      }}
    >
      <section
        aria-label="Retained hardware discovery report"
        className="flex flex-col gap-4"
      >
        <h3 className="text-title-3-semibold">
          Hardware discovery {report.status}
        </h3>
        <p>
          Laboratory execution: {run.state}. The discovery status above comes
          from the retained report.
        </p>
        <p>Qualified: false. Physical validation: false.</p>
        <p className="text-body-2-regular text-text-secondary">
          {report.qualification.scope}
        </p>
        <p>
          Method: {report.method} · Operator binding: {report.binding_id}
        </p>
        {report.error !== undefined && (
          <pre aria-label="Discovery error">{json(report.error)}</pre>
        )}
        {report.close_error !== undefined && (
          <pre aria-label="Discovery close error">
            {json(report.close_error)}
          </pre>
        )}
        <p className="text-caption-1-regular text-text-secondary">
          Retained report: {artifact.name} · Artifact identity: {artifact.id} ·
          SHA-256 verified: {artifact.sha256}
        </p>
        <section
          aria-label="Observed hardware source identity"
          className="flex flex-col gap-2"
        >
          <h4 className="text-headline-medium">Observed source identity</h4>
          {Object.keys(endpoint).length > 0 && (
            <dl className="grid gap-2 md:grid-cols-2">
              {Object.entries(endpoint).map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{display(value)}</dd>
                </div>
              ))}
            </dl>
          )}
          <pre>{json(report.source)}</pre>
          <p className="text-body-2-regular text-text-secondary">
            Source identity records the execution-host selection. Structural
            discovery does not establish equipment identity or physical
            qualification.
          </p>
        </section>
        <section
          aria-label="Observed discovery runtime"
          className="flex flex-col gap-2"
        >
          <h4 className="text-headline-medium">Observed runtime</h4>
          <p>Python: {display(report.runtime.python)}</p>
          <p>Platform: {display(report.runtime.platform)}</p>
          <pre>{json(report.runtime)}</pre>
        </section>
        {report.method === "sunspec.scan" && (
          <section
            aria-label="SunSpec structural discovery"
            className="flex flex-col gap-2"
          >
            <h4 className="text-headline-medium">Discovered model addresses</h4>
            <p>
              Structural scan status: {display(discovery?.status)}. Model
              instances are indexed per model ID within each candidate base.
            </p>
            <SimpleTable
              label="Discovered SunSpec model addresses"
              rows={models}
              getRowId={(row) =>
                `${row.base_address}:${row.model_id}:${row.instance}`
              }
              scopeKey={run.id}
              empty="No SunSpec model headers were retained."
              columns={[
                {
                  id: "base",
                  label: "Candidate base",
                  value: (row) => row.base_address,
                },
                {
                  id: "device",
                  label: "Device ID",
                  value: (row) => row.device_id,
                },
                {
                  id: "kind",
                  label: "Register kind",
                  value: (row) => row.register_kind,
                },
                {
                  id: "model",
                  label: "Model ID",
                  value: (row) => row.model_id,
                },
                {
                  id: "instance",
                  label: "Per-ID instance",
                  value: (row) => row.instance,
                },
                {
                  id: "header",
                  label: "Header address",
                  value: (row) => row.header_address,
                },
                {
                  id: "payload",
                  label: "Payload address",
                  value: (row) => row.payload_address,
                },
                {
                  id: "length",
                  label: "Reported length",
                  value: (row) => row.length,
                },
                {
                  id: "status",
                  label: "Recorded status",
                  value: (row) => row.status,
                },
              ]}
            />
            <p>
              Point bindings require an explicitly authored model ID, per-ID
              instance, payload offset and count, plus the selected candidate
              base when ambiguous. The reported model chain supplies addresses
              and lengths; point semantics, units, access rights and fixed
              register mappings are not inferred.
            </p>
            <details>
              <summary>
                Retained candidate bases, headers and read receipts
              </summary>
              <pre>{json(discovery?.trials ?? [])}</pre>
            </details>
          </section>
        )}
        {report.method === "serial.enumerate" && (
          <section aria-label="Serial port metadata">
            <h4 className="text-headline-medium">Retained serial metadata</h4>
            <pre>{json(discovery?.ports ?? [])}</pre>
          </section>
        )}
        {report.method === "can.enumerate" && (
          <section aria-label="CAN provider detection">
            <h4 className="text-headline-medium">
              Retained CAN provider configurations
            </h4>
            <pre>{json(discovery?.configurations ?? [])}</pre>
            <p>
              Only selected providers were queried. Empty detection can reflect
              provider support, platform dependencies, hardware availability or
              timeout; providers may send discovery traffic.
            </p>
          </section>
        )}
        <details>
          <summary>Requested and effective discovery configuration</summary>
          <pre>
            {json({
              requested: report.requested_configuration,
              effective: report.effective_configuration,
              resource_limits: report.resource_limits,
              budget_scope: report.budget_scope,
            })}
          </pre>
        </details>
        <details>
          <summary>Host observation clock receipt</summary>
          <pre>{json(report.clock)}</pre>
        </details>
        <p className="text-body-2-regular text-text-secondary">
          Host observation timestamps, model counts, addresses and register
          lengths describe discovery evidence. They are not physical time-series
          measurements.
        </p>
        <section
          aria-label="Discovery wire trace artifacts"
          className="flex flex-col gap-2"
        >
          <h4 className="text-headline-medium">Protocol callback evidence</h4>
          <p>
            Receipts retain SDK buffer direction, length, SHA-256, captured
            prefix bytes and host callback timing. Decoder buffers may overlap;
            they do not establish an exact socket stream, successful sends or
            physical wire traffic. Capture limits may truncate retained prefixes.
          </p>
          <SimpleTable
            label="Discovery wire artifact identities"
            rows={wireArtifacts}
            getRowId={(row) => row.id}
            scopeKey={run.id}
            empty="No retained wire trace artifact identity is available."
            columns={[
              { id: "name", label: "Artifact", value: (row) => row.name },
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
              {
                id: "download",
                label: "Evidence bytes",
                value: (row) => row.id,
                render: (row) => (
                  <Button
                    disabled={downloading !== null}
                    onClick={() => {
                      void download(row);
                    }}
                  >
                    Download {row.name}
                  </Button>
                ),
              },
            ]}
          />
        </section>
        <details>
          <summary>Complete retained discovery report and provenance</summary>
          <pre>
            {json({
              run_id: run.id,
              spec_digest: run.spec_digest,
              artifact,
              artifacts,
              report,
            })}
          </pre>
        </details>
        <p className="text-caption-1-regular text-text-secondary">
          Implementation SHA-256: {report.implementation_sha256}
        </p>
        {downloadError && (
          <p role="alert">
            Hardware discovery download unavailable: {downloadError}
          </p>
        )}
        <Button
          disabled={downloading !== null}
          onClick={() => {
            void download(artifact, blob);
          }}
        >
          Download retained hardware discovery JSON
        </Button>
      </section>
    </Evidence>
  );
}
