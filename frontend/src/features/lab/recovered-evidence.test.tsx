import { Blob } from "node:buffer";
import { createHash, webcrypto } from "node:crypto";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import type { LabRun } from "./definition";
import { RecoveredEvidencePanel } from "./recovered-evidence";

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("Blob", Blob);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
const hash = (wire: string) => createHash("sha256").update(wire).digest("hex");
function fixture(id = "run-1", status = "lost") {
  const wire = "not JSON\npartial";
  const file = {
    id: `raw-${id}`,
    name: "raw.jsonl",
    sha256: hash(wire),
    size: Buffer.byteLength(wire),
    role: "input",
    visibility: "evaluation",
  };
  const manifest = {
    id: `manifest-${id}`,
    name: "manifest.json",
    sha256: hash("manifest"),
    size: 8,
    role: "input",
    visibility: "evaluation",
  };
  const document = {
    format: "tinystudio.operator-recovered-evidence.v1",
    classification: "operator_recovered_evidence",
    source: {
      project_id: "server-project-id",
      run_id: id,
      attempt_id: `attempt-${id}`,
      target_id: "target-1",
      spec_digest: "a".repeat(64),
    },
    source_states: { run: "failed", attempt: "failed" },
    source_states_observed_at: "2026-11-13T12:00:00Z",
    author_id: "operator-1",
    request_digest: "b".repeat(64),
    idempotency_key: `recover-${id}`,
    manifest,
    producer_manifest: {
      outcome: { physical_stop: "unverified" },
      cleanup: { status: "confirmed" },
    },
    qualification: {
      scope: "bytes_and_existing_server_identity_only",
      operator_supplied: true,
      scientific_truth: false,
      physical_completion: false,
      runtime_attestation_verified: false,
      cleanup_and_outcome: "producer_claims_only",
      execution_authority_granted: false,
    },
    files: [
      {
        name: "evidence/raw.jsonl",
        artifact: file,
        attempt_id: null,
        verified_bytes: {
          size: file.size,
          sha256: file.sha256,
          jsonl: {
            newline_count: 1,
            tail_bytes: 7,
            ends_with_newline: false,
            semantic_validation: "not_performed",
          },
        },
      },
    ],
  };
  const text = JSON.stringify(document);
  const receipt = {
    id: `receipt-${id}`,
    name: "receipt.json",
    size: Buffer.byteLength(text),
    sha256: hash(text),
    role: "operator_recovered_evidence",
    visibility: "evaluation",
  };
  const ref = {
    classification: "operator_recovered_evidence",
    receipt,
    source_attempt_id: document.source.attempt_id,
    source_states: { run: "lost", attempt: "lost" },
    author_id: document.author_id,
    idempotency_key: document.idempotency_key,
    request_digest: document.request_digest,
    manifest_artifact_id: manifest.id,
    manifest_sha256: manifest.sha256,
  };
  const run: LabRun = {
    id,
    definition_id: "definition-1",
    state: status,
    target_id: "target-1",
    spec_digest: "a".repeat(64),
    result: null,
  };
  return { wire, file, text, ref, run };
}
function studio(
  get: ReturnType<typeof vi.fn>,
  download: ReturnType<typeof vi.fn>,
  project = "alpha",
) {
  return {
    api: { get, download, post: vi.fn() },
    project,
    scopeKey: `operator:${project}`,
    route: "/lab",
    user: { id: "operator-1" },
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
  } as unknown as StudioRuntime;
}
const view = (runtime: StudioRuntime, run: LabRun) => (
  <StudioContext.Provider value={runtime}>
    <RecoveredEvidencePanel run={run} />
  </StudioContext.Provider>
);

it.each(["failed", "cancelled", "lost", "recovery_required"])(
  "shows recovered bytes independently on a %s run",
  async (state) => {
    const f = fixture("run-1", state);
    const before = JSON.stringify(f.run);
    const get = vi.fn(async () => ({
      classification: "operator_recovered_evidence",
      references: [f.ref],
    }));
    const download = vi.fn(async () => new Blob([f.text]));
    const runtime = studio(get, download);
    render(view(runtime, f.run));
    await screen.findByText("Receipt bytes verified");
    expect(screen.getByText(`Current run status: ${state}`)).toBeVisible();
    expect(screen.getByText(/Receipt source run status: failed/)).toBeVisible();
    expect(
      screen.getByText(/Attachment source run status: lost/),
    ).toBeVisible();
    expect(screen.getByText(/Source attempt: attempt-run-1/)).toBeVisible();
    expect(screen.getByText(/Attached by: operator-1/)).toBeVisible();
    expect(
      screen.getByText(
        /no scientific truth, runtime attestation or physical stop/i,
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("table", { name: "Recovered file byte identities" }),
    ).toHaveTextContent("evidence/raw.jsonl");
    expect(
      screen.getByRole("table", { name: "Recovered file byte identities" }),
    ).toHaveTextContent("7 bytes");
    expect(screen.queryByText(/valid observations|samples:/i)).toBeNull();
    expect(JSON.stringify(f.run)).toBe(before);
    expect(runtime.api.post).not.toHaveBeenCalled();
  },
);

it("preserves the exact verified receipt Blob when downloading", async () => {
  const f = fixture();
  const blob = new Blob([f.text]);
  const download = vi.fn(async () => blob);
  const createObjectURL = vi.fn(() => "blob:owned-download");
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(
    view(
      studio(
        vi.fn(async () => ({
          classification: "operator_recovered_evidence",
          references: [f.ref],
        })),
        download,
      ),
      f.run,
    ),
  );
  await screen.findByText("Receipt bytes verified");
  fireEvent.click(
    screen.getByRole("button", {
      name: `Download recovery receipt ${f.ref.receipt.id}`,
    }),
  );
  await waitFor(() => expect(createObjectURL).toHaveBeenCalledWith(blob));
  expect(download).toHaveBeenCalledTimes(1);
});

it("refuses mismatched recovered file bytes without creating a download", async () => {
  const f = fixture();
  const createObjectURL = vi.fn();
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() });
  const download = vi.fn(
    async (path: string) =>
      new Blob([path.includes("receipt-") ? f.text : "wrong bytes"]),
  );
  render(
    view(
      studio(
        vi.fn(async () => ({
          classification: "operator_recovered_evidence",
          references: [f.ref],
        })),
        download,
      ),
      f.run,
    ),
  );
  await screen.findByText("Receipt bytes verified");
  fireEvent.click(
    screen.getByRole("button", {
      name: "Download recovered file evidence/raw.jsonl",
    }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /size mismatch|checksum mismatch/i,
  );
  expect(createObjectURL).not.toHaveBeenCalled();
});

it("shows clear reference and receipt loading failures with read-only retry", async () => {
  const f = fixture();
  const get = vi
    .fn()
    .mockRejectedValueOnce(new Error("owned unavailable reference API"))
    .mockResolvedValue({
      classification: "operator_recovered_evidence",
      references: [f.ref],
    });
  render(
    view(
      studio(
        get,
        vi.fn(async () => new Blob(["corrupt receipt"])),
      ),
      f.run,
    ),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "owned unavailable reference API",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Refresh recovered evidence" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /receipt.*size mismatch|receipt.*checksum/i,
  );
});

it("ignores a late receipt from an old run", async () => {
  const old = fixture("old-run"),
    current = fixture("current-run");
  let resolve!: (blob: Blob) => void;
  const download = vi.fn((path: string) =>
    path.includes("old-run")
      ? new Promise<Blob>((done) => {
          resolve = done;
        })
      : Promise.resolve(new Blob([current.text])),
  );
  const get = vi.fn(async (path: string) => ({
    classification: "operator_recovered_evidence",
    references: [path.includes("old-run") ? old.ref : current.ref],
  }));
  const runtime = studio(get, download);
  const mounted = render(view(runtime, old.run));
  await waitFor(() => expect(resolve).toBeTypeOf("function"));
  mounted.rerender(view(runtime, current.run));
  await screen.findByText("Receipt bytes verified");
  await act(async () => {
    resolve(new Blob([old.text]));
  });
  expect(screen.getByText(/Source attempt: attempt-current-run/)).toBeVisible();
  expect(screen.queryByText(/Source attempt: attempt-old-run/)).toBeNull();
});

it("ignores references from a project that has been left", async () => {
  const f = fixture();
  let resolve!: (value: unknown) => void;
  const old = studio(
    vi.fn(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    ),
    vi.fn(),
    "alpha",
  );
  const current = studio(
    vi.fn(async () => ({
      classification: "operator_recovered_evidence",
      references: [],
    })),
    vi.fn(),
    "beta",
  );
  const mounted = render(view(old, f.run));
  mounted.rerender(view(current, f.run));
  await screen.findByText(/No operator-recovered evidence is attached/);
  await act(async () => {
    resolve({
      classification: "operator_recovered_evidence",
      references: [f.ref],
    });
  });
  expect(old.api.download).not.toHaveBeenCalled();
  expect(screen.queryByText("Receipt bytes verified")).toBeNull();
});

it("cancels an old project's in-flight file download before creating a browser URL", async () => {
  const f = fixture();
  const createObjectURL = vi.fn();
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() });
  let resolve!: (value: Blob) => void;
  const get = vi.fn(async () => ({
    classification: "operator_recovered_evidence",
    references: [f.ref],
  }));
  const download = vi.fn((path: string) =>
    path.includes("receipt-")
      ? Promise.resolve(new Blob([f.text]))
      : new Promise<Blob>((done) => {
          resolve = done;
        }),
  );
  const mounted = render(view(studio(get, download, "alpha"), f.run));
  await screen.findByText("Receipt bytes verified");
  fireEvent.click(
    screen.getByRole("button", {
      name: "Download recovered file evidence/raw.jsonl",
    }),
  );
  await waitFor(() => expect(resolve).toBeTypeOf("function"));
  const empty = studio(
    vi.fn(async () => ({
      classification: "operator_recovered_evidence",
      references: [],
    })),
    vi.fn(),
    "beta",
  );
  mounted.rerender(view(empty, f.run));
  await screen.findByText(/No operator-recovered evidence is attached/);
  await act(async () => {
    resolve(new Blob([f.wire]));
  });
  expect(createObjectURL).not.toHaveBeenCalled();
});

it("keeps byte-verified receipt downloads available when an unsafe producer integer prevents interpretation", async () => {
  const f = fixture();
  const text = f.text.replace(
    '"cleanup":{"status":"confirmed"}',
    '"cleanup":{"status":"confirmed","quota":9007199254740993}',
  );
  f.ref.receipt.sha256 = hash(text);
  f.ref.receipt.size = Buffer.byteLength(text);
  const blob = new Blob([text]);
  const createObjectURL = vi.fn(() => "blob:owned-opaque-receipt");
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const runtime = studio(
    vi.fn(async () => ({
      classification: "operator_recovered_evidence",
      references: [f.ref],
    })),
    vi.fn(async () => blob),
  );
  render(view(runtime, f.run));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /exact JSON|Integer exceeds/i,
  );
  expect(screen.queryByText("Receipt bytes verified")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", {
      name: `Download recovery receipt bytes ${f.ref.receipt.id}`,
    }),
  );
  await waitFor(() => expect(createObjectURL).toHaveBeenCalledWith(blob));
});
