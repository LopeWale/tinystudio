import type { ApiClient } from "../../api/client";
import { isTerminal } from "./definition";
const enc = encodeURIComponent;
const json = (value: unknown) => JSON.stringify(value, null, 2);
export const isOperationTerminal = (state: string) =>
  isTerminal(state) || state === "lost";
function pollDelay(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      reject(new DOMException("Operation polling cancelled", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, 1200);
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}
export interface LabOperation {
  id: string;
  state: string;
  kind: string;
  result?: unknown;
  error?: unknown;
}
export async function waitForOperation(
  api: ApiClient,
  operation: LabOperation,
  signal: AbortSignal,
  onState: (operation: LabOperation) => void,
  requireSuccess = true,
) {
  if (signal.aborted)
    throw new DOMException("Operation polling cancelled", "AbortError");
  onState(operation);
  while (!isOperationTerminal(operation.state)) {
    await pollDelay(signal);
    operation = await api.get<LabOperation>(
      `/api/lab/operations/${enc(operation.id)}`,
      signal,
    );
    if (signal.aborted)
      throw new DOMException("Operation polling cancelled", "AbortError");
    onState(operation);
  }
  if (requireSuccess && operation.state !== "succeeded")
    throw new Error(
      typeof operation.error === "string"
        ? operation.error
        : json(operation.error ?? `Operation ${operation.state}`),
    );
  return operation;
}
export async function downloadOperation(
  api: ApiClient,
  operationId: string,
  signal: AbortSignal,
  filename: string,
) {
  const blob = await api.download(
    `/api/lab/operations/${enc(operationId)}/download`,
    signal,
  );
  if (signal.aborted) return;
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}
