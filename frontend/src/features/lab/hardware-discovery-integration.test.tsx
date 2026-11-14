import { Blob } from "node:buffer";
import { webcrypto } from "node:crypto";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { makeDefinition } from "./definition";
import { LabPage } from "./index";

beforeEach(() => {
  vi.stubGlobal("Blob", Blob);
  vi.stubGlobal("crypto", webcrypto);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const catalog = {
  adapters: [],
  methods: [
    {
      id: "hardware.discover",
      name: "Hardware discovery",
      domain: "hardware",
      available: true,
      implemented: true,
    },
  ],
  hardware_discovery_methods: {
    format: "tinystudio.hardware-discovery-methods.v1",
    methods: [
      {
        id: "serial.enumerate",
        label: "Catalog serial metadata",
        schema: {
          type: "object",
          required: ["method", "binding_id"],
          additionalProperties: false,
          properties: {
            method: { const: "serial.enumerate" },
            binding_id: { type: "string", minLength: 1 },
            max_frame_bytes: { type: ["integer", "null"], minimum: 0 },
          },
        },
      },
    ],
  },
};
async function select(label: string, name: string) {
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp(`${label}$`) }),
  );
  await userEvent.click(await screen.findByRole("option", { name }));
}
function mount(runs: any[] = []) {
  const api = {
    get: vi.fn(async (path: string): Promise<any> => {
      if (path.includes("/records?"))
        throw new Error("Discovery must not request numeric record windows");
      if (path === "/api/lab/catalog") return catalog;
      if (path === "/api/lab/targets")
        return [{ id: "local", name: "Explicit execution target" }];
      if (path === "/api/lab/runs") return runs;
      if (path.endsWith("/recovered-evidence"))
        return {
          classification: "operator_recovered_evidence",
          references: [],
        };
      if (path.includes("/events?")) return { events: [], next_after: 0 };
      const run = runs.find((item) => path === `/api/lab/runs/${item.id}`);
      return run ?? [];
    }),
    post: vi.fn(async (_path: string, body: any) => ({
      id: "saved-discovery",
      name: body.definition.name,
      revision: 1,
      definition: body.definition,
    })),
    put: vi.fn(),
    download: vi.fn(async () => {
      throw new Error("No unrequested artifact I/O");
    }),
  };
  const studio = {
    api,
    scopeKey: "researcher:alpha",
    project: "alpha",
    route: "/lab",
    user: { id: "researcher" },
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
  } as unknown as StudioRuntime;
  render(
    <StudioContext.Provider value={studio}>
      <LabPage />
    </StudioContext.Provider>,
  );
  return api;
}
it("authors and saves the advertised native discovery method through New experiment without discovery I/O", async () => {
  const api = mount();
  fireEvent.click(screen.getByRole("button", { name: "New experiment" }));
  await select("Experiment engine", "Hardware discovery");
  expect(
    screen.queryByRole("heading", { name: "Engine parameters" }),
  ).toBeNull();
  expect(screen.getByLabelText("Operator discovery binding")).toHaveValue("");
  expect(
    screen.getByRole("button", { name: "Save experiment" }),
  ).toBeDisabled();
  await select("Hardware discovery method", "Catalog serial metadata");
  fireEvent.change(screen.getByLabelText("Operator discovery binding"), {
    target: { value: "exact-host-binding" },
  });
  fireEvent.change(
    screen.getByLabelText("Advanced discovery parameters JSON"),
    { target: { value: '{"max_frame_bytes":null}' } },
  );
  fireEvent.change(screen.getByLabelText("Mode"), {
    target: { value: "online" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith(
      "/api/lab/definitions",
      expect.objectContaining({
        definition: expect.objectContaining({
          engine: "hardware.discover",
          domain: "hardware",
          mode: "online",
          operating_mode: "acquisition",
          parameters: {
            method: "serial.enumerate",
            binding_id: "exact-host-binding",
            max_frame_bytes: null,
          },
        }),
      }),
      expect.any(AbortSignal),
    ),
  );
  expect(
    api.get.mock.calls.some(([path]) => /discover|methods/.test(path)),
  ).toBe(false);
  expect(api.download).not.toHaveBeenCalled();
}, 20000);
it.each(["definition", "artifact"])(
  "routes retained discovery from its %s to report guidance and trace identities without numeric record requests",
  async (source) => {
    const run = {
      id: "discovery-run",
      definition_id: "definition",
      state: "succeeded",
      spec:
        source === "definition"
          ? { definition: makeDefinition("hardware.discover") }
          : {},
      result: {
        artifacts: [
          {
            id: "discovery-report",
            name: "device-discovery.json",
            role: "device_discovery",
          },
          {
            id: "wire-identity",
            name: "discovery-wire.jsonl",
            role: "discovery_wire",
            sha256: "a".repeat(64),
          },
        ],
      },
    };
    const api = mount([run]);
    const selector = await screen.findByRole("button", {
      name: /Retained run/,
    });
    await waitFor(() => expect(selector).toBeEnabled());
    fireEvent.click(selector);
    const options = await screen.findAllByRole("option");
    fireEvent.click(
      options.find((option) => option.textContent?.includes("discovery-run"))!,
    );
    expect(await screen.findByRole("tab", { name: "Discovery" })).toBeVisible();
    // Missing SHA is rejected before transport access, while manifest identities remain inspectable.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /verifiable SHA-256/,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Records" }));
    expect(
      await screen.findByRole("heading", {
        name: "Results use reports and supporting artifacts",
      }),
    ).toBeVisible();
    expect(screen.getByText("wire-identity")).toBeVisible();
    expect(screen.getByText(/not numeric observation records/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Next records" })).toBeNull();
    expect(
      api.get.mock.calls.some(([path]) => path.includes("/records?")),
    ).toBe(false);
    fireEvent.click(
      screen.getByRole("button", { name: "Open discovery report" }),
    );
    expect(screen.getByRole("tab", { name: "Discovery" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(api.download).not.toHaveBeenCalled();
  },
  20000,
);
