/** Owns request lifetimes for one account/project. Page reads separately own route cancellation. Identity changes revoke results,
 * even when a transport ignores AbortSignal. Mutations may have reached the server. */
export class WorkspaceScope {
  private identity = "";
  private generation = 0;
  private pending = new Set<AbortController>();
  setIdentity(identity: string) {
    if (identity !== this.identity) {
      this.identity = identity;
      this.invalidate();
      return true;
    }
    return false;
  }
  invalidate() {
    this.generation++;
    for (const controller of this.pending) controller.abort();
    this.pending.clear();
  }
  capture(signal?: AbortSignal) {
    const generation = this.generation,
      controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
    this.pending.add(controller);
    return {
      signal: controller.signal,
      assert: () => {
        if (controller.signal.aborted || generation !== this.generation)
          throw new DOMException(
            "Workspace changed; response discarded",
            "AbortError",
          );
      },
      release: () => {
        this.pending.delete(controller);
        signal?.removeEventListener("abort", abort);
      },
    };
  }
}
