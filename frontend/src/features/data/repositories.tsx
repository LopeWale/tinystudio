import { AdvancedTable, DataTable } from "../../components/studio/data-table";
import { CollectionTemplate } from "../../components/studio/templates";
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
import { enc, useAction, ErrorNotice, Input, Text, track } from "./common";
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
import { cx } from "../../components/boardui/utils/cx";
import {
  RecordsInspector as DetailInspector,
  RecordComparison,
  RecordsScroll,
  useRetainedResource,
} from "./records-workspace";
import "./records-workspace.css";
export function RepositoriesPage() {
  const s = useStudio(),
    a = useAction(),
    r = useRetainedResource<any[]>("/api/repositories"),
    hub = useResource<any>("/api/hub/models");
  const [selectedRepository, selectRepository] = useState("");
  const [query, setQuery] = useState(""),
    [checked, setChecked] = useState<any[]>([]),
    [comparing, compare] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null),
    compareFocus = useRef<HTMLElement | null>(null);
  const closeInspector = () => {
    selectRepository("");
    returnFocus.current?.focus();
  };
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
  }, [s.project, s.user?.id]);
  useEffect(() => {
    if (!r.data) return;
    setChecked((previous) =>
      previous.map(
        (record) => r.data!.find((item) => item.name === record.name) || record,
      ),
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
    controller?.abort();
    D("");
  };
  function open(type: string, name = "") {
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
    disabled: !r.data?.length,
    run: (value) => {
      if (!r.data?.some((item) => item.name === value))
        throw Error("Choose an available repository");
      selectRepository(value!);
    },
  });
  async function submit() {
    if (dialog === "create") {
      if (!v.name.trim()) throw Error("Give the repository a name");
      await s.api.post("/api/repositories", {
        name: v.name.trim(),
        description: v.description,
        embedding_model: v.embedding_model.trim() || null,
      });
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
              G(x);
              if (x.total && x.loaded === x.total) P("indexing");
            },
          },
        );
        P("indexing");
        track(s, task);
        s.toast("Indexing started; follow the background task");
        r.reload();
      } catch (e: any) {
        P(e.name === "AbortError" ? "cancelled" : "failed");
        throw e;
      }
    }
    if (dialog === "query")
      Rows(
        await s.api.post(`/api/repositories/${enc(repo)}/query`, {
          text: v.text,
          limit: Number(v.limit),
          similarity_threshold: Number(v.similarity_threshold),
        }),
      );
  }
  return (
    <Page
      className="data-workspace records-page"
      title="Repositories"
      description="Searchable document collections for grounded answers."
      actions={
        <Button variant="primary" onClick={() => open("create")}>
          New repository
        </Button>
      }
    >
      <CollectionTemplate
        className="data-template"
        label="Repository collection workspace"
        toolbar={
          <div className="records-toolbar">
            <Input
              label="Find a repository"
              type="search"
              value={query}
              onChange={setQuery}
            />
            <span className="records-count">{visible.length} repositories</span>
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
              variant="ghost"
              disabled={!checked.length}
              onClick={() => setChecked([])}
            >
              Clear selection
            </Button>
            {r.error && <Button onClick={r.reload}>Retry repositories</Button>}
          </div>
        }
      >
        <ErrorNotice error={r.error} />
        {r.isStale && !r.loading && (
          <p role="status">Refresh failed. Previous results remain visible.</p>
        )}

        {r.loading && (
          <p role="status">
            {r.isStale
              ? "Refreshing repositories… Previous results remain visible."
              : "Loading repositories…"}
          </p>
        )}
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
          className={cx(
            "records-workbench",
            inspected && "records-workbench-inspecting",
          )}
          onKeyDown={(event) => {
            if (event.key === "Escape") closeInspector();
          }}
        >
          <RecordsScroll label="Repository collection">
            <AdvancedTable
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
            {!r.loading && !visible.length && (
              <EmptyState>
                {query ? (
                  <>
                    No repositories match “{query}”.{" "}
                    <Button onClick={() => setQuery("")}>Clear search</Button>
                  </>
                ) : (
                  "No repositories yet. Create one and add documents or URLs."
                )}
              </EmptyState>
            )}
          </RecordsScroll>
          {inspected && (
            <DetailInspector title={inspected.name} onClose={closeInspector}>
              <Evidence label="Repository" data={inspected}>
                <p>{inspected.description || "No description"}</p>
                <dl className="records-facts">
                  <dt>Documents</dt>
                  <dd>{inspected.documents.length}</dd>
                  <dt>Chunks</dt>
                  <dd>{inspected.chunk_count}</dd>
                  <dt>Embedding model</dt>
                  <dd>{inspected.embedding_model || "—"}</dd>
                </dl>
                <div className="records-inspector-navigation">
                  <Button
                    variant="primary"
                    onClick={() => open("add", inspected.name)}
                  >
                    Add documents
                  </Button>
                  <Button onClick={() => open("query", inspected.name)}>
                    Query
                  </Button>
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
                    Delete
                  </Button>
                </div>
                <h3>Indexed documents</h3>
                <DataTable
                  scopeKey={s.scopeKey + ":" + s.route}
                  label={`Documents in ${inspected.name}`}
                  rows={inspected.documents}
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
                {!inspected.documents.length && (
                  <p>No documents indexed yet. Add files or URLs to start.</p>
                )}
                <p className="records-hint">
                  Query this repository to inspect matching passages from its
                  indexed documents.
                </p>
              </Evidence>
            </DetailInspector>
          )}
        </div>
        <ErrorNotice error={a.error} />
        {dialog && (
          <Modal
            open
            wide
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
                onClick={() => a.run(submit)}
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
              <>
                <Input label="Name" value={v.name} onChange={field("name")} />
                <Input
                  label="Description"
                  value={v.description}
                  onChange={field("description")}
                />
                <Input
                  label="Embedding model"
                  list="repository-models"
                  value={v.embedding_model}
                  onChange={field("embedding_model")}
                />
                <datalist id="repository-models">
                  {(hub.data?.models || []).map((x: any) => (
                    <option key={x.id} value={x.id} />
                  ))}
                </datalist>
              </>
            ) : dialog === "add" ? (
              <>
                <FileUploadField
                  label="Files (txt, md, html, pdf, docx, pptx)"
                  accept=".txt,.md,.markdown,.html,.htm,.pdf,.docx,.pptx"
                  multiple
                  files={files}
                  onChange={F}
                  disabled={a.busy || phase === "indexing"}
                />
                <Text
                  label="URLs (web pages or YouTube, one per line)"
                  value={v.urls}
                  onChange={field("urls")}
                />
                <p>
                  Text is extracted, split into ~1,200-character chunks,
                  embedded, and indexed as a background task.
                </p>
                <UploadStatus
                  phase={phase}
                  progress={progress}
                  onCancel={() => controller?.abort()}
                  onRetry={() => a.run(submit)}
                />
                {phase === "indexing" && (
                  <p>
                    Transfer complete. Indexing continues in the background;
                    closing this dialog does not cancel an admitted task.
                  </p>
                )}
              </>
            ) : (
              <>
                <div className="data-grid">
                  <Input
                    label="Text"
                    value={v.text}
                    onChange={field("text")}
                    onKeyDown={(e: any) => {
                      if (e.key === "Enter") a.run(submit);
                    }}
                  />
                  <Input
                    label="Passages"
                    type="number"
                    min={1}
                    max={50}
                    value={v.limit}
                    onChange={field("limit")}
                  />
                  <Input
                    label="Min similarity"
                    type="number"
                    min={-1}
                    max={1}
                    step={0.05}
                    value={v.similarity_threshold}
                    onChange={field("similarity_threshold")}
                  />
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
                        render: (x: any) => (
                          <>{x.similarity_score.toFixed(3)}</>
                        ),
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
      </CollectionTemplate>
    </Page>
  );
}
