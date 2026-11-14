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
import { createApiClient } from "../../api/client";
import { scientificJson } from "../../api/scientific-json";
import { WorkspaceScope } from "../../controllers/workspaceScope";
import { LabPage } from "./index";
import {
  makeDefinition,
  parseDefinition,
  parseFiniteExecutionBudget,
} from "./definition";

afterEach(cleanup);
const budgetDefinition = (
  seconds: number | null | undefined,
  bytes: number | null | undefined,
) => ({
  ...makeDefinition(),
  name: "Budget study",
  execution: {
    profile: "trusted_native",
    backend: "native",
    ...(seconds === undefined ? {} : { max_seconds: seconds }),
    ...(bytes === undefined ? {} : { max_output_bytes: bytes }),
    requirements: { keep: "explicit" },
  },
});
type Write = { path: string; method: string; body: any; raw: string };
function fixture(initial = budgetDefinition(300, 536870912)) {
  const writes: Write[] = [];
  let row = {
    id: "saved-budget",
    name: initial.name,
    revision: 1,
    definition: initial,
  };
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, options?: RequestInit) => {
      const path = String(input).replace("/api/p/alpha", "/api"),
        method = options?.method ?? "GET";
      let result: unknown = [];
      if (method === "PUT" || method === "POST") {
        const raw = String(options?.body),
          body = scientificJson.parse(raw) as any;
        writes.push({ path, method, raw, body });
        if (path === "/api/lab/preflight")
          result = { compatible: true, checks: [] };
        else {
          row = {
            ...row,
            name: body.definition.name,
            revision: row.revision + 1,
            definition: body.definition,
          };
          result = row;
        }
      } else if (path === "/api/lab/catalog")
        result = { adapters: [], methods: [] };
      else if (path === "/api/lab/definitions") result = [row];
      else if (path === "/api/lab/targets")
        result = [{ id: "local", name: "Local target" }];
      return new Response(scientificJson.stringify(result), {
        headers: { "content-type": "application/json" },
      });
    },
  );
  const scope = new WorkspaceScope();
  scope.setIdentity("researcher:alpha");
  const api = createApiClient({
    scope,
    getProject: () => "alpha",
    onUnauthorized: vi.fn(),
    fetcher,
  });
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
  return { writes, fetcher };
}
async function choose(label: string, name: string) {
  const control = await screen.findByRole("button", {
    name: new RegExp(`${label}$`),
  });
  await waitFor(() => expect(control).toBeEnabled());
  await userEvent.click(control);
  await userEvent.click(await screen.findByRole("option", { name }));
}
async function openSaved() {
  fireEvent.click(
    screen.getByRole("button", { name: /^(Open experiment|Resume draft)$/ }),
  );
  await choose("Saved experiment", "Budget study · r1");
  fireEvent.click(screen.getByRole("tab", { name: "Execution" }));
}
const edit = (name: string, value: string) =>
  fireEvent.change(screen.getByLabelText(name), { target: { value } });
const save = () =>
  fireEvent.click(screen.getByRole("button", { name: "Save revision" }));
const rawDefinition = () =>
  scientificJson.parse(
    (screen.getByLabelText("Experiment definition JSON") as HTMLTextAreaElement)
      .value,
  ) as any;

it.each([
  [null, 0],
  [0, null],
  [86401, 10737418241],
  [undefined, undefined],
])(
  "parses execution seconds %s and bytes %s without replacing authored values",
  (seconds, bytes) => {
    const definition = budgetDefinition(seconds, bytes);
    const parsed = parseDefinition(scientificJson.stringify(definition));
    expect(parsed.execution).toEqual(definition.execution);
  },
);

it.each(["-1", "1.5", '"0"', "true", "1e400", "9007199254740993"])(
  "rejects malformed or inexact execution budget token %s",
  (token) => {
    const source = scientificJson
      .stringify(budgetDefinition(300, 0))
      .replace('"max_seconds":300', `"max_seconds":${token}`);
    expect(() => parseDefinition(source)).toThrow();
  },
);

it("normalizes negative-zero budget tokens to serialized integer zero", () => {
  const definition = parseDefinition(
    scientificJson
      .stringify(budgetDefinition(300, 536870912))
      .replace('"max_seconds":300', '"max_seconds":-0')
      .replace('"max_output_bytes":536870912', '"max_output_bytes":-0'),
  );
  expect(Object.is(parseFiniteExecutionBudget("-0"), -0)).toBe(false);
  expect(Object.is(definition.execution.max_seconds, -0)).toBe(false);
  expect(Object.is(definition.execution.max_output_bytes, -0)).toBe(false);
  const serialized = scientificJson.stringify(definition);
  expect(serialized).toMatch(/"max_seconds":0(?=[,}])/);
  expect(serialized).toMatch(/"max_output_bytes":0(?=[,}])/);
});

it.each(["0", "-0"])(
  "saves an explicit %s budget as integer zero in the normal execution editor",
  async (token) => {
    const f = fixture();
    await openSaved();
    edit("Maximum execution seconds", token);
    edit("Maximum output bytes", token);
    expect(screen.getByLabelText("Maximum execution seconds")).toHaveValue(
      token,
    );
    expect(screen.getByLabelText("Maximum output bytes")).toHaveValue(token);
    save();
    await waitFor(() => expect(f.writes).toHaveLength(1));
    expect(f.writes[0].body.definition.execution).toMatchObject({
      max_seconds: 0,
      max_output_bytes: 0,
      requirements: { keep: "explicit" },
    });
    expect(f.writes[0].raw).toMatch(/"max_seconds":0(?=[,}])/);
    expect(f.writes[0].raw).toMatch(/"max_output_bytes":0(?=[,}])/);
  },
);

it("loads, saves and switches engines with authored unbounded and zero budgets intact", async () => {
  const f = fixture(budgetDefinition(null, 0));
  await openSaved();
  expect(
    screen.getByRole("button", { name: /Unbounded.*Execution time budget$/ }),
  ).toBeVisible();
  expect(screen.queryByLabelText("Maximum execution seconds")).toBeNull();
  expect(screen.getByLabelText("Maximum output bytes")).toHaveValue("0");
  fireEvent.click(screen.getByRole("tab", { name: "Parameters" }));
  await choose("Experiment engine", "Recorded replay");
  fireEvent.click(screen.getByRole("tab", { name: "Execution" }));
  expect(rawDefinition().execution).toMatchObject({
    max_seconds: null,
    max_output_bytes: 0,
  });
  fireEvent.click(screen.getByRole("button", { name: "Save experiment" }));
  await waitFor(() => expect(f.writes).toHaveLength(1));
  expect(f.writes[0].raw).toContain('"max_seconds":null');
  expect(f.writes[0].raw).toContain('"max_output_bytes":0');
  expect(
    screen.getByRole("button", { name: /Unbounded.*Execution time budget$/ }),
  ).toBeVisible();
}, 15000);

it("keeps omitted defaults distinct from finite values and explicit unbounded budgets", async () => {
  const f = fixture(budgetDefinition(undefined, undefined));
  await openSaved();
  expect(
    screen.getByRole("button", { name: /Use default.*Execution time budget$/ }),
  ).toBeVisible();
  expect(screen.queryByLabelText("Maximum execution seconds")).toBeNull();
  await choose("Execution time budget", "Finite limit");
  expect(screen.getByLabelText("Maximum execution seconds")).toHaveValue("300");
  await choose("Execution time budget", "Unbounded");
  await choose("Output size budget", "Finite limit");
  edit("Maximum output bytes", "10737418241");
  await choose("Execution time budget", "Use default");
  expect(rawDefinition().execution).not.toHaveProperty("max_seconds");
  save();
  await waitFor(() => expect(f.writes).toHaveLength(1));
  expect(f.writes[0].body.definition.execution).not.toHaveProperty(
    "max_seconds",
  );
  expect(f.writes[0].body.definition.execution.max_output_bytes).toBe(
    10737418241,
  );
}, 15000);

it("retains empty and unsafe integer drafts while blocking save and preflight without rounding", async () => {
  const f = fixture();
  await openSaved();
  edit("Maximum execution seconds", "");
  expect(screen.getByLabelText("Maximum execution seconds")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Save revision" })).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Check compatibility" }),
  ).toBeDisabled();
  edit("Maximum execution seconds", "9007199254740993");
  expect(screen.getByLabelText("Maximum execution seconds")).toHaveValue(
    "9007199254740993",
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    /browser.*exact|exact.*browser/i,
  );
  expect(rawDefinition().execution.max_seconds).toBe(300);
  expect(f.writes).toHaveLength(0);
  edit("Maximum execution seconds", "9007199254740991");
  expect(screen.getByRole("button", { name: "Save revision" })).toBeEnabled();
  save();
  await waitFor(() => expect(f.writes).toHaveLength(1));
  expect(f.writes[0].body.definition.execution.max_seconds).toBe(
    9007199254740991,
  );
});
