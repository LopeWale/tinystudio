import { useComponentAction } from "../assistant/actions";
import { NumberField } from "../../components/studio/number-field";
import { useEffect, useId, useRef, useState } from "react";
import { useStudio } from "../../app/StudioContext";
import { SegmentedControl } from "../../components/affectui/controls";
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
import { Input as StyledInput } from "../../components/boardui/base/input/input";
import {
  Button,
  Evidence,
  Modal,
  Panel,
} from "../../components/studio/primitives";
import { SimpleTable } from "../../components/studio/data-table";
import { SettingsSection } from "../../components/studio/settings-section";
import { RecordsScroll } from "./records-workspace";
import { enc, ErrorNotice, Histogram, track, useAction } from "./common";
import "../../components/studio/form-controls.css";
import "./dataset-quality.css";
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

const rules = [
  ["dedupe_user", "Deduplicate user messages"],
  ["dedupe_conversation", "Deduplicate conversations"],
  ["drop_near_duplicates", "Remove near duplicates"],
  ["drop_empty", "Remove empty rows"],
  ["trim_whitespace", "Trim whitespace"],
  ["require_json_answer", "Keep only JSON answers"],
  ["redact_pii", "Redact personal information"],
];

/** One distribution at a time, with its exact evidence in the same region. */
function QualityDistributions({
  profile,
  active,
}: {
  profile: any;
  active: boolean;
}) {
  const [metric, setMetric] = useState("user_chars");
  const [presentation, setPresentation] = useState("chart");
  const [composition, setComposition] = useState("buckets");
  const metrics = [
    { value: "user_chars", label: "User messages" },
    { value: "answer_chars", label: "Responses" },
    { value: "ratings", label: "Ratings" },
  ];
  const titles: Record<string, string> = {
    user_chars: "User message length (chars)",
    answer_chars: "Assistant response length (chars)",
    ratings: "Curation ratings",
  };
  useComponentAction({
    id: "dataset.quality.distribution",
    label: "Choose quality distribution",
    kind: "select",
    disabled: !active,
    state: metric,
    options: metrics,
    run: (value) => {
      if (!metrics.some((option) => option.value === value))
        throw Error("Choose a distribution");
      setMetric(value!);
    },
  });
  useComponentAction({
    id: "dataset.quality.presentation",
    label: "Show chart or bin counts",
    kind: "select",
    disabled: !active,
    state: presentation,
    options: [
      { value: "chart", label: "Chart" },
      { value: "table", label: "Bin counts" },
    ],
    run: (value) => {
      if (value !== "chart" && value !== "table")
        throw Error("Choose a presentation");
      setPresentation(value);
    },
  });
  useComponentAction({
    id: "dataset.quality.composition",
    label: "Choose dataset composition",
    kind: "select",
    disabled: !active,
    state: composition,
    options: [
      { value: "buckets", label: "Buckets" },
      { value: "labels", label: "Labels" },
      { value: "sources", label: "Sources" },
    ],
    run: (value) => {
      if (!value || !["buckets", "labels", "sources"].includes(value))
        throw Error("Choose a composition");
      setComposition(value);
    },
  });
  const current = profile[metric];
  const pairs =
    composition === "buckets"
      ? Object.entries(profile.buckets)
      : profile[composition];
  const plot = (
    <Histogram
      key={metric}
      label={titles[metric]}
      integerValues
      focusOccupiedRange
      unit={metric === "ratings" ? "rounded rating" : "characters"}
      {...current.histogram}
      view={presentation as "chart" | "table"}
    />
  );
  return (
    <div className="quality-analysis-grid">
      <Panel title="Distribution">
        <div className="quality-analysis-controls">
          <SegmentedControl
            aria-label="Distribution measure"
            value={metric}
            onChange={setMetric}
            options={metrics}
          />
          <SegmentedControl
            aria-label="Distribution presentation"
            value={presentation}
            onChange={setPresentation}
            options={[
              { value: "chart", label: "Chart" },
              { value: "table", label: "Bin counts" },
            ]}
          />
        </div>
        <h3 className="quality-measure-title">{titles[metric]}</h3>
        <p className="quality-measure-summary">
          {metric === "ratings"
            ? `${current.count} rated datapoints · Mean ${current.mean ?? "—"}/10`
            : `Median ${current.summary.median} · p95 ${current.summary.p95} · Max ${current.summary.max}`}
        </p>
        {presentation === "table" ? (
          <div className="quality-bin-table">
            <RecordsScroll
              key={metric}
              label={`${titles[metric]} exact counts`}
            >
              {plot}
            </RecordsScroll>
          </div>
        ) : (
          plot
        )}
        <p className="quality-help quality-analysis-note">
          {metric === "ratings"
            ? "Bins use rounded ratings; the mean uses original values."
            : "Aggregate counts only. Bins do not identify individual records."}
        </p>
      </Panel>
      <Panel title="Dataset composition">
        <SegmentedControl
          aria-label="Composition measure"
          value={composition}
          onChange={setComposition}
          options={[
            { value: "buckets", label: "Buckets" },
            { value: "labels", label: "Labels" },
            { value: "sources", label: "Sources" },
          ]}
        />
        <p className="quality-composition-scope">{profile.count} datapoints</p>
        <SimpleTable
          label={`Dataset ${composition}`}
          rows={pairs}
          getRowId={([label]: any) => String(label)}
          columns={[
            { id: "value", label: "Value", value: ([label]: any) => label },
            { id: "count", label: "Count", value: ([, count]: any) => count },
          ]}
          empty={<p>No values in this profile.</p>}
        />
        <p className="quality-help quality-analysis-note">
          {composition === "labels"
            ? "A datapoint can have multiple labels. Counts overlap and must not be added together."
            : composition === "buckets"
              ? "Each datapoint belongs to one bucket."
              : "Source counts from the complete quality profile."}
        </p>
      </Panel>
    </div>
  );
}

/** Profile reads and cleaning results belong to this mounted dataset scope. */
export function QualityDialog({
  name,
  onClose,
}: {
  name: string;
  onClose: () => void;
}) {
  const s = useStudio(),
    action = useAction();
  const id = useId();
  const [view, setView] = useState("overview"),
    [attempt, reload] = useState(0);
  const [profile, setProfile] = useState<any>(),
    [error, setError] = useState("");
  const [task, setTask] = useState<any>(),
    [pendingClean, setPendingClean] = useState<any>();
  const [chosen, setChosen] = useState<string[]>([]);
  const [values, setValues] = useState({
    min_total_chars: "",
    max_total_chars: "",
    min_rating: "",
    drop_labels: "",
  });
  const [result, setResult] = useState(""),
    [stale, setStale] = useState(false);
  const alive = useRef(true),
    submitting = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    setTask(undefined);
    setProfile(undefined);
    Promise.resolve()
      .then(() =>
        resolveQuality(
          s.api,
          `/api/datasets/${enc(name)}/quality`,
          controller.signal,
          (next) => {
            if (!controller.signal.aborted) {
              setTask(next);
              s.trackTask(next);
            }
          },
        ),
      )
      .then((next) => {
        if (!controller.signal.aborted) {
          setProfile(next);
          setStale(false);
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(err.message);
      });
    return () => controller.abort();
  }, [name, s.project, s.user?.id, s.api, attempt]);
  useComponentAction({
    id: "dataset.quality.view",
    label: "Switch quality view",
    kind: "select",
    state: view,
    options: [
      { value: "overview", label: "Overview" },
      { value: "distributions", label: "Distributions" },
      { value: "cleaning", label: "Cleaning" },
    ],
    disabled: !profile,
    run: (value) => {
      if (!value || !["overview", "distributions", "cleaning"].includes(value))
        throw Error("Choose a quality view");
      setView(value);
      requestAnimationFrame(() =>
        document.getElementById(id + "-" + value)?.focus(),
      );
    },
  });
  const field = (key: keyof typeof values) => (value: any) =>
    setValues((previous) => ({ ...previous, [key]: value }));
  const cleaningChoices =
    chosen.length +
    [values.min_total_chars, values.max_total_chars, values.min_rating].filter(
      (value) => value !== "",
    ).length +
    (values.drop_labels.split(",").some((label) => label.trim()) ? 1 : 0);
  const invalidCharacters = [
    values.min_total_chars,
    values.max_total_chars,
  ].some(
    (value) =>
      value !== "" &&
      (!Number.isSafeInteger(Number(value)) || Number(value) < 0),
  );
  const thresholdError = invalidCharacters
    ? "Character limits must be non-negative whole numbers."
    : values.min_total_chars !== "" &&
        values.max_total_chars !== "" &&
        Number(values.min_total_chars) > Number(values.max_total_chars)
      ? "Minimum characters cannot exceed maximum characters."
      : values.min_rating !== "" &&
          (!Number.isFinite(Number(values.min_rating)) ||
            Number(values.min_rating) < 0 ||
            Number(values.min_rating) > 10)
        ? "Minimum rating must be between 0 and 10."
        : "";
  async function clean() {
    if (submitting.current) return;
    submitting.current = true;
    await action.run(async () => {
      if (thresholdError) throw Error(thresholdError);
      const spec: any = Object.fromEntries(chosen.map((key) => [key, true]));
      for (const key of [
        "min_total_chars",
        "max_total_chars",
        "min_rating",
      ] as const)
        spec[key] = values[key] === "" ? null : Number(values[key]);
      spec.drop_labels = values.drop_labels
        .split(",")
        .map((item) => item.trim())
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
        )) ||
        !alive.current
      )
        return;
      // Even a failed response can leave mutation outcome unknown. Keep the old
      // profile qualified until an explicit successful refresh.
      setStale(true);
      const response = await s.api.post<any>(
        `/api/datasets/${enc(name)}/clean`,
        spec,
      );
      if (!alive.current) return;
      if (track(s, response)) {
        // The large-dataset endpoint returns { queued: true, task }; retain
        // the same validated TaskView that track registered with the shell.
        setPendingClean(response.queued === true ? response.task : response);
        setResult(
          "Cleaning queued as a background task. Check its status before refreshing the profile.",
        );
        return;
      }
      setResult(
        `${response.before} → ${response.after} datapoints; removed ${JSON.stringify(response.removed)}${response.redacted_rows ? `; redacted PII in ${response.redacted_rows} rows` : ""}`,
      );
      await s.refreshOverview();
    });
    submitting.current = false;
  }
  async function refresh() {
    if (pendingClean) {
      await action.run(async () => {
        const current = await s.api.get<any>(
          `/api/tasks/${enc(pendingClean.id)}`,
        );
        if (!alive.current) return;
        if (!["succeeded", "failed", "stopped"].includes(current.state)) {
          setResult(
            `Cleaning ${current.state}. The displayed profile predates this operation.`,
          );
          return;
        }
        setPendingClean(undefined);
        setResult(
          current.state === "succeeded"
            ? "Cleaning completed. Refreshing the profile…"
            : `Cleaning ${current.state}: ${current.error || "Check task details"}. Refreshing the profile…`,
        );
        reload((value) => value + 1);
        await s.refreshOverview();
      });
    } else reload((value) => value + 1);
  }
  const distribution = (title: string, pairs: any[], valueLabel = "Count") => (
    <Panel title={title}>
      <SimpleTable
        label={title}
        rows={pairs}
        getRowId={([label]: any) => String(label)}
        columns={[
          { id: "value", label: "Value", value: ([label]: any) => label },
          { id: "count", label: valueLabel, value: ([, count]: any) => count },
        ]}
        empty={<p>No values in this profile.</p>}
        renderRow={([label, count]: any, { key, ...props }, cells) => (
          <Evidence
            as="tr"
            {...props}
            key={key}
            label={title}
            data={{ label, count }}
          >
            {cells}
          </Evidence>
        )}
      />
    </Panel>
  );
  return (
    <Modal
      open
      wide
      className="dataset-quality-dialog"
      title={`Data quality · ${name}`}
      onClose={onClose}
      footer={
        <div className="quality-footer">
          <span>
            Cleaning changes this dataset. Saved snapshots stay unchanged.
          </span>
          <Button onClick={onClose}>Done</Button>
        </div>
      }
    >
      <div className="quality-workspace">
        <div className="quality-navigation">
          <SegmentedControl
            kind="tabs"
            variant="underline"
            aria-label="Quality views"
            value={view}
            onChange={setView}
            options={[
              {
                value: "overview",
                label: "Overview",
                controls: id + "-overview",
              },
              {
                value: "distributions",
                label: "Distributions",
                controls: id + "-distributions",
              },
              {
                value: "cleaning",
                label: "Cleaning",
                controls: id + "-cleaning",
              },
            ]}
          />
          <Button
            size="sm"
            disabled={action.busy || (!profile && !error)}
            onClick={refresh}
          >
            {pendingClean ? "Check cleaning status" : "Refresh profile"}
          </Button>
        </div>
        <ErrorNotice error={error || action.error} />
        {stale && (
          <p className="quality-notice" role="status">
            Profile captured before the latest cleaning request.{" "}
            {pendingClean
              ? "Check cleaning status for completion."
              : "Refresh to see updated evidence."}
          </p>
        )}
        {result && (
          <p className="quality-notice" role="status">
            {result}
          </p>
        )}
        {!profile && !error && (
          <p role="status" className="quality-loading">
            {task
              ? `Profiling ${task.state} in the background. You can close this dialog; the task continues.`
              : "Profiling…"}
          </p>
        )}
        {error && (
          <div className="quality-loading">
            <p>The quality profile could not be loaded.</p>
            <Button onClick={() => reload((value) => value + 1)}>
              Retry profile
            </Button>
          </div>
        )}
        {profile && (
          <Evidence
            label="Dataset quality profile"
            data={profile}
            className="quality-evidence"
          >
            <section
              role="tabpanel"
              tabIndex={-1}
              aria-label="Overview"
              id={id + "-overview"}
              hidden={view !== "overview"}
              className="quality-pane"
            >
              <RecordsScroll label="Quality overview">
                <div className="quality-summary">
                  {Object.entries({
                    Datapoints: profile.count,
                    "Approx. tokens": profile.approx_tokens.total,
                    "Mean turns": profile.turns.mean,
                    "JSON answers": `${profile.format.answers_json_pct}%`,
                  }).map(([label, value]) => (
                    <Evidence key={label} label={label} data={{ value }}>
                      <div className="quality-stat">
                        <span>{label}</span>
                        <strong>{String(value)}</strong>
                      </div>
                    </Evidence>
                  ))}
                </div>
                <div className="quality-overview-grid">
                  <Panel title="Duplicates and outliers">
                    <dl className="quality-facts">
                      <dt>Exact duplicate user turns</dt>
                      <dd>{profile.duplicates.exact_user_rows}</dd>
                      <dt>Near duplicate pairs</dt>
                      <dd>{profile.duplicates.near_pair_count}</dd>
                      <dt>Empty-like rows</dt>
                      <dd>{profile.outliers.empty_like.length}</dd>
                      <dt>Length outliers</dt>
                      <dd>
                        {profile.outliers.too_short.length +
                          profile.outliers.too_long.length}
                      </dd>
                    </dl>
                    <details className="quality-examples">
                      <summary>Review duplicate examples</summary>
                      <p>
                        Exact duplicate user messages:{" "}
                        {profile.duplicates.examples
                          .map((group: any) => group.join(" · "))
                          .join("; ") || "none"}
                      </p>
                      {distribution(
                        "Near-duplicate examples",
                        profile.duplicates.near_pairs
                          .slice(0, 20)
                          .map((pair: any) => [
                            `Positions ${pair.a + 1} and ${pair.b + 1}`,
                            pair.similarity,
                          ]),
                        "Similarity",
                      )}
                      <p>
                        Positions refer to this profile’s ordering, not record
                        IDs. Similarity is a score, not a count. Exact record
                        lookup is unavailable.
                      </p>
                    </details>
                  </Panel>
                  <Panel title="Conversation structure">
                    <dl className="quality-facts">
                      <dt>With system prompt</dt>
                      <dd>{profile.turns.with_system_prompt}</dd>
                      <dt>Rated datapoints</dt>
                      <dd>{profile.ratings.count}</dd>
                      {profile.ratings.count > 0 && (
                        <>
                          <dt>Mean rating</dt>
                          <dd>{profile.ratings.mean}/10</dd>
                        </>
                      )}
                    </dl>
                    <p className="quality-help">
                      Review distributions for length and composition. Use
                      Cleaning to choose changes explicitly.
                    </p>
                  </Panel>
                </div>
              </RecordsScroll>
            </section>
            <section
              role="tabpanel"
              tabIndex={-1}
              aria-label="Distributions"
              id={id + "-distributions"}
              hidden={view !== "distributions"}
              className="quality-pane"
            >
              <RecordsScroll label="Quality distributions">
                <QualityDistributions
                  profile={profile}
                  active={view === "distributions"}
                />
              </RecordsScroll>
            </section>
            <section
              role="tabpanel"
              tabIndex={-1}
              aria-label="Cleaning"
              id={id + "-cleaning"}
              hidden={view !== "cleaning"}
              className="quality-pane"
            >
              <RecordsScroll label="Cleaning options">
                <div className="quality-cleaning-grid">
                  <SettingsSection
                    title="Cleaning rules"
                    description="Choose the transformations to apply to this dataset."
                  >
                    <div className="quality-rules">
                      {rules.map(([key, label]) => (
                        <Checkbox
                          key={key}
                          isSelected={chosen.includes(key)}
                          isDisabled={action.busy || !!pendingClean}
                          onChange={(on) =>
                            setChosen((previous) =>
                              on
                                ? [...previous, key]
                                : previous.filter((item) => item !== key),
                            )
                          }
                        >
                          {label}
                        </Checkbox>
                      ))}
                    </div>
                  </SettingsSection>
                  <SettingsSection
                    title="Optional thresholds"
                    description="Leave a limit blank to keep it unrestricted. Character limits apply to the whole conversation."
                  >
                    <div className="quality-thresholds">
                      <NumberField
                        label="Min total chars"
                        min={0}
                        value={values.min_total_chars}
                        onChange={field("min_total_chars")}
                        disabled={action.busy || !!pendingClean}
                      />
                      <NumberField
                        label="Max total chars"
                        min={0}
                        value={values.max_total_chars}
                        onChange={field("max_total_chars")}
                        disabled={action.busy || !!pendingClean}
                      />
                      <NumberField
                        label="Min rating (0–10)"
                        min={0}
                        max={10}
                        step={0.5}
                        value={values.min_rating}
                        onChange={field("min_rating")}
                        disabled={action.busy || !!pendingClean}
                      />
                    </div>
                    <p className="quality-help quality-fence-note">
                      Profile outlier fences: below{" "}
                      {profile.outliers.fences.low} or above{" "}
                      {profile.outliers.fences.high} characters.
                    </p>
                    <StyledInput
                      label="Drop labels (comma separated)"
                      value={values.drop_labels}
                      onChange={field("drop_labels")}
                      isDisabled={action.busy || !!pendingClean}
                      fieldClassName="studio-form-control"
                    />
                    {thresholdError && <ErrorNotice error={thresholdError} />}
                  </SettingsSection>
                </div>
                <div className="quality-cleaning-actions">
                  <p>
                    {cleaningChoices
                      ? `${cleaningChoices} selected · Applied in place and recorded in cleaning history.`
                      : "Choose a rule or enter a threshold to continue."}
                  </p>
                  <Button
                    variant="primary"
                    loading={action.busy}
                    disabled={
                      !!pendingClean ||
                      cleaningChoices === 0 ||
                      !!thresholdError
                    }
                    onClick={clean}
                  >
                    Apply cleaning
                  </Button>
                </div>
              </RecordsScroll>
            </section>
          </Evidence>
        )}
      </div>
    </Modal>
  );
}
