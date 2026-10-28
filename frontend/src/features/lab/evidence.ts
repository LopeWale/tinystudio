import type { LabRun } from "./definition";
export type RetainedArtifact = {
  id: string;
  name: string;
  role?: string;
  sha256?: string;
  visibility?: string;
};
export function dataArtifacts(run: LabRun) {
  const value = (run.result as any)?.artifacts;
  const artifacts = (Array.isArray(value) ? value : []).filter(
    (item: any): item is RetainedArtifact =>
      item && typeof item.id === "string" && typeof item.name === "string",
  );
  const pick = (role: string, name: string) =>
    artifacts.find((item: RetainedArtifact) => item.role === role) ??
    artifacts.find((item: RetainedArtifact) => item.name === name);
  return {
    all: artifacts as RetainedArtifact[],
    derived: pick("transformed_dataset", "processed.jsonl"),
    original: pick("normalized_original", "raw.jsonl"),
    profile: pick("data_profile", "profile.json"),
    partitions: pick("partition_manifest", "partitions.json"),
    representations: pick("representation_manifest", "representations.json"),
  };
}
