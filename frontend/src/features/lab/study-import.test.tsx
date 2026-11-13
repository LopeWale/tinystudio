import { afterEach, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
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
import { createApiClient } from "../../api/client";
import { scientificJson } from "../../api/scientific-json";
import { WorkspaceScope } from "../../controllers/workspaceScope";
import { StudyImportPanel } from "./study-import";
import { Studies } from "./studies";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const previewPath = "/api/lab/researcher-actions/source-review";
const importPath = "/api/lab/researcher-actions/publish-observations";
const study = {
  id: "study-1",
  revision: 2,
  digest: "a".repeat(64),
  actions: {
    observation_import_preview: previewPath,
    observation_import: importPath,
  },
};
const projection = {
  selection: { lines: [2, 7] },
  number_encoding: "decimal_strings",
  unit_id: { pointer: "/device~1id" },
  condition: { literal: "researcher-selected" },
  data: {
    temperature: { pointer: "/values/0" },
    exact: { literal: "9007199254740993" },
  },
};
function preview(body: any) {
  return {
    format: "tinystudio.study-observation-import.v1",
    study_id: "study-1",
    revision: body.revision,
    protocol_digest: "a".repeat(64),
    source: {
      id: body.artifact_id,
      sha256: "b".repeat(64),
      size: 152,
      role: "source",
      visibility: "researcher",
    },
    projection: { ...body.projection, kind: "observation" },
    summary: {
      count: 2,
      rows_sha256: "c".repeat(64),
      number_encoding: body.projection.number_encoding,
      preview: [
        {
          line: 2,
          raw_line_sha256: "d".repeat(64),
          record: { unit_id: "device-1", data: { temperature: "1.2500" } },
        },
      ],
      disclosure: {
        numbers: "Exact source numeric tokens are retained as strings.",
      },
    },
    implementation_sha256: "e".repeat(64),
    digest: "f".repeat(64),
  };
}
const imported = (body: any) => {
  const { summary, ...prepared } = preview(body);
  const { preview: _displayRows, ...retainedSummary } = summary;
  return {
    receipt: {
      id: "receipt-1",
      study_id: "study-1",
      revision: body.revision,
      protocol_digest: "a".repeat(64),
      kind: "observation_import",
      seq: 15,
      data: {
        format: "tinystudio.study-observation-import.v1",
        intent_digest: "1".repeat(64),
        preview: { ...prepared, summary: retainedSummary },
        count: 2,
        first_sequence: 13,
        last_sequence: 14,
      },
    },
    count: 2,
    first_sequence: 13,
    last_sequence: 14,
    replayed: false,
  };
};
type Call = {
  path: string;
  body: any;
  raw?: string;
  signal?: AbortSignal | null;
};
function fixture(override?: (call: Call) => unknown | Promise<unknown>) {
  const calls: Call[] = [];
  const scope = new WorkspaceScope();
  scope.setIdentity("researcher:alpha");
  let project = "alpha";
  const api = createApiClient({
    scope,
    getProject: () => project,
    onUnauthorized: vi.fn(),
    fetcher: vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof init?.body === "string" ? init.body : undefined;
      const call: Call = {
        path: String(input),
        raw,
        body: raw ? scientificJson.parse(raw) : undefined,
        signal: init?.signal,
      };
      calls.push(call);
      const value =
        (await override?.(call)) ??
        (call.path.endsWith("source-review")
          ? preview(call.body)
          : imported(call.body));
      return value instanceof Response
        ? value
        : new Response(scientificJson.stringify(value), {
            headers: { "content-type": "application/json" },
          });
    }) as typeof fetch,
  });
  const runtime = (): StudioRuntime =>
    ({
      api,
      scopeKey: `researcher:${project}`,
      project,
      user: { id: "researcher" },
      route: "/lab",
      evidence: new EvidenceRegistry(),
      toast: vi.fn(),
    }) as unknown as StudioRuntime;
  return {
    api,
    calls,
    runtime,
    changeProject(next: string) {
      project = next;
      scope.setIdentity(`researcher:${next}`);
    },
  };
}
function show(
  f: ReturnType<typeof fixture>,
  props: Partial<ComponentProps<typeof StudyImportPanel>> = {},
) {
  const onImported = vi.fn(),
    onPendingChange = vi.fn();
  const view = (next = props) => (
    <StudioContext.Provider value={f.runtime()}>
      <StudyImportPanel
        study={study}
        onImported={onImported}
        onPendingChange={onPendingChange}
        {...next}
      />
    </StudioContext.Provider>
  );
  const rendered = render(view());
  return {
    ...rendered,
    onImported,
    onPendingChange,
    update(next: typeof props) {
      rendered.rerender(view(next));
    },
  };
}
const edit = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = (name: string) =>
  fireEvent.click(screen.getByRole("button", { name }));
function author() {
  edit("Observation source artifact ID", "retained-source");
  edit("Observation projection (JSON)", scientificJson.stringify(projection));
}
async function review() {
  author();
  click("Preview observation import");
  await screen.findByText("Source SHA-256: " + "b".repeat(64));
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

it("starts empty and previews through server action links without automatically importing", async () => {
  const f = fixture();
  show(f);
  expect(screen.getByLabelText("Observation source artifact ID")).toHaveValue(
    "",
  );
  expect(screen.getByLabelText("Observation projection (JSON)")).toHaveValue(
    "{}",
  );
  expect(
    screen.getByRole("button", { name: "Preview observation import" }),
  ).toBeDisabled();
  await review();
  expect(f.calls).toHaveLength(1);
  expect(f.calls[0]).toMatchObject({
    path: "/api/p/alpha/lab/researcher-actions/source-review",
    body: { artifact_id: "retained-source", revision: 2, projection },
  });
  expect(screen.getByText(/Selected observation rows: 2/)).toBeVisible();
  expect(screen.getByText(/five rows.*display only/i)).toBeVisible();
  expect(screen.getByText(/repeats are retained/i)).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Import 2 observation rows" }),
  ).toBeEnabled();
});

it("invalidates a preview on source or incomplete JSON edits and blocks invalid requests", async () => {
  const f = fixture();
  show(f);
  await review();
  edit("Observation source artifact ID", "another-source");
  expect(
    screen.queryByRole("button", { name: "Import 2 observation rows" }),
  ).not.toBeInTheDocument();
  click("Preview observation import");
  await waitFor(() => expect(f.calls).toHaveLength(2));
  await screen.findByRole("button", { name: "Import 2 observation rows" });
  edit("Observation projection (JSON)", "{");
  expect(
    screen.getByRole("button", { name: "Preview observation import" }),
  ).toBeDisabled();
  expect(
    screen.queryByRole("button", { name: "Import 2 observation rows" }),
  ).not.toBeInTheDocument();
  expect(f.calls).toHaveLength(2);
});

it("imports the exact reviewed payload and uses a new key only after a successful receipt", async () => {
  const f = fixture();
  const shown = show(f);
  author();
  const authored = {
    ...projection,
    data: {
      ...projection.data,
      signed: { literal: -0 },
      approximate: { literal: 1e30 },
    },
  };
  edit("Observation projection (JSON)", scientificJson.stringify(authored));
  click("Preview observation import");
  await screen.findByRole("button", { name: "Import 2 observation rows" });
  click("Import 2 observation rows");
  await screen.findByText(/Imported 2 observation rows/);
  expect(shown.onImported).toHaveBeenCalledTimes(1);
  const first = f.calls[1];
  expect(first.path).toBe(
    "/api/p/alpha/lab/researcher-actions/publish-observations",
  );
  expect(first.body).toEqual({
    ...f.calls[0].body,
    preview_digest: "f".repeat(64),
    idempotency_key: expect.any(String),
  });
  expect(first.raw).toContain('"literal":-0.0');
  expect(first.raw).toContain('"literal":1e+30');
  expect(first.body.projection).not.toHaveProperty("kind");
  click("Import 2 observation rows");
  await waitFor(() => expect(shown.onImported).toHaveBeenCalledTimes(2));
  expect(f.calls[2].body.idempotency_key).not.toBe(first.body.idempotency_key);
});

it("preserves uncertain imports, locks source editing, and retries identical bytes and key", async () => {
  let attempts = 0;
  const f = fixture((call) => {
    if (call.path.endsWith("publish-observations") && ++attempts === 1)
      throw new TypeError("Connection lost after publication");
  });
  const shown = show(f);
  await review();
  click("Import 2 observation rows");
  await screen.findByRole("button", {
    name: "Retry original observation import",
  });
  expect(
    screen.getByLabelText("Observation source artifact ID"),
  ).toBeDisabled();
  expect(screen.getByLabelText("Observation projection (JSON)")).toBeDisabled();
  expect(shown.onPendingChange).toHaveBeenLastCalledWith(true);
  shown.update({ study: { ...study, revision: 1 } });
  expect(
    screen.getByText(/original import.*protocol revision 2/i),
  ).toBeVisible();
  click("Retry original observation import");
  await screen.findByText(/Imported 2 observation rows/);
  expect(f.calls[2].raw).toBe(f.calls[1].raw);
  expect(shown.onPendingChange).toHaveBeenLastCalledWith(false);
});

it("allows a new preview after a definite source rejection", async () => {
  const f = fixture((call) =>
    call.path.endsWith("publish-observations")
      ? new Response(JSON.stringify({ detail: "Source changed" }), {
          status: 409,
          headers: { "content-type": "application/json" },
        })
      : undefined,
  );
  show(f);
  await review();
  click("Import 2 observation rows");
  await screen.findByText(/Source changed/);
  expect(screen.getByLabelText("Observation source artifact ID")).toBeEnabled();
  expect(
    screen.getByRole("button", { name: "Preview observation import" }),
  ).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: "Import 2 observation rows" }),
  ).not.toBeInTheDocument();
});

it("retains the uncertain original import when its retry is forbidden, then replays the same request", async () => {
  let attempts = 0;
  const f = fixture((call) => {
    if (!call.path.endsWith("publish-observations")) return;
    if (++attempts === 1)
      throw new TypeError("Connection lost after publication");
    if (attempts === 2)
      return new Response(
        JSON.stringify({ detail: "Retry permission denied" }),
        {
          status: 403,
          headers: { "content-type": "application/json" },
        },
      );
    return { ...imported(call.body), replayed: true };
  });
  const shown = show(f);
  await review();
  click("Import 2 observation rows");
  await screen.findByRole("button", {
    name: "Retry original observation import",
  });
  click("Retry original observation import");
  await screen.findByText("Retry permission denied");
  expect(
    screen.getByLabelText("Observation source artifact ID"),
  ).toBeDisabled();
  expect(screen.getByLabelText("Observation projection (JSON)")).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Preview observation import" }),
  ).toBeDisabled();
  expect(shown.onPendingChange).toHaveBeenLastCalledWith(true);
  expect(shown.onImported).not.toHaveBeenCalled();
  click("Retry original observation import");
  await screen.findByText(/Imported 2 observation rows/);
  expect(f.calls[2].raw).toBe(f.calls[1].raw);
  expect(f.calls[3].raw).toBe(f.calls[1].raw);
  expect(shown.onImported).toHaveBeenCalledTimes(1);
  expect(shown.onPendingChange).toHaveBeenLastCalledWith(false);
});

it("keeps the original key unresolved when another import receipt is returned for the same study", async () => {
  let attempts = 0;
  const f = fixture((call) => {
    if (!call.path.endsWith("publish-observations")) return;
    const result = imported(call.body);
    if (++attempts === 1) result.receipt.data.preview.digest = "9".repeat(64);
    return result;
  });
  const shown = show(f);
  await review();
  click("Import 2 observation rows");
  await screen.findByText(/Import receipt does not match the original preview/);
  expect(shown.onImported).not.toHaveBeenCalled();
  expect(shown.onPendingChange).toHaveBeenLastCalledWith(true);
  expect(
    screen.getByLabelText("Observation source artifact ID"),
  ).toBeDisabled();
  click("Retry original observation import");
  await screen.findByText(/Imported 2 observation rows/);
  expect(f.calls[2].raw).toBe(f.calls[1].raw);
  expect(shown.onImported).toHaveBeenCalledTimes(1);
  expect(shown.onPendingChange).toHaveBeenLastCalledWith(false);
});

it.each(["source", "revision", "study", "project"])(
  "suppresses stale preview responses after a %s change",
  async (change) => {
    const response = deferred<unknown>();
    const f = fixture(() => response.promise);
    const shown = show(f);
    author();
    click("Preview observation import");
    const original = f.calls[0];
    if (change === "source")
      edit("Observation source artifact ID", "new-source");
    if (change === "revision")
      shown.update({ study: { ...study, revision: 1 } });
    if (change === "study")
      shown.update({ study: { ...study, id: "study-2" } });
    if (change === "project") {
      f.changeProject("beta");
      shown.update({});
    }
    await act(async () => {
      response.resolve(preview(original.body));
      await response.promise;
    });
    expect(original.signal?.aborted).toBe(true);
    expect(
      screen.queryByText("Source SHA-256: " + "b".repeat(64)),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Import 2 observation rows" }),
    ).not.toBeInTheDocument();
  },
);

it("preserves an uncertain import during study and protocol navigation, then refreshes records and totals", async () => {
  let importAttempts = 0,
    recordLoads = 0,
    headerLoads = 0;
  const f = fixture((call) => {
    if (call.path.endsWith("source-review")) return preview(call.body);
    if (call.path.endsWith("publish-observations")) {
      if (++importAttempts === 1) throw new Error("Uncertain response");
      return { ...imported(call.body), replayed: true };
    }
    if (call.path.endsWith("/studies"))
      return [
        {
          id: "study-1",
          name: "Curation study",
          status: "draft",
          revision: 2,
          record_count: 0,
        },
      ];
    if (call.path.includes("/records?")) {
      recordLoads++;
      return { records: [], next_after: 0, latest_seq: 0, has_more: false };
    }
    if (call.path.endsWith("/revisions"))
      return [
        {
          revision: 1,
          name: "Earlier protocol",
          status: "draft",
          digest: "a".repeat(64),
        },
      ];
    if (call.path.endsWith("/studies/study-1")) {
      headerLoads++;
      return {
        ...study,
        head_revision: 2,
        last_sequence: 0,
        summary: { records: 0, declared_unit_ids: 0 },
        study: {
          schema_version: 1,
          name: "Curation study",
          description: "",
          hypothesis: "",
          independent_unit: "",
          status: "draft",
          conditions: [],
          outcomes: [],
          sampling_plan: {},
          stopping_rules: {},
          metadata: {},
        },
      };
    }
    return [];
  });
  render(
    <StudioContext.Provider value={f.runtime()}>
      <Studies />
    </StudioContext.Provider>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Open study Curation study" }),
  );
  await screen.findByLabelText("Observation source artifact ID");
  await review();
  click("Import 2 observation rows");
  await screen.findByRole("button", {
    name: "Retry original observation import",
  });
  expect(screen.getByRole("button", { name: "New study" })).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Open protocol revision 1" }),
  ).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Save as new protocol revision" }),
  ).toBeDisabled();
  const recordsBefore = recordLoads,
    headerBefore = headerLoads;
  click("Retry original observation import");
  await screen.findByText(/Imported 2 observation rows/);
  await waitFor(() => {
    expect(recordLoads).toBeGreaterThan(recordsBefore);
    expect(headerLoads).toBeGreaterThan(headerBefore);
  });
  expect(screen.getByRole("button", { name: "New study" })).toBeEnabled();
});
