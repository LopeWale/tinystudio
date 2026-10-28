// Owns hash navigation and its listener. Page handlers remain feature modules.
export class HashRouter {
  #routes; #location; #window; #beforeRender; #isCurrent; #onError; #onLoading; #listening = false;
  constructor({ routes, beforeRender, isCurrent, onError, onLoading = () => {}, window = globalThis.window, location = globalThis.location }) {
    this.#routes = routes; this.#beforeRender = beforeRender; this.#isCurrent = isCurrent;
    this.#onError = onError; this.#onLoading = onLoading; this.#window = window; this.#location = location;
  }
  go = (hash) => { this.#location.hash = hash; };
  render = async () => {
    const hash = this.#location.hash || "#/";
    const view = this.#beforeRender(hash);
    this.#onLoading(true);
    for (const { pattern, run } of this.#routes) {
      const match = hash.match(pattern);
      if (!match) continue;
      try { await run(match); }
      catch (error) { if (!["StaleRenderError", "AuthenticationRequiredError"].includes(error.name) && this.#isCurrent(view)) this.#onError(error); }
      finally { if (this.#isCurrent(view)) this.#onLoading(false); }
      return;
    }
    this.go("#/datasets");
  };
  start() {
    if (this.#listening) return;
    this.#window.addEventListener("hashchange", this.render); this.#listening = true;
  }
  destroy() { if (this.#listening) this.#window.removeEventListener("hashchange", this.render); this.#listening = false; }
}
