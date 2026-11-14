import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { LabPage } from "./index";

afterEach(cleanup);
async function select(label: string, name: string) {
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp(`${label}$`) }),
  );
  await userEvent.click(await screen.findByRole("option", { name }));
}
it("authors and saves a runtime probe through New experiment using the advertised native fields", async () => {
  const api = {
    get: vi.fn(async (path: string): Promise<any> =>
      path === "/api/lab/catalog"
        ? {
            adapters: [],
            methods: [
              {
                id: "runtime.qualify",
                name: "Runtime qualification",
                domain: "runtime",
                available: true,
                implemented: true,
              },
            ],
            runtime_methods: {
              format: "tinystudio.runtime-methods.v1",
              methods: [
                {
                  id: "nf4.training",
                  label: "Target-site NF4 measurement",
                  parameters: {
                    objective: { enum: ["dpo"] },
                    dtype: { enum: ["bf16"] },
                    fixture: { enum: ["retained"] },
                    nf4_execution: {
                      properties: { cpu_inference: { enum: ["portable"] } },
                    },
                  },
                },
              ],
            },
          }
        : path === "/api/lab/targets"
          ? [{ id: "local", name: "Explicit target" }]
          : [],
    ),
    post: vi.fn(async (_path: string, body: any) => ({
      id: "saved-probe",
      name: body.definition.name,
      revision: 1,
      definition: body.definition,
    })),
    put: vi.fn(),
    download: vi.fn(),
  };
  const runtime = {
    api,
    scopeKey: "researcher:alpha",
    project: "alpha",
    user: { id: "researcher" },
    route: "/lab",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
  } as unknown as StudioRuntime;
  render(
    <StudioContext.Provider value={runtime}>
      <LabPage />
    </StudioContext.Provider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "New experiment" }));
  await select("Experiment engine", "Runtime qualification");
  expect(
    screen.queryByRole("heading", { name: "Engine parameters" }),
  ).toBeNull();
  expect(screen.getByLabelText("Selected probe device")).toHaveValue("");
  await select("Runtime probe method", "Target-site NF4 measurement");
  await select(
    "Probe training objective",
    "Direct preference optimization (DPO)",
  );
  fireEvent.change(screen.getByLabelText("Selected probe device"), {
    target: { value: "explicit-target-device" },
  });
  await select("Probe data type", "bf16 (alias for bfloat16)");
  await select("Probe fixture", "Retained model and data artifacts");
  await select("NF4 CPU inference behavior", "Portable CPU execution");
  fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(
      "/api/lab/definitions",
      expect.objectContaining({
        definition: expect.objectContaining({
          engine: "runtime.qualify",
          domain: "runtime",
          operating_mode: "replay",
          parameters: {
            method: "nf4.training",
            objective: "dpo",
            device: "explicit-target-device",
            dtype: "bf16",
            fixture: "retained",
            nf4_execution: { cpu_inference: "portable" },
          },
        }),
      }),
      expect.any(AbortSignal),
    ),
  );
}, 20000);
