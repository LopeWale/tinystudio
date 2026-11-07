import { CollectionToolbar } from "../../components/studio/collection-toolbar";
import { AdvancedTable } from "../../components/studio/data-table";
import { CollectionTemplate } from "../../components/studio/templates";
import { useComponentAction } from "../assistant/actions";
import { InspectionWorkspace } from "../../components/studio/workspace";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useResource, useStudio } from "../../app/StudioContext";
import {
  Badge,
  Button,
  Evidence,
  Field,
  Modal,
  Page,
  Panel,
} from "../../components/studio/primitives";
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
import {
  RecordsScroll,
  RecordComparison,
  useRetainedResource,
} from "../data/records-workspace";
import "../data/records-workspace.css";
import "./serving-records.css";
import "./traces-workspace.css";
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
function compactTraceTime(time?: string) {
  if (!time) return "Time unavailable";
  const recorded = new Date(time);
  if (Number.isNaN(recorded.getTime())) return time;
  return recorded.toLocaleString(undefined, {
    year: "2-digit",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
export function TracesPage() {
  const studio = useStudio(),
    [page, setPage] = useState({ scope: studio.scopeKey, offset: 0, size: 25 });
  // Scope changes cannot issue an intermediate request at the previous offset.
  const { offset, size: pageSize } =
    page.scope === studio.scopeKey ? page : { offset: 0, size: 25 };
  const setOffset = (next: number) =>
    setPage({ scope: studio.scopeKey, offset: next, size: pageSize });
  const resizePage = useCallback(
    (size: number) => {
      setPage((previous) => {
        const current =
          previous.scope === studio.scopeKey
            ? previous
            : { scope: studio.scopeKey, offset: 0, size: 25 };
        if (current.size === size) return current;
        return {
          ...current,
          size,
          offset: Math.floor(current.offset / size) * size,
        };
      });
    },
    [studio.scopeKey],
  );
  const traces = useRetainedResource<Trace[]>(
      `/api/traces?limit=${pageSize + 1}&offset=${offset}`,
    ),
    datasets = useResource<any[]>("/api/datasets"),
    [dataset, setDataset] = useState(""),
    [fix, setFix] = useState<Trace | null>(null),
    [inspectedId, inspect] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [query, setQuery] = useState(""),
    [checked, setChecked] = useState<Trace[]>([]),
    [comparing, compare] = useState(false),
    [promoting, setPromoting] = useState(false),
    [reviewRows, setReviewRows] = useState<readonly Trace[]>([]);
  const returnFocus = useRef<HTMLElement | null>(null),
    compareFocus = useRef<HTMLElement | null>(null),
    collectionFocus = useRef<HTMLDivElement | null>(null),
    pendingFocus = useRef<"trigger" | "collection" | null>(null);
  const closeInspector = () => {
    if (!inspectedId) return;
    pendingFocus.current = "trigger";
    inspect(null);
  };
  useLayoutEffect(() => {
    if (inspectedId || !pendingFocus.current) return;
    const target =
      pendingFocus.current === "trigger" && returnFocus.current?.isConnected
        ? returnFocus.current
        : collectionFocus.current;
    pendingFocus.current = null;
    target?.focus();
  }, [inspectedId]);
  // The extra record proves another page exists; exclude it from page rows and inspection.
  const pageTraces = traces.data?.slice(0, pageSize);
  const visibleTraces = (pageTraces || []).filter((trace) =>
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
    setPromoting(false);
    setFix(null);
    setQuery("");
    setPage({ scope: studio.scopeKey, offset: 0, size: 25 });
  }, [studio.scopeKey]);
  useEffect(() => {
    if (!traces.data) return;
    setChecked((previous) =>
      previous.map(
        (record) => pageTraces?.find((item) => item.id === record.id) || record,
      ),
    );
  }, [traces.data]);
  useEffect(() => {
    if (
      !traces.loading &&
      !traces.error &&
      !traces.isStale &&
      traces.data?.length === 0 &&
      offset > 0
    )
      setPage((current) =>
        current.scope === studio.scopeKey && current.offset === offset
          ? { ...current, offset: Math.max(0, offset - pageSize) }
          : current,
      );
  }, [
    traces.data,
    traces.loading,
    traces.error,
    traces.isStale,
    offset,
    pageSize,
    studio.scopeKey,
  ]);
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
    const updated = await studio.api.post<Trace>(
      `/api/traces/${encodeURIComponent(trace.id)}/score`,
      {
        score: value,
      },
    );
    updateSelectedSnapshot({ ...trace, score: value, ...updated });
    traces.reload();
  }
  const selectedSnapshot = useRef<{ scope: string; trace: Trace } | null>(null);
  const loadedSelection = pageTraces?.find((trace) => trace.id === inspectedId);
  useLayoutEffect(() => {
    if (loadedSelection)
      selectedSnapshot.current = {
        scope: studio.scopeKey,
        trace: loadedSelection,
      };
    else if (!inspectedId) selectedSnapshot.current = null;
  }, [loadedSelection, inspectedId, studio.scopeKey]);
  const updateSelectedSnapshot = (trace: Trace) => {
    if (
      mounted.current &&
      selectedSnapshot.current?.scope === studio.scopeKey &&
      selectedSnapshot.current.trace.id === trace.id
    ) {
      selectedSnapshot.current = { scope: studio.scopeKey, trace };
      setChecked((previous) =>
        previous.map((item) => (item.id === trace.id ? trace : item)),
      );
    }
  };
  const selected =
    loadedSelection ||
    (selectedSnapshot.current?.scope === studio.scopeKey &&
    selectedSnapshot.current.trace.id === inspectedId
      ? selectedSnapshot.current.trace
      : undefined);
  const selectedIndex = reviewRows.findIndex(
    (trace) => trace.id === inspectedId,
  );
  const navigateTrace = (direction: number) => {
    const next = reviewRows[selectedIndex + direction];
    if (selectedIndex >= 0 && next && !busy) inspect(next.id);
  };
  useComponentAction({
    id: "traces.inspect",
    label: "Inspect an inference trace",
    kind: "select",
    disabled: !pageTraces?.length,
    state: { selected: inspectedId },
    options: (pageTraces || []).map((trace) => ({
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
      className="traces-workspace data-workspace records-page serving-records-page collection-page"
      title="Traces"
      description="Review recent inferences, score responses, and turn reviewed examples into training data."
      actions={
        <div className="data-actions">
          <Button onClick={() => setPromoting(true)}>
            Promote reviewed responses
          </Button>
          <Button onClick={traces.reload}>Refresh</Button>
        </div>
      }
    >
      <CollectionTemplate
        className="data-template"
        label="Trace review workspace"
      >
        {(error || traces.error || datasets.error) && (
          <p role="alert">
            {error || traces.error?.message || datasets.error?.message}
          </p>
        )}
        <Modal
          open={promoting}
          title="Promote reviewed responses"
          onClose={() => setPromoting(false)}
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
            Across this project, only traces scored 90% or above are included.
            Saved corrections become the training response.
            {!choices.length && " Create a dataset before adding traces."}
          </p>
        </Modal>

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
                <div className="trace-review">
                  <header className="trace-review-heading">
                    <div>
                      <strong>{selected.model}</strong>
                      <span className="trace-review-id">{selected.id}</span>
                    </div>
                    <Button size="sm" onClick={closeInspector}>
                      Close inspector
                    </Button>
                  </header>
                  <nav
                    className="trace-review-navigation"
                    aria-label="Trace review navigation"
                  >
                    <Button
                      size="sm"
                      disabled={busy || selectedIndex <= 0}
                      onClick={() => navigateTrace(-1)}
                    >
                      Previous trace
                    </Button>
                    <span>
                      {selectedIndex < 0
                        ? loadedSelection
                          ? "Outside current search"
                          : "Outside current page"
                        : `${selectedIndex + 1} of ${reviewRows.length}`}
                    </span>
                    <Button
                      size="sm"
                      disabled={
                        busy ||
                        selectedIndex < 0 ||
                        selectedIndex >= reviewRows.length - 1
                      }
                      onClick={() => navigateTrace(1)}
                    >
                      Next trace
                    </Button>
                  </nav>
                  <dl className="trace-review-metadata">
                    <div>
                      <dt>Recorded</dt>
                      <dd className="trace-review-recorded">
                        {selected.time || "Time unavailable"}
                      </dd>
                    </div>
                    <div>
                      <dt>Score</dt>
                      <dd>
                        {selected.score == null
                          ? "Unscored"
                          : `${(selected.score * 100).toFixed(1)}%`}
                      </dd>
                    </div>
                    {selected.origin && (
                      <div>
                        <dt>Origin</dt>
                        <dd>{selected.origin}</dd>
                      </div>
                    )}
                    {selected.seconds != null && (
                      <div>
                        <dt>Latency</dt>
                        <dd>{selected.seconds} s</dd>
                      </div>
                    )}
                  </dl>
                  <RecordsScroll key={selected.id} label="Trace detail">
                    <Evidence
                      label="Selected trace"
                      data={selected}
                      className="trace-review-content"
                    >
                      <section
                        className="trace-review-messages"
                        aria-label="Source messages"
                      >
                        <h3>Source messages</h3>
                        {selected.messages.map((message, index) => (
                          <section key={index} className="trace-review-message">
                            <h4>{message.role}</h4>
                            <p className="trace-review-prose">
                              {message.content}
                            </p>
                          </section>
                        ))}
                      </section>
                      <section
                        className="trace-review-response"
                        aria-label="Model response"
                      >
                        <h3>Model response</h3>
                        <p className="trace-review-prose">
                          {selected.response}
                        </p>
                      </section>
                      {selected.corrected && (
                        <section
                          className="trace-review-response"
                          aria-label="Saved correction"
                        >
                          <h3>Corrected response</h3>
                          <p className="trace-review-prose">
                            {selected.corrected}
                          </p>
                        </section>
                      )}
                      {selected.note && (
                        <section>
                          <h3>Review feedback</h3>
                          <p className="trace-review-prose">{selected.note}</p>
                        </section>
                      )}
                      {!!selected.document_chunks?.length && (
                        <details>
                          <summary>Retrieved evidence</summary>
                          <pre>
                            {JSON.stringify(selected.document_chunks, null, 2)}
                          </pre>
                        </details>
                      )}
                    </Evidence>
                  </RecordsScroll>
                  <footer
                    className="trace-review-actions"
                    aria-label="Selected trace review actions"
                  >
                    <Button
                      size="sm"
                      aria-label={`Mark response correct: ${selected.id}`}
                      disabled={busy}
                      onClick={() => void action(() => score(selected, 1))}
                    >
                      Correct
                    </Button>
                    <Button
                      size="sm"
                      aria-label={`Mark response incorrect: ${selected.id}`}
                      disabled={busy}
                      onClick={() => void action(() => score(selected, 0))}
                    >
                      Incorrect
                    </Button>
                    <Button
                      size="sm"
                      disabled={busy}
                      onClick={() => setFix(selected)}
                    >
                      Correct response
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
                              `/api/traces/${encodeURIComponent(selected.id)}`,
                            );
                            setChecked((previous) =>
                              previous.filter(
                                (item) => item.id !== selected.id,
                              ),
                            );
                            pendingFocus.current = "collection";
                            inspect(null);
                            traces.reload();
                          }
                        })
                      }
                    >
                      Delete
                    </Button>
                  </footer>
                </div>
              )
            }
          >
            <div
              className="trace-table-host"
              ref={collectionFocus}
              tabIndex={-1}
            >
              <Panel title="Recent inference traces">
                <AdvancedTable
                  layout="fill"
                  toolbarContent={
                    <CollectionToolbar
                      label="Find a trace on this page"
                      query={query}
                      onQueryChange={setQuery}
                      selectedCount={checked.length}
                      onCompare={(event) => {
                        compareFocus.current = event.currentTarget;
                        compare(true);
                      }}
                      onClearSelection={() => setChecked([])}
                    />
                  }
                  label="Recent inference traces"
                  rows={visibleTraces}
                  onVisibleRowsChange={setReviewRows}
                  getRowId={(trace) => trace.id}
                  scopeKey={studio.scopeKey}
                  externalPage={{
                    offset,
                    pageSize,
                    count: pageTraces?.length ?? 0,
                    hasNext: (traces.data?.length ?? 0) > pageSize,
                    status: traces.loading
                      ? "pending"
                      : traces.error
                        ? "error"
                        : "success",
                    onOffsetChange: setOffset,
                    onPageSizeChange: resizePage,
                  }}
                  empty={
                    traces.loading
                      ? "Loading traces…"
                      : traces.error
                        ? "Traces could not be loaded. Refresh to retry."
                        : "No traces available."
                  }
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
                        <div className="trace-identity">
                          <span className="trace-model" title={trace.model}>
                            {trace.model}
                          </span>
                          <span
                            className="trace-recorded"
                            title={trace.time || "Time unavailable"}
                          >
                            {compactTraceTime(trace.time)}
                          </span>
                        </div>
                      ),
                    },
                    {
                      id: "message",
                      label: "User message",
                      hideable: false,
                      value: (trace) =>
                        [...trace.messages]
                          .reverse()
                          .find((message) => message.role === "user")?.content,
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
                  ]}
                />
              </Panel>
            </div>
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
            onSaved={(trace) => {
              updateSelectedSnapshot(trace);
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
  onSaved: (trace: Trace) => void;
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
      const updated = await api.post<Trace>(
        `/api/traces/${encodeURIComponent(trace.id)}/score`,
        { score: 1, corrected: answer, note },
        owner.current?.signal,
      );
      if (!owner.current?.signal.aborted)
        onSaved({ ...trace, score: 1, corrected: answer, note, ...updated });
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
