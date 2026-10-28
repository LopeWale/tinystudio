import { DataTable } from "../../components/studio/data-table";
import { CollectionTemplate } from "../../components/studio/templates";
import "./analytical-workspace.css";
import { useState, useEffect } from "react";
import { useStudio, useResource } from "../../app/StudioContext";
import {
  Page,
  Panel,
  Button,
  Modal,
  Evidence,
} from "../../components/studio/primitives";
import {
  enc,
  lines,
  snapshots,
  useAction,
  ErrorNotice,
  Input,
  Text,
  Select,
  Chips,
  track,
  DetailInspector,
} from "./common";
export function MetricsPage() {
  const s = useStudio(),
    a = useAction(),
    r = useResource<any[]>("/api/metrics");
  const [editing, E] = useState<any>(null),
    [kind, K] = useState("rubric"),
    [v, V] = useState<any>({}),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState(""),
    [inspected, inspect] = useState<string | null>(null);
  useEffect(() => {
    inspect(null);
    E(null);
    setQuery("");
    setFilter("");
  }, [s.scopeKey]);
  const selected = r.data?.find((me) => me.name === inspected);
  const matching = (r.data || []).filter(
    (me) =>
      me.name.toLowerCase().includes(query.trim().toLowerCase()) &&
      (!filter || me.kind === filter),
  );
  const snaps = snapshots(s.overview);
  const field = (k: string) => (x: any) => V((p: any) => ({ ...p, [k]: x }));
  function open(me: any) {
    a.clearError();
    E(me);
    K(me.kind || "rubric");
    V({
      name: me.name || "",
      description: me.description || "",
      should: (me.rules?.should || []).join("\n"),
      should_not: (me.rules?.should_not || []).join("\n"),
      check: me.check || "exact_match",
      url: me.webhook?.url || "",
      token: "",
      snapshot: snaps[0]?.name || "",
    });
  }
  const save = () =>
    a.run(async () => {
      if (!v.name.trim()) throw Error("Give the metric a name");
      if (editing.name)
        await s.api.put(`/api/metrics/${enc(editing.name)}`, {
          description: v.description,
          should: lines(v.should),
          should_not: lines(v.should_not),
        });
      else
        await s.api.post("/api/metrics", {
          name: v.name.trim(),
          description: v.description,
          kind,
          ...(kind === "rubric"
            ? { should: lines(v.should), should_not: lines(v.should_not) }
            : kind === "code"
              ? { check: v.check }
              : { url: v.url.trim(), token: v.token || null }),
        });
      E(null);
      r.reload();
    });
  return (
    <Page
      className="data-workspace analytical-workspace collection-page analytical-collection-page metrics-collection-page"
      title="Metrics"
      description="Define good answers with rules, code checks, or your evaluator."
      actions={
        <Button variant="primary" onClick={() => open({})}>
          Create metric
        </Button>
      }
    >
      <CollectionTemplate
        className="data-template"
        label="Metric definition workspace"
        summary={
          <div className="analytical-scope">
            <strong>{r.data?.length ?? "—"} scoring definitions</strong>
            <span>
              {(r.data || []).filter((me) => me.kind === "rubric").length}{" "}
              rubrics ·{" "}
              {(r.data || []).filter((me) => me.kind === "code").length} code
              checks ·{" "}
              {(r.data || []).filter((me) => me.kind === "webhook").length}{" "}
              webhooks
            </span>
            <a href="#/evaluations">Review evaluation evidence →</a>
          </div>
        }
      >
        <ErrorNotice error={r.error} />

        <div
          className={`data-master-detail ${selected ? "has-selection" : ""}`}
        >
          <div className="data-master">
            <DataTable
              scopeKey={s.scopeKey + ":" + s.route}
              label="Metrics"
              layout="fill"
              toolbarContent={
                <div className="analytical-toolbar">
                  <Input
                    label="Find a metric"
                    type="search"
                    value={query}
                    onChange={setQuery}
                  />
                  <Select
                    label="Metric kind"
                    value={filter}
                    onChange={setFilter}
                    options={[
                      { value: "", label: "All kinds" },
                      "rubric",
                      "code",
                      "webhook",
                    ]}
                  />
                </div>
              }
              empty={
                r.loading
                  ? "Loading metrics…"
                  : r.data?.length
                    ? "No metrics match these filters."
                    : "No metrics yet."
              }
              rows={matching}
              getRowId={(me: any) => String(me.name)}
              pagination="client"
              columns={[
                {
                  id: "col-0",
                  label: "Metric",
                  value: (me: any) => me.name,
                  render: (me: any) => (
                    <>
                      <Button
                        variant="ghost"
                        aria-pressed={selected?.name === me.name}
                        onClick={() => inspect(me.name)}
                      >
                        {me.name}
                      </Button>
                    </>
                  ),
                },
                {
                  id: "col-1",
                  label: "Kind",
                  value: (me: any) => me.kind,
                  render: (me: any) => <>{me.kind}</>,
                },
                {
                  id: "col-2",
                  label: "Definition",
                  value: (me: any) =>
                    me.description || me.check || me.webhook?.url,
                  render: (me: any) => (
                    <>{me.description || me.check || me.webhook?.url || "—"}</>
                  ),
                },
                {
                  id: "col-3",
                  label: "Rules",
                  value: (me: any) =>
                    me.kind === "rubric"
                      ? `${me.rules?.should?.length || 0} should / ${me.rules?.should_not?.length || 0} should not`
                      : "—",
                  render: (me: any) => (
                    <>
                      {me.kind === "rubric"
                        ? `${me.rules?.should?.length || 0} should / ${me.rules?.should_not?.length || 0} should not`
                        : "—"}
                    </>
                  ),
                },
              ]}
              renderRow={(me: any, { key, ...props }, cells) => (
                <Evidence as="tr" {...props} key={key} label="Metric" data={me}>
                  {cells}
                </Evidence>
              )}
            />
          </div>
          {selected ? (
            <DetailInspector
              title={selected.name}
              onClose={() => inspect(null)}
            >
              <div
                className="analytical-inspector-scroll-wrap"
                key={selected.name}
              >
                <div className="analytical-inspector-fade" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </div>
                <div
                  className="analytical-inspector-scroll"
                  onScroll={(event) =>
                    event.currentTarget.parentElement?.style.setProperty(
                      "--analytical-inspector-fade",
                      String(
                        Math.min(
                          1,
                          Math.max(0, event.currentTarget.scrollTop) / 24,
                        ),
                      ),
                    )
                  }
                >
                  <div className="analytical-definition">
                    <div className="data-actions">
                      <Button size="sm" onClick={() => open(selected)}>
                        {selected.kind === "rubric"
                          ? "Edit rules"
                          : "Manage metric"}
                      </Button>
                      <span className="data-muted">{selected.kind}</span>
                    </div>
                    <Evidence label="Metric definition" data={selected}>
                      <p>
                        {selected.description || "No description supplied."}
                      </p>
                      {selected.kind === "rubric" ? (
                        <>
                          <section>
                            <h3>Should follow</h3>
                            {selected.rules?.should?.length ? (
                              <ul>
                                {selected.rules.should.map(
                                  (rule: string, i: number) => (
                                    <li key={i}>{rule}</li>
                                  ),
                                )}
                              </ul>
                            ) : (
                              <p className="data-muted">
                                No positive rules defined.
                              </p>
                            )}
                          </section>
                          <section>
                            <h3>Should not follow</h3>
                            {selected.rules?.should_not?.length ? (
                              <ul>
                                {selected.rules.should_not.map(
                                  (rule: string, i: number) => (
                                    <li key={i}>{rule}</li>
                                  ),
                                )}
                              </ul>
                            ) : (
                              <p className="data-muted">
                                No negative rules defined.
                              </p>
                            )}
                          </section>
                          <p className="data-muted">
                            Rubrics require the configured helper model to score
                            predictions.
                          </p>
                        </>
                      ) : selected.kind === "code" ? (
                        <section>
                          <h3>Deterministic check</h3>
                          <code>{selected.check}</code>
                          <p className="data-muted">
                            Scores use this check against each prediction and
                            its reference.
                          </p>
                        </section>
                      ) : (
                        <section>
                          <h3>Evaluator endpoint</h3>
                          <code>
                            {selected.webhook?.url || "No endpoint supplied"}
                          </code>
                          <p className="data-muted">
                            The external evaluator supplies the score and
                            optional reasoning.
                          </p>
                        </section>
                      )}
                    </Evidence>
                    <a href="#/evaluations">Open evaluations →</a>
                  </div>
                </div>
              </div>
            </DetailInspector>
          ) : null}
        </div>
        {editing && (
          <Modal
            open
            wide
            title={editing.name || "Create metric"}
            onClose={() => E(null)}
            footer={
              <div className="data-actions">
                {editing.name && (
                  <Button
                    variant="danger"
                    onClick={() =>
                      a.run(async () => {
                        if (
                          await s.confirm(`Delete metric "${editing.name}"?`)
                        ) {
                          await s.api.del(`/api/metrics/${enc(editing.name)}`);
                          E(null);
                          r.reload();
                        }
                      })
                    }
                  >
                    Delete metric
                  </Button>
                )}
                {(!editing.name || kind === "rubric") && (
                  <Button variant="primary" loading={a.busy} onClick={save}>
                    {editing.name ? "Save rules" : "Create"}
                  </Button>
                )}
              </div>
            }
          >
            <ErrorNotice error={a.error} />
            {!editing.name && (
              <>
                <Chips
                  items={["rubric", "code", "webhook"]}
                  chosen={[kind]}
                  onChange={(x) => K(x.at(-1) || kind)}
                />
                <Input label="Name" value={v.name} onChange={field("name")} />
              </>
            )}
            <Text
              label="Description"
              value={v.description}
              onChange={field("description")}
            />
            {kind === "rubric" ? (
              <>
                {!editing.name && (
                  <div className="data-actions">
                    <Select
                      label="Generate rules from snapshot"
                      value={v.snapshot}
                      onChange={field("snapshot")}
                      options={snaps.map((x) => x.name)}
                    />
                    <Button
                      loading={a.busy}
                      onClick={() =>
                        a.run(async () => {
                          if (!v.name.trim())
                            throw Error("Name the metric first");
                          if (!v.snapshot)
                            throw Error("Create a snapshot first");
                          track(
                            s,
                            await s.api.post("/api/metrics/generate", {
                              name: v.name.trim(),
                              snapshot: v.snapshot,
                              description: v.description.trim(),
                            }),
                          );
                          E(null);
                        })
                      }
                    >
                      Generate rules with AI
                    </Button>
                  </div>
                )}
                <div className="data-grid">
                  <Text
                    label="Should follow (one per line)"
                    value={v.should}
                    onChange={field("should")}
                  />
                  <Text
                    label="Should not follow (one per line)"
                    value={v.should_not}
                    onChange={field("should_not")}
                  />
                </div>
              </>
            ) : kind === "code" ? (
              <Select
                label="Code check"
                value={v.check}
                onChange={field("check")}
                disabled={!!editing.name}
                options={[
                  "exact_match",
                  "json_valid",
                  "token_f1",
                  "similarity",
                  "contains_reference",
                ]}
              />
            ) : (
              <>
                <Input
                  label="Evaluation URL"
                  value={v.url}
                  onChange={field("url")}
                  readOnly={!!editing.name}
                />
                {!editing.name && (
                  <Input
                    label="Token (optional)"
                    type="password"
                    value={v.token}
                    onChange={field("token")}
                  />
                )}
              </>
            )}
          </Modal>
        )}
      </CollectionTemplate>
    </Page>
  );
}
