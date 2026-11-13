import { Blob } from "node:buffer";
import { createHash, webcrypto } from "node:crypto";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { createApiClient } from "../../api/client";
import { scientificJson } from "../../api/scientific-json";
import { WorkspaceScope } from "../../controllers/workspaceScope";
import type { LabRun } from "./definition";
import { RuntimeProbeResult } from "./runtime-probe";

beforeEach(() => {
  vi.stubGlobal("Blob", Blob);
  vi.stubGlobal("crypto", webcrypto);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
const hash = (bytes: string) =>
  createHash("sha256").update(bytes).digest("hex");
function report(
  status: "verified" | "failed" | "unavailable" = "verified",
): any {
  const configuration = {
    method: "nf4.training",
    objective: "sft",
    fixture: "retained",
    device: "cpu",
    dtype: "float32",
    seed: 31,
    training: { steps: 3, learning_rate: 0.0007 },
    fixture_config: {},
    nf4_execution: { cpu_inference: "portable" },
    reload_comparison: { mode: "bitwise" },
    inputs: [
      {
        artifact_id: "model-1",
        role: "pretrained_model",
        sha256: "a".repeat(64),
      },
      { artifact_id: "data-1", role: "snapshot", sha256: "b".repeat(64) },
    ],
  };
  const scope = { ...configuration, generalizes_to_other_models: false };
  return {
    format: "tinystudio.runtime-probe.v1",
    method_version: "1.0.0",
    implementation_sha256: "c".repeat(64),
    status,
    stage:
      status === "unavailable"
        ? "prerequisite_inspection"
        : "evidence_verification",
    requested_configuration: {
      parameters: {
        method: "nf4.training",
        objective: "sft",
        fixture: "retained",
        device: "cpu",
        dtype: "float32",
        nf4_execution: { cpu_inference: "portable" },
        reload_comparison: { mode: "bitwise" },
      },
      seed: 31,
      inputs: configuration.inputs,
    },
    configuration,
    scope,
    qualification: {
      kind: "runtime_compatibility_probe",
      status,
      qualified: status === "verified",
      scope,
      generalization:
        "Only the selected inputs and measured operation; other models remain unprobed.",
    },
    prerequisites: {
      format: "tinystudio.runtime-prerequisites.v1",
      status: status === "unavailable" ? "unavailable" : "unprobed",
      qualified: false,
      eligible: status !== "unavailable",
      reason:
        status === "unavailable"
          ? "Selected runtime dependencies are unavailable."
          : "Prerequisites found; measurement is separate.",
      selection:
        status === "unavailable" ? null : { device: "cpu", dtype: "float32" },
      inventory: {
        os: "Linux",
        machine: "observed-machine",
        python: "3.12.2",
        dependencies: {
          torch: "observed-torch-version",
          bitsandbytes:
            status === "unavailable" ? null : "observed-bnb-version",
        },
        metadata_errors: {},
      },
      missing_dependencies: status === "unavailable" ? ["bitsandbytes"] : [],
    },
    stages:
      status === "unavailable"
        ? [
            { stage: "selection_validation", status: "completed" },
            { stage: "prerequisite_inspection", status: "completed" },
          ]
        : [
            { stage: "selection_validation", status: "completed" },
            { stage: "training_and_reload", status: "completed" },
            {
              stage: "evidence_verification",
              status: status === "failed" ? "failed" : "completed",
            },
          ],
    error:
      status === "verified"
        ? null
        : {
            type:
              status === "failed"
                ? "ReloadVerificationError"
                : "RuntimeUnavailable",
            message:
              status === "failed"
                ? "Reload probe did not match the saved state."
                : "Selected runtime dependencies are unavailable.",
          },
    diagnostics:
      status === "verified"
        ? []
        : [
            {
              stage:
                status === "failed"
                  ? "evidence_verification"
                  : "prerequisite_inspection",
              code: status,
              type: "MeasuredDiagnostic",
              message: "The retained measurement did not verify compatibility.",
            },
          ],
    receipt:
      status === "failed"
        ? { status: "failed", checks: { base_state: false } }
        : null,
    evidence:
      status === "verified"
        ? {
            optimization: {
              adapter_changed: true,
              optimizer_parameters_verified: true,
              frozen_base_verified: true,
              adapter_before_sha256: "d".repeat(64),
              adapter_after_sha256: "e".repeat(64),
            },
            nf4_settings: { quant_type: "nf4", compute_dtypes: ["float32"] },
            execution_policy: configuration.nf4_execution,
            execution_configuration: {
              qualified: false,
              cpu_inference: "portable",
            },
            runtime_verification: {
              status: "passed",
              comparison_policy: configuration.reload_comparison,
              observed_runtime: { device: "cpu", dtype: "float32" },
              checks: { base_state: true },
            },
            source_inputs: configuration.inputs,
            training_config: {
              device: "cpu",
              dtype: "float32",
              objective: "sft",
              seed: 31,
            },
            pretrained_manifest_sha256: "f".repeat(64),
            observed_values: { signed_zero: -0, approximate_float: 1e30 },
          }
        : {},
  };
}
function run(
  payload: unknown = report(),
  id = "probe-1",
  checksum?: string,
): LabRun {
  return {
    id,
    definition_id: "probe-definition",
    state: "succeeded",
    spec_digest: "1".repeat(64),
    result: {
      artifacts: [
        {
          id: `artifact-${id}`,
          name: "runtime-probe.json",
          role: "runtime_probe",
          sha256: checksum ?? hash(scientificJson.stringify(payload)),
        },
      ],
    },
  };
}
function fixture(
  handler: (
    path: string,
    signal?: AbortSignal | null,
  ) => Response | Promise<Response>,
) {
  let project = "alpha";
  const scope = new WorkspaceScope();
  scope.setIdentity("researcher:alpha");
  const fetcher = vi.fn(async (path: RequestInfo | URL, init?: RequestInit) =>
    handler(String(path), init?.signal),
  );
  const api = createApiClient({
    scope,
    getProject: () => project,
    onUnauthorized: vi.fn(),
    fetcher,
  });
  const evidence = new EvidenceRegistry();
  const runtime = (): StudioRuntime =>
    ({
      api,
      project,
      scopeKey: `researcher:${project}`,
      user: { id: "researcher" },
      route: "/lab",
      evidence,
      toast: vi.fn(),
    }) as unknown as StudioRuntime;
  return {
    fetcher,
    evidence,
    runtime,
    project(name: string) {
      project = name;
      scope.setIdentity(`researcher:${name}`);
    },
  };
}
const response = (payload: unknown) =>
  new Response(scientificJson.stringify(payload), {
    headers: { "content-type": "application/json" },
  });
const view = (value: StudioRuntime, current: LabRun) => (
  <StudioContext.Provider value={value}>
    <RuntimeProbeResult run={current} />
  </StudioContext.Provider>
);

it.each(["failed", "unavailable"] as const)(
  "leads with %s compatibility even when the Laboratory run succeeded",
  async (status) => {
    const payload = report(status);
    if (status === "unavailable") {
      payload.requested_configuration.parameters.device = "cuda:3";
      payload.configuration.device = "cuda:3";
      payload.scope.device = "cuda:3";
    }
    const f = fixture(() => response(payload));
    render(view(f.runtime(), run(payload)));
    expect(
      await screen.findByRole("heading", {
        name: `Runtime compatibility ${status}`,
      }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: /compatibility verified/i }),
    ).toBeNull();
    expect(screen.getByText(payload.error.message)).toBeVisible();
    expect(
      screen.getByText(
        "The retained measurement did not verify compatibility.",
      ),
    ).toBeVisible();
    expect(screen.getByText(/Laboratory execution: succeeded/)).toBeVisible();
    const requested = screen.getByRole("region", {
      name: "Requested runtime probe selection",
    });
    expect(
      within(requested).getByText(status === "unavailable" ? "cuda:3" : "cpu"),
    ).toBeVisible();
    expect(within(requested).getByText("float32")).toBeVisible();
    expect(within(requested).getByText("retained")).toBeVisible();
  },
);

function withSeed(seed: string): any {
  const payload = report();
  payload.integer_encoding = "safe_numbers_or_decimal_strings";
  payload.configuration.seed = seed;
  payload.scope.seed = seed;
  payload.requested_configuration.seed = seed;
  payload.evidence.training_config.seed = seed;
  return payload;
}

it.each(["18446744073709551615", "-9223372036854775808", "9007199254740993"])(
  "preserves canonical seed %s exactly without numeric conversion",
  async (seed) => {
    const payload = withSeed(seed),
      f = fixture(() => response(payload));
    render(view(f.runtime(), run(payload)));
    await screen.findByRole("heading", {
      name: "Runtime compatibility verified for this scope",
    });
    expect(
      within(
        screen.getByRole("region", {
          name: "Requested runtime probe selection",
        }),
      ).getByText(seed),
    ).toBeVisible();
    expect(
      within(
        screen.getByRole("region", { name: "Retained runtime probe scope" }),
      ).getByText(seed),
    ).toBeVisible();
    const retained = f.evidence
      .snapshot()
      .find(
        (entry) =>
          (entry.data as any).report?.integer_encoding ===
          "safe_numbers_or_decimal_strings",
      )!.data as any;
    expect(retained.report.configuration.seed).toBe(seed);
    expect(retained.report.scope.seed).toBe(seed);
    expect(typeof retained.report.configuration.seed).toBe("string");
  },
);

it("rejects an unsafe plain integer seed token without rounding the retained JSON", async () => {
  const seed = "18446744073709551615",
    payload = withSeed(seed);
  const bytes = scientificJson.stringify(payload).replaceAll(`"${seed}"`, seed);
  const f = fixture(() => new Response(bytes));
  render(view(f.runtime(), run(payload, "unsafe-seed", hash(bytes))));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Integer exceeds exact JSON representation",
  );
  expect(
    screen.queryByRole("heading", { name: /compatibility verified/i }),
  ).toBeNull();
});

it("compares adjacent large decimal seed strings exactly across configuration and scope", async () => {
  const payload = withSeed("18446744073709551615");
  payload.scope.seed = "18446744073709551614";
  const f = fixture(() => response(payload));
  render(view(f.runtime(), run(payload)));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /inconsistent.*evidence/i,
  );
  expect(
    screen.queryByRole("heading", { name: /compatibility verified/i }),
  ).toBeNull();
});

it.each(["01", "+31", "-0", "31.0", "1e20", " 31", "31 ", ""])(
  "rejects noncanonical decimal seed text %j",
  async (seed) => {
    const payload = withSeed(seed),
      f = fixture(() => response(payload));
    render(view(f.runtime(), run(payload)));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /invalid.*envelope/i,
    );
    expect(
      screen.queryByRole("heading", { name: /compatibility verified/i }),
    ).toBeNull();
  },
);

it("preserves an authored precision alias separately from the measured data type", async () => {
  const payload = report();
  payload.requested_configuration.parameters.dtype = "fp32";
  const f = fixture(() => response(payload));
  render(view(f.runtime(), run(payload)));
  await screen.findByRole("heading", {
    name: "Runtime compatibility verified for this scope",
  });
  expect(
    within(
      screen.getByRole("region", { name: "Requested runtime probe selection" }),
    ).getByText("fp32"),
  ).toBeVisible();
  expect(
    within(
      screen.getByRole("region", { name: "Retained runtime probe scope" }),
    ).getByText("float32"),
  ).toBeVisible();
});

it("retains cancellation as an unfinished measurement without qualifying the runtime", async () => {
  const payload = report("failed");
  payload.status = "cancelled";
  payload.qualification.status = "cancelled";
  payload.stage = "training_and_reload";
  payload.stages = [
    { stage: "selection_validation", status: "completed" },
    { stage: "training_and_reload", status: "started" },
  ];
  payload.error = {
    type: "ExecutionCancelled",
    message: "The requested measurement was cancelled.",
  };
  payload.diagnostics = [];
  payload.receipt = null;
  const f = fixture(() => response(payload));
  render(view(f.runtime(), run(payload)));
  expect(
    await screen.findByRole("heading", {
      name: "Runtime compatibility cancelled",
    }),
  ).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: /compatibility verified/i }),
  ).toBeNull();
  expect(
    screen.getByText("The requested measurement was cancelled."),
  ).toBeVisible();
});

it("renders verified compatibility only for the retained scope, observed inventory, policy and ordered stages", async () => {
  const payload = report(),
    f = fixture(() => response(payload));
  render(view(f.runtime(), run(payload)));
  expect(
    await screen.findByRole("heading", {
      name: "Runtime compatibility verified for this scope",
    }),
  ).toBeVisible();
  expect(
    screen.getByText(/Other models, platforms and operations remain unprobed/),
  ).toBeVisible();
  expect(screen.getByText("observed-machine")).toBeVisible();
  expect(screen.getByText("observed-torch-version")).toBeVisible();
  expect(screen.getByText(/SHA-256 verified/)).toBeVisible();
  const rows = within(
    screen.getByRole("table", { name: "Ordered runtime probe stages" }),
  )
    .getAllByRole("row")
    .slice(1);
  expect(rows.map((row) => row.textContent)).toEqual([
    expect.stringContaining("selection validation"),
    expect.stringContaining("training and reload"),
    expect.stringContaining("evidence verification"),
  ]);
  expect(
    screen.getByLabelText("Retained NF4 execution policy"),
  ).toHaveTextContent('"cpu_inference": "portable"');
  expect(
    screen.getByLabelText("Retained reload comparison policy"),
  ).toHaveTextContent('"mode": "bitwise"');
  expect(
    screen.getByLabelText("Retained reload comparison policy"),
  ).not.toHaveTextContent("atol");
  expect(f.fetcher).toHaveBeenCalledWith(
    "/api/p/alpha/lab/artifacts/artifact-probe-1",
    expect.objectContaining({ method: "GET" }),
  );
  expect(
    f.evidence
      .snapshot()
      .some(
        (entry) =>
          (entry.data as any).report?.implementation_sha256 ===
          payload.implementation_sha256,
      ),
  ).toBe(true);
});

it("retains an invalid selection failure with explicitly absent normalized configuration and inventory", async () => {
  const payload = report("failed");
  payload.configuration = null;
  payload.prerequisites = null;
  payload.scope = {};
  payload.qualification.scope = {};
  payload.stage = "selection_validation";
  payload.stages = [{ stage: "selection_validation", status: "failed" }];
  payload.error = {
    type: "ValueError",
    message: "dtype requires an explicit selection",
  };
  payload.requested_configuration.parameters.dtype = null;
  const f = fixture(() => response(payload));
  render(view(f.runtime(), run(payload)));
  expect(
    await screen.findByRole("heading", {
      name: "Runtime compatibility failed",
    }),
  ).toBeVisible();
  expect(
    screen.getByText(
      "No normalized configuration was retained; selection validation did not complete.",
    ),
  ).toBeVisible();
  expect(screen.getByText("Runtime inventory was not measured.")).toBeVisible();
  expect(
    screen.getByText("dtype requires an explicit selection"),
  ).toBeVisible();
});

it.each([
  [
    "qualification status",
    (payload: any) => {
      payload.qualification.qualified = false;
    },
  ],
  [
    "qualification scope",
    (payload: any) => {
      payload.qualification.scope = { ...payload.scope, device: "cuda:0" };
    },
  ],
  [
    "universal scope",
    (payload: any) => {
      payload.scope.generalizes_to_other_models = true;
    },
  ],
  [
    "failed evidence",
    (payload: any) => {
      payload.evidence.runtime_verification.status = "failed";
    },
  ],
  [
    "unknown format",
    (payload: any) => {
      payload.format = "unknown";
    },
  ],
] as const)(
  "refuses malformed %s claims after verifying the retained artifact",
  async (_, mutate) => {
    const payload = report();
    mutate(payload);
    const f = fixture(() => response(payload));
    render(view(f.runtime(), run(payload)));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /invalid|inconsistent/i,
    );
    expect(
      screen.queryByRole("heading", { name: /compatibility verified/i }),
    ).toBeNull();
  },
);

it("refuses corrupt artifacts and retries a transport failure without substituting a result", async () => {
  const payload = report();
  let attempts = 0;
  const f = fixture(() =>
    ++attempts === 1
      ? new Response('{"detail":"Stored report temporarily unavailable"}', {
          status: 503,
          headers: { "content-type": "application/json" },
        })
      : response(payload),
  );
  const rendered = render(view(f.runtime(), run(payload)));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Stored report temporarily unavailable",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Retry runtime probe report" }),
  );
  await screen.findByRole("heading", { name: /compatibility verified/i });
  rendered.rerender(view(f.runtime(), run(payload, "corrupt", "0".repeat(64))));
  expect(
    screen.queryByRole("heading", { name: /compatibility verified/i }),
  ).toBeNull();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Runtime probe checksum mismatch",
  );
});

it("masks changed runs and ignores a late response after project ownership changes", async () => {
  let finish!: (response: Response) => void,
    pendingSignal: AbortSignal | null | undefined;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const f = fixture((path, signal) => {
    if (path.endsWith("artifact-pending")) {
      pendingSignal = signal;
      return pending;
    }
    return response(path.includes("/beta/") ? report("unavailable") : report());
  });
  const rendered = render(view(f.runtime(), run()));
  await screen.findByRole("heading", { name: /compatibility verified/i });
  rendered.rerender(view(f.runtime(), run(report(), "pending")));
  expect(
    screen.queryByRole("heading", { name: /compatibility verified/i }),
  ).toBeNull();
  await waitFor(() => expect(pendingSignal).toBeDefined());
  f.project("beta");
  rendered.rerender(view(f.runtime(), run(report("unavailable"), "beta")));
  expect(pendingSignal?.aborted).toBe(true);
  await screen.findByRole("heading", {
    name: "Runtime compatibility unavailable",
  });
  await act(async () => finish(response(report())));
  expect(
    screen.queryByRole("heading", { name: /compatibility verified/i }),
  ).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("downloads the original verified JSON bytes and retains all numerical evidence", async () => {
  const payload = report(),
    bytes = scientificJson.stringify(payload, 4) + "\n",
    f = fixture(() => new Response(bytes));
  const makeUrl = vi.fn((_blob: unknown) => "blob:verified-report"),
    revokeUrl = vi.fn((_url: string) => {});
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = makeUrl;
      static revokeObjectURL = revokeUrl;
    },
  );
  const clicked = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  render(view(f.runtime(), run(payload, "download", hash(bytes))));
  await screen.findByRole("heading", { name: /compatibility verified/i });
  fireEvent.click(
    screen.getByRole("button", {
      name: "Download retained runtime probe JSON",
    }),
  );
  expect(clicked).toHaveBeenCalledOnce();
  expect(clicked.mock.instances[0]).toHaveAttribute(
    "download",
    "runtime-probe.json",
  );
  expect(await (makeUrl.mock.calls[0][0] as Blob).text()).toBe(bytes);
  expect(bytes).toContain('"signed_zero": -0.0');
  expect(revokeUrl).toHaveBeenCalledWith("blob:verified-report");
});

it("reports a missing retained measurement without deriving qualification from run success", () => {
  const f = fixture(() => response(report()));
  render(
    view(f.runtime(), {
      id: "empty",
      definition_id: "probe-definition",
      state: "succeeded",
      result: { artifacts: [] },
    }),
  );
  expect(screen.getByText(/No retained runtime probe report/)).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: /compatibility verified/i }),
  ).toBeNull();
  expect(f.fetcher).not.toHaveBeenCalled();
});
