// A stream and its history belong to one account, project and mounted view.
export class PlaygroundSessions {
  #getScope; #fetch; #sessions = new Map(); #scope = null; #view = null; #active = null; #disposed = false;
  constructor({ getScope, fetch }) { this.#getScope = getScope; this.#fetch = fetch; }
  #key(scope) { return `${scope.user ?? ""}:${scope.project ?? ""}`; }
  syncScope() {
    const scope = this.#getScope();
    if (this.#key(scope) === this.#key(this.#scope || {})) return;
    this.detach();
    if (this.#scope && this.#scope.user !== scope.user) this.#sessions.clear();
    this.#scope = { ...scope };
  }
  #session() {
    this.syncScope();
    const key = this.#key(this.#scope || {});
    if (!this.#sessions.has(key)) this.#sessions.set(key, { model: null, system: "", temperature: 0.2, repositories: [], repoLimit: 3, repoThreshold: 0, history: [] });
    return this.#sessions.get(key);
  }
  snapshot() { const session = this.#session(); return { ...session, repositories: [...session.repositories], history: session.history.map((message) => ({ ...message })) }; }
  configure(values) {
    const session = this.#session();
    for (const key of ["model", "system", "temperature", "repositories", "repoLimit", "repoThreshold"]) {
      if (key in values) session[key] = Array.isArray(values[key]) ? [...values[key]] : values[key];
    }
  }
  get busy() { return this.#active !== null; }
  attach(view) { this.detach(); this.syncScope(); this.#view = view; }
  isView(view) { return !this.#disposed && this.#view === view && this.#key(this.#scope || {}) === this.#key(this.#getScope()); }
  #removeTurn(turn) { turn.session.history = turn.session.history.filter((message) => message !== turn.user && message !== turn.reply); }
  cancel() {
    const turn = this.#active;
    if (!turn) return;
    this.#active = null; this.#removeTurn(turn); turn.controller.abort();
    if (turn.reader) Promise.resolve(turn.reader.cancel()).catch(() => {});
  }
  detach() { this.#view = null; this.cancel(); }
  reset() { this.cancel(); this.#session().history = []; }
  async send(text, { view, onUpdate, onLatency } = {}) {
    const session = this.#session();
    if (this.#disposed || !this.isView(view) || this.busy || !text.trim() || !session.model || !this.#scope?.project) return false;
    const turn = { session, view, user: { role: "user", content: text.trim() }, reply: { role: "assistant", content: "" }, controller: new AbortController(), reader: null };
    this.#active = turn;
    const current = () => this.#active === turn && this.isView(view);
    const messages = [...(session.system.trim() ? [{ role: "system", content: session.system.trim() }] : []), ...session.history.map((message) => ({ ...message })), { ...turn.user }];
    const body = JSON.stringify({ model: session.model, messages, temperature: session.temperature, max_tokens: 512, repositories: session.repositories.length ? { ids: [...session.repositories], limit: session.repoLimit, similarity_threshold: session.repoThreshold } : null });
    const endpoint = `/api/p/${encodeURIComponent(this.#scope.project)}/playground/chat`;
    session.history.push(turn.user, turn.reply); onUpdate?.();
    try {
      const response = await this.#fetch(endpoint, { method: "POST", body, signal: turn.controller.signal });
      if (!current()) return false;
      if (!response.ok) { let detail = response.statusText; try { detail = (await response.json()).detail; } catch (_) {} throw new Error(detail); }
      turn.reader = response.body.getReader();
      const decoder = new TextDecoder(); let buffer = "";
      for (;;) {
        const { value, done } = await turn.reader.read();
        if (!current()) return false;
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let index;
        while ((index = buffer.indexOf("\n\n")) >= 0) {
          const line = buffer.slice(0, index).trim(); buffer = buffer.slice(index + 2);
          if (!line.startsWith("data:")) continue;
          const payload = JSON.parse(line.slice(5));
          if (payload.error) throw new Error(payload.error);
          if (payload.document_chunks) turn.reply.content += "Retrieved: " + payload.document_chunks.map((chunk, i) => `[${i + 1}] ${chunk.document} (${chunk.similarity_score.toFixed(2)})`).join(", ") + "\n\n";
          if (payload.delta) turn.reply.content += payload.delta;
          if (payload.document_chunks || payload.delta) onUpdate?.();
          if (payload.done) onLatency?.(payload.seconds);
        }
      }
      return true;
    } catch (error) {
      try { await turn.reader?.cancel(); } catch (_) {}
      this.#removeTurn(turn);
      if (current()) { onUpdate?.(); throw error; }
      return false;
    } finally {
      turn.reader?.releaseLock?.();
      if (this.#active === turn) this.#active = null;
    }
  }
  destroy() { this.detach(); this.#disposed = true; this.#sessions.clear(); }
}
