import { Select, SelectItem } from "@/components/boardui/base/select/select";
import { Textarea } from "@/components/boardui/base/textarea/textarea";
import { TextField, InputBase } from "@/components/boardui/base/input/input";
import { Label } from "@/components/boardui/base/input/label";
import { HintText } from "@/components/boardui/base/input/hint-text";
import { Checkbox } from "@/components/boardui/base/checkbox/checkbox";
import { cx } from "@/components/boardui/utils/cx";
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
  Page,
  Panel,
} from "../../components/studio/primitives";
export { PlaygroundController } from "../../controllers/playground";
export { PlaygroundProvider } from "../../app/PlaygroundProvider";
function NumericSetting({
  label,
  hint,
  value,
  min,
  max,
  step = 1,
  disabled,
  onChange,
}: {
  label: string;
  hint?: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <TextField size="small" value={String(value)} isDisabled={disabled} onChange={onChange}>
      <Label>{label}</Label>
      <InputBase type="number" min={min} max={max} step={step} />
      {hint && <HintText>{hint}</HintText>}
    </TextField>
  );
}
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
    log = useRef<HTMLDivElement>(null),
    composerToolbar = useRef<HTMLDivElement>(null);
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
          className={cx("playground-grid", showSettings && "inspector-open")}
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
                  onClick={() => {
                    setShowSettings(false);
                    composerToolbar.current?.querySelector<HTMLButtonElement>("button")?.focus();
                  }}
                >
                  Close configuration
                </Button>
              }
            >
              <AssistantScrollArea
                className="playground-settings-body"
                aria-label="Playground configuration"
              >
                <section
                  className="playground-settings-group"
                  aria-labelledby="system-settings-title"
                >
                  <h3 id="system-settings-title" className="text-body-medium">
                    System instructions
                  </h3>
                  <Textarea
                    label="System prompt"
                    size="small"
                    rows={2}
                    value={state.system}
                    isDisabled={state.busy}
                    onChange={(system) => controller.configure({ system })}
                    placeholder="You are a helpful assistant."
                  />
                </section>
                <section
                  className="playground-settings-group"
                  aria-labelledby="retrieval-settings-title"
                >
                  <h3
                    id="retrieval-settings-title"
                    className="text-body-medium"
                  >
                    Retrieval
                  </h3>
                  <fieldset className="playground-repositories">
                    <legend className="text-body-2-medium">
                      Knowledge repositories
                    </legend>
                    <p className="text-caption-1-regular">
                      Select reference material to retrieve with each message.
                    </p>
                    <AssistantScrollArea
                      className="playground-repository-options"
                      aria-label="Repository selection"
                    >
                      <div className="playground-repository-list">
                        {repositories.data?.map((repo) => (
                          <Checkbox
                            key={repo.name}
                            isSelected={state.repositories.includes(repo.name)}
                            isDisabled={state.busy || repositories.loading}
                            onChange={(selected) =>
                              controller.configure({
                                repositories: selected
                                  ? [...state.repositories, repo.name]
                                  : state.repositories.filter(
                                      (name) => name !== repo.name,
                                    ),
                              })
                            }
                          >
                            {repo.name}{" "}
                            <span className="text-text-tertiary">
                              · {repo.chunk_count} chunks
                            </span>
                          </Checkbox>
                        ))}
                      </div>
                    </AssistantScrollArea>
                    {repositories.loading && (
                      <p role="status">Loading repositories…</p>
                    )}
                    {!repositories.loading && !repositories.data?.length && (
                      <p>Create a repository to add reference material.</p>
                    )}
                  </fieldset>
                  <div className="row">
                    <NumericSetting
                      label="Passages"
                      min={1}
                      max={20}
                      value={state.repoLimit}
                      disabled={state.busy}
                      onChange={(value) =>
                        controller.configure({
                          repoLimit: Math.min(
                            20,
                            Math.max(1, Number(value) || 1),
                          ),
                        })
                      }
                    />
                    <NumericSetting
                      label="Minimum similarity"
                      min={-1}
                      max={1}
                      step={0.05}
                      value={state.repoThreshold}
                      disabled={state.busy}
                      onChange={(value) =>
                        controller.configure({
                          repoThreshold: Math.min(
                            1,
                            Math.max(-1, Number(value)),
                          ),
                        })
                      }
                    />
                  </div>
                </section>
                <section
                  className="playground-settings-group"
                  aria-labelledby="generation-settings-title"
                >
                  <h3
                    id="generation-settings-title"
                    className="text-body-medium"
                  >
                    Generation
                  </h3>
                  <NumericSetting
                    label="Temperature"
                    hint="Lower values favor focused responses; higher values add variety."
                    min={0}
                    max={1}
                    step={0.05}
                    value={state.temperature}
                    disabled={state.busy}
                    onChange={(value) =>
                      controller.configure({
                        temperature: Math.min(1, Math.max(0, Number(value))),
                      })
                    }
                  />
                </section>
              </AssistantScrollArea>
            </Panel>
          )}
          <Panel
            className="playground-conversation"
            title="Conversation"
            actions={
              <>
                {state.seconds != null ? (
                  <Badge>{state.seconds.toFixed(2)} s</Badge>
                ) : state.busy ? (
                  <span role="status">Generating…</span>
                ) : null}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => controller.reset()}
                >
                  Reset conversation
                </Button>
              </>
            }
          >
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
                      <article className="playground-message text-body-regular">
                        <strong className="text-caption-1-semibold">
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
            <div className="playground-composer-dock">
              <form className="playground-composer" onSubmit={submit}>
                <Textarea
                  aria-label="Message your model"
                  fieldClassName="playground-prompt-field"
                  rows={2}
                  autoResize
                  maxRows={6}
                  value={state.draft}
                  onChange={(draft) => controller.configure({ draft })}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      void controller.send();
                    }
                  }}
                  placeholder="Try a question or paste a prompt…"
                />
                <div className="playground-composer-toolbar" ref={composerToolbar}>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => setShowSettings((value) => !value)}
                    aria-expanded={showSettings}
                    aria-controls="playground-inspector"
                  >
                    {showSettings ? "Hide settings" : "Settings"}
                  </Button>
                  <div className="playground-composer-send">
                    <Select
                      aria-label="Model"
                      className="playground-model-select"
                      size="sm"
                      selectedKey={state.model || null}
                      isDisabled={state.busy || models.loading}
                      placeholder={
                        models.loading
                          ? "Loading models…"
                          : options.length
                            ? "Choose a model"
                            : "No models available"
                      }
                      onSelectionChange={(key) => {
                        if (key != null) controller.selectModel(String(key));
                      }}
                    >
                      {options.map((model) => (
                        <SelectItem
                          key={model.id}
                          id={model.id}
                          textValue={model.alias || model.id}
                        >
                          <span>
                            {model.alias || model.id}
                            {model.status === "up" ? " ●" : ""}
                          </span>
                        </SelectItem>
                      ))}
                    </Select>
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
                  </div>
                </div>
              </form>
              <div
                className="playground-context text-caption-1-regular"
                aria-label="Active Playground context"
              >
                {state.system.trim() && <span>System instructions active</span>}
                {state.repositories.map((repository) => (
                  <span key={repository}>{repository}</span>
                ))}
                <small>Temperature {state.temperature}</small>
                <small className="playground-keyboard-hint">
                  Enter to send · Shift + Enter for a new line
                </small>
              </div>
            </div>
          </Panel>
        </div>
      </ConversationTemplate>
    </Page>
  );
}
