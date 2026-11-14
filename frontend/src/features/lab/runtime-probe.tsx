import { useEffect, useState } from "react";
import { useStudio } from "../../app/StudioContext";
import { scientificJson } from "../../api/scientific-json";
import {
  Button,
  EmptyState,
  Evidence,
} from "../../components/studio/primitives";
import { SimpleTable } from "../../components/studio/data-table";
import type { LabRun } from "./definition";
import { dataArtifacts, type RetainedArtifact } from "./evidence";
import type { RetainedJson } from "./retained-json";
import {
  loadRuntimeProbeReport,
  RUNTIME_PROBE_ARTIFACT_ROLE,
  type RuntimeProbeReport,
} from "./runtime-probe-api";

const json = (value: unknown) => scientificJson.stringify(value, 2);
const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
const display = (value: unknown) =>
  value === undefined
    ? "Not reported"
    : typeof value === "string"
      ? value
      : scientificJson.stringify(value);
const errorText = (failure: unknown) =>
  failure instanceof Error ? failure.message : String(failure);

/** Compatibility belongs to the retained measurement, independently of the Lab job's state. */
export function RuntimeProbeResult({ run }: { run: LabRun }) {
  const { scopeKey } = useStudio();
  const artifact = dataArtifacts(run).all.find(
    (item) => item.role === RUNTIME_PROBE_ARTIFACT_ROLE,
  );
  if (!artifact)
    return (
      <EmptyState>
        No retained runtime probe report is available for this run.
        Compatibility has not been established in this view.
      </EmptyState>
    );
  return (
    <RetainedRuntimeProbe
      key={JSON.stringify([scopeKey, run.id, artifact.id, artifact.sha256])}
      run={run}
      artifact={artifact}
    />
  );
}

function RetainedRuntimeProbe({
  run,
  artifact,
}: {
  run: LabRun;
  artifact: RetainedArtifact;
}) {
  const { api } = useStudio();
  const [state, setState] = useState<{
    retained?: RetainedJson<RuntimeProbeReport>;
    error?: string;
  }>({});
  const [retry, setRetry] = useState(0),
    [downloadError, setDownloadError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setState({});
    setDownloadError("");
    void loadRuntimeProbeReport(api, artifact, controller.signal)
      .then((retained) => {
        if (!controller.signal.aborted) setState({ retained });
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setState({ error: errorText(failure) });
      });
    return () => controller.abort();
  }, [api, artifact.id, artifact.sha256, retry]);
  if (state.error)
    return (
      <>
        <p role="alert">Runtime probe report unavailable: {state.error}</p>
        <Button onClick={() => setRetry((value) => value + 1)}>
          Retry runtime probe report
        </Button>
      </>
    );
  if (!state.retained)
    return <p role="status">Loading retained runtime probe report…</p>;
  const { value: report, blob } = state.retained;
  const requested = object(report.requested_configuration.parameters),
    scope = report.scope;
  const prerequisites = object(report.prerequisites),
    inventory = object(prerequisites.inventory);
  const dependencies = Object.entries(object(inventory.dependencies)).map(
    ([name, version]) => ({ name, version }),
  );
  const qualified = report.status === "verified";
  const headline = qualified
    ? "Runtime compatibility verified for this scope"
    : `Runtime compatibility ${report.status}`;
  const fields = [
    { key: "method", label: "Method" },
    { key: "objective", label: "Objective" },
    { key: "device", label: "Device" },
    { key: "dtype", label: "Data type" },
    { key: "fixture", label: "Fixture" },
    { key: "seed", label: "Seed" },
  ];
  const requestedValue = (key: string) =>
    key === "seed" && !Object.hasOwn(requested, "seed")
      ? report.requested_configuration.seed
      : requested[key];
  const sourceInputs = scope.inputs ?? [];
  const diagnostics = report.diagnostics.filter(
    (item) => item.message !== report.error?.message,
  );

  const download = () => {
    let url: string | undefined;
    try {
      setDownloadError("");
      url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = artifact.name;
      link.click();
    } catch (failure) {
      setDownloadError(errorText(failure));
    } finally {
      if (url) URL.revokeObjectURL(url);
    }
  };
  return (
    <Evidence
      label="Retained runtime compatibility measurement"
      data={{ run_id: run.id, spec_digest: run.spec_digest, artifact, report }}
    >
      <section
        aria-label="Retained runtime probe report"
        className="flex flex-col gap-4"
      >
        <h3 className="text-title-3-semibold">{headline}</h3>
        <p className="text-body-2-regular text-text-secondary">
          {qualified
            ? "Verification applies only to the retained objective, device, data type, fixture, input identities and settings. Other models, platforms and operations remain unprobed."
            : "This report does not establish verified runtime compatibility. The retained stage, diagnostics and evidence describe the measured outcome."}
        </p>
        <p>
          Laboratory execution: {run.state}. Compatibility above comes from the
          retained measurement.
        </p>
        {report.error && <p>{report.error.message || report.error.type}</p>}
        <p className="text-caption-1-regular text-text-secondary">
          Retained report: {artifact.name} · SHA-256 verified
        </p>
        <section
          aria-label="Requested runtime probe selection"
          className="flex flex-col gap-2"
        >
          <h4 className="text-headline-medium">Requested selection</h4>
          <dl className="grid gap-2 md:grid-cols-2">
            {fields.map((field) => (
              <div key={field.key}>
                <dt>{field.label}</dt>
                <dd>{display(requestedValue(field.key))}</dd>
              </div>
            ))}
          </dl>
          <details>
            <summary>Authored selection and input bindings</summary>
            <pre>{json(report.requested_configuration)}</pre>
          </details>
        </section>
        <section
          aria-label="Retained runtime probe scope"
          className="flex flex-col gap-2"
        >
          <h4 className="text-headline-medium">
            {qualified
              ? "Verified measurement scope"
              : "Declared measurement scope"}
          </h4>
          {report.configuration ? (
            <>
              <dl className="grid gap-2 md:grid-cols-2">
                {fields.map((field) => (
                  <div key={field.key}>
                    <dt>{field.label}</dt>
                    <dd>{display(scope[field.key])}</dd>
                  </div>
                ))}
              </dl>
              <div>
                <h5 className="text-body-medium">
                  Retained NF4 execution policy
                </h5>
                <pre aria-label="Retained NF4 execution policy">
                  {json(report.configuration.nf4_execution)}
                </pre>
              </div>
              <div>
                <h5 className="text-body-medium">
                  Retained reload comparison policy
                </h5>
                <pre aria-label="Retained reload comparison policy">
                  {json(report.configuration.reload_comparison)}
                </pre>
              </div>
              <details>
                <summary>
                  Resolved fixture configuration and training settings
                </summary>
                <pre>
                  {json({
                    fixture_config: report.configuration.fixture_config,
                    training: report.configuration.training,
                  })}
                </pre>
              </details>
              {report.fixture && (
                <details>
                  <summary>Constructed fixture evidence</summary>
                  <pre>{json(report.fixture)}</pre>
                </details>
              )}
            </>
          ) : (
            <p>
              No normalized configuration was retained; selection validation did
              not complete.
            </p>
          )}
          <SimpleTable
            label="Runtime probe source identities"
            rows={sourceInputs}
            getRowId={(row) => `${row.role}:${row.artifact_id}`}
            scopeKey={run.id}
            empty="No source artifact identities were retained for this measurement scope."
            columns={[
              { id: "role", label: "Role", value: (row) => row.role },
              {
                id: "identity",
                label: "Artifact identity",
                value: (row) => row.artifact_id,
              },
              { id: "checksum", label: "SHA-256", value: (row) => row.sha256 },
            ]}
          />
        </section>
        <section
          aria-label="Observed runtime inventory"
          className="flex flex-col gap-2"
        >
          <h4 className="text-headline-medium">Observed runtime inventory</h4>
          {Object.keys(inventory).length ? (
            <>
              <dl className="grid gap-2 md:grid-cols-3">
                {["os", "machine", "python"].map((key) => (
                  <div key={key}>
                    <dt>
                      {key === "os"
                        ? "Operating system"
                        : key === "machine"
                          ? "Machine"
                          : "Python"}
                    </dt>
                    <dd>{display(inventory[key])}</dd>
                  </div>
                ))}
              </dl>
              <SimpleTable
                label="Observed runtime dependency versions"
                rows={dependencies}
                getRowId={(row) => row.name}
                scopeKey={run.id}
                columns={[
                  { id: "package", label: "Package", value: (row) => row.name },
                  {
                    id: "version",
                    label: "Reported version",
                    value: (row) =>
                      row.version === null
                        ? "Version not reported"
                        : display(row.version),
                  },
                ]}
              />
              <p className="text-caption-1-regular text-text-secondary">
                Inventory and prerequisite discovery describe the inspected
                environment. Measurement stages establish the compatibility
                status above.
              </p>
            </>
          ) : (
            <p>Runtime inventory was not measured.</p>
          )}
          {report.prerequisites && (
            <details>
              <summary>Retained prerequisite inspection</summary>
              <pre>{json(report.prerequisites)}</pre>
            </details>
          )}
        </section>
        <section
          aria-label="Runtime probe stages and diagnostics"
          className="flex flex-col gap-2"
        >
          <h4 className="text-headline-medium">Measurement stages</h4>
          <p>Last retained stage: {report.stage.replaceAll("_", " ")}.</p>
          <SimpleTable
            label="Ordered runtime probe stages"
            rows={report.stages.map((stage, index) => ({
              ...stage,
              sequence: index + 1,
            }))}
            getRowId={(row) => String(row.sequence)}
            scopeKey={run.id}
            columns={[
              { id: "sequence", label: "Order", value: (row) => row.sequence },
              {
                id: "stage",
                label: "Stage",
                value: (row) => row.stage.replaceAll("_", " "),
              },
              {
                id: "status",
                label: "Recorded status",
                value: (row) => row.status,
              },
            ]}
          />
          {diagnostics.map((diagnostic, index) => (
            <div key={index}>
              <p>{diagnostic.message}</p>
              <p className="text-caption-1-regular text-text-secondary">
                {diagnostic.stage} · {diagnostic.code}
                {diagnostic.type ? ` · ${diagnostic.type}` : ""}
              </p>
            </div>
          ))}
          {report.receipt !== null && (
            <details>
              <summary>Retained measurement receipt</summary>
              <pre>{json(report.receipt)}</pre>
            </details>
          )}
          <details>
            <summary>Optimization, execution and reload evidence</summary>
            <pre>{json(report.evidence)}</pre>
          </details>
        </section>
        <details>
          <summary>Complete retained report and provenance</summary>
          <pre>
            {json({
              run_id: run.id,
              spec_digest: run.spec_digest,
              artifact,
              report,
            })}
          </pre>
        </details>
        <p className="text-caption-1-regular text-text-secondary">
          Method version {report.method_version} · Implementation SHA-256:{" "}
          {report.implementation_sha256}
        </p>
        {downloadError && (
          <p role="alert">
            Runtime probe download unavailable: {downloadError}
          </p>
        )}
        <Button onClick={download}>Download retained runtime probe JSON</Button>
      </section>
    </Evidence>
  );
}
