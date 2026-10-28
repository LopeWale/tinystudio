import {
  SimpleTable,
  DataTable,
  AdvancedTable,
} from "../components/studio/data-table";
import { useState, type FormEvent } from "react";
import { Button } from "../components/affectui/button-clean";
import { SegmentedControl } from "../components/affectui/controls";
import { Input } from "../components/boardui/base/input/input";
import { Checkbox } from "../components/boardui/base/checkbox/checkbox";
import { Field, Badge } from "../components/studio/primitives";
import {
  CollectionTemplate,
  DetailTemplate,
  AnalysisTemplate,
  RunTemplate,
  ConversationTemplate,
  SettingsTemplate,
  SetupTemplate,
} from "../components/studio/templates";
import "./templates.css";

// Authored demonstration records. No production queries, providers or controllers.
const records = [
  {
    id: "support",
    name: "Sample support",
    rows: 1234,
    kind: "Conversations",
    goal: "Answer account questions clearly.",
  },
  {
    id: "instructions",
    name: "Sample instructions",
    rows: 856,
    kind: "Instructions",
    goal: "Follow short, specific instructions.",
  },
  {
    id: "knowledge",
    name: "Sample knowledge",
    rows: 2410,
    kind: "Conversations",
    goal: "Explain the supplied knowledge.",
  },
];
const examples = [
  { value: "collection", label: "Collection" },
  { value: "detail", label: "Detail" },
  { value: "analysis", label: "Analysis" },
  { value: "run", label: "Run" },
  { value: "settings", label: "Settings" },
  { value: "setup", label: "Setup" },
  { value: "conversation", label: "Conversation" },
] as const;
type Example = (typeof examples)[number]["value"];

function CollectionExample() {
  const [query, setQuery] = useState("");
  const [tableType, setTableType] = useState<
    "simple" | "interactive" | "advanced"
  >("advanced");
  const Table =
    tableType === "simple"
      ? SimpleTable
      : tableType === "interactive"
        ? DataTable
        : AdvancedTable;
  const [checked, setChecked] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const visible = records.filter((record) =>
    record.name.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const inspected = records.find((record) => record.id === selected);
  return (
    <CollectionTemplate
      label="Illustrative collection"
      summary={
        <p>
          {records.length} authored datasets · {checked.length} selected
        </p>
      }
      toolbar={
        <>
          <Input
            label="Search sample datasets"
            className="catalog-collection-search"
            type="search"
            value={query}
            onChange={setQuery}
          />
          <SegmentedControl
            aria-label="Collection table type"
            value={tableType}
            onChange={setTableType}
            options={[
              { value: "simple", label: "Simple" },
              { value: "interactive", label: "Interactive" },
              { value: "advanced", label: "Advanced" },
            ]}
            variant="tray"
            size="sm"
          />
          <Button variant="ghost" onClick={() => setQuery("")}>
            Reset search
          </Button>
        </>
      }
      inspector={
        <>
          <section aria-label="Sample dataset inspection">
            <h3>{inspected?.name || "Dataset details"}</h3>
            {inspected ? (
              <>
                <p>{inspected.goal}</p>
                <dl>
                  <dt>Rows</dt>
                  <dd>{inspected.rows.toLocaleString()}</dd>
                  <dt>Kind</dt>
                  <dd>{inspected.kind}</dd>
                </dl>
                <Button variant="ghost" onClick={() => setSelected(null)}>
                  Close sample inspection
                </Button>
              </>
            ) : (
              <p>
                Inspect a row to see its goal and row count. Checking a row only
                changes selection.
              </p>
            )}
          </section>
        </>
      }
    >
      <Table
        label="Sample dataset collection"
        rows={visible}
        getRowId={(record) => record.id}
        scopeKey={`illustrative:${tableType}`}
        rowProps={(record) =>
          ({
            "data-selected": selected === record.id || undefined,
          }) as React.HTMLAttributes<HTMLTableRowElement>
        }
        columns={[
          {
            id: "select",
            label: "Select",
            value: (record) => record.id,
            sortable: false,
            filterable: false,
            hideable: false,
            render: (record) => (
              <>
                <Checkbox
                  aria-label={`Select ${record.name}`}
                  isSelected={checked.includes(record.id)}
                  onChange={(value) =>
                    setChecked((current) =>
                      value
                        ? [...current, record.id]
                        : current.filter((id) => id !== record.id),
                    )
                  }
                />
              </>
            ),
          },
          {
            id: "dataset",
            label: "Dataset",
            value: (record) => record.name,
            hideable: false,
            render: (record) => <>{record.name}</>,
          },
          {
            id: "rows",
            label: "Rows",
            value: (record) => record.rows,
            render: (record) => <>{record.rows.toLocaleString()}</>,
          },
          {
            id: "kind",
            label: "Kind",
            value: (record) => record.kind,
            render: (record) => <>{record.kind}</>,
          },
          {
            id: "actions",
            label: "Action",
            value: (record) => record.id,
            sortable: false,
            filterable: false,
            hideable: false,
            render: (record) => (
              <>
                <Button
                  size="sm"
                  variant="secondary"
                  aria-pressed={selected === record.id}
                  onClick={() => setSelected(record.id)}
                >
                  Inspect {record.name}
                </Button>
              </>
            ),
          },
        ]}
      />
      {!visible.length && (
        <p role="status">
          No sample datasets match. Reset filters to restore the authored
          records.
        </p>
      )}
      <p className="catalog-template-footnote">
        Selection is retained while filtering. All three table variants share
        the same typed records and row actions.
      </p>
    </CollectionTemplate>
  );
}
function DetailExample() {
  const [index, setIndex] = useState(0);
  const record = records[index];
  return (
    <DetailTemplate
      label="Illustrative record detail"
      summary={<h3>{record.name}</h3>}
      toolbar={
        <>
          <Button
            variant="secondary"
            disabled={!index}
            onClick={() => setIndex(index - 1)}
          >
            Previous sample
          </Button>
          <span>
            Record {index + 1} of {records.length}
          </span>
          <Button
            variant="secondary"
            disabled={index === records.length - 1}
            onClick={() => setIndex(index + 1)}
          >
            Next sample
          </Button>
        </>
      }
      inspector={
        <>
          <h3>Source record</h3>
          <dl>
            <dt>ID</dt>
            <dd>{record.id}</dd>
            <dt>Kind</dt>
            <dd>{record.kind}</dd>
            <dt>Rows</dt>
            <dd>{record.rows.toLocaleString()}</dd>
          </dl>
          <p>Metadata belongs to this exact authored record.</p>
        </>
      }
    >
      <section className="catalog-template-reading">
        <h3>Dataset goal</h3>
        <p>{record.goal}</p>
        <h3>Review context</h3>
        <p>
          Use the record controls to keep the goal and metadata synchronized.
          This example contains no actual datapoints.
        </p>
      </section>
    </DetailTemplate>
  );
}
const days = [
  { day: "Day 1", count: 2 },
  { day: "Day 2", count: 0 },
  { day: "Day 3", count: 4 },
  { day: "Day 4", count: 3 },
  { day: "Day 5", count: 1 },
  { day: "Day 6", count: 0 },
  { day: "Day 7", count: 5 },
];
function AnalysisExample() {
  const [range, setRange] = useState<"week" | "recent">("week");
  const [day, setDay] = useState<string | null>(null);
  const visible = range === "week" ? days : days.slice(-3);
  const selected = days.find((entry) => entry.day === day);
  return (
    <AnalysisTemplate
      label="Illustrative request analysis"
      summary={
        <p>
          <strong>
            {visible.reduce((sum, entry) => sum + entry.count, 0)} requests
          </strong>{" "}
          across {visible.length} authored days
        </p>
      }
      toolbar={
        <SegmentedControl
          aria-label="Sample analysis window"
          options={[
            { value: "week", label: "All 7 days" },
            { value: "recent", label: "Last 3 days" },
          ]}
          value={range}
          onChange={(next) => {
            setRange(next);
            if (
              next === "recent" &&
              !days.slice(-3).some((entry) => entry.day === day)
            )
              setDay(null);
          }}
          variant="tray"
          size="sm"
        />
      }
      inspector={
        <>
          <h3>Selected observation</h3>
          {selected ? (
            <p role="status">
              {selected.day}: {selected.count} requests
            </p>
          ) : (
            <p>Choose a bar to inspect its exact authored count.</p>
          )}
          <p>
            Unit: requests. Bars share a fixed 0–5 request scale. These values
            are illustrative, not serving metrics or a model landscape.
          </p>
        </>
      }
    >
      <div
        className="catalog-request-bars"
        aria-label="Authored daily request counts"
      >
        {visible.map((entry) => (
          <button
            key={entry.day}
            type="button"
            aria-label={`${entry.day}: ${entry.count} requests`}
            aria-pressed={day === entry.day}
            onClick={() => setDay(entry.day)}
          >
            <span>{entry.day}</span>
            <span className="catalog-request-track" aria-hidden="true">
              <span style={{ inlineSize: `${(entry.count / 5) * 100}%` }} />
            </span>
            <strong>{entry.count}</strong>
          </button>
        ))}
      </div>
      <p className="catalog-template-footnote">
        Zero counts remain selectable. A shorter window does not rescale the
        bars.
      </p>
    </AnalysisTemplate>
  );
}
function RunExample() {
  const [failure, setFailure] = useState(true);
  const [stage, setStage] = useState(1);
  const stages = [
    {
      name: "Prepare",
      state: "Completed",
      detail: "Authored fixture: 3 source records accepted.",
    },
    {
      name: "Execute",
      state: failure ? "Failed" : "Completed",
      detail: failure
        ? "Authored fixture: the worker reported a validation error. No output was produced."
        : "Authored fixture: execution completed with 3 output records.",
    },
    {
      name: "Publish",
      state: failure ? "Not started" : "Completed",
      detail: failure
        ? "Authored fixture: publication was not attempted after execution failed."
        : "Authored fixture: 3 output records were published.",
    },
  ];
  return (
    <RunTemplate
      label="Illustrative run review"
      summary={
        <p>
          <Badge>Authored {failure ? "failure" : "success"} scenario</Badge> No
          job was started.
        </p>
      }
      toolbar={
        <Button
          variant="secondary"
          onClick={() => setFailure((value) => !value)}
        >
          Show {failure ? "success" : "failure"} scenario
        </Button>
      }
      inspector={
        <>
          <h3>{stages[stage].name} evidence</h3>
          <p role="status">{stages[stage].detail}</p>
          <details>
            <summary>Fixture event</summary>
            <pre>
              {JSON.stringify(
                {
                  stage: stages[stage].name,
                  state: stages[stage].state,
                  illustrative: true,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </>
      }
    >
      <ol className="catalog-stage-list">
        {stages.map((entry, index) => (
          <li key={entry.name}>
            <Button
              variant="ghost"
              aria-pressed={stage === index}
              onClick={() => setStage(index)}
            >
              {entry.name}
              <Badge>{entry.state}</Badge>
            </Button>
          </li>
        ))}
      </ol>
    </RunTemplate>
  );
}
function SettingsExample() {
  const [saved, setSaved] = useState("Example workspace");
  const [draft, setDraft] = useState(saved);
  const [notice, setNotice] = useState("");
  const dirty = saved !== draft;
  function save(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim()) {
      setNotice("Enter a workspace name.");
      return;
    }
    setSaved(draft.trim());
    setDraft(draft.trim());
    setNotice("Saved locally in this example. No deployment setting changed.");
  }
  return (
    <SettingsTemplate
      label="Illustrative settings"
      summary={<p>Workspace profile · local draft example</p>}
      inspector={
        <>
          <h3>Saved example</h3>
          <p>{saved}</p>
          <p>
            Reset restores the last local save. Leaving the gallery discards all
            example state.
          </p>
        </>
      }
    >
      <form onSubmit={save} noValidate className="catalog-template-form">
        <Input
          label="Example workspace name"
          value={draft}
          onChange={(value) => {
            setDraft(value);
            setNotice("");
          }}
        />
        <p role="status">
          {dirty ? "Unsaved local changes" : "No unsaved local changes"}
        </p>
        {notice && (
          <p role={notice.startsWith("Enter") ? "alert" : "status"}>{notice}</p>
        )}
        <div className="catalog-template-actions">
          <Button
            variant="secondary"
            disabled={!dirty}
            onClick={() => {
              setDraft(saved);
              setNotice("");
            }}
          >
            Reset example draft
          </Button>
          <Button type="submit" disabled={!dirty}>
            Save example settings
          </Button>
        </div>
      </form>
    </SettingsTemplate>
  );
}
function SetupExample() {
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [error, setError] = useState("");
  function next(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !goal.trim()) {
      setError("Enter both a name and a goal before review.");
      return;
    }
    setError("");
    setStep(step + 1);
  }
  return (
    <SetupTemplate
      label="Illustrative project setup"
      summary={
        <p>
          Step {Math.min(step + 1, 2)} of 2 ·{" "}
          {step === 0 ? "Describe" : "Review"}
        </p>
      }
      inspector={
        <>
          <h3>Retained draft</h3>
          <p>Name: {name || "Not supplied"}</p>
          <p>Goal: {goal || "Not supplied"}</p>
          <p>This flow creates no project and never calls a workspace API.</p>
        </>
      }
    >
      {step < 2 ? (
        <form onSubmit={next} noValidate className="catalog-template-form">
          {step === 0 ? (
            <>
              <Input
                label="Example project name"
                value={name}
                onChange={setName}
              />
              <Input
                label="Example project goal"
                value={goal}
                onChange={setGoal}
              />
            </>
          ) : (
            <>
              <h3>Review {name}</h3>
              <p>{goal}</p>
              <Button variant="secondary" onClick={() => setStep(0)}>
                Back to description
              </Button>
            </>
          )}
          {error && <p role="alert">{error}</p>}
          <Button type="submit">
            {step === 0 ? "Review example" : "Confirm local example"}
          </Button>
        </form>
      ) : (
        <>
          <p role="status">
            Local example confirmed: {name}. No project was created.
          </p>
          <Button
            variant="secondary"
            onClick={() => {
              setStep(0);
              setName("");
              setGoal("");
            }}
          >
            Start another example
          </Button>
        </>
      )}
    </SetupTemplate>
  );
}
function ConversationExample() {
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<string[]>([]);
  return (
    <ConversationTemplate
      label="Illustrative conversation"
      toolbar={
        <>
          <Badge>Local composition</Badge>
          <span>No provider connected</span>
        </>
      }
      inspector={
        <>
          <h3>Conversation context</h3>
          <p>
            Messages remain in this example. Sending appends your text locally
            and generates no assistant reply.
          </p>
          <details>
            <summary>Activity and evidence</summary>
            <p>
              No tools ran, no evidence was queried, and no model was called.
            </p>
          </details>
        </>
      }
    >
      <div className="catalog-conversation-history" aria-live="polite">
        {messages.length ? (
          messages.map((message, index) => (
            <article key={index}>
              <strong>You · local example</strong>
              <p>{message}</p>
            </article>
          ))
        ) : (
          <p>
            Compose a message to exercise the local transcript and composer.
          </p>
        )}
      </div>
      <form
        className="catalog-template-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!draft.trim()) return;
          setMessages((current) => [...current, draft.trim()]);
          setDraft("");
        }}
      >
        <Field label="Example message">
          <textarea
            rows={3}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </Field>
        <div className="catalog-template-actions">
          <Button
            variant="secondary"
            disabled={!messages.length}
            onClick={() => setMessages([])}
          >
            Clear local messages
          </Button>
          <Button type="submit" disabled={!draft.trim()}>
            Add local message
          </Button>
        </div>
      </form>
    </ConversationTemplate>
  );
}
const demoComponents = {
  collection: CollectionExample,
  detail: DetailExample,
  analysis: AnalysisExample,
  run: RunExample,
  settings: SettingsExample,
  setup: SetupExample,
  conversation: ConversationExample,
};
export function ReusableTemplateGallery() {
  const [example, setExample] = useState<Example>("collection");
  const ExampleComponent = demoComponents[example];
  return (
    <section
      className="catalog-template-gallery"
      aria-label="Reusable template gallery"
    >
      <header>
        <h2>AffectUI reusable templates</h2>
        <p>
          Actual shared controls and layout slots with authored local examples.
          Controls never reach a network or model. Switching templates resets
          the previous example.
        </p>
        <p>
          Composition references:{" "}
          <a
            href="https://www.affectui.com/screens/crm/full"
            target="_blank"
            rel="noreferrer"
          >
            AffectUI CRM
          </a>{" "}
          and{" "}
          <a
            href="https://www.affectui.com/screens/evals/full"
            target="_blank"
            rel="noreferrer"
          >
            Evals
          </a>
          . Public controls are adapted MIT source; these layouts are TinyStudio
          compositions, not copied Pro screens.
        </p>
      </header>
      <SegmentedControl
        aria-label="Reusable template"
        value={example}
        onChange={setExample}
        options={examples.map((entry) => ({ ...entry }))}
        variant="underline"
        size="sm"
      />
      <div className="catalog-template-preview">
        <ExampleComponent />
      </div>
    </section>
  );
}
