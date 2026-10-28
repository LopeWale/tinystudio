import { ConversationTemplate } from "../../components/studio/templates";
import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type FormEvent,
} from "react";
import { useSearchParams } from "react-router-dom";
import { AssistantScrollArea } from "../assistant/scrollArea";
import "./playground-workspace.css";
import { useResource } from "../../app/StudioContext";
import { useComponentAction } from "../assistant/actions";
import { usePlaygroundController } from "../../app/PlaygroundProvider";
import {
  Badge,
  Button,
  EmptyState,
  Evidence,
  Field,
  Page,
  Panel,
} from "../../components/studio/primitives";
export { PlaygroundController } from "../../controllers/playground";
export { PlaygroundProvider } from "../../app/PlaygroundProvider";
export function PlaygroundPage() {
  const [params] = useSearchParams(),
    models = useResource<any[]>("/api/models"),
    repositories = useResource<any[]>("/api/repositories");
  const controller = usePlaygroundController();
  const state = useSyncExternalStore(
      controller.subscribe,
      controller.getSnapshot,
    ),
    [showSettings, setShowSettings] = useState(false),
    [logScrolled, setLogScrolled] = useState(0),
    log = useRef<HTMLDivElement>(null);
  useEffect(() => {
    controller.activate();
    return () => controller.deactivate();
  }, [controller]);
  useEffect(() => {
    const preset = params.get("model");
    if (preset) {
      controller.reset();
      controller.selectModel(preset);
    }
  }, [controller, params]);
  useEffect(() => {
    // Initial inventory selection must not erase a prompt composed while loading.
    // Explicit model changes still use selectModel() and its reset semantics.
    if (!controller.getSnapshot().model && models.data?.length)
      controller.configure({
        model: (
          models.data.find((m) => m.kind === "fine-tuned") ?? models.data[0]
        ).id,
      });
  }, [controller, models.data]);
  useEffect(() => {
    log.current?.scrollTo?.({
      top: log.current.scrollHeight,
      behavior: "instant",
    });
  }, [state.history]);
  const listed = models.data ?? [],
    options =
      state.model && !listed.some((m) => m.id === state.model)
        ? [{ id: state.model, alias: state.model }, ...listed]
        : listed;
  useComponentAction({
    id: "playground.model",
    label: "Select Playground model",
    kind: "select",
    state: state.model,
    options: options.map((model) => ({
      value: model.id,
      label: model.alias || model.id,
    })),
    disabled: state.busy || models.loading,
    run: (value) => {
      if (value) controller.selectModel(value);
    },
  });
  useComponentAction({
    id: "playground.inspector",
    label: "Show Playground configuration",
    kind: "open",
    state: showSettings,
    options: [
      { value: "open", label: "Open configuration" },
      { value: "closed", label: "Close configuration" },
    ],
    run: (value) => setShowSettings(value === "open"),
  });
  useComponentAction({
    id: "playground.temperature",
    label: "Set response temperature",
    kind: "filter",
    state: state.temperature,
    options: Array.from({ length: 21 }, (_, index) => ({
      value: String(index / 20),
      label: String(index / 20),
    })),
    disabled: state.busy,
    run: (value) => controller.configure({ temperature: Number(value) }),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void controller.send();
  };
  return (
    <Page
      title="Playground"
      className="playground-workspace"
      actions={
        <>
          <a href="#/traces">Review traces</a>
          <Button
            onClick={() => setShowSettings((v) => !v)}
            aria-expanded={showSettings}
            aria-controls="playground-inspector"
          >
            {showSettings ? "Hide settings" : "Show settings"}
          </Button>
        </>
      }
    >
      <ConversationTemplate label="Model playground">
        {(models.error || repositories.error) && (
          <p role="alert">
            {models.error?.message || repositories.error?.message}
          </p>
        )}
        <div
          className={
            showSettings ? "playground-grid inspector-open" : "playground-grid"
          }
        >
          {showSettings && (
            <Panel
              id="playground-inspector"
              className="playground-inspector"
              title="Configuration"
              actions={
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setShowSettings(false)}
                >
                  Close configuration
                </Button>
              }
            >
              <AssistantScrollArea
                className="playground-settings-body"
                aria-label="Playground configuration"
              >
                <Field label="System prompt">
                  <textarea
                    rows={4}
                    value={state.system}
                    disabled={state.busy}
                    onChange={(e) =>
                      controller.configure({ system: e.target.value })
                    }
                    placeholder="You are a helpful assistant."
                  />
                </Field>
                <Field
                  label="Knowledge repositories"
                  hint="Retrieved passages provide context. Use ⌘ or Ctrl to select multiple."
                >
                  <select
                    multiple
                    size={3}
                    value={state.repositories}
                    disabled={state.busy || repositories.loading}
                    onChange={(e) =>
                      controller.configure({
                        repositories: Array.from(e.target.selectedOptions).map(
                          (o) => o.value,
                        ),
                      })
                    }
                  >
                    {repositories.data?.map((repo) => (
                      <option value={repo.name} key={repo.name}>
                        {repo.name} ({repo.chunk_count} chunks)
                      </option>
                    ))}
                  </select>
                </Field>
                {!repositories.loading && !repositories.data?.length && (
                  <p className="muted">
                    Create a repository to add reference material.
                  </p>
                )}
                <div className="row">
                  <Field label="Passages">
                    <input
                      type="number"
                      min="1"
                      max="20"
                      value={state.repoLimit}
                      disabled={state.busy}
                      onChange={(e) =>
                        controller.configure({
                          repoLimit: Math.min(
                            20,
                            Math.max(1, Number(e.target.value) || 1),
                          ),
                        })
                      }
                    />
                  </Field>
                  <Field label="Minimum similarity">
                    <input
                      type="number"
                      min="-1"
                      max="1"
                      step=".05"
                      value={state.repoThreshold}
                      disabled={state.busy}
                      onChange={(e) =>
                        controller.configure({
                          repoThreshold: Math.min(
                            1,
                            Math.max(-1, Number(e.target.value)),
                          ),
                        })
                      }
                    />
                  </Field>
                </div>
                <Field
                  label={`Temperature · ${state.temperature}`}
                  hint="Lower values favor focused responses; higher values add variety."
                >
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step=".05"
                    value={state.temperature}
                    disabled={state.busy}
                    onChange={(e) =>
                      controller.configure({
                        temperature: Number(e.target.value),
                      })
                    }
                  />
                </Field>
              </AssistantScrollArea>
            </Panel>
          )}
          <Panel
            className="playground-conversation"
            title="Conversation"
            actions={
              state.seconds != null ? (
                <Badge>{state.seconds.toFixed(2)} s</Badge>
              ) : state.busy ? (
                <span role="status">Generating…</span>
              ) : undefined
            }
          >
            <div className="playground-toolbar">
              <Field
                label="Model"
                hint="Loaded models show ●. Other supported models load on the first message."
              >
                <select
                  value={state.model}
                  disabled={state.busy || models.loading}
                  onChange={(e) => controller.selectModel(e.target.value)}
                >
                  {!state.model && (
                    <option value="">
                      {models.loading
                        ? "Loading models…"
                        : options.length
                          ? "Choose a model"
                          : "No models available"}
                    </option>
                  )}
                  {options.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.alias || model.id}
                      {model.status === "up" ? " ●" : ""}
                    </option>
                  ))}
                </select>
              </Field>
              <Button variant="ghost" onClick={() => controller.reset()}>
                Reset conversation
              </Button>
            </div>
            {(state.system.trim() || state.repositories.length > 0) && (
              <div
                className="playground-context"
                aria-label="Active Playground context"
              >
                {state.system.trim() && <span>System instructions active</span>}
                {state.repositories.map((repository) => (
                  <span key={repository}>{repository}</span>
                ))}
                <small>Temperature {state.temperature}</small>
              </div>
            )}
            <div className="playground-transcript-frame">
              <div className="scroll-edge" aria-hidden="true">
                <i style={{ opacity: Math.min(logScrolled / 24, 1) }} />
                <i style={{ opacity: Math.min(logScrolled / 24, 1) }} />
                <i style={{ opacity: Math.min(logScrolled / 24, 1) }} />
              </div>
              <div
                ref={log}
                role="log"
                aria-label="Playground conversation"
                aria-live="polite"
                className="playground-transcript"
                onScroll={(event) =>
                  setLogScrolled(event.currentTarget.scrollTop)
                }
              >
                {state.history.length ? (
                  state.history.map((message, index) => (
                    <Evidence
                      key={index}
                      label={`Playground ${message.role} message`}
                      data={message}
                    >
                      <article className="playground-message">
                        <strong>
                          {message.role === "user" ? "You" : "Model"}
                        </strong>
                        <p
                          style={{
                            whiteSpace: "pre-wrap",
                            overflowWrap: "anywhere",
                          }}
                        >
                          {message.content || "…"}
                        </p>
                        {message.chunks && (
                          <details>
                            <summary>
                              Retrieved passages ({message.chunks.length})
                            </summary>
                            {message.chunks.map((chunk, i) => (
                              <div key={i}>
                                <strong>{chunk.document}</strong> ·{" "}
                                {typeof chunk.similarity_score === "number"
                                  ? chunk.similarity_score.toFixed(2)
                                  : "Unknown similarity"}
                                <p>{chunk.content || chunk.text}</p>
                              </div>
                            ))}
                          </details>
                        )}
                      </article>
                    </Evidence>
                  ))
                ) : (
                  <EmptyState>
                    {state.model ? (
                      "Choose your settings, then send a prompt."
                    ) : (
                      <>
                        Add a model from <a href="#/models">Models</a> to get
                        started.
                      </>
                    )}
                  </EmptyState>
                )}
              </div>
            </div>
            {state.error && <p role="alert">{state.error}</p>}
            <form className="playground-composer" onSubmit={submit}>
              <Field label="Message your model">
                <textarea
                  rows={2}
                  value={state.draft}
                  onChange={(e) =>
                    controller.configure({ draft: e.target.value })
                  }
                  onKeyDown={(e) => {
                    if (
                      e.key === "Enter" &&
                      !e.shiftKey &&
                      !e.nativeEvent.isComposing
                    ) {
                      e.preventDefault();
                      void controller.send();
                    }
                  }}
                  placeholder="Try a question or paste a prompt…"
                />
              </Field>
              <div className="actions">
                {state.busy ? (
                  <Button type="button" onClick={() => controller.cancel()}>
                    Cancel response
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    variant="primary"
                    disabled={!state.model || !state.draft.trim()}
                  >
                    Send
                  </Button>
                )}
                <small className="muted">
                  Enter to send · Shift + Enter for a new line
                </small>
              </div>
            </form>
          </Panel>
        </div>
      </ConversationTemplate>
    </Page>
  );
}
