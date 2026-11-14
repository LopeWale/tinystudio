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

function mount() {
  const api = {
    get: vi.fn(async (path: string): Promise<any> => {
      if (path === "/api/lab/catalog") return { adapters: [], methods: [] };
      if (path === "/api/lab/inputs")
        return [
          {
            id: "uploaded-source",
            name: "owned-supervisor",
            sha256: "a".repeat(64),
            size: 12,
            media_type: "text/plain",
          },
        ];
      if (path.includes("/events?")) return { events: [], next_after: 0 };
      return [];
    }),
    post: vi.fn(async (_path: string, body: any) => ({
      id: "saved-session",
      name: body.definition.name,
      revision: 1,
      definition: body.definition,
    })),
    put: vi.fn(),
    download: vi.fn(),
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

it.each(["recorded", "python"])(
  "authors %s supervisors through generic fields without discarding invalid JSON on attachment",
  async (kind) => {
    const api = mount();
    fireEvent.click(screen.getByRole("button", { name: "New experiment" }));
    await userEvent.click(
      screen.getByRole("button", { name: /Experiment engine$/ }),
    );
    await userEvent.click(
      await screen.findByRole("option", { name: "Hardware session" }),
    );
    expect(
      screen.getByRole("heading", { name: "Engine parameters" }),
    ).toBeVisible();
    fireEvent.change(screen.getByLabelText("binding id"), {
      target: { value: "explicit-operator-binding" },
    });
    const incomplete = '{"kind":"python",';
    fireEvent.change(screen.getByRole("textbox", { name: "supervisor" }), {
      target: { value: incomplete },
    });
    expect(
      screen.getByRole("button", { name: "Save experiment" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("tab", { name: "Inputs" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Attach owned-supervisor" }),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Parameters" }));
    expect(screen.getByRole("textbox", { name: "supervisor" })).toHaveValue(
      incomplete,
    );
    expect(
      screen.getByRole("button", { name: "Save experiment" }),
    ).toBeDisabled();
    const supervisor = {
      kind,
      artifact_id: "uploaded-source",
      ...(kind === "python" ? { callable: "custom.program" } : {}),
    };
    fireEvent.change(screen.getByRole("textbox", { name: "supervisor" }), {
      target: { value: JSON.stringify(supervisor) },
    });
    const parameters = {
      arbitrary: [null, false, -0.25],
      payload: { data: { encoding: "base64", value: "AAH/DQo=" } },
    };
    fireEvent.change(
      screen.getByRole("textbox", { name: "supervisor parameters" }),
      { target: { value: JSON.stringify(parameters) } },
    );
    fireEvent.change(screen.getByRole("textbox", { name: "evidence" }), {
      target: { value: '{"flush_records":null,"fsync":false}' },
    });
    fireEvent.change(
      screen.getByRole("textbox", { name: "artifact visibility" }),
      { target: { value: "truth" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith(
        "/api/lab/definitions",
        expect.objectContaining({
          definition: expect.objectContaining({
            engine: "hardware.session",
            execution: expect.objectContaining({
              backend: "native",
              profile: "trusted_native",
            }),
            inputs: [
              {
                artifact_id: "uploaded-source",
                sha256: "a".repeat(64),
                role: "supervisor",
                visibility: "model",
              },
            ],
            parameters: {
              binding_id: "explicit-operator-binding",
              supervisor,
              supervisor_parameters: parameters,
              evidence: { flush_records: null, fsync: false },
              ipc_timeout_seconds: null,
              artifact_visibility: "truth",
            },
          }),
        }),
        expect.any(AbortSignal),
      ),
    );
    expect(api.download).not.toHaveBeenCalled();
  },
  20000,
);
