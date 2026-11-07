import { useStudio } from "../../app/StudioContext";
import { RetainedBaselineComparison } from "./baseline-comparison";
import { RepresentationInspector } from "./representation-inspector";
import { CoverageInspector, type CoverageSource } from "./coverage-inspector";
import { eventAvailableForDecision } from "./clock";
import { useEffect, useMemo, useState } from "react";
import { useTrendController } from "./trend-controller";
import { TrendWorkbench } from "./trend-workbench";
import { dataArtifacts } from "./evidence";
import { exactNumber, fieldValue, type TrendSource } from "./trends";
import {
  Select,
  SelectItem,
} from "../../components/boardui/base/select/select";
import {
  Button,
  EmptyState,
  Evidence,
} from "../../components/studio/primitives";
import { Input } from "../../components/boardui/base/input/input";
import { DefinitionGraph } from "./graph";
import type { ExperimentDefinition, LabRun } from "./definition";
import { InspectionWorkspace } from "../../components/studio/workspace";
import "../data/analytical-workspace.css";
export const replayViews = [
  "Episode replay",
  "State / topology",
  "Dataset coverage",
  "Representation inspector",
  "Baseline comparison",
  "Deployment tradeoffs",
];
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
export function numericFields(records: Record<string, unknown>[]): string[] {
  return [
    ...new Set(
      records.flatMap((record) =>
        Object.keys(record).filter(
          (key) =>
            finite(record[key]) && !["time", "available_at"].includes(key),
        ),
      ),
    ),
  ];
}
export function timelineSeries(
  records: Record<string, unknown>[],
  field: string,
): [number, number | null][] {
  return records.flatMap((record) =>
    finite(record.time)
      ? [
          [record.time, finite(record[field]) ? record[field] : null] as [
            number,
            number | null,
          ],
        ]
      : [],
  );
}

export type ReplayRecordSource = {
  label: string;
  status: "waiting" | "loading" | "ready" | "error" | "missing";
  error?: unknown;
};

export function ReplayViews({
  records,
  rawRecords,
  events,
  run,
  active = true,
  recordSources,
  onRetryRecords,
}: {
  records: any[];
  rawRecords: any[];
  events: any[];
  run: LabRun;
  active?: boolean;
  recordSources?: ReplayRecordSource[];
  onRetryRecords?: () => void;
}) {
  const { scopeKey } = useStudio();
  const [coverageSelectionState, setCoverageSelectionState] = useState<{
    scopeKey: string;
    source: CoverageSource;
  }>();
  const coverageSelection =
    coverageSelectionState?.scopeKey === scopeKey
      ? coverageSelectionState.source
      : undefined;
  const hasObservations = records.length > 0 || rawRecords.length > 0;
  const sourceStates = recordSources ?? [
    { label: "Processed records", status: "ready" as const },
    { label: "Original records", status: "ready" as const },
  ];
  const loadingRecords = sourceStates.some(
    (source) => source.status === "loading",
  );
  const failedSources = sourceStates.filter(
    (source) => source.status === "error",
  );
  const waitingRecords = sourceStates.some(
    (source) => source.status === "waiting",
  );
  const missingSources = sourceStates.filter(
    (source) => source.status === "missing",
  );
  const recordNotice = failedSources.length ? (
    <EmptyState
      heading="Replay records unavailable"
      role="alert"
      actions={
        onRetryRecords && (
          <Button onClick={onRetryRecords}>Retry replay records</Button>
        )
      }
    >
      {failedSources.map((source) => (
        <p key={source.label}>
          {source.label}:{" "}
          {source.error instanceof Error
            ? source.error.message
            : String(source.error ?? "Retrieval failed.")}
        </p>
      ))}
      <p>Other retained evidence remains available through Evidence view.</p>
    </EmptyState>
  ) : loadingRecords ? (
    <EmptyState heading="Loading replay records" role="status" aria-busy="true">
      Retrieving the retained observation windows for this run.
    </EmptyState>
  ) : waitingRecords ? (
    <EmptyState heading="Replay is not available yet" role="status">
      Retained observations can be retrieved after this run finishes.
    </EmptyState>
  ) : missingSources.length ? (
    <EmptyState
      heading={
        missingSources.length === sourceStates.length
          ? "No replay artifacts retained"
          : "Some replay artifacts were not retained"
      }
    >
      {missingSources.map((source) => (
        <p key={source.label}>{source.label}: no retained artifact.</p>
      ))}
      <p>Other retained evidence remains available through Evidence view.</p>
    </EmptyState>
  ) : !hasObservations ? (
    <EmptyState heading="No replay observations">
      The retained artifacts returned no observations. Other retained evidence
      remains available through Evidence view.
    </EmptyState>
  ) : null;
  const [inspecting, setInspecting] = useState(false);
  const [view, setView] = useState(replayViews[0]);
  const episodes = useMemo(
    () => [
      ...new Set(
        [...records, ...rawRecords].map((row) =>
          String(row.episode_id ?? "unassigned"),
        ),
      ),
    ],
    [records, rawRecords],
  );
  const [chosenEpisode, setChosenEpisode] = useState("");
  const episode = episodes.includes(chosenEpisode)
    ? chosenEpisode
    : (episodes[0] ?? "unassigned");
  const episodeRecords = useMemo(
    () =>
      records.filter(
        (row) => String(row.episode_id ?? "unassigned") === episode,
      ),
    [records, episode],
  );
  const episodeRaw = useMemo(
    () =>
      rawRecords.filter(
        (row) => String(row.episode_id ?? "unassigned") === episode,
      ),
    [rawRecords, episode],
  );
  const retained = dataArtifacts(run);
  const spec = (run as any).spec?.definition ?? (run as any).definition;
  const sources = useMemo<TrendSource[]>(() => {
    const bindings = spec?.inputs ?? [];
    const sourceGroup =
      bindings.length === 1
        ? bindings[0].artifact_id
        : !bindings.length
          ? run.id
          : undefined;
    const positions = (rows: any[]) =>
      rows.flatMap((row, index) =>
        String(row.episode_id ?? "unassigned") === episode ? [index] : [],
      );
    return [
      {
        runId: run.id,
        artifactId: retained.derived?.id ?? "unresolved:processed.jsonl",
        sourceGroup,
        kind: "derived",
        records: episodeRecords,
        rowIndices: positions(records),
      },
      {
        runId: run.id,
        artifactId: retained.original?.id ?? "unresolved:raw.jsonl",
        sourceGroup,
        kind: "original",
        records: episodeRaw,
        rowIndices: positions(rawRecords),
      },
    ];
  }, [
    run.id,
    retained.derived?.id,
    retained.original?.id,
    spec,
    episodeRecords,
    episodeRaw,
    records,
    rawRecords,
    episode,
  ]);
  const trend = useTrendController(
    sources,
    spec?.mode === "online" ? "availability" : "event",
    "3d",
  );
  const cursor = Math.max(0, trend.cursor),
    playing = trend.playing,
    setPlaying = trend.setPlaying;
  useEffect(() => {
    if (!active || !hasObservations) setPlaying(false);
  }, [active, hasObservations, setPlaying]);
  const point = trend.selected?.record;
  const result = run.result as any;
  const timedEvents = events.filter((event) =>
    eventAvailableForDecision(event.payload, point, episode),
  );
  return (
    <section className="lab-replay" aria-label="Replay workbench">
      <div className="lab-actions lab-evidence-controls">
        <Select
          size="sm"
          aria-label="Evidence view"
          selectedKey={view}
          onSelectionChange={(key) => {
            if (key !== null) {
              setView(String(key));
              setPlaying(false);
              setInspecting(false);
            }
          }}
        >
          {replayViews.map((name) => (
            <SelectItem id={name} key={name}>
              {name}
            </SelectItem>
          ))}
        </Select>
        {replayViews.slice(0, 4).includes(view) && hasObservations && (
          <>
            <div className="lab-actions lab-replay-controls">
              <Select
                size="sm"
                aria-label="Replay episode"
                selectedKey={episode}
                onSelectionChange={(key) => {
                  setChosenEpisode(String(key));
                  trend.reset();
                }}
              >
                {episodes.map((id) => (
                  <SelectItem id={id} key={id}>
                    {id}
                  </SelectItem>
                ))}
              </Select>
              <Button
                size="sm"
                disabled={trend.samples.length < 2}
                onClick={() => setPlaying((old) => !old)}
              >
                {playing ? "Pause replay" : "Play replay"}
              </Button>
              <Button
                size="sm"
                disabled={trend.cursor <= 0}
                onClick={() => trend.navigate(trend.cursor - 1)}
              >
                Previous frame
              </Button>
              <Input
                className="lab-replay-frame"
                aria-label="Replay frame"
                size="small"
                type="number"
                value={String(cursor)}
                onChange={(value) =>
                  trend.navigate(Math.trunc(Number(value)) || 0)
                }
              />
              <Button
                size="sm"
                disabled={trend.cursor >= trend.samples.length - 1}
                onClick={() => trend.navigate(trend.cursor + 1)}
              >
                Next frame
              </Button>
            </div>
          </>
        )}
        {view === replayViews[0] && hasObservations && (
          <Button
            size="sm"
            variant="ghost"
            aria-expanded={inspecting}
            onClick={() => setInspecting((value) => !value)}
          >
            {inspecting ? "Close observation" : "Inspect observation"}
          </Button>
        )}
      </div>
      {replayViews.slice(0, 4).includes(view) && (
        <InspectionWorkspace
          label={view === replayViews[0] ? "Selected observation" : view}
          inspector={
            view !== replayViews[0] || inspecting ? (
              <div className="lab-evidence-details">
                {view === replayViews[0] && (
                  <>
                    <h4>Selected observation</h4>
                    {point ? (
                      <dl className="lab-selected-observation">
                        {trend.fields.map((field) => (
                          <div key={field} className="lab-observation-field">
                            <dt>{field}</dt>
                            <dd>
                              {exactNumber(fieldValue(point, field))}{" "}
                              {field === "value"
                                ? (point.units ?? "units not recorded")
                                : (point.field_units?.[field] ??
                                  "units not recorded")}
                            </dd>
                          </div>
                        ))}
                        <dt>Event / sample time</dt>
                        <dd>
                          {point.event_time ?? point.time ?? "Not recorded"}
                        </dd>
                        <dt>Available at</dt>
                        <dd>
                          {point.fit_availability?.clock_comparable === false
                            ? `Fitted-value availability unknown; source observation ${point.available_at ?? "not recorded"}`
                            : (point.available_at ?? "Not recorded")}
                        </dd>
                        <dt>Observation</dt>
                        <dd>
                          {point.estimated === true
                            ? "Estimated"
                            : point.observed === true
                              ? "Observed"
                              : "Not established"}
                        </dd>
                        <dt>Source records</dt>
                        <dd>
                          {trend.selected?.identity.sourceRecordIds.join(
                            ", ",
                          ) || "Not recorded"}
                        </dd>
                      </dl>
                    ) : (
                      <EmptyState>No observation selected.</EmptyState>
                    )}
                    <details>
                      <summary>Full selected record</summary>
                      <pre>{JSON.stringify(point ?? null, null, 2)}</pre>
                    </details>
                    <h4>Events available at this decision</h4>
                    {timedEvents.length ? (
                      <ol>
                        {timedEvents.map((event) => (
                          <li key={event.seq}>
                            {event.kind} · t={event.payload.time}
                            <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <EmptyState>
                        No events have matching episode, clock and availability
                        metadata for this decision. All run events remain in the
                        event timeline below.
                      </EmptyState>
                    )}
                  </>
                )}
                {view === replayViews[1] && (
                  <>
                    {spec ? (
                      <FrozenTopology definition={spec} />
                    ) : (
                      <EmptyState>
                        A frozen run definition is required to inspect topology.
                        The editor graph is not substituted for retained run
                        state.
                      </EmptyState>
                    )}
                    <h4>Retained state at frame {cursor}</h4>
                    {point ? (
                      <pre>{JSON.stringify(point, null, 2)}</pre>
                    ) : (
                      <EmptyState>
                        {failedSources.length ||
                        missingSources.length ||
                        loadingRecords ||
                        waitingRecords
                          ? "No observation is available for this frame. Record retrieval has not completed successfully."
                          : "No state records retained for this run."}
                      </EmptyState>
                    )}
                  </>
                )}
                {view === replayViews[2] && (
                  <>
                    <CoverageInspector
                      runId={run.id}
                      available={retained.all.some(
                        (artifact) => artifact.role === "transformed_dataset",
                      )}
                      onSelect={(source) => {
                        setCoverageSelectionState({ scopeKey, source });
                        const loaded = trend.samples.find(
                          (p) =>
                            p.identity.artifactId ===
                              source.identity.artifact_id &&
                            p.identity.rowIndex === source.identity.row_index &&
                            p.identity.runId === source.identity.run_id,
                        );
                        if (loaded) trend.select(loaded.identity.key);
                      }}
                    />
                    {coverageSelection?.identity.run_id === run.id && (
                      <>
                        <p>
                          Inspecting exact retained artifact{" "}
                          {coverageSelection.identity.artifact_id} row{" "}
                          {coverageSelection.identity.row_index}.{" "}
                          {trend.samples.some(
                            (p) =>
                              p.identity.artifactId ===
                                coverageSelection.identity.artifact_id &&
                              p.identity.rowIndex ===
                                coverageSelection.identity.row_index,
                          )
                            ? "This row is loaded in the chart."
                            : "This row is outside the loaded chart window; the chart pin has not moved."}
                        </p>
                        <RepresentationInspector
                          identity={coverageSelection.identity}
                        />
                      </>
                    )}
                  </>
                )}
                {view === replayViews[3] && (
                  <RepresentationInspector
                    identity={
                      trend.selected
                        ? {
                            run_id: trend.selected.identity.runId,
                            artifact_id: trend.selected.identity.artifactId,
                            row_index: trend.selected.identity.rowIndex,
                          }
                        : undefined
                    }
                  />
                )}
              </div>
            ) : undefined
          }
        >
          {recordNotice}
          {hasObservations && (
            <Evidence
              label="Laboratory trend component state"
              data={{
                run_id: run.id,
                selection: trend.selected,
                preview: trend.preview?.identity,
                clock: trend.model.clock,
                viewport: trend.range,
                time_basis: trend.basis,
                fields: trend.fields,
                graph: {
                  view: trend.view,
                  mapping: trend.mapping,
                  group: trend.spatialGroup?.key,
                  geometry: trend.geometry,
                  opacity: trend.opacity,
                  camera: trend.camera,
                  camera_revision: trend.cameraRevision,
                },
              }}
            >
              <TrendWorkbench controller={trend} />
            </Evidence>
          )}
        </InspectionWorkspace>
      )}
      {hasObservations && replayViews.slice(0, 4).includes(view) && (
        <p className="lab-evidence-window text-text-secondary">
          Loaded window:{" "}
          {sourceStates
            .map(
              (source, index) =>
                `${source.label}: ${
                  source.status === "ready"
                    ? `${index === 0 ? records.length : rawRecords.length} loaded`
                    : source.status === "error"
                      ? "unavailable"
                      : source.status === "missing"
                        ? "not retained"
                        : source.status
                }`,
            )
            .join(" · ")}{" "}
          · up to 1,000 per artifact. Original and derived time coordinates
          remain independent.
        </p>
      )}
      {view === replayViews[4] && (
        <RetainedBaselineComparison
          artifacts={retained.all}
          comparison={result?.baseline_comparison}
          records={Array.isArray(result?.records) ? result.records : []}
          runId={run.id}
          scopeKey={scopeKey}
          execution={result?.qualification?.execution ?? run.state}
          runState={run.state}
        />
      )}
      {view === replayViews[5] && (
        <>
          {result?.deployment_tradeoffs ? (
            <pre>{JSON.stringify(result.deployment_tradeoffs, null, 2)}</pre>
          ) : (
            <EmptyState>
              Deployment tradeoffs require measured target latency, memory,
              energy or cost, and quality metrics for candidate artifacts. No
              deployment measurements are attached to this run.
            </EmptyState>
          )}
        </>
      )}
    </section>
  );
}
function FrozenTopology({ definition }: { definition: ExperimentDefinition }) {
  return (
    <div className="lab-frozen-topology">
      <DefinitionGraph definition={definition} onChange={() => {}} readOnly />
    </div>
  );
}
