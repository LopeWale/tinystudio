import { QualityDialog } from "./dataset-quality";
export { resolveQuality } from "./dataset-quality";
import { DatasetEditFields } from "./dataset-editor";
import { SegmentedControl } from "../../components/affectui/controls";
import { Input as StyledInput } from "../../components/boardui/base/input/input";
import "../../components/studio/form-controls.css";
import {
  CollectionToolbar,
  CollectionSelectionActions,
} from "../../components/studio/collection-toolbar";
import { AdvancedTable, SimpleTable } from "../../components/studio/data-table";
import {
  CollectionTemplate,
  DetailTemplate,
  SetupTemplate,
  AnalysisTemplate,
} from "../../components/studio/templates";
import { useComponentAction } from "../assistant/actions";
import { useState, useEffect, useRef, useId } from "react";
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
  const collectionSearch = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!comparing && compareFocus.current) {
      const trigger = compareFocus.current;
      if (trigger.isConnected && !trigger.matches(":disabled")) trigger.focus();
      else collectionSearch.current?.focus();
    }
  }, [comparing]);
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
      className="data-workspace records-page collection-page"
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
          <div className="records-table-host">
            <AdvancedTable
              layout="fill"
              empty={
                r.loading ? (
                  "Loading records…"
                ) : (
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
                )
              }
              toolbarContent={
                <CollectionToolbar
                  searchRef={collectionSearch}
                  label="Find a dataset"
                  query={q}
                  onQueryChange={Q}
                  selectedCount={checked.length}
                  onCompare={(event) => {
                    compareFocus.current = event.currentTarget;
                    compare(true);
                  }}
                  onClearSelection={() => setChecked([])}
                />
              }
              assistantActionsEnabled={!comparing}
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
          </div>
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
  const [view, setView] = useState("records");
  const [visibleRows, setVisibleRows] = useState<readonly any[]>([]);
  const panelId = useId();
  const recordsFocus = useRef<HTMLDivElement>(null);
  const dialogGeneration = useRef(0);
  const submissionGeneration = useRef<number | null>(null);
  const closeDialog = () => {
    dialogGeneration.current++;
    D("");
  };
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
    if (
      returnFocus.current?.isConnected &&
      !returnFocus.current.closest("[hidden]")
    )
      returnFocus.current.focus();
    else recordsFocus.current?.focus();
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
  const snaps = useRetainedResource<any[]>("/api/snapshots");
  useEffect(() => {
    setView("records");
    dialogGeneration.current++;
    B("");
    O(0);
    D("");
    S(null);
    setChecked([]);
    compare(false);
    return () => {
      dialogGeneration.current++;
    };
  }, [name, s.project, s.user?.id]);
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
    if (type === "snapshots") {
      setView("snapshots");
      requestAnimationFrame(() =>
        document.getElementById(panelId + "-snapshots")?.focus(),
      );
      return;
    }
    dialogGeneration.current++;
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
    id: "dataset.view",
    label: "Switch dataset view",
    kind: "select",
    state: view,
    options: [
      { value: "records", label: "Records" },
      { value: "snapshots", label: "Snapshots" },
    ],
    disabled: Boolean(dialog),
    run: (value) => {
      if (value !== "records" && value !== "snapshots")
        throw Error("Choose a dataset view");
      setView(value);
      requestAnimationFrame(() =>
        document.getElementById(panelId + "-" + value)?.focus(),
      );
    },
  });
  useComponentAction({
    id: "dataset.bucket",
    label: "Filter datapoints by bucket",
    kind: "filter",
    state: bucket,
    disabled: view !== "records" || comparing || Boolean(dialog),
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
    options: visibleRows.map((row: any) => ({
      value: String(row.id),
      label: `Datapoint ${row.id}`,
    })),
    disabled:
      !visibleRows.length || view !== "records" || comparing || Boolean(dialog),
    run: (value) => {
      const row = visibleRows.find((item: any) => String(item.id) === value);
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
    disabled: Boolean(dialog) || a.busy,
    options: [
      ["snapshots", "Snapshots"],
      ["create-snapshot", "Create snapshot"],
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
          "create-snapshot",
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
  function runSave() {
    const generation = dialogGeneration.current;
    if (submissionGeneration.current === generation) return;
    submissionGeneration.current = generation;
    void a.run(save).finally(() => {
      if (submissionGeneration.current === generation)
        submissionGeneration.current = null;
    });
  }
  async function save() {
    const generation = dialogGeneration.current;
    const isCurrent = () => generation === dialogGeneration.current;
    try {
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
      if (dialog === "create-snapshot")
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
        if (!isCurrent()) return;
        if (dialog === "auto-label")
          result = await s.api.post(
            `/api/datasets/${enc(name)}/auto-label`,
            {},
          );
      }
      if (!isCurrent()) return;
      if (result) track(s, result);
      D("");
      r.reload();
      snaps.reload();
      await s.refreshOverview();
    } catch (error) {
      if (isCurrent()) throw error;
    }
  }
  return (
    <Page
      className="data-workspace records-page dataset-detail-page"
      title={name}
      navigation={
        <a className="dataset-back" href="#/datasets">
          ← Back to datasets
        </a>
      }
      description={`${r.data?.total ?? "…"} datapoints`}
      actions={
        <div className="data-actions">
          <Button
            variant="primary"
            onClick={() =>
              open(view === "snapshots" ? "create-snapshot" : "add")
            }
          >
            {view === "snapshots" ? "Create snapshot" : "Add datapoint"}
          </Button>
          <Button onClick={() => open("quality")}>Quality</Button>
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
                Delete dataset
              </Button>
            </div>
          </details>
        </div>
      }
    >
      <div className="dataset-view-tabs">
        <SegmentedControl
          kind="tabs"
          variant="underline"
          size="sm"
          aria-label="Dataset views"
          value={view}
          onChange={setView}
          options={[
            {
              value: "records",
              label: "Records",
              controls: panelId + "-records",
            },
            {
              value: "snapshots",
              label: "Snapshots",
              controls: panelId + "-snapshots",
            },
          ]}
        />
      </div>
      <div
        id={panelId + "-records"}
        role="tabpanel"
        tabIndex={-1}
        aria-label="Records"
        hidden={view !== "records"}
        className="dataset-view-panel"
      >
        <DetailTemplate
          className="data-template"
          label="Dataset record workspace"
        >
          <ErrorNotice error={r.error || a.error} />
          {r.isStale && !r.loading && (
            <p role="status">
              Refresh failed. Previous results remain visible.
            </p>
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
                  value: (row) =>
                    row.rating == null ? "—" : `${row.rating}/10`,
                },
              ]}
              onRemove={toggleRecord}
              onClose={() => {
                compare(false);
                requestAnimationFrame(() => {
                  if (
                    compareFocus.current?.isConnected &&
                    !compareFocus.current.matches(":disabled") &&
                    !compareFocus.current.closest("[hidden]")
                  )
                    compareFocus.current.focus();
                  else recordsFocus.current?.focus();
                });
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
              <div className="records-table-host">
                {r.loading && (
                  <p role="status">
                    {r.isStale
                      ? "Refreshing datapoints… Previous results remain visible."
                      : "Loading datapoints…"}
                  </p>
                )}
                <AdvancedTable
                  layout="fill"
                  scopeKey={s.scopeKey + ":" + s.route}
                  label="Dataset records"
                  onVisibleRowsChange={setVisibleRows}
                  assistantActionsEnabled={
                    view === "records" && !comparing && !dialog
                  }
                  toolbarContent={
                    <div
                      className="dataset-record-controls"
                      ref={recordsFocus}
                      tabIndex={-1}
                    >
                      <SegmentedControl
                        aria-label="Bucket"
                        size="sm"
                        variant="tray"
                        value={bucket}
                        onChange={(value) => {
                          B(value);
                          O(0);
                        }}
                        options={[
                          {
                            value: "",
                            label: "All buckets",
                            count: r.data?.total ?? "…",
                          },
                          ...["training", "validation", "uncategorized"].map(
                            (value) => ({
                              value,
                              label: value,
                              count: meta.buckets?.[value] ?? "…",
                            }),
                          ),
                        ]}
                      />
                      <CollectionSelectionActions
                        selectedCount={checked.length}
                        returnFocus={recordsFocus}
                        onCompare={(event) => {
                          compareFocus.current = event.currentTarget;
                          compare(true);
                        }}
                        onClearSelection={() => setChecked([])}
                      />
                    </div>
                  }
                  rows={rows}
                  getRowId={(row: any) => String(row.id)}
                  pagination="external"
                  columns={[
                    {
                      id: "col-0",
                      label: "Select",
                      width: 56,
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
                      width: 48,
                      value: (row: any) => offset + rows.indexOf(row) + 1,
                      render: (row: any) => (
                        <>{offset + rows.indexOf(row) + 1}</>
                      ),
                    },
                    {
                      id: "col-2",
                      label: "User message",
                      width: "30%",
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
                            className="dataset-record-open"
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
                      width: "28%",
                      value: (row: any) =>
                        [...row.messages]
                          .reverse()
                          .find((message: any) => message.role === "assistant")
                          ?.content || "—",
                      render: (row: any) => (
                        <>
                          <span className="data-cell-preview">
                            {[...row.messages]
                              .reverse()
                              .find(
                                (message: any) => message.role === "assistant",
                              )?.content || "—"}
                          </span>
                        </>
                      ),
                    },
                    {
                      id: "col-4",
                      label: "Bucket",
                      width: 100,
                      value: (row: any) => row.bucket,
                      render: (row: any) => <>{row.bucket}</>,
                    },
                    {
                      id: "col-5",
                      label: "Labels",
                      width: 100,
                      value: (row: any) => (row.labels || []).join(", "),
                      render: (row: any) => (
                        <>{(row.labels || []).join(", ") || "—"}</>
                      ),
                    },
                    {
                      id: "col-6",
                      label: "Data type",
                      width: 110,
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
                    className: cx(
                      selected?.id === row.id && "records-inspected",
                    ),
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
              </div>
              <Pager
                offset={offset}
                limit={15}
                total={
                  bucket ? meta.buckets?.[bucket] || 0 : r.data?.total || 0
                }
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
                    {visibleRows.some((row: any) => row.id === selected.id)
                      ? `${visibleRows.findIndex((row: any) => row.id === selected.id) + 1} of ${visibleRows.length} on this page`
                      : "Outside the current page"}
                  </span>
                  <Button
                    size="sm"
                    disabled={
                      visibleRows.findIndex(
                        (row: any) => row.id === selected.id,
                      ) <= 0
                    }
                    onClick={() =>
                      S(
                        visibleRows[
                          visibleRows.findIndex(
                            (row: any) => row.id === selected.id,
                          ) - 1
                        ],
                      )
                    }
                  >
                    Previous record
                  </Button>
                  <Button
                    size="sm"
                    disabled={
                      visibleRows.findIndex(
                        (row: any) => row.id === selected.id,
                      ) < 0 ||
                      visibleRows.findIndex(
                        (row: any) => row.id === selected.id,
                      ) >=
                        visibleRows.length - 1
                    }
                    onClick={() =>
                      S(
                        visibleRows[
                          visibleRows.findIndex(
                            (row: any) => row.id === selected.id,
                          ) + 1
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
        </DetailTemplate>
      </div>
      <div
        id={panelId + "-snapshots"}
        role="tabpanel"
        tabIndex={-1}
        aria-label="Snapshots"
        hidden={view !== "snapshots"}
        className="dataset-view-panel"
      >
        <DetailTemplate
          className="data-template dataset-snapshots"
          label="Dataset snapshots"
        >
          <div className="dataset-snapshot-summary">
            <h2>Frozen versions</h2>
            <p>
              Snapshots preserve the training and validation records used by
              fine tuning and evaluations.
            </p>
          </div>
          <ErrorNotice error={snaps.error} />
          {snaps.error && (
            <Button onClick={snaps.reload}>Retry snapshots</Button>
          )}
          {snaps.loading && (
            <p role="status">
              {snaps.isStale
                ? "Refreshing snapshots… Previous results remain visible."
                : "Loading snapshots…"}
            </p>
          )}
          {snaps.isStale && !snaps.loading && (
            <p role="status">
              Refresh failed. Previous snapshots remain visible.
            </p>
          )}
          <AdvancedTable
            layout="fill"
            scopeKey={s.scopeKey + ":" + s.route}
            label="Snapshots"
            rows={ownSnaps}
            getRowId={(x: any) => String(x.name)}
            pagination="client"
            assistantActionsEnabled={view === "snapshots" && !dialog}
            empty={
              <EmptyState>
                {snaps.loading
                  ? "Loading snapshots…"
                  : snaps.error
                    ? "Snapshots could not be refreshed."
                    : "No snapshots yet. Create a frozen version to start fine tuning or evaluation."}
              </EmptyState>
            }
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
                            await s.api.del(`/api/snapshots/${enc(x.name)}`);
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
              <Evidence as="tr" {...props} key={key} label="Snapshot" data={x}>
                {cells}
              </Evidence>
            )}
          />
        </DetailTemplate>
      </div>
      {dialog === "quality" && (
        <QualityDialog
          key={s.scopeKey + ":" + name}
          name={name}
          onClose={() => {
            closeDialog();
            r.reload();
          }}
        />
      )}
      {dialog === "job" && (
        <JobDialog onClose={closeDialog} snapshot={ownSnaps[0]?.name} />
      )}{" "}
      {dialog && !["quality", "job"].includes(dialog) && (
        <Modal
          open
          wide={dialog !== "create-snapshot" && dialog !== "split"}
          className="dataset-edit-dialog"
          title={
            {
              "create-snapshot": "Create snapshot",
              split: "Autosplit dataset",
              enrich: "Enrich dataset",
              add: "Add datapoint",
              labels: "Labels",
              "auto-label": "Labels",
            }[dialog]
          }
          onClose={closeDialog}
          footer={
            <div className="data-actions">
              <Button variant="ghost" onClick={closeDialog}>
                Cancel
              </Button>
              {dialog === "labels" && (
                <Button
                  disabled={a.busy}
                  onClick={() => {
                    dialogGeneration.current++;
                    a.clearError();
                    D("auto-label");
                  }}
                >
                  Auto-label with helper LLM
                </Button>
              )}
              <Button variant="primary" loading={a.busy} onClick={runSave}>
                {dialog === "auto-label"
                  ? "Save labels & auto-label"
                  : dialog === "create-snapshot"
                    ? "Create snapshot"
                    : "Save"}
              </Button>
            </div>
          }
        >
          <ErrorNotice error={a.error} />
          <DatasetEditFields
            mode={dialog}
            values={v}
            onChange={(key, value) => field(key)(value)}
            labels={labels}
            onLabelsChange={L}
            disabled={a.busy}
            snapshotPlaceholder={`${name}-v${ownSnaps.length + 1}`}
          />
        </Modal>
      )}
    </Page>
  );
}
