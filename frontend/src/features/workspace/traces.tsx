import { AdvancedTable } from "../../components/studio/data-table";
import { CollectionTemplate } from "../../components/studio/templates";
import { useComponentAction } from "../assistant/actions";
import {
  InspectionWorkspace,
  WorkspaceDisclosure,
} from "../../components/studio/workspace";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useResource, useStudio } from "../../app/StudioContext";
import {
  Badge,
  Button,
  EmptyState,
  Evidence,
  Field,
  Modal,
  Page,
  Panel,
} from "../../components/studio/primitives";
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
import { Input } from "../data/common";
import {
  RecordsScroll,
  RecordComparison,
  useRetainedResource,
} from "../data/records-workspace";
import "../data/records-workspace.css";
import "./serving-records.css";
interface Trace {
  id: string;
  time?: string;
  model: string;
  messages: { role: string; content: string }[];
  response: string;
  corrected?: string;
  score?: number | null;
  note?: string;
  origin?: string;
  seconds?: number;
  document_chunks?: any[];
}
export function TracesPage() {
  const studio = useStudio(),
    [offset, setOffset] = useState(0),
    traces = useRetainedResource<Trace[]>(
      `/api/traces?limit=200&offset=${offset}`,
    ),
    datasets = useResource<any[]>("/api/datasets"),
    [dataset, setDataset] = useState(""),
    [fix, setFix] = useState<Trace | null>(null),
    [inspectedId, inspect] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [query, setQuery] = useState(""),
    [checked, setChecked] = useState<Trace[]>([]),
    [comparing, compare] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null),
    compareFocus = useRef<HTMLElement | null>(null);
  const closeInspector = () => {
    inspect(null);
    returnFocus.current?.focus();
  };
  const visibleTraces = (traces.data || []).filter((trace) =>
    `${trace.id} ${trace.model} ${trace.messages.map((message) => message.content).join(" ")} ${trace.response} ${trace.corrected || ""}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  const toggleTrace = (trace: Trace) =>
    setChecked((previous) =>
      previous.some((item) => item.id === trace.id)
        ? previous.filter((item) => item.id !== trace.id)
        : [...previous, trace],
    );
  useEffect(() => {
    inspect(null);
    setChecked([]);
    compare(false);
    setQuery("");
    setOffset(0);
  }, [studio.scopeKey]);
  useEffect(() => {
    if (!traces.data) return;
    setChecked((previous) =>
      previous.map(
        (record) =>
          traces.data!.find((item) => item.id === record.id) || record,
      ),
    );
  }, [traces.data]);
  const choices = (datasets.data ?? []).filter((d) => !d.missing),
    currentDataset = choices.some((d) => d.name === dataset)
      ? dataset
      : (choices[0]?.name ?? "");
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  async function action(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (e: any) {
      if (mounted.current && e.name !== "AbortError") setError(e.message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  async function score(trace: Trace, value: number) {
    await studio.api.post(`/api/traces/${encodeURIComponent(trace.id)}/score`, {
      score: value,
    });
    traces.reload();
  }
  const selected = traces.data?.find((trace) => trace.id === inspectedId);
  useComponentAction({
    id: "traces.inspect",
    label: "Inspect an inference trace",
    kind: "select",
    disabled: !traces.data?.length,
    state: { selected: inspectedId },
    options: (traces.data || []).map((trace) => ({
      value: trace.id,
      label: `${trace.model} · ${trace.id}`,
    })),
    run: (id) => inspect(id || null),
  });
  useComponentAction({
    id: "traces.close",
    label: "Close trace inspector",
    kind: "open",
    disabled: !selected,
    run: closeInspector,
  });
  useComponentAction({
    id: "traces.refresh",
    label: "Refresh inference traces",
    kind: "open",
    run: traces.reload,
  });
  return (
    <Page
      className="traces-workspace data-workspace records-page serving-records-page"
      title="Traces"
      description="Review recent inferences, score responses, and turn reviewed examples into training data."
      actions={<Button onClick={traces.reload}>Refresh</Button>}
    >
      <CollectionTemplate
        label="Trace review workspace"
        toolbar={
          <div className="records-toolbar">
            <Input
              label="Find a trace on this page"
              type="search"
              value={query}
              onChange={setQuery}
            />
            <span className="records-count">
              {visibleTraces.length} traces on this page
            </span>
            <span className="records-selection" role="status">
              {checked.length} selected
            </span>
            <Button
              disabled={checked.length < 2}
              onClick={(event) => {
                compareFocus.current = event.currentTarget;
                compare(true);
              }}
            >
              Compare selected
            </Button>
            <Button
              disabled={!checked.length}
              variant="ghost"
              onClick={() => setChecked([])}
            >
              Clear selection
            </Button>
          </div>
        }
      >
        {(error || traces.error || datasets.error) && (
          <p role="alert">
            {error || traces.error?.message || datasets.error?.message}
          </p>
        )}
        <WorkspaceDisclosure
          title="Promote reviewed responses"
          summary="Add approved corrections to a training dataset"
        >
          <div className="trace-promotion">
            <Field label="Add to dataset">
              <select
                value={currentDataset}
                disabled={!choices.length || busy}
                onChange={(e) => setDataset(e.target.value)}
              >
                {!choices.length && (
                  <option value="">No datasets available</option>
                )}
                {choices.map((d) => (
                  <option key={d.name}>{d.name}</option>
                ))}
              </select>
            </Field>
            <Button
              variant="primary"
              disabled={!currentDataset || busy}
              loading={busy}
              onClick={() =>
                void action(async () => {
                  const result = await studio.api.post<{ promoted: number }>(
                    "/api/traces/promote",
                    { dataset: currentDataset, min_score: 0.9 },
                  );
                  studio.toast(
                    `Added ${result.promoted} datapoints to ${currentDataset}`,
                  );
                  await studio.refreshOverview();
                })
              }
            >
              Add scored ≥ 0.9 to dataset
            </Button>
          </div>
          <p className="muted">
            Only traces scored 90% or above are included. Saved corrections
            become the training response.
            {!choices.length && " Create a dataset before adding traces."}
          </p>
        </WorkspaceDisclosure>

        {comparing && (
          <RecordComparison
            records={checked.map((trace) => ({ ...trace, name: trace.id }))}
            fields={[
              { label: "Model", value: (trace) => trace.model },
              {
                label: "User message",
                value: (trace) =>
                  [...trace.messages]
                    .reverse()
                    .find((message: any) => message.role === "user")?.content ||
                  "—",
              },
              { label: "Response", value: (trace) => trace.response },
              { label: "Correction", value: (trace) => trace.corrected || "—" },
              {
                label: "Score",
                value: (trace) =>
                  trace.score == null
                    ? "Unscored"
                    : `${(trace.score * 100).toFixed(1)}%`,
              },
              {
                label: "Latency",
                value: (trace) =>
                  trace.seconds == null ? "—" : `${trace.seconds} s`,
              },
              { label: "Origin", value: (trace) => trace.origin || "—" },
            ]}
            onRemove={toggleTrace}
            onClose={() => {
              compare(false);
              compareFocus.current?.focus();
            }}
          />
        )}
        <div
          hidden={comparing}
          className="serving-inspection-frame"
          onKeyDown={(event) => {
            if (event.key === "Escape") closeInspector();
          }}
        >
          <InspectionWorkspace
            label="Trace inspector"
            inspector={
              selected && (
                <RecordsScroll label="Trace detail">
                  <Evidence label="Selected trace" data={selected}>
                    <div className="trace-review-heading">
                      <strong>{selected.model}</strong>
                      <Button size="sm" onClick={closeInspector}>
                        Close inspector
                      </Button>
                    </div>
                    <p className="muted">
                      {selected.time || "Time unavailable"} ·{" "}
                      {selected.score == null
                        ? "Unscored"
                        : `${(selected.score * 100).toFixed(1)}%`}
                    </p>
                    <h3>Messages</h3>
                    {selected.messages.map((message, index) => (
                      <section key={index}>
                        <small className="muted">{message.role}</small>
                        <pre>{message.content}</pre>
                      </section>
                    ))}
                    <h3>Response</h3>
                    <pre>{selected.response}</pre>
                    {selected.corrected && (
                      <>
                        <h3>Corrected response</h3>
                        <pre>{selected.corrected}</pre>
                      </>
                    )}
                    {selected.note && <p>{selected.note}</p>}
                    {!!selected.document_chunks?.length && (
                      <details>
                        <summary>Retrieved evidence</summary>
                        <pre>
                          {JSON.stringify(selected.document_chunks, null, 2)}
                        </pre>
                      </details>
                    )}
                    <Button onClick={() => setFix(selected)}>
                      Correct response
                    </Button>
                  </Evidence>
                </RecordsScroll>
              )
            }
          >
            <RecordsScroll label="Trace collection">
              <Panel title="Recent inference traces">
                {traces.loading && !traces.data ? (
                  <p role="status">Loading traces…</p>
                ) : !visibleTraces.length ? (
                  <EmptyState>
                    {query ? (
                      <>
                        No traces match “{query}”.{" "}
                        <Button onClick={() => setQuery("")}>
                          Clear search
                        </Button>
                      </>
                    ) : (
                      <>
                        No traces on this page. Chat in the{" "}
                        <a href="#/playground">Playground</a> or call the API.
                      </>
                    )}
                  </EmptyState>
                ) : (
                  <AdvancedTable
                    label="Recent inference traces"
                    rows={visibleTraces}
                    getRowId={(trace) => trace.id}
                    scopeKey={`${studio.scopeKey}:${offset}`}
                    pagination="external"
                    rowProps={(trace) => ({
                      className:
                        trace.id === inspectedId ? "trace-selected" : undefined,
                    })}
                    renderRow={(trace, { key, ...props }, cells) => (
                      <Evidence
                        key={key}
                        as="tr"
                        {...props}
                        label="Inference trace"
                        data={trace}
                      >
                        {cells}
                      </Evidence>
                    )}
                    columns={[
                      {
                        id: "select",
                        label: "Select",
                        value: (trace) => trace.id,
                        sortable: false,
                        filterable: false,
                        hideable: false,
                        render: (trace) => (
                          <>
                            <Checkbox
                              aria-label={`Select trace ${trace.id}`}
                              isSelected={checked.some(
                                (item) => item.id === trace.id,
                              )}
                              onChange={() => toggleTrace(trace)}
                            />
                          </>
                        ),
                      },
                      {
                        id: "when",
                        label: "When · model",
                        value: (trace) => `${trace.time || ""} ${trace.model}`,
                        hideable: false,
                        render: (trace) => (
                          <>
                            {trace.time
                              ? new Date(trace.time).toLocaleString()
                              : "—"}
                            <br />
                            <code>{trace.model}</code>
                            {trace.origin && <Badge>{trace.origin}</Badge>}
                          </>
                        ),
                      },
                      {
                        id: "message",
                        label: "User message",
                        hideable: false,
                        value: (trace) =>
                          [...trace.messages]
                            .reverse()
                            .find((message) => message.role === "user")
                            ?.content,
                        render: (trace) => (
                          <>
                            <span className="trace-preview">
                              {
                                [...trace.messages]
                                  .reverse()
                                  .find((m) => m.role === "user")?.content
                              }
                            </span>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={(event) => {
                                returnFocus.current = event.currentTarget;
                                inspect(trace.id);
                              }}
                            >
                              Inspect trace
                            </Button>
                          </>
                        ),
                      },
                      {
                        id: "response",
                        label: "Response",
                        value: (trace) => trace.corrected || trace.response,
                        render: (trace) => (
                          <>
                            <span className="trace-preview">
                              {trace.corrected || trace.response}
                            </span>
                            {trace.corrected && <Badge>Corrected</Badge>}
                          </>
                        ),
                      },
                      {
                        id: "score",
                        label: "Score",
                        value: (trace) => trace.score,
                        render: (trace) => (
                          <>
                            {trace.score == null
                              ? "Unscored"
                              : `${(trace.score * 100).toFixed(1)}%`}
                          </>
                        ),
                      },
                      {
                        id: "actions",
                        label: "Review actions",
                        value: (trace) => trace.id,
                        sortable: false,
                        filterable: false,
                        hideable: false,
                        render: (trace) => (
                          <>
                            <div className="actions">
                              <Button
                                size="sm"
                                aria-label={`Mark response correct: ${trace.id}`}
                                disabled={busy}
                                onClick={() =>
                                  void action(() => score(trace, 1))
                                }
                              >
                                Correct
                              </Button>
                              <Button
                                size="sm"
                                aria-label={`Mark response incorrect: ${trace.id}`}
                                disabled={busy}
                                onClick={() =>
                                  void action(() => score(trace, 0))
                                }
                              >
                                Incorrect
                              </Button>
                              <Button size="sm" onClick={() => setFix(trace)}>
                                Fix
                              </Button>
                              <Button
                                size="sm"
                                variant="danger"
                                disabled={busy}
                                onClick={() =>
                                  void action(async () => {
                                    if (
                                      await studio.confirm(
                                        "Delete this inference trace?",
                                        "Delete trace",
                                      )
                                    ) {
                                      await studio.api.del(
                                        `/api/traces/${encodeURIComponent(trace.id)}`,
                                      );
                                      setChecked((previous) =>
                                        previous.filter(
                                          (item) => item.id !== trace.id,
                                        ),
                                      );
                                      if (inspectedId === trace.id)
                                        closeInspector();
                                      traces.reload();
                                    }
                                  })
                                }
                              >
                                Delete
                              </Button>
                            </div>
                          </>
                        ),
                      },
                    ]}
                  />
                )}
                <div className="actions">
                  <Button
                    disabled={!offset || traces.loading}
                    onClick={() => setOffset((v) => Math.max(0, v - 200))}
                  >
                    Previous 200
                  </Button>
                  <span>
                    Showing {traces.data?.length ? offset + 1 : 0}–
                    {offset + (traces.data?.length ?? 0)}
                  </span>
                  <Button
                    disabled={
                      traces.loading || (traces.data?.length ?? 0) < 200
                    }
                    onClick={() => setOffset((v) => v + 200)}
                  >
                    Next 200
                  </Button>
                </div>
              </Panel>
            </RecordsScroll>
          </InspectionWorkspace>
        </div>
        {traces.isStale && (
          <p role="status">
            {traces.loading ? "Refreshing traces…" : "Refresh failed."} Previous
            results remain visible.
          </p>
        )}
        {fix && (
          <TraceCorrection
            trace={fix}
            onClose={() => setFix(null)}
            onSaved={() => {
              setFix(null);
              traces.reload();
            }}
          />
        )}
      </CollectionTemplate>
    </Page>
  );
}
function TraceCorrection({
  trace,
  onClose,
  onSaved,
}: {
  trace: Trace;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { api } = useStudio(),
    [answer, setAnswer] = useState(trace.corrected || trace.response),
    [note, setNote] = useState(trace.note || ""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    owner = useRef<AbortController | null>(null);
  useEffect(() => {
    owner.current = new AbortController();
    return () => owner.current?.abort();
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.post(
        `/api/traces/${encodeURIComponent(trace.id)}/score`,
        { score: 1, corrected: answer, note },
        owner.current?.signal,
      );
      onSaved();
    } catch (e: any) {
      if (e.name !== "AbortError") setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal open title="Correct this response" onClose={onClose}>
      <form onSubmit={submit}>
        <Field
          label="Corrected answer"
          hint="Used when this trace is promoted to a dataset."
        >
          <textarea
            rows={8}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            required
          />
        </Field>
        <Field label="Feedback (optional)">
          <input value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        {error && <p role="alert">{error}</p>}
        <div className="actions">
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={busy}>
            Save as correct
          </Button>
        </div>
      </form>
    </Modal>
  );
}
