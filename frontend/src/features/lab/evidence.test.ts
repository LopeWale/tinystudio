import { expect, it } from "vitest";
import { dataArtifacts } from "./evidence";
it("discovers retained C evidence roles without requiring B filenames", () => {
  const artifacts = [
    { id: "old", name: "processed.jsonl" },
    { id: "data", name: "transformed.jsonl", role: "transformed_dataset" },
    { id: "raw", name: "raw-normalized.jsonl", role: "normalized_original" },
    { id: "profile", name: "profile.json", role: "data_profile" },
  ];
  const retained = dataArtifacts({
    id: "R",
    definition_id: "D",
    state: "succeeded",
    result: { artifacts },
  });
  expect(retained.derived?.id).toBe("data");
  expect(retained.original?.id).toBe("raw");
  expect(retained.profile?.id).toBe("profile");
});
it("keeps missing evidence explicit instead of inventing descriptors", () => {
  expect(
    dataArtifacts({
      id: "R",
      definition_id: "D",
      state: "succeeded",
      result: { artifacts: {} },
    }).derived,
  ).toBeUndefined();
});
