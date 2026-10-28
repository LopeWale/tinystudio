import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import {
  RepresentationInspector,
  type RetainedIdentity,
  type RepresentationInspection,
} from "./representation-inspector";

afterEach(cleanup);
const identity: RetainedIdentity = {
  run_id: "run",
  artifact_id: "raw",
  row_index: 7,
};
const inspection = (row: number, name: string): RepresentationInspection => ({
  identity: { ...identity, row_index: row },
  observation: { value: -1.2345678901234567, units: "V", wire_hex: "0080ff" },
  legacy_unqualified_records: 2,
  qualification: "Exact fingerprint matching",
  matches: [
    {
      artifact_id: "codec",
      artifact_sha256: "stored-sha",
      row_index: row + 20,
      record: {
        representation_id: name,
        representation_kind: "raw_bytes",
        model_input_text: null,
        model_input_base64: "AIA=",
        token_ids: [0, 128],
        token_count: 2,
        full_token_count: 3,
        input_sha256: "exact-sha",
        untruncated_sha256: "full-sha",
        specification_hash: "spec-sha",
        loss: {
          truncated: true,
          omitted_tokens: 1,
          numeric_quantized: false,
          excluded_fields: ["value"],
        },
      },
    },
  ],
});
function wrap(
  get: any,
  selected = identity,
  scopeKey = "project",
  evidence = new EvidenceRegistry(),
) {
  const runtime = {
    api: { get },
    scopeKey,
    route: "/lab",
    project: scopeKey,
    user: { id: "user" },
    evidence,
  } as unknown as StudioRuntime;
  return (
    <StudioContext.Provider value={runtime}>
      <RepresentationInspector identity={selected} />
    </StudioContext.Provider>
  );
}
it("shows exact retained decimal, binary bytes, tokens, hashes and loss with evidence query", async () => {
  const evidence = new EvidenceRegistry();
  const get = vi.fn().mockResolvedValue(inspection(7, "binary"));
  render(wrap(get, identity, "project", evidence));
  await screen.findByText("Input bytes (base64)");
  expect(screen.getByText("AIA=")).toBeInTheDocument();
  expect(screen.getByText("[0,128]")).toBeInTheDocument();
  expect(screen.getByText("exact-sha")).toBeInTheDocument();
  expect(screen.getByText("full-sha")).toBeInTheDocument();
  expect(screen.getByText(/"value": -1.2345678901234567/)).toBeInTheDocument();
  expect(
    screen.getByText("Loss, truncation and excluded fields").nextElementSibling,
  ).toHaveTextContent('"omitted_tokens": 1');
  expect(get.mock.calls[0][0]).toBe(
    "/api/lab/runs/run/representations?artifact_id=raw&row=7",
  );
});
it("aborts old exact row requests and never displays a late response for another row", async () => {
  let oldResolve!: (data: RepresentationInspection) => void;
  const get = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          oldResolve = resolve;
        }),
    )
    .mockResolvedValue(inspection(8, "current-codec"));
  const { rerender } = render(wrap(get));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
  const oldSignal = get.mock.calls[0][1] as AbortSignal;
  rerender(wrap(get, { ...identity, row_index: 8 }));
  expect(oldSignal.aborted).toBe(true);
  await screen.findByText("current-codec");
  await act(async () => oldResolve(inspection(7, "obsolete-codec")));
  expect(screen.queryByText("obsolete-codec")).not.toBeInTheDocument();
  expect(screen.getByText("current-codec")).toBeInTheDocument();
});
it("clears evidence immediately on scope changes and ignores the obsolete project reply", async () => {
  let resolveOld!: (data: RepresentationInspection) => void;
  const get = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockResolvedValue(inspection(7, "new-project"));
  const { rerender } = render(wrap(get));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
  const signal = get.mock.calls[0][1] as AbortSignal;
  rerender(wrap(get, identity, "other"));
  expect(signal.aborted).toBe(true);
  await screen.findByText("new-project");
  await act(async () => resolveOld(inspection(7, "foreign-project")));
  expect(screen.queryByText("foreign-project")).not.toBeInTheDocument();
});
it("reports unmatched legacy evidence explicitly instead of substituting selected numeric fields as codecs", async () => {
  const get = vi
    .fn()
    .mockResolvedValue({ ...inspection(7, "unused"), matches: [] });
  render(wrap(get));
  expect(
    await screen.findByText(/2 legacy rows lack a qualifying fingerprint/),
  ).toBeInTheDocument();
  expect(screen.queryByText("Input SHA-256")).not.toBeInTheDocument();
});

it("rejects a response for another absolute row instead of showing its codecs", async () => {
  render(wrap(vi.fn().mockResolvedValue(inspection(999, "wrong-row"))));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "does not match the selected retained row",
  );
  expect(screen.queryByText("wrong-row")).not.toBeInTheDocument();
});
