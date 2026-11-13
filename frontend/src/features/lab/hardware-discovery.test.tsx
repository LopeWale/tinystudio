import { Blob } from "node:buffer";
import { createHash, webcrypto } from "node:crypto";
import { useState } from "react";
import {
  act,
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
import { scientificJson } from "../../api/scientific-json";
import { makeDefinition, type LabRun } from "./definition";
import {
  HardwareDiscoveryFields,
  HardwareDiscoveryResult,
} from "./hardware-discovery";
import type { HardwareDiscoveryMethod } from "./hardware-discovery-api";

beforeEach(() => {
  vi.stubGlobal("Blob", Blob);
  vi.stubGlobal("crypto", webcrypto);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
const hash = (text: string) => createHash("sha256").update(text).digest("hex");
const methods: HardwareDiscoveryMethod[] = [
  {
    id: "can.enumerate",
    label: "Selected CAN providers",
    schema: {
      type: "object",
      required: ["method", "binding_id", "interfaces"],
      additionalProperties: false,
      properties: {
        method: { const: "can.enumerate" },
        binding_id: { type: "string", minLength: 1 },
        interfaces: {
          type: "array",
          minItems: 1,
          uniqueItems: true,
          items: { type: "string", minLength: 1 },
        },
        timeout: { type: "number", minimum: 0 },
      },
    },
  },
  {
    id: "serial.enumerate",
    label: "Serial metadata",
    required: ["method", "binding_id"],
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
  {
    id: "sunspec.scan",
    label: "Authored SunSpec scan",
    schema: {
      type: "object",
      required: ["method", "binding_id", "scan"],
      additionalProperties: false,
      properties: {
        method: { const: "sunspec.scan" },
        binding_id: { type: "string", minLength: 1 },
        timeout: { type: "number", minimum: 0 },
        scan: {
          type: "object",
          required: ["base_addresses", "device_id"],
          additionalProperties: false,
          properties: {
            base_addresses: {
              type: "array",
              minItems: 1,
              items: { type: "integer", minimum: 0, maximum: 65535 },
            },
            device_id: { type: "integer", minimum: 0, maximum: 255 },
            register_kind: { enum: ["holding_registers", "input_registers"] },
            max_models: { type: ["integer", "null"], minimum: 0 },
            read_model_data: { type: "boolean" },
          },
        },
      },
    },
  },
];
async function select(label: string, name: string) {
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp(`${label}$`) }),
  );
  await userEvent.click(await screen.findByRole("option", { name }));
}
function Fields({
  parameters = {},
  operatingMode = "acquisition",
}: {
  parameters?: Record<string, unknown>;
  operatingMode?: string;
}) {
  const [definition, setDefinition] = useState({
    ...makeDefinition("hardware.discover"),
    parameters,
    operating_mode: operatingMode,
  });
  const [valid, setValid] = useState(false);
  return (
    <>
      <HardwareDiscoveryFields
        definition={definition}
        methods={methods}
        onChange={(patch) =>
          setDefinition((current) => ({ ...current, ...patch }))
        }
        onValidityChange={setValid}
      />
      <output data-testid="parameters">
        {scientificJson.stringify(definition.parameters)}
      </output>
      <output data-testid="valid">{String(valid)}</output>
      <output data-testid="operating-mode">{definition.operating_mode}</output>
    </>
  );
}
const current = () =>
  scientificJson.parse(screen.getByTestId("parameters").textContent!);
it("authors discovery explicitly without automatic I/O, guessed bindings, scan addresses or resource limits", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const definition = makeDefinition("hardware.discover");
  expect(definition.domain).toBe("hardware");
  expect(definition.operating_mode).toBe("acquisition");
  expect(definition.parameters).toEqual({});
  render(<Fields />);
  expect(screen.getByLabelText("Operator discovery binding")).toHaveValue("");
  expect(current()).toEqual({});
  await select("Hardware discovery method", "Authored SunSpec scan");
  fireEvent.change(screen.getByLabelText("Operator discovery binding"), {
    target: { value: "operator-authored-binding" },
  });
  expect(current()).toEqual({
    method: "sunspec.scan",
    binding_id: "operator-authored-binding",
  });
  expect(screen.getByTestId("valid")).toHaveTextContent("false");
  fireEvent.change(
    screen.getByLabelText("Advanced discovery parameters JSON"),
    {
      target: {
        value:
          '{"scan":{"base_addresses":[117],"device_id":19,"max_models":null}}',
      },
    },
  );
  expect(current()).toEqual({
    method: "sunspec.scan",
    binding_id: "operator-authored-binding",
    scan: { base_addresses: [117], device_id: 19, max_models: null },
  });
  expect(screen.getByTestId("valid")).toHaveTextContent("true");
  expect(fetcher).not.toHaveBeenCalled();
}, 20000);
it("preserves incompatible advanced fields across method changes and visibly requires correction", async () => {
  render(
    <Fields
      parameters={{
        method: "sunspec.scan",
        binding_id: "exact-binding",
        scan: { base_addresses: [231], device_id: 7 },
        legacy_precision: "9007199254740993",
      }}
    />,
  );
  await select("Hardware discovery method", "Serial metadata");
  expect(current()).toEqual({
    method: "serial.enumerate",
    binding_id: "exact-binding",
    scan: { base_addresses: [231], device_id: 7 },
    legacy_precision: "9007199254740993",
  });
  expect(
    (
      screen.getByLabelText(
        "Advanced discovery parameters JSON",
      ) as HTMLTextAreaElement
    ).value,
  ).toContain("9007199254740993");
  expect(screen.getByRole("alert")).toHaveTextContent(/unsupported.*scan/i);
  expect(screen.getByTestId("valid")).toHaveTextContent("false");
  fireEvent.change(
    screen.getByLabelText("Advanced discovery parameters JSON"),
    { target: { value: "{}" } },
  );
  expect(current()).toEqual({
    method: "serial.enumerate",
    binding_id: "exact-binding",
  });
  expect(screen.getByTestId("valid")).toHaveTextContent("true");
  fireEvent.change(
    screen.getByLabelText("Advanced discovery parameters JSON"),
    { target: { value: '{"count":9007199254740993}' } },
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    /exact JSON representation/,
  );
  expect(screen.getByTestId("valid")).toHaveTextContent("false");
}, 20000);

it("requires authored CAN providers and retains an explicit zero timeout", async () => {
  render(<Fields />);
  await select("Hardware discovery method", "Selected CAN providers");
  fireEvent.change(screen.getByLabelText("Operator discovery binding"), {
    target: { value: "can-grant" },
  });
  expect(current()).not.toHaveProperty("interfaces");
  expect(current()).not.toHaveProperty("timeout");
  expect(screen.getByTestId("valid")).toHaveTextContent("false");
  fireEvent.change(
    screen.getByLabelText("Advanced discovery parameters JSON"),
    { target: { value: '{"interfaces":["researcher-provider"],"timeout":0}' } },
  );
  expect(current()).toEqual({
    method: "can.enumerate",
    binding_id: "can-grant",
    interfaces: ["researcher-provider"],
    timeout: 0,
  });
  expect(screen.getByTestId("valid")).toHaveTextContent("true");
  fireEvent.change(
    screen.getByLabelText("Advanced discovery parameters JSON"),
    {
      target: {
        value: '{"interfaces":["researcher-provider","researcher-provider"]}',
      },
    },
  );
  expect(screen.getByRole("alert")).toHaveTextContent(/duplicate/);
  expect(screen.getByTestId("valid")).toHaveTextContent("false");
}, 20000);

it.each(["replay", "simulation", "shadow", "physical_control"])(
  "retains imported %s mode visibly until explicitly corrected to acquisition",
  async (operatingMode) => {
    render(
      <Fields
        operatingMode={operatingMode}
        parameters={{ method: "serial.enumerate", binding_id: "grant-a" }}
      />,
    );
    expect(screen.getByTestId("operating-mode")).toHaveTextContent(
      operatingMode,
    );
    expect(screen.getByTestId("valid")).toHaveTextContent("false");
    expect(screen.getByRole("alert")).toHaveTextContent(
      /Live discovery requires acquisition/,
    );
    await select("Discovery operating mode", "Acquisition");
    expect(screen.getByTestId("operating-mode")).toHaveTextContent(
      "acquisition",
    );
    expect(screen.getByTestId("valid")).toHaveTextContent("true");
  },
  20000,
);
function report(status = "partial"): any {
  const config = {
    method: "sunspec.scan",
    binding_id: "grant-a",
    max_frame_bytes: null,
    timeout: 0,
    scan: {
      base_addresses: [117],
      device_id: 19,
      register_kind: "holding_registers",
      max_models: null,
      read_model_data: false,
    },
  };
  return {
    format: "tinystudio.hardware-discovery.v1",
    engine: "hardware.discover",
    method: "sunspec.scan",
    binding_id: "grant-a",
    status,
    requested_configuration: config,
    effective_configuration: config,
    resource_limits: {},
    budget_scope: {
      max_models: "per_candidate_base",
      max_frame_bytes: "per_trace_packet_prefix",
    },
    source: {
      binding_id: "grant-a",
      endpoint: {
        host: "observed-device",
        port: 1502,
        framing: "socket",
        allow_dns: false,
      },
    },
    discovery: {
      format: "tinystudio.sunspec-discovery.v1",
      config: config.scan,
      status: "partial",
      complete: false,
      registers_read: 6,
      trials: [
        {
          base_address: 117,
          device_id: 19,
          register_kind: "holding_registers",
          status: "partial",
          complete: false,
          signature: [21365, 28243],
          headers: [{ address: 119, registers: [103, 9] }],
          models: [
            {
              model_id: 103,
              instance: 0,
              header_address: 119,
              payload_address: 121,
              length: 9,
              header_registers: [103, 9],
              registers: [],
              data_reads: [],
              status: "header_only",
            },
          ],
          reads: [],
          registers_read: 6,
          end_marker: null,
        },
      ],
    },
    clock: {
      basis: "execution_host_observation",
      started: { wall_time: 1.25, monotonic: 0.5 },
      finished: { wall_time: 2.5, monotonic: 1.75 },
    },
    implementation_sha256: "a".repeat(64),
    qualification: {
      qualified: false,
      physical_validation: false,
      scope: "Structural discovery only.",
    },
    runtime: {
      python: "observed-python",
      platform: "observed-platform",
      dependencies: [
        {
          name: "pymodbus",
          version: "observed-version",
          loaded: true,
          module_version: null,
        },
      ],
    },
    extra_exact: "9007199254740993",
  };
}
function run(payload = report(), id = "run-a", checksum?: string): LabRun {
  return {
    id,
    definition_id: "definition-a",
    state: "succeeded",
    result: {
      artifacts: [
        {
          id: `report-${id}`,
          name: "device-discovery.json",
          role: "device_discovery",
          sha256: checksum ?? hash(scientificJson.stringify(payload)),
        },
        {
          id: `wire-${id}`,
          name: "discovery-wire.jsonl",
          role: "discovery_wire",
          sha256: "b".repeat(64),
        },
      ],
    },
  };
}
function runtime(
  download: ReturnType<typeof vi.fn>,
  project = "alpha",
): StudioRuntime {
  return {
    api: { download },
    project,
    scopeKey: `researcher:${project}`,
    route: "/lab",
    user: { id: "researcher" },
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
  } as unknown as StudioRuntime;
}
const view = (studio: StudioRuntime, currentRun: LabRun) => (
  <StudioContext.Provider value={studio}>
    <HardwareDiscoveryResult run={currentRun} />
  </StudioContext.Provider>
);
it.each([
  "complete",
  "partial",
  "not_found",
  "failed",
  "unavailable",
  "cancelled",
])(
  "shows retained %s discovery status separately from a succeeded Lab job with structural evidence and trace identities",
  async (status) => {
    const payload = report(status),
      download = vi.fn(
        async () => new Blob([scientificJson.stringify(payload)]),
      );
    render(view(runtime(download), run(payload)));
    expect(
      await screen.findByRole("heading", {
        name: `Hardware discovery ${status}`,
      }),
    ).toBeVisible();
    expect(screen.getByText(/Laboratory execution: succeeded/)).toBeVisible();
    expect(screen.getByText(/Physical validation: false/)).toBeVisible();
    expect(screen.getByText(/Qualified: false/)).toBeVisible();
    expect(screen.getByText("observed-device")).toBeVisible();
    expect(
      screen.getByRole("table", { name: "Discovered SunSpec model addresses" }),
    ).toHaveTextContent("103");
    expect(
      screen.getByRole("table", { name: "Discovered SunSpec model addresses" }),
    ).toHaveTextContent("121");
    expect(screen.getByText(/point.*offset.*count/i)).toBeVisible();
    expect(screen.getByText("wire-run-a")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Download discovery-wire.jsonl" }),
    ).toBeVisible();
    expect(download).toHaveBeenCalledTimes(1);
  },
);
it.each(["checksum", "qualification", "model"])(
  "rejects tampered or malformed %s report evidence",
  async (kind) => {
    const payload = report();
    if (kind === "qualification")
      payload.qualification.physical_validation = true;
    if (kind === "model")
      payload.discovery.trials[0].models[0].length = "guessed";
    const download = vi.fn(
      async () => new Blob([scientificJson.stringify(payload)]),
    );
    render(
      view(
        runtime(download),
        run(payload, "run-a", kind === "checksum" ? "f".repeat(64) : undefined),
      ),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      kind === "checksum" ? /checksum mismatch/ : /invalid|inconsistent/,
    );
    expect(
      screen.queryByRole("heading", { name: /Hardware discovery partial/ }),
    ).toBeNull();
  },
);
it("discards stale project and run responses even when transport ignores abort", async () => {
  const payload = report(),
    pending: ((blob: Blob) => void)[] = [];
  const download = vi.fn(
    () => new Promise<Blob>((resolve) => pending.push(resolve)),
  );
  const rendered = render(view(runtime(download), run(payload)));
  await waitFor(() => expect(pending).toHaveLength(1));
  rendered.rerender(view(runtime(download, "beta"), run(payload, "run-b")));
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () =>
    pending[0](new Blob([scientificJson.stringify(payload)])),
  );
  expect(
    screen.queryByRole("heading", { name: /Hardware discovery partial/ }),
  ).toBeNull();
  const currentPayload = report("cancelled");
  // Match the current run metadata to its current retained bytes.
  rendered.rerender(
    view(runtime(download, "beta"), run(currentPayload, "run-c")),
  );
  await waitFor(() => expect(pending).toHaveLength(3));
  await act(async () =>
    pending[1](new Blob([scientificJson.stringify(payload)])),
  );
  expect(
    screen.queryByRole("heading", { name: /Hardware discovery partial/ }),
  ).toBeNull();
  await act(async () =>
    pending[2](new Blob([scientificJson.stringify(currentPayload)])),
  );
  expect(
    await screen.findByRole("heading", {
      name: "Hardware discovery cancelled",
    }),
  ).toBeVisible();
});

it("downloads the original checksum-verified report bytes and independently verifies wire receipt bytes", async () => {
  const payload = report(),
    raw = `  ${scientificJson.stringify(payload, 2)}\n\n`;
  const wire =
    '{"direction":"rx","size":3,"captured_bytes":0,"truncated":true,"prefix_base64":"","sha256":"retained-packet-digest"}\n';
  const currentRun = run(payload, "raw-bytes", hash(raw));
  const artifacts = (currentRun.result as any).artifacts;
  artifacts[1] = { ...artifacts[1], id: "wire /?alpha", sha256: hash(wire) };
  const reportBlob = new Blob([raw]),
    wireBlob = new Blob([wire]);
  const download = vi.fn(async (path: string) =>
    path.includes("report-") ? reportBlob : wireBlob,
  );
  const createObjectURL = vi.fn((_blob: Blob) => "blob:checked-bytes"),
    revokeObjectURL = vi.fn();
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(view(runtime(download), currentRun));
  await screen.findByRole("heading", { name: "Hardware discovery partial" });
  fireEvent.click(
    screen.getByRole("button", {
      name: "Download retained hardware discovery JSON",
    }),
  );
  await waitFor(() => expect(createObjectURL).toHaveBeenCalledWith(reportBlob));
  expect(
    await (createObjectURL.mock.calls[0][0] as unknown as Blob).text(),
  ).toBe(raw);
  expect(download).toHaveBeenCalledTimes(1);
  fireEvent.click(
    screen.getByRole("button", { name: "Download discovery-wire.jsonl" }),
  );
  await waitFor(() => expect(createObjectURL).toHaveBeenCalledWith(wireBlob));
  expect(download).toHaveBeenLastCalledWith(
    "/api/lab/artifacts/wire%20%2F%3Falpha",
    expect.any(AbortSignal),
  );
  expect(revokeObjectURL).toHaveBeenCalledTimes(2);
});

it("refuses tampered wire downloads without publishing unchecked bytes", async () => {
  const payload = report();
  const download = vi.fn(
    async (path: string) =>
      new Blob([
        path.includes("report-")
          ? scientificJson.stringify(payload)
          : "altered-wire-bytes",
      ]),
  );
  const createObjectURL = vi.fn();
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() });
  render(view(runtime(download), run(payload)));
  await screen.findByRole("heading", { name: "Hardware discovery partial" });
  fireEvent.click(
    screen.getByRole("button", { name: "Download discovery-wire.jsonl" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /artifact checksum mismatch/,
  );
  expect(createObjectURL).not.toHaveBeenCalled();
});

it("rejects unsafe literal integers in checksum-valid report JSON", async () => {
  const payload = report();
  const raw = scientificJson
    .stringify(payload)
    .replace('"9007199254740993"', "9007199254740993");
  const download = vi.fn(async () => new Blob([raw]));
  render(view(runtime(download), run(payload, "unsafe-integer", hash(raw))));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /exact JSON representation/,
  );
  expect(
    screen.queryByRole("heading", { name: /Hardware discovery partial/ }),
  ).toBeNull();
});

it.each(["serial.enumerate", "can.enumerate"] as const)(
  "shows exact retained %s metadata without SunSpec assumptions",
  async (method) => {
    const payload = report("complete");
    payload.method = method;
    payload.requested_configuration = { method, binding_id: "grant-a" };
    payload.effective_configuration = {
      method,
      binding_id: "grant-a",
      max_frame_bytes: null,
    };
    payload.discovery =
      method === "serial.enumerate"
        ? {
            format: "tinystudio.serial-enumeration.v1",
            status: "ok",
            ports: [
              {
                device: "observed-port",
                serial_number: "9007199254740993",
                description: null,
              },
            ],
            limitations: ["Metadata only"],
          }
        : {
            format: "tinystudio.can-enumeration.v1",
            status: "ok",
            interfaces: ["observed-provider"],
            configurations: [
              { channel: "observed-channel", interface: "observed-provider" },
            ],
            timeout: 0,
            discovery_may_send_traffic: true,
          };
    const download = vi.fn(
      async () => new Blob([scientificJson.stringify(payload)]),
    );
    render(view(runtime(download), run(payload)));
    expect(
      await screen.findByRole("heading", {
        name: "Hardware discovery complete",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("region", {
        name:
          method === "serial.enumerate"
            ? "Serial port metadata"
            : "CAN provider detection",
      }),
    ).toHaveTextContent(
      method === "serial.enumerate" ? "observed-port" : "observed-channel",
    );
    expect(
      screen.queryByRole("table", {
        name: "Discovered SunSpec model addresses",
      }),
    ).toBeNull();
  },
);

it("retains an unavailable outcome when discovery failed before a structural report was available", async () => {
  const payload = report("unavailable");
  payload.discovery = null;
  payload.error = { type: "ImportError", message: "Observed SDK unavailable" };
  const download = vi.fn(
    async () => new Blob([scientificJson.stringify(payload)]),
  );
  render(view(runtime(download), run(payload)));
  expect(
    await screen.findByRole("heading", {
      name: "Hardware discovery unavailable",
    }),
  ).toBeVisible();
  expect(screen.getByLabelText("Discovery error")).toHaveTextContent(
    "Observed SDK unavailable",
  );
});

it("discards a late wire download after the project changes", async () => {
  const payload = report(),
    wire = "retained-wire-bytes\n";
  const currentRun = run(payload);
  (currentRun.result as any).artifacts[1].sha256 = hash(wire);
  let finish!: (blob: Blob) => void;
  const download = vi.fn(async (path: string, _signal?: AbortSignal) =>
    path.includes("report-")
      ? new Blob([scientificJson.stringify(payload)])
      : new Promise<Blob>((resolve) => {
          finish = resolve;
        }),
  );
  const createObjectURL = vi.fn();
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() });
  const rendered = render(view(runtime(download), currentRun));
  await screen.findByRole("heading", { name: "Hardware discovery partial" });
  fireEvent.click(
    screen.getByRole("button", { name: "Download discovery-wire.jsonl" }),
  );
  await waitFor(() => expect(download).toHaveBeenCalledTimes(2));
  const pendingSignal = download.mock.calls[1][1];
  rendered.rerender(view(runtime(download, "beta"), currentRun));
  await act(async () => finish(new Blob([wire])));
  expect(pendingSignal?.aborted).toBe(true);
  expect(createObjectURL).not.toHaveBeenCalled();
});
