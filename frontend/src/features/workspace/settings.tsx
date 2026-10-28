import { SettingsTemplate } from "../../components/studio/templates";
import "./admin-workspace.css";
import { useLayoutEffect, useState } from "react";
import { useStudio, useResource } from "../../app/StudioContext";
import { Page, Panel, Button, Badge } from "../../components/studio/primitives";
import { Input, Check, ErrorNotice, useAction } from "../data/common";
export interface SettingSpec {
  key: string;
  label: string;
  group: string;
  secret: boolean;
  value?: string | null;
  set: boolean;
  source: string;
}
export function settingsChanges(
  specs: SettingSpec[],
  draft: Record<string, string>,
  clear: Record<string, boolean>,
) {
  const values: Record<string, string | null> = {};
  for (const spec of specs) {
    const value = draft[spec.key] ?? (spec.secret ? "" : spec.value || "");
    if (clear[spec.key]) values[spec.key] = null;
    else if (spec.secret) {
      if (value) values[spec.key] = value;
    } else if (value !== (spec.value || "")) values[spec.key] = value || null;
  }
  return values;
}
const groups = [
  [
    "anthropic",
    "Claude (assistant)",
    "Used by the assistant. Account-listed models become selectable.",
    "anthropic",
  ],
  [
    "openai",
    "OpenAI (assistant)",
    "Use OpenAI models in the same assistant.",
    "openai",
  ],
  [
    "helper",
    "Helper LLM",
    "Synthetic data, labeling, rubric judging, neuron explanations and tests. Supports OpenAI-compatible local or hosted servers.",
    "helper",
  ],
  [
    "rag",
    "Embeddings (repositories)",
    "Default feature-extraction model for new repositories. Existing repositories keep their model.",
    "",
  ],
  ["hub", "Hugging Face", "Token for gated models and uploads.", "hub"],
];
export function SettingsPage() {
  const s = useStudio(),
    admin = s.user?.role === "admin",
    r = useResource<any>(admin ? "/api/settings" : null),
    config = useResource<any>(admin ? "/api/config" : null),
    a = useAction();
  const [draft, D] = useState<Record<string, string>>({}),
    [clear, C] = useState<Record<string, boolean>>({}),
    [testing, T] = useState<Record<string, boolean>>({}),
    [results, R] = useState<Record<string, any>>({}),
    [active, A] = useState("");
  useLayoutEffect(() => {
    D({});
    C({});
    R({});
    A("");
  }, [r.data, s.scopeKey]);
  const data = r.data,
    cfg = config.data;
  const changes = settingsChanges(data?.settings || [], draft, clear),
    dirty = Object.keys(changes).length;
  const selected =
    active ||
    groups.find(([group]) =>
      data?.settings.some((spec: SettingSpec) => spec.group === group),
    )?.[0] ||
    "anthropic";
  const test = async (what: string) => {
    T((p) => ({ ...p, [what]: true }));
    try {
      const result = await s.api.post("/api/settings/test", { what });
      R((p) => ({ ...p, [what]: result }));
    } catch (e: any) {
      if (e.name !== "AbortError")
        R((p) => ({ ...p, [what]: { ok: false, detail: e.message } }));
    } finally {
      T((p) => ({ ...p, [what]: false }));
    }
  };
  const testControl = (what: string) => (
    <div className="data-actions">
      <Button size="sm" loading={testing[what]} onClick={() => test(what)}>
        Test {what} connection
      </Button>
      <span role="status">
        {results[what] &&
          `${results[what].ok ? "ok" : "failed"} · ${results[what].detail}`}
      </span>
    </div>
  );
  const save = () =>
    a.run(async () => {
      if (!dirty) return;
      await s.api.put("/api/settings", { values: changes });
      D({});
      C({});
      s.toast("Settings saved");
      window.dispatchEvent(
        new CustomEvent("tinystudio:assistant-models-changed"),
      );
      r.reload();
      config.reload();
      await s.refreshOverview();
    });
  return (
    <Page
      className="settings-workspace admin-workspace"
      title="Settings"
      description="Configure deployment providers, model services, and infrastructure."
    >
      {!admin ? (
        <p role="alert">
          Administrator access is required to manage deployment settings.
        </p>
      ) : (
        <>
          <ErrorNotice error={r.error || config.error || a.error} />
          {!data || !cfg ? (
            <p role="status">
              {r.error || config.error
                ? "Settings could not be loaded."
                : "Loading settings…"}
            </p>
          ) : (
            <SettingsTemplate
              label="Deployment configuration"
              summary={
                <div className="admin-scope">
                  <Badge>Deployment settings</Badge>
                  <span>Administrator access</span>
                  <span>
                    {data.updated
                      ? `Last change ${data.updated}`
                      : "Provider configuration"}
                  </span>
                </div>
              }
              inspector={
                <section className="admin-context">
                  <h2>Saved configuration</h2>
                  <p>
                    Saved secrets stay hidden. Type to replace a value, or
                    select Clear saved value.
                  </p>
                  <p>
                    Provider drafts stay available when you switch sections.
                    Save changes applies every edited provider.
                  </p>
                  <p>
                    Connection tests use saved settings. Save a draft before
                    testing its replacement.
                  </p>
                </section>
              }
            >
              <div className="admin-settings-layout">
                <nav
                  className="admin-section-nav"
                  aria-label="Settings sections"
                >
                  {groups.map(([group, title]) => (
                    <Button
                      key={group}
                      variant="ghost"
                      aria-pressed={selected === group}
                      onClick={() => {
                        A(group);
                        a.clearError();
                      }}
                    >
                      {title}
                      {data.settings.some(
                        (spec: SettingSpec) =>
                          spec.group === group &&
                          Object.hasOwn(changes, spec.key),
                      ) && <Badge>Edited</Badge>}
                    </Button>
                  ))}
                  <Button
                    variant="ghost"
                    aria-pressed={selected === "infrastructure"}
                    onClick={() => A("infrastructure")}
                  >
                    Infrastructure
                  </Button>
                </nav>
                <div className="admin-settings-editor">
                  {groups
                    .filter(([group]) => group === selected)
                    .map(([group, title, description, what]) => (
                      <Panel key={group} title={title}>
                        <p className="data-muted">{description}</p>
                        <div className="admin-provider-fields">
                          {data.settings
                            .filter((spec: SettingSpec) => spec.group === group)
                            .map((spec: SettingSpec) => (
                              <div
                                key={spec.key}
                                data-sensitive={
                                  spec.secret ? "true" : undefined
                                }
                              >
                                <Input
                                  label={spec.label}
                                  type={spec.secret ? "password" : "text"}
                                  autoComplete={
                                    spec.secret ? "new-password" : "off"
                                  }
                                  value={
                                    draft[spec.key] ??
                                    (spec.secret ? "" : spec.value || "")
                                  }
                                  placeholder={
                                    spec.secret
                                      ? spec.set
                                        ? "Saved; type to replace"
                                        : "not set"
                                      : "default"
                                  }
                                  onChange={(value: string) =>
                                    D((p) => ({ ...p, [spec.key]: value }))
                                  }
                                />
                                <p className="data-muted">
                                  {spec.source} ·{" "}
                                  {spec.set ? "configured" : "not set"}
                                </p>
                                {spec.secret && (
                                  <Check
                                    label={`Clear saved ${spec.label}`}
                                    checked={!!clear[spec.key]}
                                    onChange={(value: boolean) =>
                                      C((p) => ({ ...p, [spec.key]: value }))
                                    }
                                  />
                                )}
                              </div>
                            ))}
                          {!data.settings.some(
                            (spec: SettingSpec) => spec.group === group,
                          ) && (
                            <p>
                              No editable settings reported for this provider.
                            </p>
                          )}
                        </div>
                        {what && testControl(what)}
                      </Panel>
                    ))}
                  {selected === "infrastructure" && (
                    <Panel title="Infrastructure">
                      <p className="data-muted">
                        Read-only deployment connections.
                      </p>
                      <div className="admin-infrastructure-list">
                        {[
                          [
                            "database",
                            "PostgreSQL",
                            `${cfg.database_url} · ${cfg.database_version || "?"}`,
                          ],
                          ["redis", "Redis (queue)", cfg.redis_url],
                          ["qdrant", "Qdrant (vectors)", cfg.qdrant_url],
                          [
                            "inference",
                            "Inference",
                            cfg.inference.mode === "remote"
                              ? cfg.inference.url
                              : "Models load inside the API process",
                          ],
                        ].map(([what, title, detail]) => (
                          <section key={what}>
                            <h3>{title}</h3>
                            <pre>{detail}</pre>
                            {testControl(what)}
                          </section>
                        ))}
                        <section>
                          <h3>Compute and data</h3>
                          <p>
                            Device/dtype are per host (TINYSTUDIO_DEVICE /
                            TINYSTUDIO_DTYPE). This API host reports{" "}
                            {cfg.device} / {cfg.dtype}.
                          </p>
                          <p>
                            Artifacts: <code>{cfg.data_dir}</code>
                          </p>
                        </section>
                      </div>
                    </Panel>
                  )}
                  <div className="admin-savebar">
                    <span role="status">
                      {dirty
                        ? `${dirty} unsaved ${dirty === 1 ? "change" : "changes"} across providers`
                        : "No unsaved changes"}
                    </span>
                    <Button
                      size="sm"
                      disabled={!dirty || a.busy}
                      onClick={() => {
                        D({});
                        C({});
                        a.clearError();
                      }}
                    >
                      Reset draft
                    </Button>
                    <Button
                      variant="primary"
                      size="sm"
                      loading={a.busy}
                      disabled={!dirty}
                      onClick={save}
                    >
                      Save changes
                    </Button>
                  </div>
                </div>
              </div>
            </SettingsTemplate>
          )}
        </>
      )}
    </Page>
  );
}
