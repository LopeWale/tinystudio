import { ReusableTemplateGallery } from "./templates";
import { useState } from "react";
import { Button as BoardButton } from "../components/boardui/base/buttons/button";
import { Input as BoardInput } from "../components/boardui/base/input/input";
import { AgentThinking } from "../components/boardui/application/agent-thinking/agent-thinking";
import { Page, Panel, Badge } from "../components/studio/primitives";
import { FileUploadField, UploadStatus } from "../components/studio/uploads";
import { NeuronMap, LabelBars } from "../features/interpretability/plots";
import {
  AssistantActivity,
  AssistantAttachments,
  AssistantSources,
} from "../features/assistant/display";
import type { Attachment, ToolCall } from "../controllers/conversation";
import { AssistantMarkdownSample } from "../features/assistant/messageContent.sample";
import "../features/assistant/assistant.css";
import type { UploadPhase } from "../controllers/uploads";
import "../features/interpretability/interp.css";

// These records are authored examples, never read from a project or sent to a model.
const illustrativeNeurons = [
  {
    neuron: 12,
    x: 0.15,
    y: 2.4,
    explanation: "Illustrative command phrasing",
    labels: [
      ["commands", 0.75],
      ["questions", 0.25],
    ] as [string, number][],
  },
  {
    neuron: 433,
    x: 0.6,
    y: 5.2,
    explanation: "Illustrative support requests",
    labels: [
      ["support", 0.5],
      ["commands", 0.25],
    ] as [string, number][],
  },
  {
    neuron: 920,
    x: null,
    y: 1.1,
    explanation: "Illustrative missing coordinate",
    labels: [] as [string, number][],
  },
];
const attachmentFixture: Attachment = {
  id: "illustrative-file",
  name: "illustrative-notes.md",
  size: 512,
  sha256: "0".repeat(64),
  project_id: "illustrative-project",
  user_id: "illustrative-user",
  status: "ready",
  extracted_chars: 400,
  retained_chars: 320,
};
const phases: UploadPhase[] = [
  "selected",
  "uploading",
  "importing",
  "indexing",
  "ready",
  "failed",
  "cancelled",
];
export function ComponentCatalogPage() {
  const [text, setText] = useState("Illustrative dataset"),
    [pending, setPending] = useState(false),
    [thinking, setThinking] = useState(false);
  const [files, setFiles] = useState<File[]>([]),
    [phase, setPhase] = useState<UploadPhase>("selected"),
    [loaded, setLoaded] = useState(256),
    [knownTotal, setKnownTotal] = useState(true);
  const [neuron, setNeuron] = useState<number | null>(12),
    [label, setLabel] = useState("");
  const [activityPhase, setActivityPhase] = useState("running");
  const [attachments, setAttachments] = useState<Attachment[]>([
    attachmentFixture,
  ]);
  const calls: ToolCall[] = [
    {
      id: "illustrative-call",
      name: "illustrative_inspect_records",
      kind: "tool",
      input: { dataset: "illustrative-only" },
      ...(activityPhase === "running"
        ? {}
        : {
            output:
              activityPhase === "failed"
                ? { error: "Illustrative permission failure" }
                : { count: 4, illustrative: true },
          }),
    },
  ];
  const selected = illustrativeNeurons.find((row) => row.neuron === neuron);
  return (
    <Page
      title="Component catalog"
      description="Development examples from actual TinyStudio components. All records, work states, and counters here are illustrative. Nothing is uploaded, indexed, or sent to a model."
    >
      <ReusableTemplateGallery />
      <Panel title="Origin and ownership">
        <p>
          <Badge>AffectUI + free BoardUI + TinyStudio custom</Badge>
        </p>
        <p>
          Button, Input, Agent Thinking and the upload field's underlying File
          Upload are installed free BoardUI source. Upload status, linked
          scientific views and assistant presentation are TinyStudio product
          components. No BoardUI Pro blocks or Pro activation are used.
        </p>
        <p>
          This catalog is a development artifact. Its controls change local
          example state only.
        </p>
      </Panel>
      <Panel title="Buttons and input · free BoardUI">
        <BoardInput
          label="Illustrative name"
          hint="Local example input; no save request"
          value={text}
          onChange={setText}
        />
        <div className="data-actions">
          <BoardButton
            variant="primary"
            onClick={() => setPending(!pending)}
            aria-pressed={pending}
          >
            {pending
              ? "End illustrative pending state"
              : "Show illustrative pending state"}
          </BoardButton>
          <BoardButton variant="secondary">Secondary example</BoardButton>
          <BoardButton variant="ghost">Ghost example</BoardButton>
          <BoardButton variant="danger" disabled>
            Disabled danger example
          </BoardButton>
        </div>
        <p role="status">
          {pending
            ? "Illustrative action pending. No request exists."
            : `Local input: ${text}`}
        </p>
      </Panel>
      <Panel title="Agent Thinking · free BoardUI">
        <p>
          Manually toggled presentation. It does not represent model reasoning
          or a real running request.
        </p>
        <BoardButton variant="secondary" onClick={() => setThinking(!thinking)}>
          {thinking
            ? "Hide illustrative activity"
            : "Show illustrative activity"}
        </BoardButton>
        {thinking && (
          <AgentThinking
            variant="stars"
            label="Illustrative active request"
            shimmer={false}
            showTimer={false}
          />
        )}
      </Panel>
      <Panel title="Shared file selection and transport states">
        <p>
          Choosing a file retains only a browser File reference for this mounted
          example. Accepted: .txt and .md; 1 MiB each. No bytes leave this
          browser.
        </p>
        <FileUploadField
          label="Illustrative source files"
          accept=".txt,.md"
          multiple
          files={files}
          onChange={setFiles}
          maxBytes={1048576}
        />
        <div className="data-actions">
          {phases.map((value) => (
            <BoardButton
              key={value}
              variant={phase === value ? "primary" : "secondary"}
              aria-pressed={phase === value}
              onClick={() => setPhase(value)}
            >
              Example: {value}
            </BoardButton>
          ))}
        </div>
        <label className="data-check">
          <input
            type="checkbox"
            checked={knownTotal}
            onChange={(e) => setKnownTotal(e.target.checked)}
          />
          Known illustrative byte total
        </label>
        <label>
          Illustrative bytes transferred
          <input
            aria-label="Illustrative bytes transferred"
            type="range"
            min={0}
            max={1024}
            step={128}
            value={loaded}
            onChange={(e) => {
              setLoaded(Number(e.target.value));
              setPhase("uploading");
            }}
          />
        </label>
        <UploadStatus
          phase={phase}
          progress={
            ["uploading", "importing", "indexing"].includes(phase)
              ? {
                  loaded: phase === "uploading" ? loaded : 1024,
                  total: knownTotal ? 1024 : undefined,
                }
              : undefined
          }
          error={
            phase === "failed"
              ? "Illustrative connection failure"
              : phase === "cancelled"
                ? "Illustrative cancellation; no actual transfer was started"
                : undefined
          }
          onCancel={() => setPhase("cancelled")}
          onRetry={() => {
            setLoaded(0);
            setPhase("uploading");
          }}
        />
        <p>
          Transfer completion and server import/indexing are separate phases in
          the product. This example uses explicit controls rather than simulated
          transport timers.
        </p>
      </Panel>
      <Panel title="Assistant activity, attachments and sources · TinyStudio custom">
        <p>
          Illustrative display records using the exact production presentation
          components. These are custom TinyStudio patterns, not installed
          BoardUI Pro blocks. No operation or search occurred.
        </p>
        <div className="data-actions">
          {["running", "completed", "failed"].map((value) => (
            <BoardButton
              key={value}
              variant={activityPhase === value ? "primary" : "secondary"}
              onClick={() => setActivityPhase(value)}
            >
              Illustrative operation: {value}
            </BoardButton>
          ))}
        </div>
        <AssistantActivity
          calls={calls}
          searches={[
            {
              id: "illustrative-search",
              state: activityPhase,
              query: "Illustrative documentation query",
              ...(activityPhase === "failed"
                ? { error: "Illustrative provider capability failure" }
                : {}),
            },
          ]}
          uiRequests={[
            {
              id: "illustrative-ui",
              action: "illustrative_select_record",
              state: activityPhase,
            },
          ]}
        />
        <AssistantAttachments
          attachments={attachments}
          onRemove={(id) =>
            setAttachments((items) => items.filter((item) => item.id !== id))
          }
          onDelete={(id) =>
            setAttachments((items) => items.filter((item) => item.id !== id))
          }
        />
        {!attachments.length && (
          <p role="status">
            Illustrative attachment removed locally. No stored file was deleted.
          </p>
        )}
        <BoardButton
          variant="secondary"
          onClick={() => setAttachments([attachmentFixture])}
        >
          Restore illustrative attachment
        </BoardButton>
        <p>
          Illustrative source links for display only; they are not returned
          search citations.
        </p>
        <AssistantSources
          sources={[
            {
              title: "Illustrative source link · BoardUI installation",
              url: "https://www.boardui.com/installation",
            },
            {
              title: "Unsafe illustrative URL omitted",
              url: "javascript:invalid",
            },
          ]}
        />
      </Panel>
      <Panel title="Assistant message formatting · TinyStudio">
        <AssistantMarkdownSample />
      </Panel>
      <Panel title="Linked neuron map and label shares · TinyStudio">
        <p>
          Illustrative fixture: three neuron records, one undefined x
          coordinate, and label shares computed from four distinct retained
          datapoints. Repeated tokens do not change that denominator.
        </p>
        <NeuronMap
          label="Illustrative neuron map"
          rows={illustrativeNeurons}
          selected={neuron}
          onSelect={(next) => {
            setNeuron(next);
            setLabel("");
          }}
          xLabel="Illustrative token density"
          yLabel="Illustrative activation"
        />
        <div className="data-actions">
          {illustrativeNeurons.map((row) => (
            <BoardButton
              key={row.neuron}
              variant={neuron === row.neuron ? "primary" : "secondary"}
              onClick={() => {
                setNeuron(row.neuron);
                setLabel("");
              }}
            >
              Select illustrative neuron {row.neuron}
            </BoardButton>
          ))}
        </div>
        <section aria-label="Illustrative selected neuron">
          <h3>Selected neuron {neuron}</h3>
          <p>{selected?.explanation}</p>
          {selected?.x === null && (
            <p>
              This neuron remains selectable from its record; the plot omits its
              undefined coordinate.
            </p>
          )}
          <LabelBars
            pairs={selected?.labels || []}
            denominator={4}
            selected={label}
            onSelect={setLabel}
          />
          {label && (
            <p role="status">
              Illustrative label selected: {label}. No corpus query was issued.
            </p>
          )}
        </section>
      </Panel>
    </Page>
  );
}
