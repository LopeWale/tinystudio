import type { ApiClient } from "../api/client";
import { sanitizeEvidence } from "../app/evidence";
import { ConversationSync } from "./conversation-sync.js";
export type DeepReadonly<T> = T extends (...args: any[]) => unknown
  ? T
  : T extends readonly (infer Item)[]
    ? readonly DeepReadonly<Item>[]
    : T extends object
      ? { readonly [Key in keyof T]: DeepReadonly<T[Key]> }
      : T;
export interface Attachment {
  id: string;
  name: string;
  size: number;
  sha256: string;
  project_id: string;
  user_id: string;
  extracted_chars: number;
  retained_chars: number;
  status: "ready";
}
export interface Source {
  url: string;
  title: string;
}
export interface ToolCall {
  id: string;
  name: string;
  input?: unknown;
  kind?: "ui" | "tool";
  output?: unknown;
}
export interface Message {
  role: "user" | "assistant";
  content: string;
  calls?: readonly ToolCall[];
  attachments?: readonly Attachment[];
  sources?: readonly Source[];
  selected_data?: unknown;
  searches?: readonly {
    id?: string;
    state: string;
    query?: string;
    error?: string;
  }[];
}
export interface ConversationSnapshot {
  revision: number;
  history: readonly Message[];
  provider: string;
  model: string;
}
export interface ContextSelection {
  id?: string;
  label?: string;
  route: string;
  project: string;
  user: string;
  data: unknown;
  source_route?: string;
  [key: string]: unknown;
}
export type ActivityStep =
  | { type: "ui_request"; id: string; action: string; args?: unknown }
  | { type: "tool_call"; id: string; name: string; input?: unknown }
  | { type: "tool_result"; id: string; name?: string; output?: unknown }
  | {
      type: "search";
      id?: string;
      state: string;
      query?: string;
      error?: string;
    };
export interface ConversationState extends ConversationSnapshot {
  loaded: boolean;
  busy: boolean;
  startedAt?: number;
  usage?: { model: string; input_tokens: number; output_tokens: number };
  dirty: boolean;
  conflict: boolean;
  pending?: boolean;
  draft: string;
  error: string;
  executionError: string;
  sync: string;
  steps: readonly ActivityStep[];
  attachments: readonly Attachment[];
  selection: ContextSelection | null;
  webSearch: boolean;
  allowActions: boolean;
}
export interface PageContext {
  route: string;
  project: string;
  user: string;
  title?: string;
  text?: string;
  selection?: string;
  api?: Array<{ path: string; data: unknown; record_id?: string }>;
  selected_data?: unknown;
  omissions?: string[];
}
export interface ConversationDependencies {
  api: ApiClient;
  project: string;
  user: string;
  fetcher?: typeof fetch;
  captureContext?: () => PageContext;
  onUi?: (action: string, args: any, allowed: boolean) => Promise<any>;
  validateSelection?: (selection: any) => boolean;
  syncFactory?: (
    options: any,
  ) => Pick<
    ConversationSync,
    "start" | "stop" | "requestCheck" | "notifySaved"
  >;
}
/** One owner for durable revisions, draft, pending writes and a live turn in this identity scope. */
export class ConversationController {
  private ownedObjects = new WeakSet<object>();
  private state: DeepReadonly<ConversationState> = this.own<ConversationState>({
    revision: 0,
    history: [],
    provider: "anthropic",
    model: "",
    loaded: false,
    busy: false,
    dirty: false,
    conflict: false,
    draft: "",
    error: "",
    executionError: "",
    sync: "idle",
    steps: [],
    attachments: [],
    selection: null,
    webSearch: false,
    allowActions: false,
  });
  private listeners = new Set<() => void>();
  private lifecycle?: AbortController;
  private turn?: AbortController;
  private generation = 0;
  private preferenceVersion = 0;
  private savedPreferenceVersion = 0;
  private sync?: ReturnType<
    NonNullable<ConversationDependencies["syncFactory"]>
  >;
  private queue = Promise.resolve();
  constructor(private dependencies: ConversationDependencies) {}
  getSnapshot = (): DeepReadonly<ConversationState> => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  /** Copy external inputs once; share only objects already recursively owned and frozen. */
  private own<T>(value: T): DeepReadonly<T> {
    if (value === null || typeof value !== "object")
      return value as DeepReadonly<T>;
    if (this.ownedObjects.has(value)) return value as DeepReadonly<T>;
    const copy = Array.isArray(value)
      ? value.map((item) => this.own(item))
      : Object.fromEntries(
          Object.entries(value).map(([key, item]) => [key, this.own(item)]),
        );
    this.ownedObjects.add(copy);
    return Object.freeze(copy) as DeepReadonly<T>;
  }
  private patch(update: Partial<DeepReadonly<ConversationState>>) {
    this.state = this.own({ ...this.state, ...update });
    for (const listener of this.listeners) listener();
  }
  private get path() {
    return `/api/p/${encodeURIComponent(this.dependencies.project)}/assistant/conversation`;
  }
  start() {
    if (this.lifecycle) return;
    this.lifecycle = new AbortController();
    const generation = ++this.generation;
    this.sync = (
      this.dependencies.syncFactory ??
      ((options) => new ConversationSync(options))
    )({
      scope: `${this.dependencies.user}:${this.dependencies.project}`,
      check: async ({ signal }: any) => {
        await this.refresh(false, signal);
        return this.state.revision;
      },
      onStatus: ({ state }: any) => {
        if (generation === this.generation) this.patch({ sync: state });
      },
    });
    this.sync.start();
    void this.refresh().catch(() => {});
  }
  stop() {
    ++this.generation;
    this.lifecycle?.abort();
    this.turn?.abort();
    this.lifecycle = undefined;
    this.sync?.stop();
    this.sync = undefined;
    this.turn = undefined;
    if (this.state.busy)
      this.patch({
        busy: false,
        error: "Reply interrupted. Reload to check the server-saved result.",
        dirty: true,
        pending: true,
      });
  }
  setDraft(draft: string) {
    this.patch({ draft });
  }
  setOptions(
    options: Partial<
      Pick<
        ConversationState,
        "provider" | "model" | "webSearch" | "allowActions" | "selection"
      >
    >,
  ) {
    if (
      (options.provider !== undefined &&
        options.provider !== this.state.provider) ||
      (options.model !== undefined && options.model !== this.state.model)
    )
      this.preferenceVersion++;
    this.patch(options);
  }
  setAttachments(attachments: readonly Attachment[]) {
    this.patch({ attachments });
  }
  private enqueue<T>(work: () => Promise<T>): Promise<T> {
    const pending = this.queue.catch(() => {}).then(work);
    // The private tail must always settle; callers still receive pending's
    // rejection, without a second unobserved rejection from queue bookkeeping.
    this.queue = pending.then(
      () => {},
      () => {},
    );
    return pending;
  }
  async refresh(discard = false, signal?: AbortSignal) {
    const generation = this.generation,
      requestSignal = signal ?? this.lifecycle?.signal;
    return this.enqueue(async () => {
      if (generation !== this.generation || requestSignal?.aborted) return;
      if (
        this.state.busy ||
        (this.state.dirty && !discard && !this.state.pending)
      )
        return;
      try {
        const snapshot = await this.dependencies.api.get<ConversationSnapshot>(
          this.path,
          requestSignal,
        );
        if (generation !== this.generation || signal?.aborted) return;
        if (
          this.state.pending &&
          !discard &&
          snapshot.revision <= this.state.revision
        )
          return;
        this.patch({
          ...snapshot,
          provider:
            this.preferenceVersion === this.savedPreferenceVersion
              ? snapshot.provider || this.state.provider
              : this.state.provider,
          model:
            this.preferenceVersion === this.savedPreferenceVersion
              ? snapshot.model
              : this.state.model,
          loaded: true,
          error: "",
          dirty: false,
          conflict: false,
          pending: false,
        });
      } catch (error) {
        if (generation === this.generation && !signal?.aborted)
          this.patch({ error: (error as Error).message });
        throw error;
      }
    });
  }
  async save() {
    const generation = this.generation,
      signal = this.lifecycle?.signal;
    return this.enqueue(async () => {
      if (generation !== this.generation || signal?.aborted) return;
      if (this.state.conflict)
        throw new Error("Reload the newer database conversation before saving");
      const { revision, history, provider, model } = this.state;
      const preferenceVersion = this.preferenceVersion;
      this.patch({ dirty: true });
      try {
        const saved = await this.dependencies.api.put<ConversationSnapshot>(
          this.path,
          { revision, history, provider, model },
          signal,
        );
        if (generation !== this.generation) return;
        if (preferenceVersion === this.preferenceVersion)
          this.savedPreferenceVersion = preferenceVersion;
        this.patch({
          ...saved,
          provider: this.state.provider,
          model: this.state.model,
          dirty: false,
          error: "",
        });
        this.sync?.notifySaved(saved.revision);
      } catch (error) {
        if (generation === this.generation)
          this.patch({
            error: (error as Error).message,
            conflict: (error as any).status === 409,
          });
        throw error;
      }
    });
  }
  async clear() {
    if (this.state.busy)
      throw new Error("Stop the active turn before clearing");
    this.patch({ history: [] });
    await this.save();
  }
  cancel() {
    this.turn?.abort();
  }
  async send() {
    if (
      !this.state.draft.trim() ||
      !this.state.loaded ||
      this.state.busy ||
      this.state.dirty ||
      this.state.conflict
    )
      return;
    const generation = this.generation,
      text = this.state.draft.trim(),
      selection = this.state.selection;
    const context = structuredClone(
      this.dependencies.captureContext?.() ?? {
        route: location.hash.replace(/^#/, "") || "/",
        project: this.dependencies.project,
        user: this.dependencies.user,
        api: [],
        omissions: ["No committed page-context provider was available."],
      },
    );
    if (
      context.project !== this.dependencies.project ||
      context.user !== this.dependencies.user
    )
      throw new Error(
        "Page context belongs to another account or project; wait for the current page to render.",
      );
    if (
      selection &&
      (selection.project !== this.dependencies.project ||
        String(selection.user) !== this.dependencies.user ||
        selection.route.replace(/^#/, "") !== context.route.replace(/^#/, "") ||
        this.dependencies.validateSelection?.(selection) === false)
    )
      throw new Error(
        "Picked data is stale. Pick it again on the current page.",
      );
    if (selection) context.selected_data = sanitizeEvidence(selection);
    const capturedContext = sanitizeEvidence(context) as PageContext;
    const attachments = structuredClone(this.state.attachments),
      turnOptions = {
        allowActions: this.state.allowActions,
        webSearch: this.state.webSearch,
      };
    await this.refresh();
    if (
      generation !== this.generation ||
      this.state.error ||
      this.state.busy ||
      this.state.dirty
    )
      return;
    // Refresh adopts clean remote preferences and preserves explicit local edits.
    // Resolve the model here; capturing it earlier would undo that reconciliation.
    const preferences = {
      ...turnOptions,
      provider: this.state.provider,
      model: this.state.model,
    };
    const user: Message = {
      role: "user",
      content: text,
      ...(attachments.length ? { attachments } : {}),
      ...(selection ? { selected_data: capturedContext.selected_data } : {}),
    };
    // Validate the frozen provider request before the durable user write.
    const outgoing = prepareRequest({
      provider: preferences.provider,
      model: preferences.model || null,
      project: this.dependencies.project,
      conversation_revision: this.state.revision + 1,
      allow_destructive: preferences.allowActions,
      with_ui: true,
      web_search: preferences.webSearch,
      attachment_ids: attachments.map((item) => item.id),
      context: capturedContext,
      messages: [...this.state.history, user].map(({ role, content }) => ({
        role,
        content,
      })),
    });
    this.patch({
      ...preferences,
      history: [...this.state.history, user],
      busy: true,
      startedAt: undefined,
      steps: [],
      error: "",
      executionError: "",
    });
    try {
      await this.save();
    } catch {
      this.patch({ busy: false });
      return;
    }
    if (generation !== this.generation) return;
    outgoing.conversation_revision = this.state.revision;
    const body = outgoing;
    const controller = (this.turn = new AbortController());
    let reply: Message = {
      role: "assistant",
      content: "",
      calls: [],
      sources: [],
      searches: [],
    };
    this.patch({
      draft: this.state.draft.trim() === text ? "" : this.state.draft,
      attachments: this.state.attachments.filter(
        (item) => !attachments.some((sent) => sent.id === item.id),
      ),
      selection:
        this.state.selection === selection ? null : this.state.selection,
      allowActions: false,
      startedAt: Date.now(),
      history: [...this.state.history, reply],
      dirty: true,
    });
    const current = () =>
      generation === this.generation && this.turn === controller;
    const event = async (ev: any) => {
      if (!current()) return;
      if (ev.type === "done")
        this.patch({
          usage: {
            model: ev.model,
            input_tokens: ev.usage?.input_tokens ?? 0,
            output_tokens: ev.usage?.output_tokens ?? 0,
          },
        });
      if (ev.type === "text")
        reply = { ...reply, content: reply.content + ev.delta };
      if (ev.type === "tool_call")
        reply = {
          ...reply,
          calls: [
            ...reply.calls!,
            {
              id: ev.id,
              name: ev.name,
              input: ev.input,
              kind: ev.name.startsWith("ui_") ? "ui" : "tool",
            },
          ],
        };
      if (ev.type === "tool_result")
        reply = {
          ...reply,
          calls: reply.calls!.map((call) =>
            call.id === ev.id ? { ...call, output: ev.output } : call,
          ),
        };
      if (ev.type === "search" && reply.searches!.length < 128)
        reply = {
          ...reply,
          searches: [
            ...reply.searches!,
            { id: ev.id, state: ev.state, query: ev.query, error: ev.error },
          ],
        };
      if (
        ev.type === "source" &&
        /^https?:\/\//.test(ev.url) &&
        ev.url.length <= 4000 &&
        reply.sources!.length < 128
      )
        reply = {
          ...reply,
          sources: [...reply.sources!, { url: ev.url, title: ev.title || "" }],
        };
      if (
        ev.type === "tool_call" ||
        ev.type === "tool_result" ||
        ev.type === "search" ||
        ev.type === "ui_request"
      )
        this.patch({ steps: [...this.state.steps, ev] });
      if (ev.type === "ui_request") {
        let result;
        try {
          result = (await this.dependencies.onUi?.(
            ev.action,
            ev.args || {},
            body.allow_destructive,
          )) ?? { error: "Interface operation unavailable" };
        } catch (error) {
          result = { error: (error as Error).message };
        }
        if (current())
          await this.dependencies.api.post(
            "/api/assistant/ui-result",
            { id: ev.id, result },
            controller.signal,
          );
      }
      if (
        ev.type === "conversation_admitted" ||
        ev.type === "conversation_error"
      ) {
        // A failed final save still owns its admitted CAS base. Do not promote a
        // different writer's revision or accept events from an obsolete turn.
        const admitted = body.conversation_revision + 1;
        if (
          Number.isSafeInteger(ev.revision) &&
          ev.revision === admitted &&
          this.state.revision <= admitted
        )
          this.patch({ revision: admitted });
      }
      if (ev.type === "conversation_saved") {
        this.patch({
          revision: ev.revision,
          dirty: false,
          pending: false,
          conflict: false,
          error: "",
        });
        this.sync?.notifySaved(ev.revision);
      }
      if (ev.type === "conversation_error")
        this.patch({
          error: ev.message,
          conflict: ev.conflict === true,
          dirty: true,
        });
      if (ev.type === "error") this.patch({ executionError: ev.message });
      if (current()) {
        this.patch({ history: [...this.state.history.slice(0, -1), reply] });
        // Reuse the newly owned reply's frozen subtrees on the next delta.
        reply = this.state.history.at(-1)!;
      }
    };
    try {
      const response = await (this.dependencies.fetcher ?? fetch)(
        "/api/assistant/chat",
        {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "tinystudio",
            "X-Conversation-Owner": this.dependencies.user,
          },
          body: JSON.stringify(outgoing),
          signal: controller.signal,
        },
      );
      if (!response.ok) {
        const detail = await response.json();
        throw new Error(
          detail.detail || `Assistant failed (${response.status})`,
        );
      }
      if (!response.body) throw new Error("No assistant stream");
      await readEvents(response.body, event, controller.signal);
    } catch (error) {
      if (current())
        this.patch({
          pending: controller.signal.aborted,
          executionError: controller.signal.aborted
            ? ""
            : (error as Error).message,
          error: controller.signal.aborted
            ? "Stopped listening. The server may still finish and save this reply. Reload to check."
            : (error as Error).message,
        });
    } finally {
      if (current()) {
        this.turn = undefined;
        this.patch({
          busy: false,
          error:
            this.state.dirty && !this.state.error
              ? "Reply has not been confirmed saved. Reload to check the database."
              : this.state.error,
        });
        this.sync?.requestCheck();
      }
    }
  }
}
const requestBytes = (value: unknown) =>
  new TextEncoder().encode(JSON.stringify(value)).length;
/** Bounded copies keep explicit omission markers; stored history and exact picked records stay unchanged. */
export function boundedContextData(value: unknown, maxBytes: number): unknown {
  const safe = sanitizeEvidence(value);
  if (requestBytes(safe) <= maxBytes) return safe;
  let characters = 4000,
    items = 40;
  const compact = (input: any, depth = 0): any => {
    if (typeof input === "string")
      return input.length > characters
        ? input.slice(0, characters) +
            ` [omitted: ${input.length - characters} characters]`
        : input;
    if (depth > 8) return "[omitted: nested data beyond depth 8]";
    if (Array.isArray(input))
      return input
        .slice(0, items)
        .map((entry) => compact(entry, depth + 1))
        .concat(
          input.length > items
            ? [{ omission: `${input.length - items} additional items omitted` }]
            : [],
        );
    if (input && typeof input === "object") {
      const entries = Object.entries(input),
        output: any = Object.fromEntries(
          entries
            .slice(0, items)
            .map(([key, entry]) => [key, compact(entry, depth + 1)]),
        );
      if (entries.length > items) {
        let key = "__omitted_fields__";
        while (key in output) key += "_";
        output[key] = `${entries.length - items} additional fields omitted`;
      }
      return output;
    }
    return input;
  };
  for (;;) {
    const compacted = compact(safe);
    if (requestBytes(compacted) <= maxBytes) return compacted;
    if (characters === 16 && items === 1)
      return {
        omission:
          "Source exceeds this context budget; select a narrower record.",
      };
    characters = Math.max(16, Math.floor(characters / 2));
    items = Math.max(1, Math.floor(items / 2));
  }
}
export function prepareRequest(body: any) {
  const outgoing = structuredClone(body);
  if (requestBytes(outgoing.messages.at(-1)) > 60000)
    throw new Error("Message exceeds 60 KB; shorten it before sending");
  if (outgoing.context) {
    const source = outgoing.context,
      omissions = [...(source.omissions || [])];
    const context: any = { ...source, api: [], omissions };
    if (source.selected_data !== undefined) {
      context.selected_data = boundedContextData(source.selected_data, 100000);
      if (
        requestBytes(context.selected_data) < requestBytes(source.selected_data)
      )
        omissions.push(
          "Picked evidence was compacted for this request; omission markers identify removed data. Stored picked evidence is unchanged.",
        );
    }
    for (const record of source.api || []) {
      const retained = {
        ...record,
        data: boundedContextData(record.data, 12000),
      };
      if (requestBytes(context.api) + requestBytes(retained) > 60000) {
        omissions.push(
          `API source omitted from this request for size: ${record.path}`,
        );
        continue;
      }
      context.api.push(retained);
      if (requestBytes(retained.data) < requestBytes(record.data))
        omissions.push(`API source compacted for size: ${record.path}`);
    }
    for (const [field, limit] of [
      ["text", 8000],
      ["selection", 2000],
      ["title", 500],
    ] as const)
      if (typeof context[field] === "string" && context[field].length > limit) {
        const length = context[field].length;
        context[field] =
          context[field].slice(0, limit) +
          ` [omitted: ${length - limit} characters]`;
        omissions.push(`${field} was shortened for the request budget.`);
      }
    outgoing.context = context;
  }
  let omitted = 0;
  while (requestBytes(outgoing) > 235000 && outgoing.messages.length > 1) {
    outgoing.messages.shift();
    omitted++;
  }
  if (omitted) {
    outgoing.context ||= {};
    outgoing.context.omissions = [
      ...(outgoing.context.omissions || []),
      `${omitted} older messages omitted from this request. Stored history is unchanged.`,
    ];
  }
  if (requestBytes(outgoing) > 240000)
    throw new Error("Context exceeds the request budget; pick narrower data");
  return outgoing;
}

export async function readEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: any) => Promise<void> | void,
  signal: AbortSignal,
) {
  const reader = body.getReader(),
    decoder = new TextDecoder();
  const abort = () => {
    void reader.cancel().catch(() => {});
  };
  signal.addEventListener("abort", abort, { once: true });
  let buffer = "";
  try {
    for (;;) {
      if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
      const { done, value } = await reader.read();
      if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let match;
      while ((match = buffer.match(/\r?\n\r?\n/))) {
        const index = match.index!,
          frame = buffer.slice(0, index);
        buffer = buffer.slice(index + match[0].length);
        const data = frame
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data) {
          const event = JSON.parse(data);
          if (event.type !== "keepalive") await onEvent(event);
        }
      }
    }
  } finally {
    signal.removeEventListener("abort", abort);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
