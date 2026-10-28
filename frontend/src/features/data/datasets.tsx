import { AdvancedTable, SimpleTable } from "../../components/studio/data-table";
import {
  CollectionTemplate,
  DetailTemplate,
  SetupTemplate,
  AnalysisTemplate,
} from "../../components/studio/templates";
import { useComponentAction } from "../assistant/actions";
import { useState, useEffect, useRef } from "react";
import { useParams } from "react-router-dom";
import { useStudio, useResource } from "../../app/StudioContext";
import {
  Page,
  Panel,
  Button,
  Modal,
  Evidence,
  EmptyState,
} from "../../components/studio/primitives";
import { FileUploadField, UploadStatus } from "../../components/studio/uploads";
import {
  enc,
  lines,
  useAction,
  ErrorNotice,
  Input,
  Text,
  Select,
  Chips,
  Check,
  Pager,
  RecordDate,
  Messages,
  Histogram,
  track,
  scopedDownload,
} from "./common";
import { JobDialog } from "./training";
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
import { cx } from "../../components/boardui/utils/cx";
import {
  RecordsInspector as DetailInspector,
  RecordComparison,
  RecordsScroll,
  useRetainedResource,
} from "./records-workspace";
import "./records-workspace.css";

const SYNTH_ACCEPT = ".pdf,.txt,.md,.markdown,.html,.htm";
export function compatibleFiles(files: File[], mode: string) {
  const allowed =
    mode === "upload"
      ? ["jsonl", "json"]
      : ["pdf", "txt", "md", "markdown", "html", "htm"];
  return files
    .filter((f) =>
      allowed.includes(f.name.split(".").pop()?.toLowerCase() || ""),
    )
    .slice(0, mode === "upload" ? 1 : undefined);
}
export function DatasetWizard({ onClose }: { onClose: () => void }) {
  const s = useStudio(),
    a = useAction();
  const [mode, M] = useState("synthetic"),
    [step, Step] = useState(0),
    [files, F] = useState<File[]>([]),
    [notice, N] = useState(""),
    [phase, P] = useState("selected"),
    [progress, G] = useState<any>();
  const ctrl = useRef<AbortController | null>(null);
  useEffect(() => () => ctrl.current?.abort(), [s.project, s.user?.id]);
  const [v, V] = useState<any>({
    name: "",
    urls: "",
    pairs_per_source: 25,
    temperature: 0.3,
    system_prompt: "",
    rules: "",
    question_format: "",
    answer_format: "",
    example_questions: "",
    example_answers: "",
    curate_threshold: 0,
  });
  const field = (k: string) => (x: any) => V((p: any) => ({ ...p, [k]: x }));
  function changeMode(next: string) {
    a.clearError();
    const kept = compatibleFiles(files, next);
    N(
      files.length !== kept.length
        ? "Incompatible files were removed for this source mode."
        : "",
    );
    F(kept);
    M(next);
    Step(0);
  }
  async function submit() {
    if (!v.name.trim()) throw Error("Give the dataset a name");
    const c = new AbortController();
    ctrl.current = c;
    P("uploading");
    try {
      if (mode === "upload") {
        if (files.length !== 1) throw Error("Choose one JSONL file");
        const file = files[0];
        if (file.size > 512 * 1024 * 1024)
          throw Error("Dataset upload limit is 512 MiB");
        const config = await s.api.get<any>("/api/artifacts/config", c.signal);
        let meta;
        if (config.direct_upload) {
          const grant = await s.api.post<any>(
            "/api/artifacts/uploads",
            {
              filename: file.name,
              content_type: file.type || "application/octet-stream",
              size: file.size,
            },
            c.signal,
          );
          await s.api.upload(grant.url, file, {
            method: "PUT",
            headers: grant.headers,
            external: true,
            signal: c.signal,
            onProgress: G,
          });
          P("importing");
          meta = await s.api.post<any>(
            "/api/datasets/from-object",
            { name: v.name.trim(), key: grant.key },
            c.signal,
          );
        } else {
          const fd = new FormData();
          fd.append("name", v.name.trim());
          fd.append("file", file);
          meta = await s.api.upload<any>("/api/datasets/upload", fd, {
            signal: c.signal,
            onProgress: (x) => {
              G(x);
              if (x.total && x.loaded === x.total) P("importing");
            },
          });
        }
        P("ready");
        onClose();
        s.navigate(`#/datasets/${enc(meta.name)}`);
      } else {
        if (!files.length && !v.urls.trim())
          throw Error("Add at least one file or URL");
        const fd = new FormData();
        Object.entries({
          ...v,
          name: v.name.trim(),
          goal: s.overview?.project?.goal || "",
        }).forEach(([k, x]) => fd.append(k, String(x)));
        files.forEach((f) => fd.append("files", f));
        const task = await s.api.upload("/api/datasets/synthetic", fd, {
          signal: c.signal,
          onProgress: G,
        });
        P("importing");
        track(s, task);
        onClose();
        s.toast("Generation started; watch the task panel");
      }
    } catch (e: any) {
      P(e.name === "AbortError" ? "cancelled" : "failed");
      throw e;
    }
  }
  return (
    <Modal
      open
      wide
      title="Add dataset"
      onClose={() => {
        ctrl.current?.abort();
        onClose();
      }}
      footer={
        <div className="data-actions">
          {step > 0 && <Button onClick={() => Step(step - 1)}>Back</Button>}
          <Button
            variant="primary"
            loading={a.busy}
            onClick={() =>
              a.run(async () => {
                if (!v.name.trim()) throw Error("Give the dataset a name");
                if (!files.length && !v.urls.trim())
                  throw Error(
                    mode === "upload"
                      ? "Choose a JSONL file"
                      : "Add at least one file or URL",
                  );
                if (mode === "synthetic" && step < 2) Step(step + 1);
                else await submit();
              })
            }
          >
            {mode === "upload" || step === 2 ? "Create dataset" : "Next"}
          </Button>
        </div>
      }
    >
      <SetupTemplate className="data-template data-setup" label="Dataset setup">
        <ErrorNotice error={a.error} />
        {mode === "synthetic" && (
          <p>
            Step {step + 1} of 3 ·{" "}
            {["Source", "Generation settings", "Summary"][step]}
          </p>
        )}
        {step === 0 ? (
          <>
            <Chips
              items={["synthetic", "upload"]}
              chosen={[mode]}
              onChange={(x) => changeMode(x.at(-1) || mode)}
            />
            <Input
              label="Dataset name"
              value={v.name}
              onChange={field("name")}
            />
            <FileUploadField
              label={mode === "upload" ? "JSONL file" : "Source files"}
              accept={mode === "upload" ? ".jsonl,.json" : SYNTH_ACCEPT}
              multiple={mode !== "upload"}
              files={files}
              onChange={F}
              maxBytes={mode === "upload" ? 512 * 1024 * 1024 : undefined}
              disabled={a.busy}
            />
            {notice && <p role="status">{notice}</p>}
            {mode === "synthetic" && (
              <>
                <Text
                  label="Web page URLs (one per line)"
                  value={v.urls}
                  onChange={field("urls")}
                />
                <Input
                  label="Pairs per source"
                  type="number"
                  min={1}
                  max={500}
                  value={v.pairs_per_source}
                  onChange={field("pairs_per_source")}
                />
                <Input
                  label="System prompt (optional)"
                  value={v.system_prompt}
                  onChange={field("system_prompt")}
                />
              </>
            )}
          </>
        ) : step === 1 ? (
          <>
            <Text
              label="Rules & constraints (one per line)"
              value={v.rules}
              onChange={field("rules")}
            />
            <div className="data-grid">
              {[
                ["question_format", "Question guidance"],
                ["answer_format", "Answer format"],
                ["example_questions", "Example questions (one per line)"],
                ["example_answers", "Example answers (same order)"],
              ].map(([k, l]) => (
                <Text key={k} label={l} value={v[k]} onChange={field(k)} />
              ))}
            </div>
            <Input
              label="Creativity level"
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={v.temperature}
              onChange={field("temperature")}
            />
            <Input
              label="Curation threshold (0 keeps all)"
              type="number"
              min={0}
              max={10}
              step={0.5}
              value={v.curate_threshold}
              onChange={field("curate_threshold")}
            />
          </>
        ) : (
          <>
            <h3>
              Ready to generate{" "}
              {(files.length + lines(v.urls).length) *
                Number(v.pairs_per_source)}{" "}
              pairs
            </h3>
            <p>
              {files.length} files · {lines(v.urls).length} web pages ·{" "}
              {v.pairs_per_source} pairs/source
            </p>
            <p>
              Creativity {v.temperature} · Curation{" "}
              {v.curate_threshold || "off"} · Helper{" "}
              {s.overview?.llm?.model || "not configured"}
            </p>
            <pre className="data-prose">{v.rules}</pre>
          </>
        )}
        {["uploading", "importing"].includes(phase) && (
          <p>
            Cancellation stops this transfer or wait. A server task already
            admitted may continue; check background tasks.
          </p>
        )}
        <UploadStatus
          phase={phase}
          progress={progress}
          error={a.error || undefined}
          onCancel={() => ctrl.current?.abort()}
          onRetry={() => a.run(submit)}
        />
      </SetupTemplate>
    </Modal>
  );
}
export function DatasetsPage() {
  const s = useStudio();
  const r = useRetainedResource<any[]>("/api/datasets");
  const [q, Q] = useState(""),
    [open, O] = useState(false),
    [inspected, inspect] = useState<any>(null),
    [checked, setChecked] = useState<any[]>([]),
    [comparing, compare] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null),
    compareFocus = useRef<HTMLElement | null>(null);
  const closeInspection = () => {
    inspect(null);
    returnFocus.current?.focus();
  };
  const visible = (r.data || []).filter((d) =>
    d.name.toLowerCase().includes(q.trim().toLowerCase()),
  );
  const toggle = (record: any) =>
    setChecked((previous) =>
      previous.some((x) => x.name === record.name)
        ? previous.filter((x) => x.name !== record.name)
        : [...previous, record],
    );
  useEffect(() => {
    if (!r.data) return;
    setChecked((previous) =>
      previous.map(
        (record) => r.data!.find((item) => item.name === record.name) || record,
      ),
    );
    inspect((record: any) =>
      record
        ? r.data!.find((item) => item.name === record.name) || record
        : null,
    );
  }, [r.data]);
  useComponentAction({
    id: "datasets.filter",
    label: "Find a dataset",
    kind: "filter",
    state: q,
    run: (value) => Q(value || ""),
  });
  return (
    <Page
      className="data-workspace records-page"
      title="Datasets"
      description="Prepare, review, and snapshot the data your models learn from."
      actions={
        <Button variant="primary" onClick={() => O(true)}>
          Create dataset
        </Button>
      }
    >
      <CollectionTemplate
        className="data-template"
        label="Dataset collection workspace"
        toolbar={
          <div
            className={cx(
              "records-toolbar",
              !checked.length && "records-toolbar--unselected",
            )}
          >
            <Input
              label="Find a dataset"
              type="search"
              value={q}
              onChange={Q}
            />
            <span className="records-count">{visible.length} datasets</span>
            <span className="records-selection" role="status">
              {checked.length} selected
            </span>
            <Button
              className="records-bulk-action"
              disabled={checked.length < 2}
              onClick={(event) => {
                compareFocus.current = event.currentTarget;
                compare(true);
              }}
            >
              Compare selected
            </Button>
            <Button
              className="records-bulk-action"
              disabled={!checked.length}
              variant="ghost"
              onClick={() => setChecked([])}
            >
              Clear selection
            </Button>
          </div>
        }
      >
        <ErrorNotice error={r.error} />
        {r.isStale && !r.loading && (
          <p role="status">Refresh failed. Previous results remain visible.</p>
        )}
        {r.error && <Button onClick={r.reload}>Retry datasets</Button>}
        {r.loading && (
          <p role="status">
            {r.isStale
              ? "Refreshing datasets… Previous results remain visible."
              : "Loading datasets…"}
          </p>
        )}
        {comparing && (
          <RecordComparison
            records={checked}
            fields={[
              {
                label: "Datapoints",
                value: (d) => String(d.datapoints ?? "—"),
              },
              {
                label: "Snapshots",
                value: (d) => String(d.snapshot_count ?? "—"),
              },
              { label: "Origin", value: (d) => d.origin?.kind || "Uploaded" },
              { label: "Created", value: (d) => d.created || "—" },
            ]}
            onRemove={toggle}
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
            if (event.key === "Escape") closeInspection();
          }}
        >
          <RecordsScroll label="Dataset collection">
            <AdvancedTable
              scopeKey={s.scopeKey + ":" + s.route}
              label="Datasets"
              rows={visible}
              getRowId={(d: any) => String(d.name)}
              pagination="client"
              columns={[
                {
                  id: "col-0",
                  label: "Select",
                  value: (d: any) => checked.some((x) => x.name === d.name),
                  sortable: false,
                  filterable: false,
                  hideable: false,
                  render: (d: any) => (
                    <>
                      <Checkbox
                        aria-label={`Select dataset ${d.name}`}
                        isSelected={checked.some((x) => x.name === d.name)}
                        onChange={() => toggle(d)}
                      />
                    </>
                  ),
                },
                {
                  id: "col-1",
                  label: "Dataset",
                  value: (d: any) => d.name,
                  render: (d: any) => (
                    <>
                      <a
                        className="data-record-link"
                        href={`#/datasets/${enc(d.name)}`}
                      >
                        {d.name}
                      </a>
                    </>
                  ),
                },
                {
                  id: "col-2",
                  label: "Datapoints",
                  value: (d: any) => d.datapoints,
                  render: (d: any) => <>{d.datapoints}</>,
                },
                {
                  id: "col-3",
                  label: "Snapshots",
                  value: (d: any) => d.snapshot_count,
                  render: (d: any) => <>{d.snapshot_count}</>,
                },
                {
                  id: "col-4",
                  label: "Origin",
                  value: (d: any) => d.origin?.kind || "Uploaded",
                  render: (d: any) => <>{d.origin?.kind || "Uploaded"}</>,
                },
                {
                  id: "col-5",
                  label: "Created",
                  value: (d: any) => d.created,
                  render: (d: any) => (
                    <>
                      <RecordDate value={d.created} />
                    </>
                  ),
                },
                {
                  id: "col-6",
                  label: "Inspect",
                  value: (d: any) => d.name,
                  sortable: false,
                  filterable: false,
                  hideable: false,
                  render: (d: any) => (
                    <>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={`Inspect dataset ${d.name}`}
                        aria-pressed={inspected?.name === d.name}
                        onClick={(event) => {
                          returnFocus.current = event.currentTarget;
                          inspect(d);
                        }}
                      >
                        Inspect
                      </Button>
                    </>
                  ),
                },
              ]}
              renderRow={(d: any, { key, ...props }, cells) => (
                <Evidence as="tr" {...props} key={key} label="Dataset" data={d}>
                  {cells}
                </Evidence>
              )}
              rowProps={(d: any) => ({
                className: cx(
                  inspected?.name === d.name && "records-inspected",
                ),
              })}
            />
            {!r.loading && !visible.length && (
              <EmptyState>
                {q ? (
                  <>
                    No datasets match “{q}”.{" "}
                    <Button onClick={() => Q("")}>Clear search</Button>
                  </>
                ) : (
                  "No datasets yet. Upload JSONL or generate examples from documents."
                )}
              </EmptyState>
            )}
          </RecordsScroll>
          {inspected && (
            <DetailInspector title={inspected.name} onClose={closeInspection}>
              <Evidence label="Inspected dataset" data={inspected}>
                <dl className="records-facts">
                  <dt>Datapoints</dt>
                  <dd>{inspected.datapoints}</dd>
                  <dt>Snapshots</dt>
                  <dd>{inspected.snapshot_count}</dd>
                  <dt>Origin</dt>
                  <dd>{inspected.origin?.kind || "Uploaded"}</dd>
                  <dt>Created</dt>
                  <dd>{inspected.created || "—"}</dd>
                </dl>
                <a
                  className="data-record-link"
                  href={`#/datasets/${enc(inspected.name)}`}
                >
                  Open dataset →
                </a>
              </Evidence>
            </DetailInspector>
          )}
        </div>
        {open && (
          <DatasetWizard
            onClose={() => {
              O(false);
              r.reload();
            }}
          />
        )}
      </CollectionTemplate>
    </Page>
  );
}
export function DatasetPage() {
  const params = useParams(),
    name = params.name || params.dataset || "";
  const s = useStudio(),
    a = useAction();
  const [bucket, B] = useState(""),
    [offset, O] = useState(0),
    [dialog, D] = useState(""),
    [selected, S] = useState<any>(null),
    [v, V] = useState<any>({}),
    [labels, L] = useState<any[]>([]),
    [checked, setChecked] = useState<any[]>([]),
    [comparing, compare] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null),
    compareFocus = useRef<HTMLElement | null>(null);
  const closeInspection = () => {
    S(null);
    returnFocus.current?.focus();
  };
  const toggleRecord = (record: any) =>
    setChecked((previous) =>
      previous.some((x) => x.id === record.id)
        ? previous.filter((x) => x.id !== record.id)
        : [...previous, record],
    );
  const r = useRetainedResource<any>(
    `/api/datasets/${enc(name)}?offset=${offset}&limit=15${bucket ? `&bucket=${enc(bucket)}` : ""}`,
  );
  const snaps = useResource<any[]>("/api/snapshots");
  useEffect(() => {
    B("");
    O(0);
    D("");
    S(null);
    setChecked([]);
    compare(false);
  }, [name, s.project]);
  const meta = r.data?.meta || {},
    rows = r.data?.rows || [],
    ownSnaps = (snaps.data || []).filter((x) => x.dataset === name);
  useEffect(() => {
    if (!r.data) return;
    setChecked((previous) =>
      previous.map(
        (record) =>
          r.data.rows.find((item: any) => item.id === record.id) || record,
      ),
    );
    S((record: any) =>
      record
        ? r.data.rows.find((item: any) => item.id === record.id) || record
        : null,
    );
  }, [r.data]);
  const field = (k: string) => (x: any) => V((p: any) => ({ ...p, [k]: x }));
  function open(type: string) {
    a.clearError();
    D(type);
    V({
      train_pct: 80,
      synthetic_in_validation: false,
      count: 20,
      temperature: 0.8,
      instructions: "",
      avoid: "",
      system: "",
      user: "",
      assistant: "",
      bucket: "uncategorized",
      name: "",
    });
    L(
      meta.labels?.length
        ? meta.labels.map((x: any) => ({ ...x }))
        : [{ name: "", description: "" }],
    );
  }
  useComponentAction({
    id: "dataset.bucket",
    label: "Filter datapoints by bucket",
    kind: "filter",
    state: bucket,
    options: [
      { value: "", label: "All buckets" },
      ...["training", "validation", "uncategorized"].map((value) => ({
        value,
        label: value,
      })),
    ],
    run: (value) => {
      if (value && !["training", "validation", "uncategorized"].includes(value))
        throw Error("Unknown bucket");
      B(value || "");
      O(0);
    },
  });
  useComponentAction({
    id: "dataset.select",
    label: "Inspect a visible datapoint",
    kind: "select",
    state: selected?.id == null ? "" : String(selected.id),
    options: rows.map((row: any) => ({
      value: String(row.id),
      label: `Datapoint ${row.id}`,
    })),
    disabled: !rows.length,
    run: (value) => {
      const row = rows.find((item: any) => String(item.id) === value);
      if (!row) throw Error("Choose a visible datapoint");
      a.clearError();
      S(row);
    },
  });
  useComponentAction({
    id: "dataset.operation",
    label: "Open dataset setup",
    kind: "open",
    state: dialog,
    options: [
      ["snapshots", "Snapshots"],
      ["quality", "Quality"],
      ["labels", "Labels"],
      ["split", "Autosplit"],
      ["add", "Add datapoint"],
      ["enrich", "Enrich"],
      ["job", "Start finetuning"],
    ].map(([value, label]) => ({ value, label })),
    run: (value) => {
      if (
        ![
          "snapshots",
          "quality",
          "labels",
          "split",
          "add",
          "enrich",
          "job",
        ].includes(value || "")
      )
        throw Error("Unknown dataset setup");
      open(value!);
    },
  });
  async function save() {
    let result;
    if (dialog === "split")
      result = await s.api.post(`/api/datasets/${enc(name)}/split`, {
        train_pct: Number(v.train_pct),
        synthetic_in_validation: v.synthetic_in_validation,
      });
    if (dialog === "enrich")
      result = await s.api.post("/api/augment", {
        dataset: name,
        count: Number(v.count),
        temperature: Number(v.temperature),
        instructions: v.instructions,
        avoid: v.avoid,
      });
    if (dialog === "add")
      result = await s.api.post("/api/datapoints", {
        dataset: name,
        bucket: v.bucket,
        messages: [
          ...(v.system.trim()
            ? [{ role: "system", content: v.system.trim() }]
            : []),
          { role: "user", content: v.user.trim() },
          { role: "assistant", content: v.assistant.trim() },
        ],
      });
    if (dialog === "snapshots")
      result = await s.api.post("/api/snapshots", {
        dataset: name,
        name: v.name.trim() || `${name}-v${ownSnaps.length + 1}`,
      });
    if (dialog === "labels" || dialog === "auto-label") {
      await s.api.put(`/api/datasets/${enc(name)}/labels`, {
        labels: labels
          .filter((x) => x.name.trim())
          .map((x) => ({ ...x, name: x.name.trim() })),
      });
      if (dialog === "auto-label")
        result = await s.api.post(`/api/datasets/${enc(name)}/auto-label`, {});
    }
    if (result) track(s, result);
    D("");
    r.reload();
    snaps.reload();
    await s.refreshOverview();
  }
  return (
    <Page
      className="data-workspace records-page"
      title={name}
      description={`${r.data?.total ?? "…"} datapoints`}
      actions={
        <div className="data-actions">
          <Button variant="primary" onClick={() => open("add")}>
            Add datapoint
          </Button>
          <details className="records-command-menu">
            <summary>Dataset actions</summary>
            <div className="records-command-options">
              {[
                ["job", "Start finetuning"],
                ["enrich", "Enrich"],
                ["split", "Autosplit"],
                ["labels", "Labels"],
              ].map(([key, label]) => (
                <Button key={key} variant="ghost" onClick={() => open(key)}>
                  {label}
                </Button>
              ))}
            </div>
          </details>
          <a
            href={scopedDownload(
              s.project,
              `/api/datasets/${enc(name)}/download`,
            )}
          >
            Download
          </a>
          <Button
            variant="danger"
            onClick={() =>
              a.run(async () => {
                if (
                  await s.confirm(
                    `Delete "${name}" and its datapoints? Snapshots are kept.`,
                  )
                ) {
                  await s.api.del(`/api/datasets/${enc(name)}`);
                  s.navigate("#/datasets");
                }
              })
            }
          >
            Delete
          </Button>
        </div>
      }
    >
      <DetailTemplate
        className="data-template"
        label="Dataset record workspace"
        summary={
          <>
            <nav className="data-view-navigation" aria-label="Dataset views">
              <a href={`#/datasets/${enc(name)}`} aria-current="page">
                Rows
              </a>
              <Button variant="ghost" onClick={() => open("quality")}>
                Quality
              </Button>
              <Button variant="ghost" onClick={() => open("snapshots")}>
                Snapshots
              </Button>
            </nav>
            <div className="data-toolbar">
              {["uncategorized", "training", "validation"].map((b) => (
                <Evidence
                  key={b}
                  label="Dataset bucket"
                  data={{ bucket: b, count: meta.buckets?.[b] }}
                >
                  <Button
                    aria-pressed={bucket === b}
                    onClick={() => {
                      B(bucket === b ? "" : b);
                      O(0);
                    }}
                  >
                    {b}: {meta.buckets?.[b] ?? "…"}
                  </Button>
                </Evidence>
              ))}
            </div>
          </>
        }
        toolbar={
          <div
            className={cx(
              "records-toolbar",
              !checked.length && "records-toolbar--unselected",
            )}
          >
            <Select
              label="Bucket"
              value={bucket}
              onChange={(value: string) => {
                B(value);
                O(0);
              }}
              options={[
                { value: "", label: "All buckets" },
                "uncategorized",
                "training",
                "validation",
              ]}
            />
            <span className="records-selection" role="status">
              {checked.length} selected
            </span>
            <Button
              className="records-bulk-action"
              disabled={checked.length < 2}
              onClick={(event) => {
                compareFocus.current = event.currentTarget;
                compare(true);
              }}
            >
              Compare selected
            </Button>
            <Button
              className="records-bulk-action"
              disabled={!checked.length}
              variant="ghost"
              onClick={() => setChecked([])}
            >
              Clear selection
            </Button>
            {r.error && <Button onClick={r.reload}>Retry datapoints</Button>}
          </div>
        }
      >
        <ErrorNotice error={r.error || a.error} />
        {r.isStale && !r.loading && (
          <p role="status">Refresh failed. Previous results remain visible.</p>
        )}
        {!ownSnaps.length && (
          <p>Create a snapshot to run finetuning and evaluation.</p>
        )}
        {comparing && (
          <RecordComparison
            records={checked}
            fields={[
              {
                label: "User message",
                value: (row) =>
                  [...row.messages]
                    .reverse()
                    .find((m: any) => m.role === "user")?.content || "—",
              },
              {
                label: "Assistant response",
                value: (row) =>
                  [...row.messages]
                    .reverse()
                    .find((m: any) => m.role === "assistant")?.content || "—",
              },
              { label: "Bucket", value: (row) => row.bucket || "—" },
              {
                label: "Labels",
                value: (row) => (row.labels || []).join(", ") || "—",
              },
              { label: "Source", value: (row) => row.source || "uploaded" },
              {
                label: "Rating",
                value: (row) => (row.rating == null ? "—" : `${row.rating}/10`),
              },
            ]}
            onRemove={toggleRecord}
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
            selected && "records-workbench-inspecting",
          )}
          onKeyDown={(event) => {
            if (event.key === "Escape") closeInspection();
          }}
        >
          <div className="records-collection">
            <RecordsScroll label="Datapoint collection">
              {r.loading && (
                <p role="status">
                  {r.isStale
                    ? "Refreshing datapoints… Previous results remain visible."
                    : "Loading datapoints…"}
                </p>
              )}
              <AdvancedTable
                scopeKey={s.scopeKey + ":" + s.route}
                label="Dataset records"
                rows={rows}
                getRowId={(row: any) => String(row.id)}
                pagination="external"
                columns={[
                  {
                    id: "col-0",
                    label: "Select",
                    value: (row: any) =>
                      checked.some((item) => item.id === row.id),
                    sortable: false,
                    filterable: false,
                    hideable: false,
                    render: (row: any) => (
                      <>
                        <Checkbox
                          aria-label={`Select datapoint ${row.id}`}
                          isSelected={checked.some(
                            (item) => item.id === row.id,
                          )}
                          onChange={() => toggleRecord(row)}
                        />
                      </>
                    ),
                  },
                  {
                    id: "col-1",
                    label: "#",
                    value: (row: any) => offset + rows.indexOf(row) + 1,
                    render: (row: any) => <>{offset + rows.indexOf(row) + 1}</>,
                  },
                  {
                    id: "col-2",
                    label: "User message",
                    hideable: false,
                    value: (row: any) =>
                      [...row.messages]
                        .reverse()
                        .find((m: any) => m.role === "user")?.content ||
                      "No user message",
                    render: (row: any) => (
                      <>
                        <Button
                          variant="ghost"
                          aria-label={`Inspect datapoint ${row.id}: ${[...row.messages].reverse().find((m: any) => m.role === "user")?.content || "No user message"}`}
                          aria-pressed={selected?.id === row.id}
                          onClick={(event) => {
                            returnFocus.current = event.currentTarget;
                            a.clearError();
                            S(row);
                          }}
                        >
                          <span className="data-cell-preview">
                            {[...row.messages]
                              .reverse()
                              .find((m: any) => m.role === "user")?.content ||
                              "No user message"}
                          </span>
                        </Button>
                      </>
                    ),
                  },
                  {
                    id: "col-3",
                    label: "Assistant response",
                    value: (row: any) => row.messages.at(-1)?.content,
                    render: (row: any) => (
                      <>
                        <span className="data-cell-preview">
                          {row.messages.at(-1)?.content}
                        </span>
                      </>
                    ),
                  },
                  {
                    id: "col-4",
                    label: "Bucket",
                    value: (row: any) => row.bucket,
                    render: (row: any) => <>{row.bucket}</>,
                  },
                  {
                    id: "col-5",
                    label: "Labels",
                    value: (row: any) => (row.labels || []).join(", "),
                    render: (row: any) => (
                      <>{(row.labels || []).join(", ") || "—"}</>
                    ),
                  },
                  {
                    id: "col-6",
                    label: "Data type",
                    value: (row: any) => row.source || "uploaded",
                    render: (row: any) => <>{row.source || "uploaded"}</>,
                  },
                ]}
                renderRow={(row: any, { key, ...props }, cells) => (
                  <Evidence
                    as="tr"
                    {...props}
                    key={key}
                    label="Datapoint"
                    data={row}
                  >
                    {cells}
                  </Evidence>
                )}
                rowProps={(row: any) => ({
                  className: cx(selected?.id === row.id && "records-inspected"),
                })}
              />
              {!r.loading && !rows.length && (
                <EmptyState>
                  No datapoints in this view.{" "}
                  {bucket && (
                    <Button
                      onClick={() => {
                        B("");
                        O(0);
                      }}
                    >
                      Show all buckets
                    </Button>
                  )}
                </EmptyState>
              )}
            </RecordsScroll>
            <Pager
              offset={offset}
              limit={15}
              total={bucket ? meta.buckets?.[bucket] || 0 : r.data?.total || 0}
              onChange={O}
            />
          </div>
          {selected && (
            <DetailInspector
              title={`Datapoint ${selected.id ?? ""}`}
              onClose={closeInspection}
            >
              <div className="records-inspector-navigation">
                <span>
                  {rows.some((row: any) => row.id === selected.id)
                    ? `${rows.findIndex((row: any) => row.id === selected.id) + 1} of ${rows.length} on this page`
                    : "Outside the current page"}
                </span>
                <Button
                  size="sm"
                  disabled={
                    rows.findIndex((row: any) => row.id === selected.id) <= 0
                  }
                  onClick={() =>
                    S(
                      rows[
                        rows.findIndex((row: any) => row.id === selected.id) - 1
                      ],
                    )
                  }
                >
                  Previous record
                </Button>
                <Button
                  size="sm"
                  disabled={
                    rows.findIndex((row: any) => row.id === selected.id) < 0 ||
                    rows.findIndex((row: any) => row.id === selected.id) >=
                      rows.length - 1
                  }
                  onClick={() =>
                    S(
                      rows[
                        rows.findIndex((row: any) => row.id === selected.id) + 1
                      ],
                    )
                  }
                >
                  Next record
                </Button>
              </div>
              <Evidence label="Selected datapoint" data={selected}>
                <Messages messages={selected.messages} />
                <dl className="records-facts">
                  <dt>Bucket</dt>
                  <dd>{selected.bucket}</dd>
                  <dt>Labels</dt>
                  <dd>{(selected.labels || []).join(", ") || "none"}</dd>
                  <dt>Source</dt>
                  <dd>{selected.source || "uploaded"}</dd>
                  <dt>Rating</dt>
                  <dd>
                    {selected.rating == null ? "—" : `${selected.rating}/10`}
                  </dd>
                </dl>
              </Evidence>
            </DetailInspector>
          )}
        </div>
        {dialog === "quality" && (
          <QualityDialog
            name={name}
            onClose={() => {
              D("");
              r.reload();
            }}
          />
        )}
        {dialog === "job" && (
          <JobDialog onClose={() => D("")} snapshot={ownSnaps[0]?.name} />
        )}{" "}
        {dialog && !["quality", "job"].includes(dialog) && (
          <Modal
            open
            wide
            title={
              {
                snapshots: "Snapshots",
                split: "Autosplit dataset",
                enrich: "Enrich dataset",
                add: "Add datapoint",
                labels: "Labels",
                "auto-label": "Labels",
              }[dialog]
            }
            onClose={() => D("")}
            footer={
              <div className="data-actions">
                {dialog === "labels" && (
                  <Button
                    onClick={() => {
                      a.clearError();
                      D("auto-label");
                    }}
                  >
                    Auto-label with helper LLM
                  </Button>
                )}
                <Button
                  variant="primary"
                  loading={a.busy}
                  onClick={() => a.run(save)}
                >
                  {dialog === "auto-label"
                    ? "Save labels & auto-label"
                    : dialog === "snapshots"
                      ? "Create snapshot"
                      : "Save"}
                </Button>
              </div>
            }
          >
            <ErrorNotice error={a.error} />
            {dialog === "snapshots" ? (
              <>
                <p>Snapshots freeze current training and validation buckets.</p>
                <SimpleTable
                  scopeKey={s.scopeKey + ":" + s.route}
                  label="Snapshots"
                  rows={ownSnaps}
                  getRowId={(x: any) => String(x.name)}
                  pagination="none"
                  columns={[
                    {
                      id: "col-0",
                      label: "Name",
                      value: (x: any) => x.name,
                      render: (x: any) => <>{x.name}</>,
                    },
                    {
                      id: "col-1",
                      label: "Training",
                      value: (x: any) => x.training_count,
                      render: (x: any) => <>{x.training_count}</>,
                    },
                    {
                      id: "col-2",
                      label: "Validation",
                      value: (x: any) => x.validation_count,
                      render: (x: any) => <>{x.validation_count}</>,
                    },
                    {
                      id: "col-3",
                      label: "Actions",
                      value: (x: any) => x.name,
                      sortable: false,
                      filterable: false,
                      hideable: false,
                      render: (x: any) => (
                        <>
                          <a
                            href={scopedDownload(
                              s.project,
                              `/api/snapshots/${enc(x.name)}/download`,
                            )}
                          >
                            Download
                          </a>
                          <Button
                            variant="danger"
                            onClick={() =>
                              a.run(async () => {
                                if (
                                  await s.confirm(
                                    `Delete snapshot "${x.name}"? Existing results are kept.`,
                                  )
                                ) {
                                  await s.api.del(
                                    `/api/snapshots/${enc(x.name)}`,
                                  );
                                  snaps.reload();
                                  await s.refreshOverview();
                                }
                              })
                            }
                          >
                            Delete
                          </Button>
                        </>
                      ),
                    },
                  ]}
                  renderRow={(x: any, { key, ...props }, cells) => (
                    <Evidence
                      as="tr"
                      {...props}
                      key={key}
                      label="Snapshot"
                      data={x}
                    >
                      {cells}
                    </Evidence>
                  )}
                />
                <Input
                  label="New snapshot name"
                  value={v.name}
                  onChange={field("name")}
                  placeholder={`${name}-v${ownSnaps.length + 1}`}
                />
              </>
            ) : dialog === "split" ? (
              <>
                <p>Assign uncategorized rows; existing assignments are kept.</p>
                <Input
                  label="Training percentage"
                  type="number"
                  min={1}
                  max={99}
                  value={v.train_pct}
                  onChange={field("train_pct")}
                />
                <Check
                  label="Allow synthetic data in validation"
                  checked={v.synthetic_in_validation}
                  onChange={field("synthetic_in_validation")}
                />
              </>
            ) : dialog === "enrich" ? (
              <>
                <Input
                  label="New datapoints"
                  type="number"
                  min={1}
                  max={2000}
                  value={v.count}
                  onChange={field("count")}
                />
                <Input
                  label="Temperature"
                  type="number"
                  min={0}
                  max={1.5}
                  step={0.1}
                  value={v.temperature}
                  onChange={field("temperature")}
                />
                <Text
                  label="Instructions"
                  value={v.instructions}
                  onChange={field("instructions")}
                />
                <Text label="Avoid" value={v.avoid} onChange={field("avoid")} />
              </>
            ) : dialog === "add" ? (
              <>
                {["system", "user", "assistant"].map((k) => (
                  <Text key={k} label={k} value={v[k]} onChange={field(k)} />
                ))}
                <Select
                  label="Bucket"
                  value={v.bucket}
                  onChange={field("bucket")}
                  options={["uncategorized", "training", "validation"]}
                />
              </>
            ) : (
              <>
                <p>
                  Define a vocabulary; helper auto-labeling assigns one label
                  per datapoint.
                </p>
                {labels.map((x, i) => (
                  <div className="data-grid" key={i}>
                    <Input
                      label={`Label ${i + 1} name`}
                      value={x.name}
                      onChange={(name: string) =>
                        L(labels.map((l, j) => (j === i ? { ...l, name } : l)))
                      }
                    />
                    <Input
                      label={`Label ${i + 1} description`}
                      value={x.description}
                      onChange={(description: string) =>
                        L(
                          labels.map((l, j) =>
                            j === i ? { ...l, description } : l,
                          ),
                        )
                      }
                    />
                    <Button onClick={() => L(labels.filter((_, j) => j !== i))}>
                      Remove label {i + 1}
                    </Button>
                  </div>
                ))}
                <Button
                  onClick={() => L([...labels, { name: "", description: "" }])}
                >
                  Add label
                </Button>
              </>
            )}
          </Modal>
        )}
      </DetailTemplate>
    </Page>
  );
}
export async function resolveQuality(
  api: any,
  path: string,
  signal: AbortSignal,
  onTask: (t: any) => void,
  wait = (ms: number) =>
    new Promise<void>((resolve, reject) => {
      const stop = () => {
        clearTimeout(timer);
        reject(new DOMException("Cancelled", "AbortError"));
      };
      const timer = setTimeout(() => {
        signal.removeEventListener("abort", stop);
        resolve();
      }, ms);
      signal.addEventListener("abort", stop, { once: true });
    }),
) {
  if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
  let profile = await api.get(path, signal);
  if (profile.queued) {
    let task = profile.task;
    onTask(task);
    for (;;) {
      if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
      if (task.state === "succeeded") {
        profile = task.result;
        break;
      }
      if (["failed", "stopped"].includes(task.state))
        throw Error(task.error || `Profiling ${task.state}`);
      await wait(2500);
      task = await api.get(`/api/tasks/${enc(task.id)}`, signal);
      onTask(task);
    }
  }
  if (!profile || profile.truncated)
    throw Error("The profiling task did not return a complete result.");
  return profile;
}
function QualityDialog({
  name,
  onClose,
}: {
  name: string;
  onClose: () => void;
}) {
  const s = useStudio(),
    a = useAction();
  const [p, P] = useState<any>(),
    [error, E] = useState(""),
    [task, T] = useState<any>(),
    [chosen, C] = useState<string[]>([]),
    [selected, S] = useState<any>(null),
    [v, V] = useState<any>({
      min_total_chars: "",
      max_total_chars: "",
      min_rating: "",
      drop_labels: "",
    }),
    [result, R] = useState("");
  useEffect(() => {
    const c = new AbortController();
    Promise.resolve()
      .then(() =>
        resolveQuality(
          s.api,
          `/api/datasets/${enc(name)}/quality`,
          c.signal,
          (t) => {
            T(t);
            s.trackTask(t);
          },
        ),
      )
      .then(P)
      .catch((e) => {
        if (e.name !== "AbortError") E(e.message);
      });
    return () => c.abort();
  }, [name, s.project, s.user?.id, s.api]);
  const opts = [
    "dedupe_user",
    "dedupe_conversation",
    "drop_near_duplicates",
    "drop_empty",
    "trim_whitespace",
    "require_json_answer",
    "redact_pii",
  ];
  const field = (k: string) => (x: any) => V((p: any) => ({ ...p, [k]: x }));
  const dist = (title: string, pairs: any[]) => (
    <Panel title={title}>
      <SimpleTable
        scopeKey={s.scopeKey + ":" + s.route}
        label={title}
        rows={pairs}
        getRowId={([label, n]: any) => String(label)}
        pagination="none"
        columns={[
          {
            id: "col-0",
            label: "Value",
            value: ([label, n]: any) => label,
            render: ([label, n]: any) => <>{label}</>,
          },
          {
            id: "col-1",
            label: "Count",
            value: ([label, n]: any) => n,
            render: ([label, n]: any) => <>{n}</>,
          },
        ]}
        renderRow={([label, n]: any, { key, ...props }, cells) => (
          <Evidence
            as="tr"
            {...props}
            key={key}
            label={title}
            data={{ label, count: n }}
          >
            {cells}
          </Evidence>
        )}
      />
    </Panel>
  );
  return (
    <Modal open wide title={`Data quality · ${name}`} onClose={onClose}>
      <AnalysisTemplate
        className="data-template data-setup"
        label="Dataset quality evidence"
      >
        <ErrorNotice error={error || a.error} />
        {!p && !error && (
          <p role="status">
            {task
              ? `Profiling ${task.state} in the background. You can close this dialog; the task continues.`
              : "Profiling…"}
          </p>
        )}
        {p && (
          <Evidence label="Dataset quality profile" data={p}>
            <div className="data-grid">
              {Object.entries({
                Datapoints: p.count,
                "Approx. tokens": p.approx_tokens.total,
                "Mean turns": p.turns.mean,
                "With system prompt": p.turns.with_system_prompt,
                "Exact duplicate user turns": p.duplicates.exact_user_rows,
                "Near duplicate pairs": p.duplicates.near_pair_count,
                "Empty-like rows": p.outliers.empty_like.length,
                "Length outliers":
                  p.outliers.too_short.length + p.outliers.too_long.length,
                "JSON answers": `${p.format.answers_json_pct}%`,
                ...(p.ratings.count ? { "Mean rating": p.ratings.mean } : {}),
              }).map(([k, n]) => (
                <Evidence key={k} label={k} data={{ value: n }}>
                  <div className="data-card">
                    <p>{k}</p>
                    <strong className="data-stat">{String(n)}</strong>
                  </div>
                </Evidence>
              ))}
            </div>
            <div className="data-grid">
              {[
                ["user_chars", "User message length (chars)"],
                ["answer_chars", "Assistant response length (chars)"],
              ].map(([key, title]) => (
                <Panel key={key} title={title}>
                  <p>
                    Median {p[key].summary.median} · p95 {p[key].summary.p95} ·
                    Max {p[key].summary.max}
                  </p>
                  <Histogram label={title} {...p[key].histogram} />
                  <p className="data-muted">
                    Aggregate profile; this chart does not identify individual
                    records.
                  </p>
                </Panel>
              ))}
              {dist("Labels", p.labels)}
              {dist("Sources", p.sources)}
              {dist("Buckets", Object.entries(p.buckets))}
              {p.ratings.count > 0 && (
                <Panel title="Curation ratings">
                  <Histogram
                    label="Curation ratings"
                    {...p.ratings.histogram}
                  />
                </Panel>
              )}
            </div>
            <Panel title="Duplicates and outliers">
              <p>
                Duplicate examples:{" "}
                {p.duplicates.examples
                  .map((g: any) => g.join(" · "))
                  .join("; ") || "none"}
              </p>
              <div className="data-actions">
                {p.duplicates.near_pairs
                  .slice(0, 20)
                  .map((x: any, i: number) => (
                    <Button
                      key={i}
                      onClick={() =>
                        S({ a: x.a, b: x.b, similarity: x.similarity })
                      }
                    >
                      #{x.a + 1} ≈ #{x.b + 1} ({x.similarity})
                    </Button>
                  ))}
              </div>
              <p>
                Near-duplicate positions refer to the profile ordering. Exact
                record lookup is not exposed by this API; no row drilldown is
                offered.
              </p>
              {selected && (
                <pre className="data-prose">
                  {JSON.stringify(selected, null, 2)}
                </pre>
              )}
            </Panel>
            <Panel title="Clean">
              <p>
                Applies in place and is logged to cleaning history. Snapshots
                are not affected.
              </p>
              <Chips items={opts} chosen={chosen} onChange={C} />
              <div className="data-grid">
                <Input
                  label={`Min total chars (short below ${p.outliers.fences.low})`}
                  type="number"
                  min={0}
                  value={v.min_total_chars}
                  onChange={field("min_total_chars")}
                />
                <Input
                  label={`Max total chars (long above ${p.outliers.fences.high})`}
                  type="number"
                  min={0}
                  value={v.max_total_chars}
                  onChange={field("max_total_chars")}
                />
                <Input
                  label="Min rating (1–10)"
                  type="number"
                  min={0}
                  max={10}
                  step={0.5}
                  value={v.min_rating}
                  onChange={field("min_rating")}
                />
                <Input
                  label="Drop labels (comma separated)"
                  value={v.drop_labels}
                  onChange={field("drop_labels")}
                />
              </div>
              <Button
                variant="primary"
                loading={a.busy}
                onClick={() =>
                  a.run(async () => {
                    const spec: any = Object.fromEntries(
                      chosen.map((k) => [k, true]),
                    );
                    for (const k of [
                      "min_total_chars",
                      "max_total_chars",
                      "min_rating",
                    ])
                      spec[k] = v[k] === "" ? null : Number(v[k]);
                    spec.drop_labels = v.drop_labels
                      .split(",")
                      .map((x: string) => x.trim())
                      .filter(Boolean);
                    if (
                      !chosen.length &&
                      spec.min_total_chars === null &&
                      spec.max_total_chars === null &&
                      spec.min_rating === null &&
                      !spec.drop_labels.length
                    )
                      throw Error("Pick at least one cleaning rule");
                    if (
                      !(await s.confirm(
                        "Apply cleaning in place? Snapshots remain unchanged.",
                        "Clean dataset",
                      ))
                    )
                      return;
                    const res = await s.api.post<any>(
                      `/api/datasets/${enc(name)}/clean`,
                      spec,
                    );
                    if (track(s, res)) {
                      R("Cleaning queued as a background task");
                      return;
                    }
                    R(
                      `${res.before} → ${res.after} datapoints; removed ${JSON.stringify(res.removed)}${res.redacted_rows ? `; redacted PII in ${res.redacted_rows} rows` : ""}`,
                    );
                    await s.refreshOverview();
                  })
                }
              >
                Apply cleaning
              </Button>
              <p role="status">{result}</p>
            </Panel>
          </Evidence>
        )}
      </AnalysisTemplate>
    </Modal>
  );
}
