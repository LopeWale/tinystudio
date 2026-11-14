import { describe, expect, it } from "vitest";
import {
  hardwareSessionInputPatch,
  inputExpectations,
  makeDefinition,
  parseDefinition,
  templates,
} from "./definition";

const attached = (artifact_id: string, role = "supervisor") => ({
  artifact_id,
  sha256: "a".repeat(64),
  role,
  visibility: "model" as const,
});

describe("hardware session authoring", () => {
  it("exposes a native template without selecting an operator endpoint or controller", () => {
    expect(templates.some((row) => row.id === "hardware.session")).toBe(true);
    const definition = makeDefinition("hardware.session");
    expect(definition.execution).toMatchObject({
      backend: "native",
      profile: "trusted_native",
    });
    expect(definition.parameters).toEqual({
      binding_id: "",
      supervisor: { kind: "recorded", artifact_id: "" },
      supervisor_parameters: {},
      evidence: { flush_records: 1, fsync: true },
      ipc_timeout_seconds: null,
      artifact_visibility: "evaluation",
    });
    expect(inputExpectations(definition)).toEqual([
      expect.objectContaining({ role: "supervisor", required: false }),
    ]);
  });

  it.each(["recorded", "python"])(
    "preserves the authored %s selector and arbitrary advanced settings",
    (kind) => {
      const definition = makeDefinition("hardware.session");
      definition.inputs = [attached("source-id", "model")];
      definition.parameters = {
        binding_id: "researcher-selected-binding",
        supervisor: {
          kind,
          artifact_id: "source-id",
          ...(kind === "python" ? { callable: "logic.program" } : {}),
        },
        supervisor_parameters: {
          arbitrary: [null, false, -0.25],
          native_payload: { data: { encoding: "hex", value: "0001ff" } },
        },
        evidence: { flush_records: null, fsync: false },
        ipc_timeout_seconds: 0.0000000000005,
        artifact_visibility: "truth",
        future_option: { retained: "unchanged" },
      };
      expect(parseDefinition(JSON.stringify(definition))).toEqual(definition);
    },
  );

  it("binds a genuinely new unique supervisor role without replacing callable or advanced fields", () => {
    const definition = makeDefinition("hardware.session");
    definition.parameters.supervisor = {
      kind: "python",
      artifact_id: "",
      callable: "custom.run",
      advanced: { retained: true },
    };
    const before = structuredClone(definition);
    expect(
      hardwareSessionInputPatch(definition, [attached("uploaded")]),
    ).toEqual({
      inputs: [attached("uploaded")],
      parameters: {
        ...definition.parameters,
        supervisor: {
          ...(definition.parameters.supervisor as object),
          artifact_id: "uploaded",
        },
      },
    });
    expect(definition).toEqual(before);
  });

  it("does not infer a replacement from existing roles, ambiguous attachments, removals or unrelated inputs", () => {
    const definition = makeDefinition("hardware.session");
    definition.parameters.supervisor = {
      kind: "recorded",
      artifact_id: "authored-id",
    };
    definition.inputs = [attached("existing")];
    for (const inputs of [
      [{ ...attached("existing"), visibility: "truth" as const }],
      [attached("one"), attached("two")],
      [],
      [attached("source", "model")],
    ]) {
      expect(hardwareSessionInputPatch(definition, inputs)).toEqual({ inputs });
    }
  });

  it("keeps ordinary input attachment unchanged for other engines", () => {
    const inputs = [attached("source")];
    expect(
      hardwareSessionInputPatch(makeDefinition("recorded.replay"), inputs),
    ).toEqual({ inputs });
  });
});
