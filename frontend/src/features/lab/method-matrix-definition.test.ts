import { expect, it } from "vitest";
import {
  evaluationParameters,
  inputExpectations,
  makeDefinition,
} from "./definition";

it("switches operations without leaking incompatible settings and preserves common generation limits", () => {
  const current = {
    operation: "generate",
    max_new_tokens: 7,
    device: "mps",
    compare_retrieval: true,
  };
  expect(evaluationParameters(current, "generate")).toBe(current);
  const matrix = evaluationParameters(current, "matrix");
  expect(matrix).toEqual({
    operation: "matrix",
    max_new_tokens: 7,
    device: "mps",
    methods: [],
    baseline_method: "",
  });
  expect(current.compare_retrieval).toBe(true);
  expect(
    evaluationParameters({ ...matrix, methods: [{ id: "saved" }] }, "generate"),
  ).toEqual({
    operation: "generate",
    max_new_tokens: 7,
    device: "mps",
    compare_retrieval: false,
  });
  expect(evaluationParameters(matrix, "precomputed")).toEqual({
    operation: "precomputed",
  });
});

it("derives required matrix inputs from authored methods, including conventional-only comparisons", () => {
  const definition = makeDefinition("evaluation.compare");
  definition.parameters = {
    operation: "matrix",
    methods: [{ kind: "affine" }],
  };
  const required = () =>
    inputExpectations(definition)
      .filter((item) => item.required)
      .map((item) => item.role);
  expect(required()).toEqual(["cases"]);
  definition.parameters.methods = [{ kind: "model", retrieval: true }];
  expect(required()).toEqual(["cases", "model", "retrieval_index"]);
  definition.parameters.methods = [null, "invalid"];
  expect(required()).toEqual(["cases"]);
});
