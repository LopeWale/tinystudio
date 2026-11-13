import { expect, it } from "vitest";
import {
  createAnalysisDraft,
  parseAnalysisDraft,
  parseSnapshotBoundary,
} from "./study-analysis-definition";

function completed() {
  return {
    ...createAnalysisDraft({ revision: 3, last_sequence: 12 }),
    name: "Observed temperatures",
    method: "means.welch",
    selectionMode: "all",
    confidence: "0.91",
    alternative: "two-sided",
    groupA: "control",
    groupB: "heated",
    outcomePath: "/values/temperature",
    conditionPath: "/condition",
    unitPath: "/unit_id",
    measurementMode: "named",
    measurementUnit: "degrees C",
    repeated: "error",
    missing: "error",
  };
}

it("starts without assumed scientific choices or admission counts", () => {
  const draft = createAnalysisDraft({ revision: 3, last_sequence: 12 });
  expect(draft).toMatchObject({
    revision: "3",
    throughSeq: "12",
    method: "",
    confidence: "",
    selectionMode: "",
    repeated: "",
    missing: "",
  });
  expect(() => parseAnalysisDraft(draft)).toThrow();
  expect(parseSnapshotBoundary(draft)).toEqual({
    revision: 3,
    through_seq: 12,
  });
});

it("authors explicit selection and policies without inferring independence or replacing corrections", () => {
  const authored = parseAnalysisDraft({
    ...completed(),
    selectionMode: "ids",
    recordIds: "old-record\ncorrection",
    excludeIds: "withdrawn",
    kinds: "observation\nnegative",
    assumptions: "Units were independently assigned.",
  });
  expect(authored.parameters).toEqual({
    method: "means.welch",
    selection: {
      mode: "ids",
      record_ids: ["old-record", "correction"],
      exclude_ids: ["withdrawn"],
      kinds: ["observation", "negative"],
    },
    confidence: 0.91,
    alternative: "two-sided",
    groups: ["control", "heated"],
    outcome_path: "/values/temperature",
    condition_path: "/condition",
    unit_path: "/unit_id",
    measurement_unit: "degrees C",
    repeated: "error",
    missing: "error",
    assumptions: ["Units were independently assigned."],
  });
  expect(authored.parameters).not.toHaveProperty("incomplete_pairs");
  expect(() =>
    parseAnalysisDraft({ ...completed(), method: "means.paired" }),
  ).toThrow(/incomplete/i);
  expect(
    parseAnalysisDraft({
      ...completed(),
      method: "means.paired",
      incompletePairs: "omit",
    }).parameters.incomplete_pairs,
  ).toBe("omit");
});

it("parses authored JSON scientifically and retains signed zero and approximate large floats", () => {
  const authored = parseAnalysisDraft({
    ...completed(),
    conditionMode: "json",
    groupA: "-0.0",
    groupB: "1e30",
    inputSources: '{"outcome":"retained instrument reading"}',
    execution: '{"profile":"trusted_native","custom_number":-0.0}',
  });
  expect(Object.is((authored.parameters.groups as unknown[])[0], -0)).toBe(
    true,
  );
  expect((authored.parameters.groups as unknown[])[1]).toBe(1e30);
  expect(Object.is(authored.execution.custom_number, -0)).toBe(true);
  expect(() =>
    parseAnalysisDraft({
      ...completed(),
      conditionMode: "json",
      groupA: "9007199254740993",
    }),
  ).toThrow(/exact JSON|exact integer/i);
  expect(() => parseAnalysisDraft({ ...completed(), confidence: "" })).toThrow(
    /confidence/i,
  );
  expect(() =>
    parseAnalysisDraft({ ...completed(), confidence: "1e400" }),
  ).toThrow(/finite/i);
  expect(() =>
    parseSnapshotBoundary({ revision: "9007199254740993", throughSeq: "0" }),
  ).toThrow();
});

it("allows arbitrary custom methods without forcing builtin mappings or policies", () => {
  const authored = parseAnalysisDraft({
    ...createAnalysisDraft({ revision: 1, last_sequence: 0 }),
    name: "Site model",
    method: "custom.python",
    selectionMode: "all",
    customName: "Clustered temperature model",
    customVersion: "2026.11",
    customCodeArtifactId: "code-123",
    customOptions: '{"threshold":-0.0,"approximation":1e30}',
    extraArtifactIds: "covariates-123",
  });
  expect(authored).toMatchObject({
    custom_code_artifact_id: "code-123",
    extra_artifact_ids: ["covariates-123"],
    parameters: {
      method: "custom.python",
      selection: { mode: "all", record_ids: [], exclude_ids: [], kinds: [] },
      custom_method: {
        name: "Clustered temperature model",
        version: "2026.11",
      },
    },
  });
  expect(authored.parameters).not.toHaveProperty("confidence");
  expect(authored.parameters).not.toHaveProperty("unit_path");
  expect(
    Object.is(
      (authored.parameters.options as Record<string, unknown>).threshold,
      -0,
    ),
  ).toBe(true);
  expect(() =>
    parseAnalysisDraft({ ...completed(), inputSources: '{"outcome":false}' }),
  ).toThrow(/source/i);
});

it("preserves exact text condition values and compares structured conditions independently of key order", () => {
  expect(
    parseAnalysisDraft({ ...completed(), groupA: " condition A " }).parameters
      .groups,
  ).toEqual([" condition A ", "heated"]);
  expect(() =>
    parseAnalysisDraft({
      ...completed(),
      conditionMode: "json",
      groupA: '{"a":1,"b":2}',
      groupB: '{"b":2,"a":1}',
    }),
  ).toThrow(/distinct condition/);
  const signed = parseAnalysisDraft({
    ...completed(),
    conditionMode: "json",
    groupA: "-0.0",
    groupB: "0",
  }).parameters.groups as number[];
  expect(Object.is(signed[0], -0)).toBe(true);
  expect(Object.is(signed[1], 0)).toBe(true);
});
