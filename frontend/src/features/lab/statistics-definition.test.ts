import { expect, it } from "vitest";
import { makeDefinition, parseDefinition, templates } from "./definition";

it("authors explicit editable statistical assumptions in the existing experiment contract", () => {
  const definition = makeDefinition("statistics.plan");
  expect(templates.find((item) => item.id === "statistics.plan")?.name).toBe(
    "Statistical planning",
  );
  expect(definition.domain).toBe("statistics");
  expect(definition.parameters).toEqual({
    method: "means.independent",
    solve_for: "sample_size",
    alpha: 0.05,
    power: 0.8,
    alternative: "two-sided",
    delta: 0.5,
    sd: 1,
    unit: "unitless",
    ratio: 1,
    attrition: 0,
    input_sources: {},
    sensitivity: [],
  });
  expect(parseDefinition(JSON.stringify(definition))).toEqual(definition);
});
