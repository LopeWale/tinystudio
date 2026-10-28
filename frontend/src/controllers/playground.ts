interface Message {
  role: "user" | "assistant" | "system";
  content: string;
  chunks?: any[];
}
interface PlaygroundState {
  model: string;
  system: string;
  temperature: number;
  repositories: string[];
  repoLimit: number;
  repoThreshold: number;
  history: Message[];
  busy: boolean;
  error: string;
  seconds: number | null;
  draft: string;
}
interface PlaygroundScope {
  project: string;
  user: string;
  onUnauthorized: () => void;
  fetcher?: typeof fetch;
}
/** One mutable conversation and stream owner. React observes immutable snapshots;
 * deactivate revokes all stream callbacks, including non-cooperative transports. */
export class PlaygroundController {
  private state: PlaygroundState = {
    model: "",
    system: "",
    temperature: 0.2,
    repositories: [],
    repoLimit: 3,
    repoThreshold: 0,
    history: [],
    busy: false,
    error: "",
    seconds: null,
    draft: "",
  };
  private listeners = new Set<() => void>();
  private active = false;
  private generation = 0;
  private turn: AbortController | null = null;
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  constructor(private scope: PlaygroundScope) {}
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  getSnapshot = () => this.state;
  private update(patch: Partial<PlaygroundState>) {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }
  activate() {
    this.active = true;
  }
  deactivate() {
    this.active = false;
    this.cancel();
  }
  configure(
    patch: Partial<
      Pick<
        PlaygroundState,
        | "model"
        | "system"
        | "temperature"
        | "repositories"
        | "repoLimit"
        | "repoThreshold"
        | "draft"
      >
    >,
  ) {
    if (this.state.busy && Object.keys(patch).some((k) => k !== "draft"))
      return;
    this.update(patch);
  }
  selectModel(model: string) {
    if (this.state.model !== model) {
      this.reset();
      this.configure({ model });
    }
  }
  cancel() {
    this.generation++;
    this.turn?.abort();
    this.turn = null;
    void this.reader?.cancel().catch(() => {});
    this.reader = null;
    if (this.state.busy)
      this.update({ busy: false, history: this.state.history.slice(0, -2) });
  }
  reset() {
    this.cancel();
    this.update({ history: [], error: "", seconds: null, draft: "" });
  }
  async send() {
    const text = this.state.draft.trim();
    if (
      !this.active ||
      this.state.busy ||
      !text ||
      !this.state.model ||
      !this.scope.project
    )
      return;
    const previous = this.state.history,
      controller = new AbortController(),
      generation = ++this.generation;
    this.turn = controller;
    const current = () =>
      this.active &&
      this.turn === controller &&
      !controller.signal.aborted &&
      generation === this.generation;
    const config = this.state,
      messages: Message[] = [
        ...(config.system.trim()
          ? [{ role: "system" as const, content: config.system.trim() }]
          : []),
        ...previous.map(({ role, content }) => ({ role, content })),
        { role: "user", content: text },
      ];
    const user: Message = { role: "user", content: text };
    let reply: Message = { role: "assistant", content: "" };
    this.update({
      history: [...previous, user, reply],
      busy: true,
      error: "",
      seconds: null,
      draft: "",
    });
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    try {
      const response = await (this.scope.fetcher ?? fetch)(
        `/api/p/${encodeURIComponent(this.scope.project)}/playground/chat`,
        {
          method: "POST",
          credentials: "same-origin",
          signal: controller.signal,
          headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "tinystudio",
            "X-Conversation-Owner": this.scope.user,
          },
          body: JSON.stringify({
            model: config.model,
            messages,
            temperature: config.temperature,
            max_tokens: 512,
            repositories: config.repositories.length
              ? {
                  ids: config.repositories,
                  limit: config.repoLimit,
                  similarity_threshold: config.repoThreshold,
                }
              : null,
          }),
        },
      );
      if (!current()) return;
      if (!response.ok) {
        if (response.status === 401) this.scope.onUnauthorized();
        let detail = response.statusText;
        try {
          detail = (await response.json()).detail ?? detail;
        } catch {}
        throw new Error(detail || `Request failed (${response.status})`);
      }
      if (!response.body)
        throw new Error("The inference server returned no response stream.");
      reader = response.body.getReader();
      this.reader = reader;
      const decoder = new TextDecoder();
      let buffer = "",
        completed = false;
      const apply = (frame: string) => {
        const data = frame
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (!data) return;
        const event = JSON.parse(data);
        if (event.error) throw new Error(String(event.error));
        if (event.document_chunks)
          reply = { ...reply, chunks: event.document_chunks };
        if (typeof event.delta === "string")
          reply = { ...reply, content: reply.content + event.delta };
        if (event.done) {
          completed = true;
          this.update({
            seconds: typeof event.seconds === "number" ? event.seconds : null,
          });
        }
        this.update({ history: [...previous, user, reply] });
      };
      for (;;) {
        const chunk = await reader.read();
        if (!current()) return;
        if (chunk.done) {
          buffer += decoder.decode();
          if (buffer.trim()) apply(buffer);
          break;
        }
        buffer += decoder.decode(chunk.value, { stream: true });
        let match: RegExpExecArray | null;
        while ((match = /\r?\n\r?\n/.exec(buffer))) {
          const frame = buffer.slice(0, match.index);
          buffer = buffer.slice(match.index + match[0].length);
          apply(frame);
        }
      }
      if (!completed)
        throw new Error(
          "The response stream ended before completion. Please retry.",
        );
    } catch (error: any) {
      try {
        await reader?.cancel();
      } catch {}
      if (current())
        this.update({
          history: previous,
          error: error.message || "Inference failed",
          draft: this.state.draft || text,
        });
    } finally {
      try {
        reader?.releaseLock();
      } catch {}
      if (this.turn === controller) {
        this.turn = null;
        this.reader = null;
        if (this.active) this.update({ busy: false });
      }
    }
  }
}
