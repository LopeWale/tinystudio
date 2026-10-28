import { SettingsTemplate } from "../../components/studio/templates";
import "./admin-workspace.css";
import { useLayoutEffect, useRef, useState } from "react";
import { SettingsSection } from "../../components/studio/settings-section";
import { Input } from "../../components/boardui/base/input/input";
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
import "./settings.css";
import "../../components/studio/form-controls.css";
import { useStudio, useResource } from "../../app/StudioContext";
import { Page, Button, Badge } from "../../components/studio/primitives";
import { ErrorNotice, useAction } from "../data/common";
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
  const s = useStudio();
  // A settings session owns drafts and requests for exactly one permission scope.
  return (
    <SettingsSession
      key={JSON.stringify([s.scopeKey, s.user?.id, s.user?.role])}
    />
  );
}
function SettingsSession() {
  const s = useStudio(),
    admin = s.user?.role === "admin",
    r = useResource<any>(admin ? "/api/settings" : null),
    config = useResource<any>(admin ? "/api/config" : null),
    a = useAction();
  const [draft, D] = useState<Record<string, string>>({}),
    [clear, C] = useState<Record<string, boolean>>({}),
    [testing, T] = useState<Record<string, boolean>>({}),
    [results, R] = useState<Record<string, any>>({}),
    [active, A] = useState(""),
    [saved, setSaved] = useState<any>(),
    [refreshError, setRefreshError] = useState("");
  const mounted = useRef(false),
    revisions = useRef<Record<string, number>>({}),
    providerGeneration = useRef(0),
    savePending = useRef(false);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const data = saved || r.data,
    cfg = config.data;
  const changes = settingsChanges(data?.settings || [], draft, clear),
    dirty = Object.keys(changes).length;
  const selected =
    active ||
    groups.find(([group]) =>
      data?.settings.some((spec: SettingSpec) => spec.group === group),
    )?.[0] ||
    "anthropic";
  const choose = (group: string) => {
    if (group !== selected) {
      providerGeneration.current++;
      T({});
    }
    A(group);
    a.clearError();
  };
  const edited = (key: string) => {
    revisions.current[key] = (revisions.current[key] || 0) + 1;
  };
  const test = async (what: string) => {
    const generation = providerGeneration.current;
    const current = () =>
      mounted.current && generation === providerGeneration.current;
    T((p) => ({ ...p, [what]: true }));
    try {
      const result = await s.api.post("/api/settings/test", { what });
      if (current()) R((p) => ({ ...p, [what]: result }));
    } catch (e: any) {
      if (current() && e.name !== "AbortError")
        R((p) => ({ ...p, [what]: { ok: false, detail: e.message } }));
    } finally {
      if (current()) T((p) => ({ ...p, [what]: false }));
    }
  };
  const testControl = (what: string) => (
    <div className="settings-connection-controls">
      <Button size="sm" loading={testing[what]} onClick={() => test(what)}>
        Test {what} connection
      </Button>
      <span role="status" className="text-body-2-regular text-text-secondary">
        {results[what] &&
          `${results[what].ok ? "ok" : "failed"} · ${results[what].detail}`}
      </span>
    </div>
  );
  const save = () => {
    if (!dirty || savePending.current) return;
    const submittedRevisions = { ...revisions.current };
    const submittedKeys = Object.keys(changes);
    savePending.current = true;
    setRefreshError("");
    return a.run(async () => {
      try {
        let response: any;
        try {
          response = await s.api.put("/api/settings", { values: changes });
        } catch (error) {
          // useAction owns active-session errors; abandoned operations are silent.
          if (mounted.current) throw error;
          return;
        }
        if (!mounted.current) return;
        const unchanged = (key: string) =>
          revisions.current[key] === submittedRevisions[key];
        // The PUT returns effective values, including environment fallbacks after clearing.
        if (response?.settings) setSaved(response);
        D((previous) =>
          Object.fromEntries(
            Object.entries(previous).filter(
              ([key]) => !submittedKeys.includes(key) || !unchanged(key),
            ),
          ),
        );
        C((previous) =>
          Object.fromEntries(
            Object.entries(previous).filter(
              ([key]) => !submittedKeys.includes(key) || !unchanged(key),
            ),
          ),
        );
        R({});
        providerGeneration.current++;
        T({});
        s.toast("Settings saved");
        window.dispatchEvent(
          new CustomEvent("tinystudio:assistant-models-changed"),
        );
        // Refresh if a nonstandard adapter omits the canonical PUT response.
        if (!response?.settings) {
          setSaved(undefined);
          r.reload();
        }
        try {
          await s.refreshOverview();
        } catch {
          if (mounted.current)
            setRefreshError(
              "Settings were saved, but the workspace summary could not be refreshed.",
            );
        }
      } finally {
        savePending.current = false;
      }
    });
  };
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
          {refreshError && (
            <p
              role="status"
              className="text-body-2-regular text-text-secondary"
            >
              {refreshError}
            </p>
          )}
          {!data || !cfg ? (
            <div className="settings-loading">
              <p role="status">
                {r.error || config.error
                  ? "Settings could not be loaded."
                  : "Loading settings…"}
              </p>
              {(r.error || config.error) && (
                <Button
                  onClick={() => {
                    r.reload();
                    config.reload();
                  }}
                >
                  Retry
                </Button>
              )}
            </div>
          ) : (
            <SettingsTemplate label="Deployment configuration">
              <div className="settings-layout">
                <nav className="settings-nav" aria-label="Settings sections">
                  <p className="text-caption-1-semibold text-text-tertiary">
                    Providers
                  </p>
                  {groups.map(([group, title]) => (
                    <Button
                      key={group}
                      variant="ghost"
                      aria-pressed={selected === group}
                      onClick={() => choose(group)}
                    >
                      {title}
                      {data.settings.some(
                        (spec: SettingSpec) =>
                          spec.group === group &&
                          Object.hasOwn(changes, spec.key),
                      ) && <Badge>Edited</Badge>}
                    </Button>
                  ))}
                  <p className="text-caption-1-semibold text-text-tertiary">
                    Deployment
                  </p>
                  <Button
                    variant="ghost"
                    aria-pressed={selected === "infrastructure"}
                    onClick={() => choose("infrastructure")}
                  >
                    Infrastructure
                  </Button>
                </nav>
                <div className="settings-editor">
                  {groups
                    .filter(([group]) => group === selected)
                    .map(([group, title, description, what]) => (
                      <div key={group}>
                        <header className="settings-editor-heading">
                          <h2 className="text-headline-medium text-text-primary">
                            {title}
                          </h2>
                          <p className="text-body-2-regular text-text-tertiary">
                            {description}
                          </p>
                        </header>
                        <div className="settings-form-surface">
                          <div className="settings-fields">
                            {data.settings
                              .filter(
                                (spec: SettingSpec) => spec.group === group,
                              )
                              .map((spec: SettingSpec) => (
                                <SettingsSection
                                  key={spec.key}
                                  title={spec.label}
                                  description={
                                    <>
                                      <p>
                                        {spec.source} ·{" "}
                                        {spec.set ? "configured" : "not set"}
                                      </p>
                                      {spec.secret && (
                                        <p>
                                          Saved secrets stay hidden. Type to
                                          replace.
                                        </p>
                                      )}
                                    </>
                                  }
                                >
                                  <div
                                    data-sensitive={
                                      spec.secret ? "true" : undefined
                                    }
                                    className="settings-field-control"
                                  >
                                    <Input
                                      fieldClassName="studio-form-control"
                                      size="small"
                                      aria-label={spec.label}
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
                                      onChange={(value: string) => {
                                        edited(spec.key);
                                        D((p) => ({ ...p, [spec.key]: value }));
                                      }}
                                    />
                                    {spec.secret && (
                                      <Checkbox
                                        isSelected={!!clear[spec.key]}
                                        onChange={(value: boolean) => {
                                          edited(spec.key);
                                          C((p) => ({
                                            ...p,
                                            [spec.key]: value,
                                          }));
                                        }}
                                      >
                                        Clear saved {spec.label}
                                      </Checkbox>
                                    )}
                                  </div>
                                </SettingsSection>
                              ))}
                            {!data.settings.some(
                              (spec: SettingSpec) => spec.group === group,
                            ) && (
                              <p className="settings-empty">
                                No editable settings reported for this provider.
                              </p>
                            )}
                            {what && (
                              <SettingsSection
                                title="Connection"
                                description="Tests use saved settings. Save your changes before testing a replacement."
                              >
                                {testControl(what)}
                              </SettingsSection>
                            )}
                          </div>
                          <SaveFooter
                            dirty={dirty}
                            busy={a.busy}
                            onReset={() => {
                              D({});
                              C({});
                              a.clearError();
                            }}
                            onSave={save}
                          />
                        </div>
                        <p className="settings-form-help text-body-2-regular text-text-tertiary">
                          Save changes applies every edited provider. Clearing a
                          saved override may restore an environment default.
                        </p>
                      </div>
                    ))}
                  {selected === "infrastructure" && (
                    <div>
                      <header className="settings-editor-heading">
                        <h2 className="text-headline-medium text-text-primary">
                          Infrastructure
                        </h2>
                        <p className="text-body-2-regular text-text-tertiary">
                          Read-only deployment connections.
                        </p>
                      </header>
                      <div className="settings-form-surface">
                        <div className="settings-fields">
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
                              cfg.inference?.mode === "remote"
                                ? cfg.inference.url
                                : "Models load inside the API process",
                            ],
                          ].map(([what, title, detail]) => (
                            <SettingsSection
                              key={what}
                              title={title}
                              description={
                                <code className="settings-endpoint">
                                  {detail}
                                </code>
                              }
                            >
                              {testControl(what)}
                            </SettingsSection>
                          ))}
                          <SettingsSection
                            title="Compute and data"
                            description="Device and dtype are set per host with TINYSTUDIO_DEVICE / TINYSTUDIO_DTYPE."
                          >
                            <p className="text-body-2-regular">
                              This API host reports {cfg.device} / {cfg.dtype}.
                            </p>
                            <p className="text-body-2-regular">
                              Artifacts:{" "}
                              <code className="settings-endpoint">
                                {cfg.data_dir}
                              </code>
                            </p>
                          </SettingsSection>
                        </div>
                        <SaveFooter
                          dirty={dirty}
                          busy={a.busy}
                          onReset={() => {
                            D({});
                            C({});
                            a.clearError();
                          }}
                          onSave={save}
                        />
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </SettingsTemplate>
          )}
        </>
      )}
    </Page>
  );
}
function SaveFooter({
  dirty,
  busy,
  onReset,
  onSave,
}: {
  dirty: number;
  busy: boolean;
  onReset(): void;
  onSave(): void;
}) {
  return (
    <footer className="settings-save-footer">
      <span role="status" className="text-body-2-regular text-text-tertiary">
        {dirty
          ? `${dirty} unsaved ${dirty === 1 ? "change" : "changes"} across providers`
          : "No unsaved changes"}
      </span>
      <Button
        size="sm"
        variant="ghost"
        disabled={!dirty || busy}
        onClick={onReset}
      >
        Reset draft
      </Button>
      <Button
        variant="primary"
        size="sm"
        loading={busy}
        disabled={!dirty}
        onClick={onSave}
      >
        Save changes
      </Button>
    </footer>
  );
}
