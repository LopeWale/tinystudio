import { sha256 } from "@noble/hashes/sha2.js";
import type { ApiClient } from "../../api/client";

export interface TransferIntent {
  name: string;
  size: number;
  sha256: string;
  media_type: string;
  role: string;
  visibility: "model" | "evaluation" | "truth";
}
export interface TransferArtifact extends TransferIntent {
  id: string;
}
export interface SavedTransfer {
  format: "tinystudio.browser-artifact-transfer.v1";
  origin: string;
  project: string;
  intent: TransferIntent;
  idempotency_key: string;
  session_id?: string;
  state?: string;
}
export interface TransferProgress {
  phase: "hashing" | "admitting" | "uploading" | "verifying" | "verified";
  loaded: number;
  total: number;
  sessionId?: string;
}
interface Plan {
  size: number;
  part_bytes: number;
  part_count: number;
  zero: boolean;
  mode: string;
}
interface Session {
  id: string;
  state: string;
  intent: TransferIntent;
  plan: Plan;
  operation_state: string | null;
  artifact: unknown;
}
type ResumeStorage = Pick<
  Storage,
  "length" | "key" | "getItem" | "setItem" | "removeItem"
>;
type Selection = Pick<TransferIntent, "role" | "visibility">;
const FORMAT = "tinystudio.browser-artifact-transfer.v1";
const PREFIX = "tinystudio:artifact-transfer:";
const SHA256 = /^[0-9a-f]{64}$/;
const MIN_PART = 5 * 1024 ** 2;
const MAX_PART = 5 * 1024 ** 3 - 5 * 1024 ** 2;
const MAX_OBJECT = 5 * 1024 ** 4 - 5 * 1024 ** 3;
const MAX_PARTS = 10_000;

export class ArtifactTransferError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ArtifactTransferError";
  }
}
function requireValue(
  passed: unknown,
  message = "Transfer response does not match the selected file.",
): asserts passed {
  if (!passed) throw new ArtifactTransferError(message);
}
const object = (value: unknown): value is Record<string, any> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const integer = (value: unknown, minimum = 0): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= minimum;
// A selected upper budget above every representable File size cannot constrain
// these uploads. Keep exact safe integers for sizes/geometry on the wire; do not
// turn a larger host allowance into a new browser cap. The server is authoritative.
const allowance = (value: unknown, minimum = 0): value is number =>
  integer(value, minimum) ||
  (typeof value === "number" && value > Number.MAX_SAFE_INTEGER);
const text = (value: unknown): value is string =>
  typeof value === "string" &&
  value.length > 0 &&
  !/[\u0000-\u001f\u007f]/.test(value);
const segment = (value: unknown): value is string =>
  text(value) && ![".", ".."].includes(value) && !/[\\/\?#]/.test(value);
const sameIntent = (value: unknown, expected: TransferIntent) =>
  object(value) &&
  Object.keys(value).length === Object.keys(expected).length &&
  (Object.keys(expected) as (keyof TransferIntent)[]).every(
    (key) => value[key] === expected[key],
  );
function checkAbort(signal?: AbortSignal) {
  if (signal?.aborted)
    throw new DOMException(
      "Import paused; the transfer remains resumable.",
      "AbortError",
    );
}
function readSlice(blob: Blob, signal?: AbortSignal): Promise<ArrayBuffer> {
  checkAbort(signal);
  if (blob.arrayBuffer) return blob.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    const clean = () => signal?.removeEventListener("abort", abort);
    const abort = () => {
      reader.abort();
      clean();
      reject(new DOMException("Import paused.", "AbortError"));
    };
    signal?.addEventListener("abort", abort, { once: true });
    reader.onload = () => {
      clean();
      resolve(reader.result as ArrayBuffer);
    };
    reader.onerror = () => {
      clean();
      reject(
        new ArtifactTransferError("A selected file slice could not be read."),
      );
    };
    reader.readAsArrayBuffer(blob);
  });
}
/** Linear SHA-256 over bounded slices, using noble's incremental implementation. */
export async function hashFile(
  file: File,
  options: {
    signal?: AbortSignal;
    chunkBytes?: number;
    onProgress?: (loaded: number) => void;
  } = {},
) {
  const chunk = options.chunkBytes ?? 1024 ** 2;
  requireValue(
    integer(file.size) && integer(chunk, 1),
    "Invalid file size or hashing buffer size.",
  );
  const hash = sha256.create();
  try {
    for (let offset = 0; offset < file.size; offset += chunk) {
      checkAbort(options.signal);
      hash.update(
        new Uint8Array(
          await readSlice(
            file.slice(offset, Math.min(file.size, offset + chunk)),
            options.signal,
          ),
        ),
      );
      checkAbort(options.signal);
      options.onProgress?.(Math.min(file.size, offset + chunk));
    }
    checkAbort(options.signal);
    options.onProgress?.(file.size);
    return Array.from(hash.digest(), (value) =>
      value.toString(16).padStart(2, "0"),
    ).join("");
  } finally {
    hash.destroy();
  }
}

function validateIntent(value: unknown): asserts value is TransferIntent {
  requireValue(
    object(value) &&
      text(value.name) &&
      integer(value.size) &&
      typeof value.sha256 === "string" &&
      SHA256.test(value.sha256) &&
      text(value.media_type) &&
      text(value.role) &&
      ["model", "evaluation", "truth"].includes(value.visibility),
  );
  requireValue(
    Object.keys(value).sort().join() ===
      "media_type,name,role,sha256,size,visibility",
  );
}
function validateSession(
  value: unknown,
  intent: TransferIntent,
  id?: string,
): Session {
  requireValue(
    object(value) &&
      segment(value.id) &&
      (!id || value.id === id) &&
      sameIntent(value.intent, intent) &&
      text(value.state),
  );
  const plan = value.plan;
  requireValue(
    object(plan) &&
      integer(plan.size) &&
      plan.size === intent.size &&
      integer(plan.part_bytes, 1) &&
      plan.part_bytes >= MIN_PART &&
      plan.part_bytes <= MAX_PART &&
      plan.size <= MAX_OBJECT &&
      integer(plan.part_count) &&
      plan.part_count <= MAX_PARTS &&
      plan.part_count === Math.ceil(plan.size / plan.part_bytes) &&
      typeof plan.zero === "boolean" &&
      plan.zero === (plan.size === 0) &&
      plan.mode === (plan.zero ? "empty_put" : "multipart"),
  );
  requireValue(
    value.operation_state === null || typeof value.operation_state === "string",
  );
  if (value.state === "verified") validateArtifact(value.artifact, intent);
  return value as Session;
}
function validateArtifact(
  value: unknown,
  intent?: TransferIntent,
): TransferArtifact {
  requireValue(
    object(value) &&
      text(value.id) &&
      text(value.name) &&
      integer(value.size) &&
      typeof value.sha256 === "string" &&
      SHA256.test(value.sha256) &&
      text(value.media_type) &&
      text(value.role) &&
      ["model", "evaluation", "truth"].includes(value.visibility),
    "Verified artifact metadata is invalid.",
  );
  if (intent)
    requireValue(
      (Object.keys(intent) as (keyof TransferIntent)[]).every(
        (key) => value[key] === intent[key],
      ),
      "Verified artifact metadata differs from the immutable upload request.",
    );
  return value as TransferArtifact;
}
function wait(milliseconds: number, signal?: AbortSignal) {
  checkAbort(signal);
  return new Promise<void>((resolve, reject) => {
    const finish = () => {
      signal?.removeEventListener("abort", abort);
      resolve();
    };
    const timer = setTimeout(finish, milliseconds);
    const abort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      reject(new DOMException("Import paused.", "AbortError"));
    };
    signal?.addEventListener("abort", abort, { once: true });
  });
}

export class ArtifactTransferClient {
  private readonly origin: string;
  private readonly project: string;
  private readonly configuredStorage?: ResumeStorage;
  private get storage(): ResumeStorage {
    return this.configuredStorage ?? globalThis.localStorage;
  }
  private readonly fetcher: typeof fetch;
  private readonly pollIntervalMs: number;
  constructor(
    private readonly api: ApiClient,
    options: {
      origin: string;
      project: string;
      storage?: ResumeStorage;
      fetcher?: typeof fetch;
      pollIntervalMs?: number;
    },
  ) {
    const origin = new URL(options.origin);
    requireValue(
      ["http:", "https:"].includes(origin.protocol) &&
        !origin.username &&
        !origin.password &&
        !origin.search &&
        !origin.hash &&
        ["", "/"].includes(origin.pathname) &&
        text(options.project),
      "A project and credential-free Studio origin are required.",
    );
    this.origin = origin.origin;
    this.project = options.project;
    this.configuredStorage = options.storage;
    this.fetcher = options.fetcher ?? globalThis.fetch;
    this.pollIntervalMs = options.pollIntervalMs ?? 1000;
    requireValue(
      integer(this.pollIntervalMs),
      "Invalid transfer polling interval.",
    );
  }
  private key(intent: TransferIntent) {
    return (
      PREFIX +
      JSON.stringify([
        this.origin,
        this.project,
        intent.name,
        intent.size,
        intent.sha256,
        intent.media_type,
        intent.role,
        intent.visibility,
      ])
    );
  }
  private save(record: SavedTransfer) {
    try {
      this.storage.setItem(this.key(record.intent), JSON.stringify(record));
    } catch {
      throw new ArtifactTransferError(
        "Browser resume metadata could not be saved. No further transfer control was sent.",
      );
    }
  }
  private read(key: string): SavedTransfer | null {
    const raw = this.storage.getItem(key);
    if (raw === null) return null;
    let record: any;
    try {
      record = JSON.parse(raw);
    } catch {
      throw new ArtifactTransferError(
        "Saved transfer metadata is invalid; it was left unchanged.",
      );
    }
    requireValue(
      object(record) &&
        record.format === FORMAT &&
        record.origin === this.origin &&
        record.project === this.project &&
        text(record.idempotency_key),
      "Saved transfer metadata belongs to another or invalid scope; it was left unchanged.",
    );
    validateIntent(record.intent);
    requireValue(
      this.key(record.intent) === key &&
        (record.session_id === undefined || segment(record.session_id)) &&
        (record.state === undefined || text(record.state)),
      "Saved transfer identity is invalid; it was left unchanged.",
    );
    // Return only the documented metadata; never propagate persisted extra fields.
    return {
      format: FORMAT,
      origin: this.origin,
      project: this.project,
      intent: record.intent,
      idempotency_key: record.idempotency_key,
      ...(record.session_id ? { session_id: record.session_id } : {}),
      ...(record.state ? { state: record.state } : {}),
    };
  }
  pending(): SavedTransfer[] {
    const prefix =
      PREFIX + JSON.stringify([this.origin, this.project]).slice(0, -1) + ",";
    const records: SavedTransfer[] = [];
    for (let index = 0; index < this.storage.length; index++) {
      const key = this.storage.key(index);
      if (key?.startsWith(prefix)) {
        const record = this.read(key);
        if (record && record.state !== "verified") records.push(record);
      }
    }
    return records;
  }
  private remember(value: unknown, record: SavedTransfer): Session {
    const row = validateSession(value, record.intent, record.session_id);
    // Validate correlation BEFORE replacing the last known local request.
    record.session_id = row.id;
    record.state = row.state;
    this.save(record);
    return row;
  }
  async abort(saved: SavedTransfer, options: { signal?: AbortSignal } = {}) {
    try {
      requireValue(
        saved.format === FORMAT &&
          saved.origin === this.origin &&
          saved.project === this.project,
        "Saved transfer belongs to another project or origin.",
      );
      validateIntent(saved.intent);
      const record = this.read(this.key(saved.intent));
      requireValue(
        record && record.session_id && record.session_id === saved.session_id,
        "Select a saved transfer before aborting it.",
      );
      const path =
        "/api/lab/transfers/" + encodeURIComponent(record.session_id);
      this.remember(await this.api.get(path, options.signal), record);
      const row = this.remember(
        await this.api.post(path + "/abort", {}, options.signal),
        record,
      );
      if (row.state === "aborted")
        this.storage.removeItem(this.key(record.intent));
      else
        requireValue(
          false,
          "Abort is not confirmed. The saved transfer remains available for inspection.",
        );
    } catch (cause) {
      if (options.signal?.aborted)
        throw new DOMException(
          "Abort acknowledgement is unconfirmed.",
          "AbortError",
        );
      if (cause instanceof ArtifactTransferError) throw cause;
      throw new ArtifactTransferError(
        "Abort was not confirmed. The saved session remains available for inspection.",
      );
    }
  }
  async upload(
    file: File,
    selection: Selection,
    options: {
      signal?: AbortSignal;
      onProgress?: (value: TransferProgress) => void;
    } = {},
  ): Promise<TransferArtifact> {
    let phase: TransferProgress["phase"] = "admitting";
    const progress = (
      next: TransferProgress["phase"],
      loaded: number,
      sessionId?: string,
    ) => {
      checkAbort(options.signal);
      phase = next;
      options.onProgress?.({
        phase: next,
        loaded,
        total: file.size,
        sessionId,
      });
    };
    try {
      checkAbort(options.signal);
      const configuration = await this.api.get<any>(
        "/api/lab/transfer-policy",
        options.signal,
      );
      requireValue(
        object(configuration) &&
          ["r2", "local"].includes(configuration.backend) &&
          typeof configuration.resumable === "boolean" &&
          object(configuration.policy) &&
          object(configuration.policy.per_file),
        "Transfer policy is unavailable.",
      );
      const maximum = configuration.policy.per_file.max_bytes;
      requireValue(
        maximum === null || allowance(maximum),
        "Transfer byte budget is invalid.",
      );
      requireValue(
        integer(file.size),
        "The selected file has an invalid byte size.",
      );
      if (configuration.backend === "local") {
        requireValue(
          maximum === null || file.size <= maximum,
          "The selected file exceeds the configured upload byte budget.",
        );
        requireValue(
          configuration.resumable === false,
          "Transfer policy is inconsistent.",
        );
        const body = new FormData();
        body.append("file", file);
        progress("uploading", 0);
        const result = await this.api.upload("/api/lab/inputs", body, {
          signal: options.signal,
          onProgress: (value) => progress("uploading", value.loaded),
        });
        checkAbort(options.signal);
        const artifact = validateArtifact(result);
        requireValue(
          artifact.size === file.size && artifact.name === file.name,
          "Imported artifact metadata differs from the selected file.",
        );
        progress("verified", file.size);
        return artifact;
      }
      requireValue(
        configuration.resumable === true &&
          object(configuration.policy.geometry) &&
          allowance(configuration.policy.geometry.concurrency, 1),
        "Resumable transfer policy is unavailable.",
      );
      requireValue(
        file.size <= MAX_OBJECT,
        "The selected file exceeds the R2 object-size protocol limit.",
      );
      progress("hashing", 0);
      const digest = await hashFile(file, {
        signal: options.signal,
        onProgress: (loaded) => progress("hashing", loaded),
      });
      const intent: TransferIntent = {
        name: file.name,
        size: file.size,
        sha256: digest,
        media_type: file.type || "application/octet-stream",
        role: selection.role,
        visibility: selection.visibility,
      };
      validateIntent(intent);
      const key = this.key(intent);
      let record = this.read(key);
      if (!record) {
        requireValue(
          maximum === null || file.size <= maximum,
          "The selected file exceeds the configured upload byte budget.",
        );
        record = {
          format: FORMAT,
          origin: this.origin,
          project: this.project,
          intent,
          idempotency_key: crypto.randomUUID(),
        };
        this.save(record); // A lost create response must reuse this exact request key.
      }
      progress("admitting", 0, record.session_id);
      let row = this.remember(
        record.session_id
          ? await this.api.get(
              "/api/lab/transfers/" + encodeURIComponent(record.session_id),
              options.signal,
            )
          : await this.api.post(
              "/api/lab/transfers",
              { ...intent, idempotency_key: record.idempotency_key },
              options.signal,
            ),
        record,
      );
      const path = "/api/lab/transfers/" + encodeURIComponent(row.id);
      if (
        ["admitted", "initializing", "initialization_uncertain"].includes(
          row.state,
        )
      )
        row = this.remember(
          await this.api.post(path + "/initialize", {}, options.signal),
          record,
        );
      if (row.state === "uploading") {
        const inventory = await this.api.get<any>(
          path + "/parts",
          options.signal,
        );
        requireValue(object(inventory) && Array.isArray(inventory.parts));
        row = this.remember(inventory.transfer, record);
        requireValue(row.state === "uploading");
        const seen = new Set<number>();
        let loaded = 0;
        for (const part of inventory.parts) {
          requireValue(
            object(part) &&
              integer(part.number, 1) &&
              part.number <= row.plan.part_count &&
              !seen.has(part.number) &&
              integer(part.size) &&
              part.size ===
                Math.min(
                  row.plan.part_bytes,
                  file.size - (part.number - 1) * row.plan.part_bytes,
                ) &&
              text(part.etag),
          );
          seen.add(part.number);
          loaded += part.size;
        }
        progress("uploading", loaded, row.id);
        const missing = Array.from(
          { length: row.plan.part_count },
          (_, index) => index + 1,
        ).filter((number) => !seen.has(number));
        let next = 0;
        const controller = new AbortController();
        const cancel = () => controller.abort();
        options.signal?.addEventListener("abort", cancel, { once: true });
        let error: unknown;
        try {
          await Promise.all(
            Array.from(
              {
                length: Math.min(
                  missing.length,
                  configuration.policy.geometry.concurrency,
                ),
              },
              async () => {
                try {
                  while (next < missing.length) {
                    checkAbort(controller.signal);
                    const number = missing[next++],
                      offset = (number - 1) * row.plan.part_bytes;
                    const size = Math.min(
                      row.plan.part_bytes,
                      file.size - offset,
                    );
                    const descriptor = await this.api.post<any>(
                      path + "/parts/" + number,
                      {},
                      controller.signal,
                    );
                    requireValue(
                      object(descriptor) &&
                        text(descriptor.url) &&
                        integer(descriptor.part_number, 1) &&
                        descriptor.part_number === number &&
                        integer(descriptor.offset) &&
                        descriptor.offset === offset &&
                        integer(descriptor.size, 1) &&
                        descriptor.size === size &&
                        integer(descriptor.expires_in, 1) &&
                        object(descriptor.headers),
                    );
                    let url: URL;
                    try {
                      url = new URL(descriptor.url);
                    } catch {
                      throw new ArtifactTransferError(
                        "Invalid signed part destination.",
                      );
                    }
                    requireValue(
                      url.protocol === "https:" &&
                        !url.username &&
                        !url.password &&
                        !url.hash,
                      "Invalid signed part destination.",
                    );
                    const headers: Record<string, string> = {};
                    let length = false;
                    const names = new Set<string>();
                    for (const [name, value] of Object.entries(
                      descriptor.headers,
                    )) {
                      const normalized = name.toLowerCase();
                      requireValue(
                        !names.has(normalized) &&
                          typeof value === "string" &&
                          !/[\r\n\u0000]/.test(value),
                        "Invalid signed part headers.",
                      );
                      names.add(normalized);
                      if (normalized === "content-length") {
                        requireValue(
                          /^(0|[1-9]\d*)$/.test(value) &&
                            integer(Number(value)) &&
                            Number(value) === size,
                          "Signed part byte length is invalid.",
                        );
                        length = true;
                      } else {
                        requireValue(
                          /^(content-type|content-md5|x-amz-checksum-(sha256|sha1|crc32|crc32c|crc64nvme))$/.test(
                            normalized,
                          ),
                          "Unexpected signed part header.",
                        );
                        headers[name] = value;
                      }
                    }
                    requireValue(length, "Signed part byte length is missing.");
                    const body = file.slice(offset, offset + size);
                    requireValue(body.size === size);
                    const response = await this.fetcher(url.href, {
                      method: "PUT",
                      body,
                      headers,
                      credentials: "omit",
                      redirect: "error",
                      signal: controller.signal,
                    });
                    requireValue(
                      response.ok,
                      "Part upload was not confirmed. Resume to reconcile the retained provider parts.",
                    );
                    loaded += size;
                    progress("uploading", loaded, row.id);
                  }
                } catch (cause) {
                  if (error === undefined) error = cause;
                  controller.abort();
                }
              },
            ),
          );
          if (error !== undefined) throw error;
        } finally {
          options.signal?.removeEventListener("abort", cancel);
        }
        checkAbort(options.signal);
        row = this.remember(
          await this.api.post(path + "/complete", {}, options.signal),
          record,
        );
      } else if (
        ["sealed", "completion_uncertain", "sealing"].includes(row.state)
      ) {
        // An explicit resume asks for durable reconciliation; the server never
        // blindly repeats an uncertain provider completion.
        row = this.remember(
          await this.api.post(path + "/complete", {}, options.signal),
          record,
        );
      }
      while (row.state === "verifying") {
        requireValue(
          !["failed", "lost", "cancelled"].includes(row.operation_state ?? ""),
          "Artifact verification stopped. Inspect the saved session; its verifier will not be restarted automatically.",
        );
        progress("verifying", file.size, row.id);
        await wait(this.pollIntervalMs, options.signal);
        row = this.remember(await this.api.get(path, options.signal), record);
      }
      requireValue(
        row.state === "verified",
        "The artifact remains unpublished. Resume or inspect the saved transfer before starting another import.",
      );
      const artifact = validateArtifact(row.artifact, intent);
      progress("verified", file.size, row.id);
      return artifact;
    } catch (cause) {
      if (
        options.signal?.aborted ||
        (cause instanceof DOMException && cause.name === "AbortError")
      )
        throw new DOMException(
          "Import paused; reselect the same file to resume.",
          "AbortError",
        );
      if (cause instanceof ArtifactTransferError) throw cause;
      // Transport exceptions can contain presigned URLs. Never expose or store
      // their message/body/cause; the local journal contains only metadata.
      throw new ArtifactTransferError(
        `Import interrupted during ${phase}. Resume with the same file; any saved transfer is retained.`,
      );
    }
  }
}
