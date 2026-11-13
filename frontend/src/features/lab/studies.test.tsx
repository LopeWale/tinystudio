import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { ApiError, createApiClient } from "../../api/client";
import { WorkspaceScope } from "../../controllers/workspaceScope";
import { LabPage } from "./index";
import { Studies } from "./studies";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function runtime(api: any): StudioRuntime {
  return {
    api,
    scopeKey: "researcher:alpha",
    project: "alpha",
    user: { id: "researcher" },
    route: "/lab",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
  } as unknown as StudioRuntime;
}
it("opens study tracking through the existing Laboratory work area", async () => {
  const api = {
    get: vi.fn(async (path: string) => {
      if (path === "/api/lab/catalog") return { adapters: [], methods: [] };
      return [];
    }),
  };
  render(
    <StudioContext.Provider value={runtime(api)}>
      <LabPage />
    </StudioContext.Provider>,
  );
  fireEvent.click(screen.getByRole("tab", { name: "Studies" }));
  expect(
    await screen.findByRole("region", { name: "Study tracking" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "New study" }));
  expect(await screen.findByLabelText("Study name")).toHaveValue("");
  expect(screen.getByLabelText("Conditions (JSON)")).toHaveValue("[]");
  expect(screen.getByLabelText("Sampling plan (JSON)")).toHaveValue("{}");
});

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
const protocol = (name = "Thermal study") => ({
  schema_version: 1,
  name,
  description: "",
  hypothesis: "",
  independent_unit: "",
  conditions: [],
  outcomes: [],
  sampling_plan: {},
  stopping_rules: {},
  status: "draft",
  metadata: {},
});
function server(seed = true) {
  const documents = new Map<string, any[]>();
  const ledger = new Map<string, any[]>();
  const envelope = (id: string, revision?: number) => {
    const versions = documents.get(id)!;
    const rev = revision ?? versions.length;
    const records = ledger.get(id) ?? [];
    return clone({
      id,
      revision: rev,
      head_revision: versions.length,
      study: versions[rev - 1],
      digest: String(rev).repeat(64),
      author_id: "researcher",
      recorded_at: "2026-11-13T12:00:00Z",
      created: "2026-11-13T12:00:00Z",
      updated: "2026-11-13T12:00:00Z",
      summary: {
        records: records.length,
        declared_unit_ids: new Set(
          records.map((r) => r.unit_id).filter(Boolean),
        ).size,
      },
      last_sequence: records.length,
    });
  };
  if (seed) documents.set("study-1", [protocol()]);
  const api = {
    get: vi.fn(async (path: string): Promise<any> => {
      if (path === "/api/lab/studies" || path.startsWith("/api/lab/studies?")) {
        const query = new URLSearchParams(path.split("?")[1]);
        const offset = Number(query.get("offset") ?? 0);
        const limit = Number(query.get("limit") ?? 100);
        return [...documents.keys()].slice(offset, offset + limit).map((id) => {
          const full = envelope(id);
          return {
            id,
            name: full.study.name,
            status: full.study.status,
            revision: full.revision,
            digest: full.digest,
            record_count: full.summary.records,
          };
        });
      }
      const match = path.match(/^\/api\/lab\/studies\/([^/?]+)(.*)$/);
      if (!match) return [];
      const [, id, suffix] = match;
      if (suffix === "/revisions")
        return documents.get(id)!.map((doc, index) => ({
          revision: index + 1,
          digest: String(index + 1).repeat(64),
          name: doc.name,
          status: doc.status,
          author_id: "researcher",
          recorded_at: "2026-11-13T12:00:00Z",
        }));
      if (suffix.startsWith("/records?")) {
        const query = new URLSearchParams(suffix.split("?")[1]);
        const after = Number(query.get("after"));
        const rows = ledger.get(id) ?? [];
        const selected = rows.filter((r) => r.seq > after).slice(0, 100);
        const next = selected.at(-1)?.seq ?? after;
        return clone({
          records: selected,
          next_after: next,
          latest_seq: rows.length,
          has_more: next < rows.length,
        });
      }
      if (suffix === "/manifest")
        return {
          format: "tinystudio.study-tracking.v1",
          study_id: id,
          revision: documents.get(id)!.length,
          through_seq: (ledger.get(id) ?? []).length,
          protocol_digest: envelope(id).digest,
          external_evidence_bytes: false,
          export_path: `/api/lab/studies/${id}/export?revision=${documents.get(id)!.length}&through_seq=${(ledger.get(id) ?? []).length}`,
        };
      return envelope(
        id,
        new URLSearchParams(suffix.slice(1)).get("revision")
          ? Number(new URLSearchParams(suffix.slice(1)).get("revision"))
          : undefined,
      );
    }),
    post: vi.fn(async (path: string, body: any): Promise<any> => {
      if (path === "/api/lab/studies") {
        const id = "created-study";
        documents.set(id, [clone(body.study)]);
        return envelope(id);
      }
      const id = path.split("/").at(-2)!;
      const rows = ledger.get(id) ?? [];
      const result = {
        ...clone(body),
        id: `record-${rows.length + 1}`,
        study_id: id,
        seq: rows.length + 1,
        protocol_digest: envelope(id, body.revision).digest,
        run_references: body.run_ids.map((run: string) => ({
          id: run,
          spec_digest: "frozen-run-digest",
          state_at_link: "succeeded",
          event_seq: 8,
          result_artifacts: [{ id: "result-1", sha256: "frozen-result-hash" }],
          outcomes: [{ event_seq: 7, kind: "negative" }],
        })),
        artifact_references: body.artifact_ids.map((artifact: string) => ({
          id: artifact,
          name: "raw.csv",
          sha256: "frozen-artifact-hash",
          size: 123,
          role: "source",
          visibility: "model",
        })),
        author_id: "researcher",
        recorded_at: "2026-11-13T12:05:00Z",
        digest: "c".repeat(64),
      };
      rows.push(result);
      ledger.set(id, rows);
      return clone(result);
    }),
    put: vi.fn(async (path: string, body: any): Promise<any> => {
      const id = path.split("/").at(-1)!;
      const versions = documents.get(id)!;
      if (body.expected_revision !== versions.length)
        throw new ApiError("Study changed; reload its head", 409, {});
      versions.push(clone(body.study));
      return envelope(id);
    }),
    download: vi.fn(async () => new Blob(["metadata-only JSONL"])),
  };
  return { api, documents, ledger, envelope };
}
function show(api: any) {
  const value = runtime(api);
  return {
    value,
    ...render(
      <StudioContext.Provider value={value}>
        <Studies />
      </StudioContext.Provider>,
    ),
  };
}
const edit = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = (name: string) =>
  fireEvent.click(screen.getByRole("button", { name }));
async function openStudy(name = "Thermal study") {
  fireEvent.click(
    await screen.findByRole("button", { name: `Open study ${name}` }),
  );
  await screen.findByLabelText("Study name");
}
it("creates and reopens a researcher-authored protocol without inventing unknown assumptions", async () => {
  const fixture = server(false);
  show(fixture.api);
  click("New study");
  expect(screen.getByRole("button", { name: "Save study" })).toBeDisabled();
  edit("Study name", "Exploratory study");
  edit("Status", "researcher-defined-stage");
  edit("Conditions (JSON)", '[{"id":"unusual-condition","custom":true}]');
  edit("Outcomes (JSON)", '["negative","missing"]');
  click("Save study");
  await screen.findByText(/Pinned protocol revision 1/);
  expect(fixture.api.post).toHaveBeenCalledWith(
    "/api/lab/studies",
    expect.objectContaining({
      study: expect.objectContaining({
        name: "Exploratory study",
        hypothesis: "",
        independent_unit: "",
        sampling_plan: {},
        stopping_rules: {},
        status: "researcher-defined-stage",
        outcomes: ["negative", "missing"],
      }),
      idempotency_key: expect.any(String),
    }),
    expect.any(AbortSignal),
  );
  await openStudy("Exploratory study");
  expect(screen.getByLabelText("Status")).toHaveValue(
    "researcher-defined-stage",
  );
  expect(screen.getByLabelText("Conditions (JSON)")).toHaveValue(
    JSON.stringify([{ id: "unusual-condition", custom: true }], null, 2),
  );
});
it("preserves malformed JSON and validation-rejected drafts without changing the create key", async () => {
  const fixture = server(false);
  fixture.api.post.mockRejectedValueOnce(
    new ApiError("Researcher metadata is invalid", 422, {}),
  );
  show(fixture.api);
  click("New study");
  edit("Study name", "Keep my draft");
  edit("Sampling plan (JSON)", '{"planned":');
  expect(screen.getByRole("button", { name: "Save study" })).toBeDisabled();
  expect(screen.getByLabelText("Sampling plan (JSON)")).toHaveValue(
    '{"planned":',
  );
  edit("Sampling plan (JSON)", '{"planned":1}');
  click("Save study");
  await screen.findByText(/Researcher metadata is invalid/);
  expect(screen.getByLabelText("Study name")).toHaveValue("Keep my draft");
  edit("Sampling plan (JSON)", '{"planned":2}');
  click("Save study");
  await screen.findByText(/Pinned protocol revision 1/);
  expect(fixture.api.post.mock.calls[0][1].idempotency_key).toBe(
    fixture.api.post.mock.calls[1][1].idempotency_key,
  );
  expect(fixture.api.post.mock.calls[1][1].study.sampling_plan).toEqual({
    planned: 2,
  });
});
it("retries the original create payload after an unknown failure and preserves newer editor changes", async () => {
  const fixture = server(false);
  fixture.api.post.mockRejectedValueOnce(
    new Error("Connection lost after sending"),
  );
  show(fixture.api);
  click("New study");
  edit("Study name", "Original request");
  click("Save study");
  await screen.findByText(/Connection lost after sending/);
  edit("Study name", "Newer unsaved name");
  click("Retry original study save");
  await screen.findByText(/Pinned protocol revision 1/);
  expect(fixture.api.post.mock.calls[1][1]).toEqual(
    fixture.api.post.mock.calls[0][1],
  );
  expect(screen.getByLabelText("Study name")).toHaveValue("Newer unsaved name");
  expect(
    screen.getByRole("button", { name: "Save as new protocol revision" }),
  ).toBeEnabled();
});
it("preserves stale-save edits and allows explicit review of the current head before saving", async () => {
  const fixture = server();
  show(fixture.api);
  await openStudy();
  edit("Hypothesis", "My retained hypothesis");
  fixture.documents
    .get("study-1")!
    .push({ ...protocol(), hypothesis: "Another writer's protocol" });
  click("Save as new protocol revision");
  await screen.findByText(/Study changed/);
  expect(screen.getByLabelText("Hypothesis")).toHaveValue(
    "My retained hypothesis",
  );
  click("Inspect latest protocol");
  await screen.findByText(/Another writer's protocol/);
  click("Use head revision 2 for next save");
  click("Save as new protocol revision");
  await screen.findByText(/Pinned protocol revision 3/);
  expect(fixture.api.put.mock.calls[1][1]).toMatchObject({
    expected_revision: 2,
    study: { hypothesis: "My retained hypothesis" },
  });
});
it("pins historical revisions and appends custom corrections with frozen evidence metadata", async () => {
  const fixture = server();
  fixture.documents
    .get("study-1")!
    .push({ ...protocol(), description: "Second protocol" });
  show(fixture.api);
  await openStudy();
  fireEvent.click(
    await screen.findByRole("button", { name: "Open protocol revision 1" }),
  );
  await screen.findByText(/Pinned protocol revision 1/);
  edit("Record kind", "negative calibration");
  edit("Declared unit ID", "device-7");
  edit("Condition", "unplanned condition");
  edit("Observed at", "2026-11-13T09:00:00");
  edit("Record data (JSON)", '{"value":null,"passed":false}');
  edit("Run IDs (JSON)", '["run-1"]');
  edit("Artifact IDs (JSON)", '["artifact-1"]');
  edit("Supersedes record ID", "earlier-record");
  expect(
    screen.getByRole("button", { name: "Append collection record" }),
  ).toBeDisabled();
  edit("Observed at", "2026-11-13T09:00:00+02:00");
  click("Append collection record");
  await screen.findByText(/Recorded collection record #1/);
  expect(fixture.api.post).toHaveBeenCalledWith(
    "/api/lab/studies/study-1/records",
    expect.objectContaining({
      revision: 1,
      kind: "negative calibration",
      unit_id: "device-7",
      observed_at: "2026-11-13T09:00:00+02:00",
      data: { value: null, passed: false },
      run_ids: ["run-1"],
      artifact_ids: ["artifact-1"],
      supersedes: "earlier-record",
    }),
    expect.any(AbortSignal),
  );
  expect(await screen.findByText(/Ledger records: 1/)).toBeVisible();
  expect(screen.getByText(/These are bookkeeping counts/)).toBeVisible();
  expect(screen.getByText(/frozen-run-digest/)).toBeInTheDocument();
  expect(screen.getByText(/frozen-artifact-hash/)).toBeInTheDocument();
});
it("retains record retry identity and body, then uses a fresh key only after success", async () => {
  const fixture = server();
  fixture.documents.get("study-1")![0].sampling_plan = { planned_records: 1 };
  fixture.api.post.mockRejectedValueOnce(new Error("Append response lost"));
  show(fixture.api);
  await openStudy();
  edit("Record data (JSON)", '{"value":5}');
  click("Append collection record");
  await screen.findByText(/Append response lost/);
  edit("Record data (JSON)", '{"value":6}');
  click("Retry original record append");
  await screen.findByText(/Recorded collection record #1/);
  expect(fixture.api.post.mock.calls[1][1]).toEqual(
    fixture.api.post.mock.calls[0][1],
  );
  expect(screen.getByLabelText("Record data (JSON)")).toHaveValue(
    '{"value":6}',
  );
  click("Append collection record");
  await screen.findByText(/Recorded collection record #2/);
  expect(fixture.api.post.mock.calls[2][1].idempotency_key).not.toBe(
    fixture.api.post.mock.calls[1][1].idempotency_key,
  );
  expect(fixture.api.post.mock.calls[2][1].data).toEqual({ value: 6 });
  expect(await screen.findByText(/Ledger records: 2/)).toBeVisible();
});
it("exports the pinned metadata snapshot while honestly identifying external artifact bytes", async () => {
  const fixture = server();
  const create = vi.fn(() => "blob:tracking"),
    revoke = vi.fn();
  const OriginalURL = URL;
  vi.stubGlobal(
    "URL",
    class extends OriginalURL {
      static createObjectURL = create;
      static revokeObjectURL = revoke;
    },
  );
  const download = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  show(fixture.api);
  await openStudy();
  click("Export tracking manifest");
  await waitFor(() => expect(download).toHaveBeenCalled());
  expect(fixture.api.download).toHaveBeenCalledWith(
    "/api/lab/studies/study-1/export?revision=1&through_seq=0",
    expect.any(AbortSignal),
  );
  expect(
    screen.getByText(/External artifact bytes are not included/),
  ).toBeVisible();
  expect(revoke).toHaveBeenCalledWith("blob:tracking");
});
it("masks delayed study responses when another study or project is selected", async () => {
  const fixture = server();
  fixture.documents.set("study-2", [protocol("Other study")]);
  let resolve!: (value: any) => void;
  const pending = new Promise((done) => {
    resolve = done;
  });
  const normalGet = fixture.api.get.getMockImplementation()!;
  fixture.api.get.mockImplementation(async (path) =>
    path === "/api/lab/studies/study-1" ? pending : normalGet(path),
  );
  const view = show(fixture.api);
  fireEvent.click(
    await screen.findByRole("button", { name: "Open study Thermal study" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Open study Other study" }),
  );
  expect(await screen.findByLabelText("Study name")).toHaveValue("Other study");
  await act(async () => {
    resolve(fixture.envelope("study-1"));
    await pending;
  });
  expect(screen.getByLabelText("Study name")).toHaveValue("Other study");
  const beta = {
    ...runtime({ get: vi.fn().mockResolvedValue([]) }),
    scopeKey: "researcher:beta",
    project: "beta",
  };
  view.rerender(
    <StudioContext.Provider value={beta}>
      <Studies />
    </StudioContext.Provider>,
  );
  expect(screen.queryByLabelText("Study name")).toBeNull();
  expect(screen.queryByText(/Pinned protocol revision/)).toBeNull();
});
it("opens history for a newly created study without losing its saved identity", async () => {
  const fixture = server(false);
  show(fixture.api);
  click("New study");
  edit("Study name", "New study history");
  click("Save study");
  await screen.findByText(/Pinned protocol revision 1/);
  fireEvent.click(
    await screen.findByRole("button", { name: "Open protocol revision 1" }),
  );
  expect(await screen.findByLabelText("Study name")).toHaveValue(
    "New study history",
  );
  expect(await screen.findByText(/Pinned protocol revision 1/)).toBeVisible();
});
it("keeps an in-progress record pinned to its original protocol after a protocol save", async () => {
  const fixture = server();
  show(fixture.api);
  await openStudy();
  edit("Record data (JSON)", '{"value":2}');
  edit("Hypothesis", "Revised protocol");
  click("Save as new protocol revision");
  await screen.findByText(/Pinned protocol revision 2/);
  expect(
    screen.getByText(/Record draft pins protocol revision 1/),
  ).toBeVisible();
  expect(screen.getByLabelText("Record data (JSON)")).toHaveValue(
    '{"value":2}',
  );
  click("Append collection record");
  await screen.findByText(/Recorded collection record #1/);
  expect(fixture.api.post.mock.calls[0][1].revision).toBe(1);
});
it("keeps invalid drafts when study navigation is canceled and discards only on explicit navigation", async () => {
  const fixture = server();
  fixture.documents.set("study-2", [protocol("Other study")]);
  show(fixture.api);
  await openStudy();
  edit("Conditions (JSON)", '[{"unfinished":');
  click("Open study Other study");
  expect(screen.getByText(/unsaved changes/)).toBeVisible();
  click("Keep current study draft");
  expect(screen.getByLabelText("Conditions (JSON)")).toHaveValue(
    '[{"unfinished":',
  );
  click("Open study Other study");
  click("Discard draft and open study");
  expect(await screen.findByLabelText("Study name")).toHaveValue("Other study");
});
it("ignores a late create response after the project owner changes", async () => {
  const fixture = server(false);
  let resolve!: (value: any) => void;
  const pending = new Promise((done) => {
    resolve = done;
  });
  fixture.api.post.mockReturnValueOnce(pending);
  const view = show(fixture.api);
  click("New study");
  edit("Study name", "Late study");
  click("Save study");
  const beta = {
    ...runtime({ get: vi.fn().mockResolvedValue([]) }),
    scopeKey: "researcher:beta",
    project: "beta",
  };
  view.rerender(
    <StudioContext.Provider value={beta}>
      <Studies />
    </StudioContext.Provider>,
  );
  fixture.documents.set("late-study", [protocol("Late study")]);
  await act(async () => {
    resolve(fixture.envelope("late-study"));
    await pending;
  });
  expect(screen.queryByLabelText("Study name")).toBeNull();
  expect(screen.queryByText(/Pinned protocol revision/)).toBeNull();
});
it("pages the ledger without showing a previous page while the next response is pending", async () => {
  const fixture = server();
  fixture.ledger.set(
    "study-1",
    Array.from({ length: 101 }, (_, index) => ({
      id: `record-${index + 1}`,
      study_id: "study-1",
      seq: index + 1,
      revision: 1,
      protocol_digest: "1".repeat(64),
      kind: "observation",
      observed_at: null,
      recorded_at: "2026-11-13T12:00:00Z",
      author_id: "researcher",
      digest: "d".repeat(64),
      run_references: [],
      artifact_references: [],
    })),
  );
  let resolve!: (value: any) => void;
  const pending = new Promise((done) => {
    resolve = done;
  });
  const normalGet = fixture.api.get.getMockImplementation()!;
  fixture.api.get.mockImplementation(async (path) =>
    path.includes("records?after=100") ? pending : normalGet(path),
  );
  show(fixture.api);
  await openStudy();
  await screen.findByText("Record #1 · observation");
  click("Next record page");
  expect(screen.queryByText("Record #1 · observation")).toBeNull();
  const result = await normalGet(
    "/api/lab/studies/study-1/records?after=100&limit=100",
  );
  await act(async () => {
    resolve(result);
    await pending;
  });
  expect(await screen.findByText("Record #101 · observation")).toBeVisible();
  click("Previous record page");
  expect(await screen.findByText("Record #1 · observation")).toBeVisible();
});
it("retries history and ledger reads while keeping protocol drafts", async () => {
  const fixture = server();
  const normalGet = fixture.api.get.getMockImplementation()!;
  let historyFailed = false,
    ledgerFailed = false;
  fixture.api.get.mockImplementation(async (path) => {
    if (path.endsWith("/revisions") && !historyFailed) {
      historyFailed = true;
      throw new Error("History unavailable");
    }
    if (path.includes("/records?") && !ledgerFailed) {
      ledgerFailed = true;
      throw new Error("Ledger unavailable");
    }
    return normalGet(path);
  });
  show(fixture.api);
  await openStudy();
  edit("Hypothesis", "Retain during retry");
  fireEvent.click(
    await screen.findByRole("button", { name: "Retry protocol history" }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Retry collection records" }),
  );
  await screen.findByRole("button", { name: "Open protocol revision 1" });
  await screen.findByText("Latest ledger sequence: 0");
  expect(screen.getByLabelText("Hypothesis")).toHaveValue(
    "Retain during retry",
  );
});
it("refuses an export snapshot that names another study", async () => {
  const fixture = server();
  const normalGet = fixture.api.get.getMockImplementation()!;
  fixture.api.get.mockImplementation(async (path) =>
    path.endsWith("/manifest")
      ? {
          format: "tinystudio.study-tracking.v1",
          study_id: "foreign-study",
          external_evidence_bytes: false,
          export_path: "/api/lab/studies/foreign-study/export",
        }
      : normalGet(path),
  );
  show(fixture.api);
  await openStudy();
  click("Export tracking manifest");
  await screen.findByText(/Tracking export metadata does not match/);
  expect(fixture.api.download).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Study name")).toHaveValue("Thermal study");
});
it("reports unavailable bookkeeping totals honestly and retries without clearing drafts", async () => {
  const fixture = server();
  const normalGet = fixture.api.get.getMockImplementation()!;
  let protocolReads = 0;
  fixture.api.get.mockImplementation(async (path) => {
    if (path === "/api/lab/studies/study-1" && ++protocolReads === 2)
      throw new Error("Totals could not refresh");
    return normalGet(path);
  });
  show(fixture.api);
  await openStudy();
  edit("Hypothesis", "Keep totals retry draft");
  await screen.findByText(/Study totals unavailable/);
  expect(screen.getByText(/Ledger records: unavailable/)).toBeVisible();
  click("Retry study totals");
  await screen.findByText(/Ledger records: 0/);
  expect(screen.getByLabelText("Hypothesis")).toHaveValue(
    "Keep totals retry draft",
  );
});
it("blocks nonfinite data and malformed evidence ID arrays while allowing authored negative values", async () => {
  const fixture = server();
  show(fixture.api);
  await openStudy();
  edit("Record data (JSON)", '{"reading":1e999}');
  expect(
    screen.getByRole("button", { name: "Append collection record" }),
  ).toBeDisabled();
  edit("Record data (JSON)", '{"reading":-4}');
  edit("Run IDs (JSON)", '[""]');
  expect(
    screen.getByRole("button", { name: "Append collection record" }),
  ).toBeDisabled();
  edit("Run IDs (JSON)", "[]");
  expect(
    screen.getByRole("button", { name: "Append collection record" }),
  ).toBeEnabled();
});
it("preserves an aware source timestamp with a space separator and nanosecond precision", async () => {
  const fixture = server();
  show(fixture.api);
  await openStudy();
  const sourceTimestamp = "2026-11-13 09:00:00.123456789+02:00";
  edit("Observed at", sourceTimestamp);
  expect(
    screen.getByRole("button", { name: "Append collection record" }),
  ).toBeEnabled();
  click("Append collection record");
  await screen.findByText(/Recorded collection record #1/);
  expect(fixture.api.post.mock.calls[0][1].observed_at).toBe(sourceTimestamp);
});
it("reopens the same historical revision after saving a newer protocol from it", async () => {
  const fixture = server();
  show(fixture.api);
  await openStudy();
  fireEvent.click(
    await screen.findByRole("button", { name: "Open protocol revision 1" }),
  );
  await screen.findByText(/Pinned protocol revision 1/);
  edit("Hypothesis", "New protocol content");
  click("Save as new protocol revision");
  await screen.findByText(/Pinned protocol revision 2/);
  click("Open protocol revision 1");
  await screen.findByText(/Pinned protocol revision 1/);
  expect(screen.getByLabelText("Hypothesis")).toHaveValue("");
});
it("reopens the current study's head when its directory row is selected from history", async () => {
  const fixture = server();
  fixture.documents
    .get("study-1")!
    .push({ ...protocol(), hypothesis: "Head protocol" });
  show(fixture.api);
  await openStudy();
  fireEvent.click(
    await screen.findByRole("button", { name: "Open protocol revision 1" }),
  );
  await screen.findByText(/Pinned protocol revision 1/);
  click("Open study Thermal study");
  await screen.findByText(/Pinned protocol revision 2/);
  expect(screen.getByLabelText("Hypothesis")).toHaveValue("Head protocol");
});
it("reaches the 101st study while preserving the selected protocol and its draft", async () => {
  const fixture = server(false);
  for (let index = 1; index <= 101; index++)
    fixture.documents.set(`study-${index}`, [protocol(`Study ${index}`)]);
  show(fixture.api);
  await openStudy("Study 1");
  edit("Hypothesis", "Preserve this draft while paging");
  click("Next study page");
  await screen.findByRole("button", { name: "Open study Study 101" });
  expect(screen.getByLabelText("Study name")).toHaveValue("Study 1");
  expect(screen.getByLabelText("Hypothesis")).toHaveValue(
    "Preserve this draft while paging",
  );
  click("Open study Study 101");
  click("Discard draft and open study");
  expect(await screen.findByLabelText("Study name")).toHaveValue("Study 101");
  click("Previous study page");
  await screen.findByRole("button", { name: "Open study Study 1" });
  expect(screen.getByLabelText("Study name")).toHaveValue("Study 101");
});
it("preserves scientific values through study fields, revision saves and record appends using the real API client", async () => {
  let revision = 1;
  let recordJson = "";
  const writes: { method: string; body: string }[] = [];
  const response = (text: string) =>
    new Response(text, { headers: { "content-type": "application/json" } });
  const source = () =>
    JSON.stringify({
      ...server().envelope("study-1"),
      revision,
      head_revision: revision,
      study: {
        ...protocol(),
        metadata: { large: "FLOAT_TOKEN", signed_zero: "ZERO_TOKEN" },
      },
    })
      .replace('"FLOAT_TOKEN"', "1e20")
      .replace('"ZERO_TOKEN"', "-0.0");
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url).replace("/api/p/alpha", "/api");
    if (init?.method === "PUT") {
      writes.push({ method: "PUT", body: String(init.body) });
      revision++;
      return response(source());
    }
    if (init?.method === "POST") {
      writes.push({ method: "POST", body: String(init.body) });
      recordJson = `{${String(init.body).slice(1, -1)},"id":"record-1","study_id":"study-1","seq":1,"protocol_digest":"digest","digest":"record-digest","recorded_at":"2026-11-13T12:00:00Z","author_id":"researcher","run_references":[],"artifact_references":[]}`;
      return response(recordJson);
    }
    if (path === "/api/lab/studies")
      return response(
        '[{"id":"study-1","name":"Thermal study","status":"draft","revision":1,"record_count":0}]',
      );
    if (path.endsWith("/revisions")) return response("[]");
    if (path.includes("/records?"))
      return response(
        `{"records":[${recordJson}],"next_after":${recordJson ? 1 : 0},"latest_seq":${recordJson ? 1 : 0},"has_more":false}`,
      );
    return response(source());
  });
  const api = createApiClient({
    scope: new WorkspaceScope(),
    getProject: () => "alpha",
    onUnauthorized: vi.fn(),
    fetcher,
  });
  show(api);
  await openStudy();
  edit("Hypothesis", "Save existing scientific metadata");
  click("Save as new protocol revision");
  await waitFor(() => expect(writes).toHaveLength(1));
  const saved = JSON.parse(writes[0].body);
  expect(Object.is(saved.study.metadata.signed_zero, -0)).toBe(true);
  expect(writes[0].body).toMatch(/"large":1e\+?20/);
  expect(
    (screen.getByLabelText("Protocol metadata (JSON)") as HTMLTextAreaElement)
      .value,
  ).toContain("-0.0");
  await screen.findByText(/Pinned protocol revision 2/);
  edit("Record data (JSON)", '{"large":1e20,"signed_zero":-0.0}');
  click("Append collection record");
  await screen.findByText(/Recorded collection record #1/);
  const recorded = JSON.parse(writes[1].body);
  expect(Object.is(recorded.data.signed_zero, -0)).toBe(true);
  expect(writes[1].body).toMatch(/"large":1e\+?20/);
  expect(recorded.data.large).toBe(1e20);
  expect(
    screen.getByText("Record #1 · observation").closest("details"),
  ).toHaveTextContent('"signed_zero": -0.0');
});
it("keeps unsafe integer source drafts blocked while admitting large scientific float values", async () => {
  const fixture = server();
  show(fixture.api);
  await openStudy();
  edit("Protocol metadata (JSON)", '{"count":9007199254740993}');
  expect(
    screen.getByRole("button", { name: "Save as new protocol revision" }),
  ).toBeDisabled();
  expect(screen.getByLabelText("Protocol metadata (JSON)")).toHaveValue(
    '{"count":9007199254740993}',
  );
  edit("Protocol metadata (JSON)", '{"scale":1e100,"signed_zero":-0.0}');
  expect(
    screen.getByRole("button", { name: "Save as new protocol revision" }),
  ).toBeEnabled();
});
