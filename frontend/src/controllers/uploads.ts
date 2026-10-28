export type UploadPhase =
  | "selected"
  | "uploading"
  | "importing"
  | "indexing"
  | "ready"
  | "failed"
  | "cancelled";
export interface UploadProgress {
  loaded: number;
  total?: number;
}
export function validateFiles(files: File[], accept = "", maxBytes?: number) {
  const rules = accept
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
  return files.map((file) => ({
    file,
    error:
      maxBytes !== undefined && file.size > maxBytes
        ? `${file.name} exceeds the ${maxBytes.toLocaleString()} byte limit`
        : rules.length &&
            !rules.some((rule) =>
              rule.startsWith(".")
                ? file.name.toLowerCase().endsWith(rule)
                : rule.endsWith("/*")
                  ? file.type.startsWith(rule.slice(0, -1))
                  : file.type === rule,
            )
          ? `${file.name} is unsupported; accepted: ${accept}`
          : "",
  }));
}
/** Owns one aggregate transfer. A completed transfer remains importing until its server response. */
export class UploadController<T> {
  private controller?: AbortController;
  private generation = 0;
  constructor(
    private publish: (state: {
      phase: UploadPhase;
      progress?: UploadProgress;
      error?: string;
      result?: T;
    }) => void,
  ) {}
  async run(
    transfer: (
      signal: AbortSignal,
      progress: (value: UploadProgress) => void,
    ) => Promise<T>,
  ) {
    this.cancel(false);
    const generation = ++this.generation;
    const controller = (this.controller = new AbortController());
    this.publish({ phase: "uploading" });
    try {
      const result = await transfer(controller.signal, (progress) => {
        if (generation === this.generation && !controller.signal.aborted)
          this.publish({
            phase:
              progress.total && progress.loaded >= progress.total
                ? "importing"
                : "uploading",
            progress,
          });
      });
      if (generation !== this.generation || controller.signal.aborted) return;
      this.publish({ phase: "ready", result });
      return result;
    } catch (error) {
      if (generation !== this.generation) return;
      this.publish({
        phase: controller.signal.aborted ? "cancelled" : "failed",
        error: controller.signal.aborted
          ? "Transfer stopped. An operation already accepted by the server may continue."
          : String((error as Error).message),
      });
      if (!controller.signal.aborted) throw error;
    } finally {
      if (generation === this.generation) this.controller = undefined;
    }
  }
  cancel(publish = true) {
    if (this.controller) {
      this.controller.abort();
      this.controller = undefined;
      ++this.generation;
      if (publish)
        this.publish({
          phase: "cancelled",
          error:
            "Transfer stopped. An operation already accepted by the server may continue.",
        });
    }
  }
  dispose() {
    this.cancel(false);
  }
}

/** One batch owns its transfer sequence; cancellation and failures retain unsent files. */
export class UploadBatchController<T> {
  private flight?: { cancelled: boolean };
  constructor(private transfer: UploadController<T>) {}
  get active() {
    return this.flight !== undefined;
  }
  async run(
    files: File[],
    upload: (
      file: File,
      signal: AbortSignal,
      progress: (value: UploadProgress) => void,
    ) => Promise<T>,
    accepted: (result: T) => void,
  ) {
    if (this.flight) throw new Error("An upload batch is already active");
    const flight = (this.flight = { cancelled: false }),
      remaining = [...files];
    try {
      for (const file of files) {
        if (flight.cancelled) break;
        let result: T | undefined;
        try {
          result = await this.transfer.run((signal, progress) =>
            upload(file, signal, progress),
          );
        } catch {
          break;
        }
        if (flight.cancelled || result === undefined) break;
        accepted(result);
        remaining.splice(remaining.indexOf(file), 1);
      }
      return { remaining, cancelled: flight.cancelled };
    } finally {
      if (this.flight === flight) this.flight = undefined;
    }
  }
  cancel(publish = true) {
    if (this.flight) this.flight.cancelled = true;
    this.transfer.cancel(publish);
  }
  dispose() {
    this.cancel(false);
  }
}
