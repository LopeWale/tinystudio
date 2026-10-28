/** Browser software fixture: the real replay component, with no service execution. */
import { createRoot } from "react-dom/client";
import { StudioContext, type StudioRuntime } from "../../src/app/StudioContext";
import { EvidenceRegistry } from "../../src/app/evidence";
import { DirectionProvider } from "../../src/components/boardui/foundations/direction/direction";
import { Page, Panel } from "../../src/components/studio/primitives";
import { ReplayViews } from "../../src/features/lab/replay";
import "../../src/styles/globals.css";
import "../../src/styles/studio.css";
import "../../src/styles/workspace.css";
import "../../src/styles/workbench.css";
import "../../src/features/lab/lab.css";
const raw = [-1.25, 0.125, null, 2.5, -0.375, 1.75].map((value, index) => ({
  record_id: `r${index}`,
  source_record_id: `source:${index}`,
  source_record_ids: [`source:${index}`],
  source_artifact_id: "fixture-recording",
  episode_id: "fixture-episode",
  device_id: "sensor",
  signal_id: "voltage",
  clock_domain: "reference",
  clock_units: "s",
  clock_origin: "fixture-start",
  clock_session: "fixture-session",
  time: index,
  available_at: index,
  value,
  units: "V",
  current: index / 10,
  field_units: { current: "A" },
  observed: value !== null,
  estimated: false,
  missing_mask: { value: value === null },
}));
const records = raw.map((row, index) =>
  index === 2
    ? {
        ...row,
        value: 0.125,
        estimated: true,
        source_record_ids: ["source:1", "source:2"],
        missing_mask: { value: false },
      }
    : { ...row },
);
// Known numeric grid verifies 3D projection/picking. It is not an RL result.
const landscape = Array.from({ length: 9 }, (_, direction) =>
  Array.from({ length: 31 }, (_, sample) => {
    const policy = (sample - 15) / 5;
    const value =
      -(policy * policy) * (0.35 + direction / 20) +
      Math.cos(policy * 2 + direction / 4) * 2 +
      Math.sin(direction / 2);
    const index = direction * 31 + sample;
    return {
      ...raw[0],
      record_id: `grid:${index}`,
      source_record_id: `grid-source:${index}`,
      source_record_ids: [`grid-source:${index}`],
      episode_id: "grid-software-fixture",
      signal_id: "sampled-landscape",
      time: index,
      available_at: index,
      policy_offset: policy,
      sampled_direction: direction,
      value,
      units: "fixture score",
      current: undefined,
      field_units: {
        policy_offset: "parameter offset",
        sampled_direction: "direction index",
      },
    };
  }),
).flat();
const showLandscape =
  new URLSearchParams(location.search).get("dataset") === "landscape";
const run: any = {
  id: "software-fixture",
  definition_id: "fixture",
  state: "succeeded",
  spec: {
    definition: {
      mode: "offline",
      inputs: [{ artifact_id: "fixture-recording" }],
    },
  },
  result: {
    artifacts: [
      {
        id: "derived-fixture",
        name: "transformed.jsonl",
        role: "transformed_dataset",
      },
      {
        id: "original-fixture",
        name: "raw-normalized.jsonl",
        role: "normalized_original",
      },
    ],
  },
};
const unavailable = () => {
  throw new Error("Software fixture must not call an application service");
};
const studio = {
  project: "software-fixture",
  user: { id: "fixture" },
  scopeKey: "fixture",
  route: "/lab",
  evidence: new EvidenceRegistry(),
  api: new Proxy({}, { get: () => unavailable }),
  navigate: unavailable,
} as unknown as StudioRuntime;
createRoot(document.getElementById("root")!).render(
  <DirectionProvider locale="en-US">
    <StudioContext.Provider value={studio}>
      <div className="workbench-shell">
        <main id="main-content" aria-label="Fixture content" tabIndex={-1}>
          <Page
            title={
              showLandscape
                ? "3D landscape verification"
                : "Interactive trend verification"
            }
            description="Disposable synthetic software fixture. This page performs no API calls, training or research studies."
            className="lab-page"
          >
            <Panel>
              <ReplayViews
                records={showLandscape ? landscape : records}
                rawRecords={showLandscape ? [] : raw}
                events={[]}
                run={run}
              />
            </Panel>
          </Page>
        </main>
      </div>
    </StudioContext.Provider>
  </DirectionProvider>,
);
