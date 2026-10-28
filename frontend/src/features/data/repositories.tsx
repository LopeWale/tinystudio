import { CollectionToolbar } from "../../components/studio/collection-toolbar";
import { AdvancedTable, DataTable } from "../../components/studio/data-table";
import {
  CollectionTemplate,
  DetailTemplate,
} from "../../components/studio/templates";
import { useComponentAction } from "../assistant/actions";
import { useState, useEffect, useRef } from "react";
import { useStudio, useResource } from "../../app/StudioContext";
import {
  Page,
  Button,
  Modal,
  Evidence,
  EmptyState,
} from "../../components/studio/primitives";
import { FileUploadField, UploadStatus } from "../../components/studio/uploads";
import { enc, useAction, ErrorNotice, track } from "./common";
import {
  Input,
  TextField,
  InputBase,
} from "../../components/boardui/base/input/input";
import { Label } from "../../components/boardui/base/input/label";
import { Textarea } from "../../components/boardui/base/textarea/textarea";
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
import { cx } from "../../components/boardui/utils/cx";
import {
  RecordComparison,
  RecordsScroll,
  useRetainedResource,
} from "./records-workspace";
import "./records-workspace.css";
import "../../components/studio/form-controls.css";
export function RepositoriesPage() {
  const s = useStudio(),
    a = useAction(),
    r = useRetainedResource<any[]>("/api/repositories"),
    hub = useResource<any>("/api/hub/models");
  const [selectedRepository, selectRepository] = useState("");
  const [documentQuery, setDocumentQuery] = useState("");
  const dialogGeneration = useRef(0);
  const submissionGeneration = useRef<number | null>(null);
  const [query, setQuery] = useState(""),
    [checked, setChecked] = useState<any[]>([]),
    [comparing, compare] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null),
    compareFocus = useRef<HTMLElement | null>(null);
  const collectionSearch = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!comparing && compareFocus.current) {
      const trigger = compareFocus.current;
      if (trigger.isConnected && !trigger.matches(":disabled")) trigger.focus();
      else collectionSearch.current?.focus();
    }
  }, [comparing]);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const wasInspecting = useRef(false);
  const closeInspector = () => selectRepository("");
  useEffect(() => {
    setDocumentQuery("");
    if (selectedRepository) detailHeading.current?.focus();
    else if (wasInspecting.current) {
      const trigger = returnFocus.current;
      if (trigger?.isConnected && !trigger.closest("[hidden]")) trigger.focus();
      else collectionSearch.current?.focus();
    }
    wasInspecting.current = Boolean(selectedRepository);
  }, [selectedRepository]);
  const toggleRepository = (record: any) =>
    setChecked((previous) =>
      previous.some((item) => item.name === record.name)
        ? previous.filter((item) => item.name !== record.name)
        : [...previous, record],
    );
  const visible = (r.data || []).filter((item) =>
    `${item.name} ${item.description || ""}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  const inspected = r.data?.find((item) => item.name === selectedRepository);
  useEffect(() => {
    selectRepository("");
    setChecked([]);
    compare(false);
    setQuery("");
    dialogGeneration.current++;
    D("");
    Rows(null);
  }, [s.project, s.user?.id]);
  useEffect(() => {
    if (
      !r.loading &&
      !r.error &&
      r.data &&
      selectedRepository &&
      !r.data.some((item) => item.name === selectedRepository)
    )
      closeInspector();
  }, [r.loading, r.error, r.data, selectedRepository]);
  useEffect(() => {
    if (!r.data) return;
    setChecked((previous) =>
      previous.flatMap((record) => {
        const current = r.data!.find((item) => item.name === record.name);
        return current ? [current] : [];
      }),
    );
  }, [r.data]);
  const [dialog, D] = useState(""),
    [repo, R] = useState(""),
    [v, V] = useState<any>({}),
    [files, F] = useState<File[]>([]),
    [results, Rows] = useState<any[] | null>(null),
    [phase, P] = useState("selected"),
    [progress, G] = useState<any>(),
    [controller, C] = useState<AbortController | null>(null);
  useEffect(
    () => () => controller?.abort(),
    [controller, s.project, s.user?.id],
  );
  const field = (k: string) => (x: any) => V((p: any) => ({ ...p, [k]: x }));
  const close = () => {
    dialogGeneration.current++;
    controller?.abort();
    D("");
  };
  function open(type: string, name = "") {
    dialogGeneration.current++;
    a.clearError();
    D(type);
    R(name);
    F([]);
    Rows(null);
    P("selected");
    V({
      name: "",
      description: "",
      embedding_model: "sentence-transformers/all-MiniLM-L6-v2",
      urls: "",
      text: "",
      limit: 5,
      similarity_threshold: 0,
    });
  }
  useComponentAction({
    id: "repositories.select",
    label: "Inspect repository documents",
    kind: "select",
    state: selectedRepository,
    options: (r.data || []).map((item) => ({
      value: item.name,
      label: item.name,
    })),
    disabled: !r.data?.length || Boolean(dialog) || a.busy,
    run: (value) => {
      if (!r.data?.some((item) => item.name === value))
        throw Error("Choose an available repository");
      selectRepository(value!);
      compare(false);
    },
  });
  function runSubmit() {
    const generation = dialogGeneration.current;
    if (submissionGeneration.current === generation) return;
    submissionGeneration.current = generation;
    void a.run(submit).finally(() => {
      if (submissionGeneration.current === generation)
        submissionGeneration.current = null;
    });
  }
  async function submit() {
    const generation = dialogGeneration.current;
    const isCurrent = () => generation === dialogGeneration.current;
    try {
      if (dialog === "create") {
        if (!v.name.trim()) throw Error("Give the repository a name");
        await s.api.post("/api/repositories", {
          name: v.name.trim(),
          description: v.description,
          embedding_model: v.embedding_model.trim() || null,
        });
        if (!isCurrent()) return;
        D("");
        r.reload();
      }
      if (dialog === "add") {
        if (!files.length && !v.urls.trim())
          throw Error("Add at least one file or URL");
        const fd = new FormData();
        files.forEach((f) => fd.append("files", f));
        fd.append("urls", v.urls);
        const c = new AbortController();
        C(c);
        P("uploading");
        try {
          const task = await s.api.upload(
            `/api/repositories/${enc(repo)}/documents`,
            fd,
            {
              signal: c.signal,
              onProgress: (x) => {
                if (!isCurrent()) return;
                G(x);
                if (x.total && x.loaded === x.total) P("indexing");
              },
            },
          );
          if (!isCurrent()) return;
          P("indexing");
          track(s, task);
          s.toast("Indexing started; follow the background task");
          r.reload();
        } catch (e: any) {
          if (isCurrent()) P(e.name === "AbortError" ? "cancelled" : "failed");
          throw e;
        }
      }
      if (dialog === "query") {
        const response = await s.api.post<any[]>(
          `/api/repositories/${enc(repo)}/query`,
          {
            text: v.text,
            limit: Number(v.limit),
            similarity_threshold: Number(v.similarity_threshold),
          },
        );
        if (isCurrent()) Rows(response);
      }
    } catch (error) {
      if (isCurrent()) throw error;
    }
  }
  return (
    <Page
      className="data-workspace records-page collection-page"
      title={inspected?.name ?? "Repositories"}
      headingRef={detailHeading}
      navigation={
        inspected && (
          <Button variant="ghost" size="sm" onClick={closeInspector}>
            ← Back to repositories
          </Button>
        )
      }
      description={
        inspected
          ? inspected.description || "Indexed documents for grounded answers."
          : "Searchable document collections for grounded answers."
      }
      actions={
        inspected ? (
          <>
            <Button
              variant="primary"
              onClick={() => open("add", inspected.name)}
            >
              Add documents
            </Button>
            <Button onClick={() => open("query", inspected.name)}>Query</Button>
            <details className="records-command-menu">
              <summary>Manage repository</summary>
              <div className="records-command-options">
                <Button
                  variant="danger"
                  onClick={() =>
                    a.run(async () => {
                      if (
                        await s.confirm(
                          `Delete repository "${inspected.name}" and its index?`,
                        )
                      ) {
                        await s.api.del(
                          `/api/repositories/${enc(inspected.name)}`,
                        );
                        closeInspector();
                        setChecked((previous) =>
                          previous.filter(
                            (item) => item.name !== inspected.name,
                          ),
                        );
                        r.reload();
                      }
                    })
                  }
                >
                  Delete repository
                </Button>
              </div>
            </details>
          </>
        ) : (
          <Button variant="primary" onClick={() => open("create")}>
            New repository
          </Button>
        )
      }
    >
      <ErrorNotice error={r.error} />
      {r.error && <Button onClick={r.reload}>Retry repositories</Button>}
      {r.isStale && !r.loading && (
        <p role="status">Refresh failed. Previous results remain visible.</p>
      )}
      {r.loading && (
        <p role="status">
          {r.isStale
            ? "Refreshing repositories… Previous results remain visible."
            : "Loading repositories…"}
        </p>
      )}{" "}
      <CollectionTemplate
        hidden={Boolean(inspected)}
        className="data-template"
        label="Repository collection workspace"
      >
        {comparing && (
          <RecordComparison
            records={checked}
            fields={[
              {
                label: "Description",
                value: (item) => item.description || "—",
              },
              {
                label: "Documents",
                value: (item) => String(item.documents.length),
              },
              {
                label: "Chunks",
                value: (item) => String(item.chunk_count ?? "—"),
              },
              {
                label: "Embedding model",
                value: (item) => item.embedding_model || "—",
              },
              {
                label: "Document names",
                value: (item) =>
                  item.documents
                    .map((document: any) => document.name)
                    .join("\n") || "—",
              },
            ]}
            onRemove={toggleRepository}
            onClose={() => {
              compare(false);
              compareFocus.current?.focus();
            }}
          />
        )}
        <div
          hidden={comparing}
          className="records-workbench"
          onKeyDown={(event) => {
            if (event.key === "Escape") closeInspector();
          }}
        >
          <RecordsScroll label="Repository collection">
            <AdvancedTable
              empty={
                r.loading ? (
                  "Loading records…"
                ) : (
                  <EmptyState>
                    {query ? (
                      <>
                        No repositories match “{query}”.{" "}
                        <Button onClick={() => setQuery("")}>
                          Clear search
                        </Button>
                      </>
                    ) : (
                      "No repositories yet. Create one and add documents or URLs."
                    )}
                  </EmptyState>
                )
              }
              toolbarContent={
                <CollectionToolbar
                  searchRef={collectionSearch}
                  label="Find a repository"
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
              assistantActionsEnabled={!comparing && !inspected}
              scopeKey={s.scopeKey + ":" + s.route}
              label="Repositories"
              rows={visible}
              getRowId={(item: any) => String(item.name)}
              pagination="client"
              columns={[
                {
                  id: "col-0",
                  label: "Select",
                  value: (item: any) =>
                    checked.some((record) => record.name === item.name),
                  sortable: false,
                  filterable: false,
                  hideable: false,
                  render: (item: any) => (
                    <>
                      <Checkbox
                        aria-label={`Select repository ${item.name}`}
                        isSelected={checked.some(
                          (record) => record.name === item.name,
                        )}
                        onChange={() => toggleRepository(item)}
                      />
                    </>
                  ),
                },
                {
                  id: "col-1",
                  label: "Repository",
                  value: (item: any) => item.name,
                  render: (item: any) => (
                    <>
                      <Button
                        variant="ghost"
                        aria-pressed={selectedRepository === item.name}
                        onClick={(event) => {
                          returnFocus.current = event.currentTarget;
                          selectRepository(item.name);
                        }}
                      >
                        {item.name}
                      </Button>
                    </>
                  ),
                },
                {
                  id: "col-2",
                  label: "Documents",
                  value: (item: any) => item.documents.length,
                  render: (item: any) => <>{item.documents.length}</>,
                },
                {
                  id: "col-3",
                  label: "Chunks",
                  value: (item: any) => item.chunk_count,
                  render: (item: any) => <>{item.chunk_count}</>,
                },
                {
                  id: "col-4",
                  label: "Embedding model",
                  value: (item: any) => item.embedding_model,
                  render: (item: any) => <>{item.embedding_model || "—"}</>,
                },
              ]}
              renderRow={(item: any, { key, ...props }, cells) => (
                <Evidence
                  as="tr"
                  {...props}
                  key={key}
                  label="Repository summary"
                  data={item}
                >
                  {cells}
                </Evidence>
              )}
              rowProps={(item: any) => ({
                className: cx(
                  selectedRepository === item.name && "records-inspected",
                ),
              })}
            />
          </RecordsScroll>
        </div>
      </CollectionTemplate>
      {inspected && (
        <DetailTemplate
          className="data-template repository-detail"
          label={`Repository ${inspected.name}`}
          summary={
            <Evidence label="Repository" data={inspected}>
              <dl className="repository-summary">
                <div>
                  <dt>Documents</dt>
                  <dd>{inspected.documents.length}</dd>
                </div>
                <div>
                  <dt>Chunks</dt>
                  <dd>{inspected.chunk_count ?? "—"}</dd>
                </div>
                <div>
                  <dt>Embedding model</dt>
                  <dd>{inspected.embedding_model || "—"}</dd>
                </div>
              </dl>
            </Evidence>
          }
        >
          <AdvancedTable
            scopeKey={s.scopeKey + ":" + s.route + ":" + inspected.name}
            label={`Documents in ${inspected.name}`}
            toolbarContent={
              <CollectionToolbar
                label="Find a document"
                query={documentQuery}
                onQueryChange={setDocumentQuery}
              />
            }
            empty={
              documentQuery ? (
                <EmptyState>
                  No documents match “{documentQuery}”.{" "}
                  <Button onClick={() => setDocumentQuery("")}>
                    Clear document search
                  </Button>
                </EmptyState>
              ) : (
                <EmptyState>
                  No documents indexed yet.{" "}
                  <Button onClick={() => open("add", inspected.name)}>
                    Add documents to this repository
                  </Button>
                </EmptyState>
              )
            }
            assistantActionsEnabled={!comparing}
            rows={inspected.documents.filter((document: any) =>
              `${document.name} ${document.kind}`
                .toLowerCase()
                .includes(documentQuery.trim().toLowerCase()),
            )}
            getRowId={(document: any) => String(document.name)}
            pagination="client"
            columns={[
              {
                id: "col-0",
                label: "Document",
                value: (document: any) => document.name,
                render: (document: any) => <>{document.name}</>,
              },
              {
                id: "col-1",
                label: "Format",
                value: (document: any) => document.kind,
                render: (document: any) => <>{document.kind}</>,
              },
              {
                id: "col-2",
                label: "Chunks",
                value: (document: any) => document.chunks,
                render: (document: any) => <>{document.chunks}</>,
              },
              {
                id: "col-3",
                label: "Actions",
                value: (document: any) => document.name,
                sortable: false,
                filterable: false,
                hideable: false,
                render: (document: any) => (
                  <>
                    <Button
                      size="sm"
                      variant="danger"
                      aria-label={`Remove ${document.name}`}
                      onClick={() =>
                        a.run(async () => {
                          if (
                            await s.confirm(
                              `Remove "${document.name}" from ${inspected.name}?`,
                            )
                          ) {
                            await s.api.del(
                              `/api/repositories/${enc(inspected.name)}/documents/${enc(document.name)}`,
                            );
                            r.reload();
                          }
                        })
                      }
                    >
                      Remove
                    </Button>
                  </>
                ),
              },
            ]}
            renderRow={(document: any, { key, ...props }, cells) => (
              <Evidence
                as="tr"
                {...props}
                key={key}
                label="Repository document"
                data={document}
              >
                {cells}
              </Evidence>
            )}
          />
        </DetailTemplate>
      )}
      <ErrorNotice error={a.error} />
      {dialog && (
        <Modal
          open
          wide={dialog !== "create"}
          title={
            dialog === "create"
              ? "New repository"
              : `${dialog === "add" ? "Add documents to" : "Query"} ${repo}`
          }
          onClose={close}
          footer={
            <Button
              variant="primary"
              loading={a.busy}
              disabled={dialog === "add" && phase === "indexing"}
              onClick={runSubmit}
            >
              {dialog === "create"
                ? "Create"
                : dialog === "add"
                  ? "Index"
                  : "Search"}
            </Button>
          }
        >
          <ErrorNotice error={a.error} />
          {dialog === "create" ? (
            <div className="repository-create-form">
              <Input
                fieldClassName="studio-form-control"
                label="Name"
                value={v.name}
                onChange={field("name")}
              />
              <Input
                fieldClassName="studio-form-control"
                label="Description"
                value={v.description}
                onChange={field("description")}
              />
              <TextField
                fieldClassName="studio-form-control"
                value={v.embedding_model}
                onChange={field("embedding_model")}
              >
                <Label>Embedding model</Label>
                <InputBase list="repository-models" />
              </TextField>
              <datalist id="repository-models">
                {(hub.data?.models || []).map((x: any) => (
                  <option key={x.id} value={x.id} />
                ))}
              </datalist>
            </div>
          ) : dialog === "add" ? (
            <>
              <div className="repository-import-grid">
                <FileUploadField
                  presentation="compact"
                  label="Files"
                  accept=".txt,.md,.markdown,.html,.htm,.pdf,.docx,.pptx"
                  multiple
                  files={files}
                  onChange={F}
                  disabled={a.busy || phase === "indexing"}
                />
                <Textarea
                  fieldClassName="studio-form-control"
                  label="URLs"
                  hint="Web pages or YouTube links, one per line."
                  rows={4}
                  isDisabled={a.busy || phase === "indexing"}
                  value={v.urls}
                  onChange={field("urls")}
                />
              </div>
              <p className="repository-dialog-hint">
                Text is extracted, split into ~1,200-character chunks, embedded,
                and indexed as a background task.
              </p>
              {phase !== "selected" && (
                <UploadStatus
                  phase={phase}
                  progress={progress}
                  onCancel={() => controller?.abort()}
                  onRetry={runSubmit}
                />
              )}
              {phase === "indexing" && (
                <p>
                  Transfer complete. Indexing continues in the background;
                  closing this dialog does not cancel an admitted task.
                </p>
              )}
            </>
          ) : (
            <>
              <div className="repository-query-grid">
                <Input
                  fieldClassName="studio-form-control"
                  label="Text"
                  placeholder="Ask a question or describe what you need"
                  value={v.text}
                  onChange={field("text")}
                  onKeyDown={(e: any) => {
                    if (e.key === "Enter") runSubmit();
                  }}
                />
                <TextField
                  fieldClassName="studio-form-control"
                  type="number"
                  value={String(v.limit)}
                  onChange={field("limit")}
                >
                  <Label>Passages</Label>
                  <InputBase min={1} max={50} />
                </TextField>
                <TextField
                  fieldClassName="studio-form-control"
                  type="number"
                  value={String(v.similarity_threshold)}
                  onChange={field("similarity_threshold")}
                >
                  <Label>Min similarity</Label>
                  <InputBase min={-1} max={1} step={0.05} />
                </TextField>
              </div>
              {results === null ? (
                <p>Search to inspect relevant passages.</p>
              ) : results.length ? (
                <DataTable
                  scopeKey={s.scopeKey + ":" + s.route}
                  label="Repository search results"
                  rows={results}
                  getRowId={(x: any) => String(results.indexOf(x))}
                  pagination="client"
                  columns={[
                    {
                      id: "col-0",
                      label: "Similarity",
                      value: (x: any) => x.similarity_score,
                      render: (x: any) => <>{x.similarity_score.toFixed(3)}</>,
                    },
                    {
                      id: "col-1",
                      label: "Document",
                      value: (x: any) => x.document,
                      render: (x: any) => <>{x.document}</>,
                    },
                    {
                      id: "col-2",
                      label: "Passage",
                      value: (x: any) => x.text,
                      render: (x: any) => (
                        <>
                          <pre className="data-prose">{x.text}</pre>
                        </>
                      ),
                    },
                  ]}
                  renderRow={(x: any, { key, ...props }, cells) => (
                    <Evidence
                      as="tr"
                      {...props}
                      key={key}
                      label="Repository result"
                      data={x}
                    >
                      {cells}
                    </Evidence>
                  )}
                />
              ) : (
                <EmptyState>
                  No matching passages. Try another search or lower the
                  similarity threshold.
                </EmptyState>
              )}
            </>
          )}
        </Modal>
      )}
    </Page>
  );
}
