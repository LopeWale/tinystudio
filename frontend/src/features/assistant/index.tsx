import { ConversationTemplate } from "../../components/studio/templates";
import {
  createContext,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { useLocation } from "react-router-dom";
import { useStudio } from "../../app/StudioContext";
import { AgentThinking } from "../../components/boardui/application/agent-thinking/agent-thinking";
import { FileUploadField, UploadStatus } from "../../components/studio/uploads";
import {
  ConversationController,
  type Attachment,
  type Message,
  type DeepReadonly,
} from "../../controllers/conversation";
import {
  UploadController,
  UploadBatchController,
} from "../../controllers/uploads";
import { Button } from "../../components/affectui/button-clean";
import {
  RiSparklingLine,
  RiCloseLine,
  RiAttachmentLine,
  RiEqualizerLine,
  RiCursorLine,
  RiArrowUpLine,
  RiStopLine,
  RiArrowDownSLine,
} from "@remixicon/react";
import { Select, SelectItem } from "@/components/boardui/base/select/select";
import { Checkbox } from "@/components/boardui/base/checkbox/checkbox";
import { Textarea } from "@/components/boardui/base/textarea/textarea";
import "./assistant.css";
import { cx } from "@/components/boardui/utils/cx";
import { AssistantScrollArea } from "./scrollArea";
import { AssistantMessageContent } from "./messageContent";
import { capturePageContext } from "./context";
import { AssistantUiBridge } from "./uiBridge";
import {
  AssistantActivity,
  AssistantAttachments,
  AssistantSources,
} from "./display";
export {
  AssistantActivity,
  AssistantAttachments,
  AssistantSources,
} from "./display";

import { ComponentActionRegistry } from "../../controllers/componentActions";
import { ComponentActionsProvider } from "./actions";
export { useComponentAction } from "./actions";
const DockContext = createContext({
  expanded: false,
  open: () => {},
  collapse: () => {},
});
export const useAssistantDock = () => useContext(DockContext);
const Context = createContext<ConversationController | null>(null);
const ResearchRouteContext = createContext("/evaluations");
export function AssistantProvider({
  children,
  fetcher,
}: {
  children: ReactNode;
  fetcher?: typeof fetch;
}) {
  const studio = useStudio();
  const [expanded, setExpanded] = useState(false);
  const registry = useMemo(
    () =>
      new ComponentActionRegistry((message, title) =>
        studio.confirm(message, title),
      ),
    [studio.confirm],
  );
  registry.setScope({
    user: String(studio.user?.id || ""),
    project: studio.project,
    route: studio.route,
  });
  const latestStudio = useRef(studio);
  latestStudio.current = studio;
  const identity = String(studio.user?.id || "");
  const project = studio.project;
  const lastResearchRoute = useRef("/evaluations");
  if (
    studio.route &&
    !/^\/(?:assistant|settings|api-keys|users)(?:[/?]|$)/.test(studio.route)
  )
    lastResearchRoute.current = studio.route;
  const controller = useMemo(() => {
    const bridge = new AssistantUiBridge({
      navigate: (hash) => latestStudio.current.navigate(hash),
      actions: registry,
      evidence: studio.evidence,
    });
    return new ConversationController({
      api: studio.api,
      fetcher,
      user: identity,
      project,
      captureContext: () => {
        const current = latestStudio.current;
        return capturePageContext({
          evidence: current.evidence,
          route: current.route,
          project: current.project,
          user: String(current.user?.id || ""),
          title: document.title,
        });
      },
      validateSelection: (selection) =>
        studio.evidence
          .snapshot()
          .some(
            (record) =>
              record.id === selection.id &&
              record.project === project &&
              record.user === identity &&
              record.route === selection.route &&
              JSON.stringify(record.data) === JSON.stringify(selection.data),
          ),
      onUi: (action, args, allowed) => bridge.perform(action, args, allowed),
    });
  }, [studio.api, identity, project, registry, fetcher]);
  useEffect(() => {
    if (identity && project) controller.start();
    return () => controller.stop();
  }, [controller, identity, project]);
  return (
    <ComponentActionsProvider registry={registry}>
      <DockContext.Provider
        value={{
          expanded,
          open: () => setExpanded(true),
          collapse: () => setExpanded(false),
        }}
      >
        <Context.Provider value={controller}>
          <ResearchRouteContext.Provider value={lastResearchRoute.current}>
            {children}
          </ResearchRouteContext.Provider>
        </Context.Provider>
      </DockContext.Provider>
    </ComponentActionsProvider>
  );
}
function useConversation() {
  const controller = useContext(Context);
  if (!controller)
    throw new Error("AssistantProvider must wrap the studio routes");
  return {
    controller,
    state: useSyncExternalStore(controller.subscribe, controller.getSnapshot),
  };
}
export function AssistantPage() {
  const studio = useStudio(),
    dock = useAssistantDock(),
    researchRoute = useContext(ResearchRouteContext);
  useEffect(() => {
    dock.open();
    studio.navigate("#" + researchRoute);
  }, []);
  return null;
}
export function AssistantWidget() {
  const dock = useAssistantDock(),
    studio = useStudio();
  const [picking, setPicking] = useState(false),
    [cancelPick, setCancelPick] = useState(0);
  const launcher = useRef<HTMLButtonElement>(null),
    panel = useRef<HTMLElement>(null);
  const panelId = useId();
  const close = () => {
    dock.collapse();
    launcher.current?.focus();
  };
  useEffect(() => {
    if (dock.expanded)
      panel.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
  }, [dock.expanded]);
  return (
    <div
      className="assistant-widget"
      data-expanded={dock.expanded && !picking}
      data-picking={picking}
      data-assistant-surface
    >
      <aside
        ref={panel}
        id={panelId}
        className="assistant-floating-panel"
        aria-label="Workspace assistant"
        hidden={!dock.expanded && !picking}
        onKeyDown={(event) => {
          if (event.key !== "Escape" || event.defaultPrevented || picking)
            return;
          if (
            (event.target as Element).closest(
              '[role="listbox"], [role="menu"], [role="dialog"]',
            )
          )
            return;
          event.preventDefault();
          event.stopPropagation();
          close();
        }}
      >
        <div className="assistant-dock-header">
          <div className="assistant-dock-title">
            <RiSparklingLine size={18} aria-hidden="true" />
            <strong>Assistant</strong>
            <span>{studio.project}</span>
          </div>
          {picking ? (
            <Button
              size="sm"
              variant="ghost"
              aria-expanded="false"
              onClick={() => setCancelPick((value) => value + 1)}
            >
              Cancel pick
            </Button>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              aria-label="Close assistant"
              title="Close assistant"
              icon={<RiCloseLine />}
              onClick={close}
            />
          )}
        </div>
        {picking && (
          <p className="assistant-pick-status" role="status">
            Pick a data row or chart. Escape cancels.
          </p>
        )}
        <div className="assistant-dock-body" hidden={picking || !dock.expanded}>
          <AssistantConversation
            compact
            expanded={dock.expanded}
            onPickingChange={setPicking}
            cancelPick={cancelPick}
          />
        </div>
      </aside>
      <Button
        ref={launcher}
        className="assistant-launcher"
        aria-label="Open assistant"
        title="Open assistant"
        aria-expanded={dock.expanded && !picking}
        aria-controls={panelId}
        icon={<RiSparklingLine />}
        onClick={() =>
          picking
            ? setCancelPick((value) => value + 1)
            : dock.expanded
              ? close()
              : dock.open()
        }
      />
    </div>
  );
}

function Elapsed({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  return (
    <span aria-label="Request elapsed time">
      {Math.max(0, Math.floor((now - startedAt) / 1000))}s
    </span>
  );
}
function MessageView({ message }: { message: DeepReadonly<Message> }) {
  return (
    <article className={cx("assistant-message", message.role)}>
      <strong>{message.role === "user" ? "You" : "Assistant"}</strong>
      <AssistantMessageContent role={message.role} content={message.content} />
      <AssistantAttachments attachments={message.attachments || []} />
      {message.selected_data != null && (
        <details>
          <summary>Picked page evidence</summary>
          <pre className="text-body-2-regular">
            {JSON.stringify(message.selected_data, null, 2)}
          </pre>
        </details>
      )}
      <AssistantActivity calls={message.calls} searches={message.searches} />
      <AssistantSources sources={message.sources || []} />
    </article>
  );
}

export function AssistantConversation({
  onPickingChange,
  cancelPick = 0,
  compact = false,
  expanded = true,
}: {
  onPickingChange?: (picking: boolean) => void;
  cancelPick?: number;
  compact?: boolean;
  expanded?: boolean;
} = {}) {
  const studio = useStudio(),
    { controller, state } = useConversation();
  const [models, setModels] = useState<any>(null),
    [config, setConfig] = useState<any>(null),
    [files, setFiles] = useState<File[]>([]),
    [upload, setUpload] = useState<any>(null),
    [picking, setPicking] = useState(false),
    [uploadBusy, setUploadBusy] = useState(false),
    [controls, setControls] = useState<"model" | "options" | "files" | null>(
      null,
    );
  const controlsId = useId();
  const modelButton = useRef<HTMLButtonElement>(null),
    optionsButton = useRef<HTMLButtonElement>(null),
    filesButton = useRef<HTMLButtonElement>(null);
  const toggleControls = (next: "model" | "options" | "files") =>
    setControls((current) => (current === next ? null : next));
  const composer = useRef<HTMLTextAreaElement>(null);
  const researchRoute = useContext(ResearchRouteContext);
  const caret = useRef<{ start: number; end: number } | null>(null),
    restoreComposer = useRef(false);
  const exitPicking = () => {
    restoreComposer.current = true;
    setPicking(false);
    onPickingChange?.(false);
  };
  const beginPicking = () => {
    caret.current = composer.current
      ? {
          start: composer.current.selectionStart,
          end: composer.current.selectionEnd,
        }
      : null;
    setPicking(true);
    onPickingChange?.(true);
  };
  useEffect(() => {
    if (!picking && restoreComposer.current) {
      restoreComposer.current = false;
      const textarea = composer.current;
      textarea?.focus();
      if (textarea && caret.current)
        textarea.setSelectionRange(caret.current.start, caret.current.end);
    }
  }, [picking]);
  useEffect(() => {
    if (cancelPick) exitPicking();
  }, [cancelPick]);
  const mounted = useRef(false);
  const route = useLocation();
  const uploadOwner = useMemo(
    () => new UploadController<Attachment>(setUpload),
    [controller],
  );
  const uploadBatch = useMemo(
    () => new UploadBatchController(uploadOwner),
    [uploadOwner],
  );
  useEffect(() => {
    mounted.current = true;
    const abort = new AbortController();
    void studio.api
      .get("/api/assistant/models", abort.signal)
      .then(setModels)
      .catch((e) => {
        if (!abort.signal.aborted) studio.toast(e.message, true);
      });
    void studio.api
      .get(
        `/api/p/${encodeURIComponent(studio.project)}/assistant/attachments/config`,
        abort.signal,
      )
      .then(setConfig)
      .catch(() => {});
    return () => {
      mounted.current = false;
      abort.abort();
      uploadBatch.dispose();
    };
  }, [studio.api, studio.project, uploadBatch]);
  useEffect(() => {
    if (!picking) return;
    const pick = (event: MouseEvent) => {
      const target = event.target as Element;
      if (target.closest("[data-assistant-surface],.assistant-page")) return;
      const selected = studio.evidence.pick(target);
      if (!selected) {
        studio.toast(
          "No current structured evidence is bound to that element",
          true,
        );
        return;
      }
      if (
        selected.project !== studio.project ||
        selected.user !== String(studio.user.id)
      )
        return;
      event.preventDefault();
      event.stopImmediatePropagation();
      controller.setOptions({
        selection: { ...selected, source_route: selected.route },
      });
      exitPicking();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        exitPicking();
      }
    };
    document.addEventListener("click", pick, true);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("click", pick, true);
      document.removeEventListener("keydown", escape);
    };
  }, [picking, studio, controller]);
  useEffect(() => {
    let controller: AbortController | undefined;
    const reload = () => {
      controller?.abort();
      controller = new AbortController();
      void studio.api
        .get("/api/assistant/models", controller.signal)
        .then(setModels)
        .catch((error) => {
          if (error.name !== "AbortError") studio.toast(error.message, true);
        });
    };
    window.addEventListener("tinystudio:assistant-models-changed", reload);
    return () => {
      controller?.abort();
      window.removeEventListener("tinystudio:assistant-models-changed", reload);
    };
  }, [studio.api]);
  useEffect(() => {
    if (picking) exitPicking();
  }, [controller, route.pathname, route.search]);
  const providers = models
    ? Object.entries(models).filter(
        ([, value]: any) =>
          value && typeof value === "object" && Array.isArray(value.models),
      )
    : [];
  const selected = models?.[state.provider];
  const attach = async () => {
    if (uploadBatch.active || !files.length) return;
    const room = 8 - controller.getSnapshot().attachments.length;
    if (room <= 0) {
      studio.toast("At most 8 files per turn", true);
      return;
    }
    setUploadBusy(true);
    try {
      const result = await uploadBatch.run(
        files.slice(0, room),
        (file, signal, onProgress) => {
          const body = new FormData();
          body.append("file", file);
          return studio.api.upload<Attachment>(
            `/api/p/${encodeURIComponent(studio.project)}/assistant/attachments`,
            body,
            { signal, onProgress },
          );
        },
        (attachment) => {
          if (mounted.current)
            controller.setAttachments([
              ...controller.getSnapshot().attachments,
              attachment,
            ]);
        },
      );
      if (mounted.current)
        setFiles([...result.remaining, ...files.slice(room)]);
    } finally {
      if (mounted.current) setUploadBusy(false);
    }
  };
  const clear = async () => {
    if (
      await studio.confirm(
        "Clear this saved conversation? This affects your conversation in this project on every device.",
        "Clear conversation",
      )
    )
      try {
        await controller.clear();
      } catch (error) {
        studio.toast((error as Error).message, true);
      }
  };
  return (
    <div
      className="assistant-conversation text-body-regular"
      data-assistant-surface
      onKeyDown={(event) => {
        if (
          !controls ||
          event.key !== "Escape" ||
          event.defaultPrevented ||
          (event.target as Element).closest(
            '[role="listbox"], [role="menu"], [role="dialog"]',
          )
        )
          return;
        event.preventDefault();
        event.stopPropagation();
        setControls(null);
        (controls === "model"
          ? modelButton
          : controls === "options"
            ? optionsButton
            : filesButton
        ).current?.focus();
      }}
    >
      <ConversationTemplate label="Assistant conversation">
        {selected?.error && <p role="alert">{selected.error}</p>}
        <p
          className="assistant-save text-caption-1-regular text-text-tertiary"
          role="status"
        >
          {!state.loaded
            ? "Loading conversation…"
            : state.busy
              ? "Request active"
              : state.dirty
                ? "Unsaved reply"
                : `Saved · revision ${state.revision}`}{" "}
          ·{" "}
          {state.sync === "error"
            ? "Sync unavailable; retrying"
            : state.sync === "offline"
              ? "Offline"
              : "Syncing automatically"}
        </p>
        {state.executionError && (
          <div
            role="alert"
            className="text-body-regular text-text-error-primary"
          >
            {state.executionError}
          </div>
        )}
        {state.error && (
          <div role="alert">
            {state.error}
            <Button
              type="button"
              disabled={state.busy}
              onClick={() =>
                void (async () => {
                  if (
                    !state.dirty ||
                    (await studio.confirm(
                      "Discard the unsaved local reply and reload the database conversation?",
                    ))
                  )
                    await controller.refresh(true);
                })().catch((e) => studio.toast(e.message, true))
              }
            >
              Reload saved conversation
            </Button>
            {state.dirty && !state.conflict && !state.busy && (
              <Button
                type="button"
                onClick={() =>
                  void controller
                    .save()
                    .catch((e) => studio.toast(e.message, true))
                }
              >
                Retry save
              </Button>
            )}
          </div>
        )}
        <AssistantScrollArea
          className="assistant-history"
          aria-live="polite"
          hidden={compact && !expanded}
          style={compact && !expanded ? { display: "none" } : undefined}
        >
          {state.history.length ? (
            state.history.map((message, i) => (
              <MessageView key={i} message={message} />
            ))
          ) : (
            <p>
              Ask about your models, datasets or an evaluation. Pick rendered
              evidence to include its exact source records.
            </p>
          )}
          {state.busy && (
            <div className="assistant-controls">
              <AgentThinking label="Request active" />
              {state.startedAt && <Elapsed startedAt={state.startedAt} />}
            </div>
          )}
          <AssistantActivity
            uiRequests={state.steps.filter(
              (step) => step.type === "ui_request",
            )}
          />
        </AssistantScrollArea>
        {state.usage && (
          <p className="assistant-save text-caption-1-regular text-text-tertiary">
            {state.usage.model} · {state.usage.input_tokens.toLocaleString()}{" "}
            input / {state.usage.output_tokens.toLocaleString()} output tokens
          </p>
        )}
        <div className="assistant-composer">
          <div className="assistant-context-chips">
            <span className="assistant-context-route" title={studio.route}>
              Context · {studio.route || "/"}
            </span>
          </div>
          {state.selection && (
            <div className="assistant-attachment text-caption-1-regular">
              Picked: {state.selection.label}
              <Button
                type="button"
                onClick={() => controller.setOptions({ selection: null })}
              >
                Remove evidence
              </Button>
            </div>
          )}
          <AssistantAttachments
            attachments={state.attachments}
            disabled={state.busy}
            onRemove={(id) =>
              controller.setAttachments(
                state.attachments.filter((attachment) => attachment.id !== id),
              )
            }
            onDelete={(id) =>
              void (async () => {
                const attachment = state.attachments.find(
                  (item) => item.id === id,
                );
                if (
                  attachment &&
                  (await studio.confirm(
                    `Delete the private upload ${attachment.name}? Saved conversations retain its display metadata.`,
                  ))
                ) {
                  await studio.api.del(
                    `/api/p/${encodeURIComponent(studio.project)}/assistant/attachments/${id}`,
                  );
                  controller.setAttachments(
                    controller
                      .getSnapshot()
                      .attachments.filter((item) => item.id !== id),
                  );
                }
              })().catch((error) => studio.toast(error.message, true))
            }
          />

          {controls && (
            <AssistantScrollArea
              className="assistant-composer-panel"
              id={controlsId}
              role="region"
              aria-label={
                controls === "model"
                  ? "Assistant settings"
                  : controls === "options"
                    ? "Turn options"
                    : "Attach files"
              }
            >
              <div className="assistant-secondary-heading">
                <strong>
                  {controls === "model"
                    ? "Assistant settings"
                    : controls === "options"
                      ? "Turn options"
                      : "Attach files"}
                </strong>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label="Close assistant controls"
                  icon={<RiCloseLine />}
                  onClick={() => {
                    setControls(null);
                    (controls === "model"
                      ? modelButton
                      : controls === "options"
                        ? optionsButton
                        : filesButton
                    ).current?.focus();
                  }}
                />
              </div>
              {controls === "model" && (
                <div className="assistant-settings">
                  <div className="assistant-controls">
                    <div className="assistant-select">
                      <span className="text-body-medium">Provider</span>
                      <Select
                        aria-label="Provider"
                        selectedKey={state.provider}
                        isDisabled={state.busy}
                        onSelectionChange={(key) =>
                          controller.setOptions({
                            provider: String(key),
                            model: "",
                          })
                        }
                      >
                        {(providers.length
                          ? providers
                          : [
                              ["anthropic", {}],
                              ["openai", {}],
                            ]
                        ).map(([id, value]: any) => (
                          <SelectItem
                            id={id}
                            key={id}
                            textValue={`${id}${value.available === false ? " · unavailable" : ""}`}
                          >
                            {id}
                            {value.available === false ? " · unavailable" : ""}
                          </SelectItem>
                        ))}
                      </Select>
                    </div>
                    <div className="assistant-select">
                      <span className="text-body-medium">Model</span>
                      <Select
                        aria-label="Model"
                        selectedKey={state.model || "__default__"}
                        isDisabled={state.busy}
                        onSelectionChange={(key) =>
                          controller.setOptions({
                            model: key === "__default__" ? "" : String(key),
                          })
                        }
                      >
                        <SelectItem
                          id="__default__"
                          textValue={
                            selected?.default
                              ? `Provider default · ${selected.default}`
                              : "Provider default"
                          }
                        >
                          {selected?.default
                            ? `Provider default · ${selected.default}`
                            : "Provider default"}
                        </SelectItem>
                        {(selected?.models || []).map((model: any) => (
                          <SelectItem
                            id={model.id}
                            key={model.id}
                            textValue={model.name || model.id}
                          >
                            {model.name || model.id}
                          </SelectItem>
                        ))}
                      </Select>
                    </div>
                    <Button
                      type="button"
                      disabled={state.busy}
                      onClick={() => void clear()}
                    >
                      Clear
                    </Button>
                  </div>
                </div>
              )}
              {controls === "options" && (
                <div className="assistant-turn-options">
                  {" "}
                  <Checkbox
                    isSelected={state.webSearch}
                    isDisabled={state.busy}
                    onChange={(webSearch) =>
                      controller.setOptions({ webSearch })
                    }
                  >
                    Provider web search
                  </Checkbox>
                  <Checkbox
                    isSelected={state.allowActions}
                    isDisabled={state.busy}
                    onChange={(allowActions) =>
                      controller.setOptions({ allowActions })
                    }
                  >
                    Allow actions / deletions this turn
                  </Checkbox>
                </div>
              )}
              {controls === "files" && config && (
                <div className="assistant-file-options">
                  <p>
                    {Math.round(config.max_bytes / 1048576)} MiB maximum each
                  </p>
                  <FileUploadField
                    label="Assistant files"
                    accept={config.accept}
                    files={files}
                    multiple
                    maxBytes={config.max_bytes}
                    onChange={setFiles}
                    disabled={state.busy || uploadBusy}
                  />
                  <Button
                    type="button"
                    disabled={!files.length || state.busy || uploadBusy}
                    onClick={() => void attach()}
                  >
                    Upload selected files
                  </Button>
                  {upload && (
                    <UploadStatus
                      {...upload}
                      onCancel={() => uploadBatch.cancel()}
                      onRetry={uploadBusy ? undefined : () => void attach()}
                    />
                  )}
                </div>
              )}
            </AssistantScrollArea>
          )}
          <div className="assistant-input-shell">
            <Textarea
              aria-label="Message"
              placeholder="Ask anything about this page…"
              fieldClassName="assistant-input-field"
              ref={composer}
              value={state.draft}
              rows={2}
              autoResize
              maxRows={6}
              onChange={(draft) => controller.setDraft(draft)}
              onKeyDown={(event) => {
                // React Aria keyboard handlers stop bubbling by default. Let the
                // floating surface handle Escape after nested overlays consume it.
                if (event.key === "Escape") event.continuePropagation();

                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  void controller
                    .send()
                    .catch((error) => studio.toast(error.message, true));
                }
              }}
            />
            <div className="assistant-composer-tools">
              <div className="assistant-tool-group">
                <Button
                  ref={filesButton}
                  size="sm"
                  variant="ghost"
                  aria-label="Attach files"
                  title="Attach files"
                  aria-expanded={controls === "files"}
                  aria-controls={controls === "files" ? controlsId : undefined}
                  disabled={!config}
                  icon={<RiAttachmentLine />}
                  onClick={() => toggleControls("files")}
                />
                <Button
                  ref={optionsButton}
                  size="sm"
                  variant="ghost"
                  aria-label="Turn options"
                  title="Turn options"
                  aria-expanded={controls === "options"}
                  aria-controls={
                    controls === "options" ? controlsId : undefined
                  }
                  icon={<RiEqualizerLine />}
                  onClick={() => toggleControls("options")}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={
                    route.pathname === "/assistant"
                      ? "Return to research to pick data"
                      : "Pick page data"
                  }
                  title="Pick page data"
                  aria-pressed={picking}
                  icon={<RiCursorLine />}
                  onClick={() =>
                    route.pathname === "/assistant"
                      ? studio.navigate("#" + researchRoute)
                      : picking
                        ? exitPicking()
                        : beginPicking()
                  }
                />
              </div>
              <div className="assistant-tool-group assistant-send">
                <Button
                  ref={modelButton}
                  className="assistant-model-trigger"
                  size="sm"
                  variant="ghost"
                  aria-label="Assistant model settings"
                  title={`${state.provider} · ${state.model || selected?.default || "Provider default"}`}
                  aria-expanded={controls === "model"}
                  aria-controls={controls === "model" ? controlsId : undefined}
                  iconRight={<RiArrowDownSLine />}
                  onClick={() => toggleControls("model")}
                >
                  {state.model || selected?.default || state.provider}
                </Button>
                {state.busy ? (
                  <Button
                    type="button"
                    size="sm"
                    aria-label="Stop listening"
                    title="Stop listening"
                    icon={<RiStopLine />}
                    onClick={() => controller.cancel()}
                  />
                ) : (
                  <Button
                    type="button"
                    size="sm"
                    aria-label="Send"
                    title="Send · ⌘ / Ctrl + Enter"
                    disabled={
                      !state.loaded || state.dirty || !state.draft.trim()
                    }
                    icon={<RiArrowUpLine />}
                    onClick={() =>
                      void controller
                        .send()
                        .catch((error) => studio.toast(error.message, true))
                    }
                  />
                )}
              </div>
            </div>
          </div>
        </div>
      </ConversationTemplate>
    </div>
  );
}
