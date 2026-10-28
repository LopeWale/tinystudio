export interface EvidenceRecord {
  id: string;
  label: string;
  data: unknown;
  route: string;
  project: string;
  user: string;
}
// Preserve the native assistant's broad credential-key guard. Scientific fields
// with ambiguous credential names are visibly redacted rather than silently sent.
const sensitiveKey =
  /(?:password|secret|token|authorization|credential|api.?key|cookie|system_prompt)/i;
const sensitiveRoute = /(?:^|\/)api-keys(?:[/?]|$)/;
export function canCaptureApiEvidence(path: string) {
  const normalized = path.replace(/^\/api\/p\/[^/]+(?=\/|$)/, "/api");
  return (
    normalized.startsWith("/api/") &&
    !/^\/api\/(?:tasks|assistant|settings|auth|keys|api-keys|users|config)(?:[/?]|$)/.test(
      normalized,
    ) &&
    !/(?:^|\/)(?:token|credentials?)(?:[/?]|$)/i.test(normalized)
  );
}
export function sanitizeEvidence(value: unknown, depth = 0): unknown {
  if (depth > 8) return "[omitted: nesting exceeds 8 levels]";
  if (Array.isArray(value)) {
    const retained = value
      .slice(0, 100)
      .map((item) => sanitizeEvidence(item, depth + 1));
    if (value.length > 100)
      retained.push(`[omitted: ${value.length - 100} additional array items]`);
    return retained;
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value),
      safe: Record<string, unknown> = Object.create(null);
    for (const [key, item] of entries.slice(0, 80))
      safe[key] = sensitiveKey.test(key)
        ? "[redacted]"
        : sanitizeEvidence(item, depth + 1);
    if (entries.length > 80) {
      let key = "__omitted_fields__";
      while (key in safe) key += "_";
      safe[key] = `[omitted: ${entries.length - 80} additional fields]`;
    }
    return safe;
  }
  if (typeof value === "string" && value.length > 12000)
    return (
      value.slice(0, 12000) +
      `[omitted: ${value.length - 12000} additional characters]`
    );
  if (typeof value === "function" || typeof value === "symbol")
    return "[omitted: unsupported value]";
  if (typeof value === "number" && !Number.isFinite(value))
    return "[undefined numeric value]";
  return value;
}
function cloneData(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(cloneData);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, cloneData(item)]),
    );
  return value;
}
function publicRecord(record: EvidenceRecord): EvidenceRecord {
  return { ...record, data: cloneData(record.data) };
}
export class EvidenceRegistry {
  private records = new Map<string, EvidenceRecord>();
  register(record: EvidenceRecord) {
    if (sensitiveRoute.test(record.route.replace(/^#/, ""))) return () => {};
    const safe = { ...record, data: sanitizeEvidence(record.data) };
    this.records.set(record.id, safe);
    return () => {
      if (this.records.get(record.id) === safe) this.records.delete(record.id);
    };
  }
  clear() {
    this.records.clear();
  }
  snapshot() {
    return [...this.records.values()].map(publicRecord);
  }
  pick(target: Element) {
    if (target.closest('[data-assistant-sensitive],input[type="password"]'))
      return undefined;
    const field = target.closest("input,textarea");
    if (
      field &&
      sensitiveKey.test(
        `${field.id} ${field.getAttribute("name") ?? ""} ${field.getAttribute("aria-label") ?? ""} ${Array.from(
          (field as HTMLInputElement | HTMLTextAreaElement).labels ?? [],
        )
          .map((label) => label.textContent)
          .join(" ")}`,
      )
    )
      return undefined;
    const node = target.closest("[data-evidence-id]"),
      record = node
        ? this.records.get(node.getAttribute("data-evidence-id") ?? "")
        : undefined;
    return record ? publicRecord(record) : undefined;
  }
}
