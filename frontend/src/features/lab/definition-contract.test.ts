import { describe, expect, it } from "vitest";
import fixture from "../../../../tests/fixtures/lab_definition_v1.json";
import { parseDefinition } from "./definition";
import { toGraph } from "./graph";

describe("shared v1 definition contract", () => {
  it("preserves code fields and domain connection semantics in the graph projection", () => {
    const definition = parseDefinition(JSON.stringify(fixture));
    expect(definition).toEqual(fixture);
    const graph = toGraph(definition);
    expect(graph.nodes.map((node) => node.id)).toEqual(["reference", "plant"]);
    expect(graph.nodes[1].position).toEqual({ x: 260, y: 0 });
    expect(graph.edges[0]).toMatchObject({
      id: "drive",
      source: "reference",
      target: "plant",
      targetHandle: "input",
    });
    expect(parseDefinition(JSON.stringify(definition))).toEqual(fixture);
  });

  it("rejects unsupported versions and dangling graph connections", () => {
    expect(() => parseDefinition(JSON.stringify({ ...fixture, schema_version: 2 }))).toThrow();
    expect(() => parseDefinition(JSON.stringify({
      ...fixture,
      connections: [{ source: "missing", target: "plant", port: "input" }],
    }))).toThrow();
  });
});
