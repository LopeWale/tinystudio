// Owns synchronization scheduling only. The consumer owns history reconciliation.
export class ConversationSync {
  #scope;
  #check;
  #onStatus;
  #window;
  #document;
  #navigator;
  #Channel;
  #clock;
  #visibleInterval;
  #hiddenInterval;
  #maxBackoff;
  #timeout;
  #active = false;
  #generation = 0;
  #timer = null;
  #flight = null;
  #trailing = false;
  #failures = 0;
  #retryAt = 0;
  #channel = null;
  #listeners = [];
  #revisions = new Set();
  #latestRevision;

  constructor({
    scope,
    check,
    onStatus = () => {},
    window = globalThis.window,
    document = globalThis.document,
    navigator = globalThis.navigator,
    BroadcastChannel = globalThis.BroadcastChannel,
    scheduler = {},
    visibleInterval = 3000,
    hiddenInterval = 15000,
    maxBackoff = 30000,
    timeout = 10000,
  }) {
    if (typeof scope !== "string" || !scope || typeof check !== "function") {
      throw new TypeError(
        "ConversationSync requires a scope and check callback",
      );
    }
    for (const value of [
      visibleInterval,
      hiddenInterval,
      maxBackoff,
      timeout,
    ]) {
      if (!Number.isFinite(value) || value <= 0)
        throw new TypeError("Sync intervals must be positive");
    }
    this.#scope = scope;
    this.#check = check;
    this.#onStatus = onStatus;
    this.#window = window;
    this.#document = document;
    this.#navigator = navigator;
    this.#Channel = BroadcastChannel;
    this.#clock = {
      setTimeout:
        scheduler.setTimeout?.bind(scheduler) ??
        globalThis.setTimeout.bind(globalThis),
      clearTimeout:
        scheduler.clearTimeout?.bind(scheduler) ??
        globalThis.clearTimeout.bind(globalThis),
      now: scheduler.now?.bind(scheduler) ?? Date.now,
    };
    this.#visibleInterval = visibleInterval;
    this.#hiddenInterval = hiddenInterval;
    this.#maxBackoff = maxBackoff;
    this.#timeout = timeout;
  }

  start() {
    if (this.#active) return;
    this.#active = true;
    this.#generation++;
    const listen = (target, event, handler) => {
      if (!target?.addEventListener) return;
      target.addEventListener(event, handler);
      this.#listeners.push(() => target.removeEventListener(event, handler));
    };
    for (const event of ["focus", "online", "pageshow"]) {
      listen(this.#window, event, () => this.requestCheck());
    }
    listen(this.#window, "offline", () => {
      this.#clearTimer();
      this.#status({ state: "offline" });
    });
    listen(this.#document, "visibilitychange", () => {
      if (this.#document.visibilityState === "hidden")
        this.#schedule(this.#hiddenInterval);
      else this.requestCheck();
    });
    try {
      if (this.#Channel) {
        this.#channel = new this.#Channel("tinystudio-conversation-sync");
        this.#channel.onmessage = ({ data }) => {
          if (data?.scope === this.#scope && this.#remember(data.revision))
            this.requestCheck();
        };
      }
    } catch (_) {
      this.#channel = null;
    } // Polling still works in restricted browsers.
    this.requestCheck();
  }

  stop() {
    this.#active = false;
    this.#generation++;
    this.#clearTimer();
    if (this.#flight) {
      this.#clock.clearTimeout(this.#flight.timer);
      this.#flight.controller.abort();
      this.#flight.cancel();
      this.#flight = null;
    }
    for (const remove of this.#listeners) remove();
    this.#listeners = [];
    if (this.#channel) {
      this.#channel.onmessage = null;
      this.#channel.close();
      this.#channel = null;
    }
    this.#trailing = false;
    this.#failures = 0;
    this.#retryAt = 0;
  }

  // Call only after an actual write acknowledgement, never after a remote GET.
  notifySaved(revision) {
    if (!this.#active || !this.#remember(revision)) return;
    try {
      this.#channel?.postMessage({ scope: this.#scope, revision });
    } catch (_) {}
  }

  requestCheck() {
    if (!this.#active) return;
    if (this.#navigator?.onLine === false) {
      this.#clearTimer();
      this.#status({ state: "offline" });
      return;
    }
    if (this.#flight) {
      this.#trailing = true;
      return;
    }
    // Wake events coalesce in a zero-delay timer; repeated events retain backoff.
    this.#schedule(0);
  }

  #remember(revision) {
    if (!(
      typeof revision === "string" ||
      (typeof revision === "number" && Number.isFinite(revision))
    ))
      return false;
    if (this.#revisions.has(revision)) return false;
    if (
      typeof revision === "number" &&
      typeof this.#latestRevision === "number" &&
      revision <= this.#latestRevision
    )
      return false;
    this.#latestRevision = revision;
    this.#revisions.add(revision);
    if (this.#revisions.size > 64)
      this.#revisions.delete(this.#revisions.values().next().value);
    return true;
  }

  #status(status) {
    if (this.#active) {
      try {
        this.#onStatus(status);
      } catch (_) {} // UI failures cannot stop scheduling.
    }
  }

  #clearTimer() {
    if (this.#timer !== null) this.#clock.clearTimeout(this.#timer);
    this.#timer = null;
  }

  #schedule(delay) {
    this.#clearTimer();
    if (!this.#active || this.#navigator?.onLine === false || this.#flight)
      return;
    const wait = Math.max(delay, this.#retryAt - this.#clock.now(), 0);
    this.#timer = this.#clock.setTimeout(() => {
      this.#timer = null;
      void this.#run();
    }, wait);
  }

  async #run() {
    if (!this.#active || this.#flight) return;
    if (this.#navigator?.onLine === false) {
      this.requestCheck();
      return;
    }
    const generation = this.#generation;
    const controller = new AbortController();
    let rejectDeadline;
    const deadline = new Promise((_, reject) => {
      rejectDeadline = reject;
    });
    const flight = {
      controller,
      cancel: () => rejectDeadline(new Error("Conversation sync stopped")),
      timer: null,
    };
    this.#flight = flight;
    flight.timer = this.#clock.setTimeout(() => {
      const error = new Error("Conversation sync timed out");
      error.name = "TimeoutError";
      // Reject the deadline first so synchronous abort handlers cannot mask timeout.
      rejectDeadline(error);
      controller.abort();
    }, this.#timeout);
    this.#status({ state: "syncing" });
    try {
      const result = await Promise.race([
        Promise.resolve().then(() => {
          if (controller.signal.aborted)
            throw new Error("Conversation sync stopped");
          return this.#check({ signal: controller.signal });
        }),
        deadline,
      ]);
      if (!this.#active || generation !== this.#generation) return;
      this.#remember(result?.revision ?? result);
      this.#failures = 0;
      this.#retryAt = 0;
      this.#status({ state: "idle" });
    } catch (error) {
      if (!this.#active || generation !== this.#generation) return;
      this.#failures++;
      this.#retryAt =
        this.#clock.now() +
        Math.min(
          this.#maxBackoff,
          this.#visibleInterval * 2 ** Math.min(this.#failures - 1, 20),
        );
      this.#status(
        this.#navigator?.onLine === false
          ? { state: "offline" }
          : { state: "error", error },
      );
    } finally {
      this.#clock.clearTimeout(flight.timer);
      if (this.#flight !== flight) return;
      this.#flight = null;
      if (this.#active && generation === this.#generation) {
        const delay = this.#trailing
          ? 0
          : this.#document?.visibilityState === "hidden"
            ? this.#hiddenInterval
            : this.#visibleInterval;
        this.#trailing = false;
        this.#schedule(delay);
      }
    }
  }
}
