import { describe, it, expect, vi } from "vitest";
import { UploadController, validateFiles } from "./uploads";
describe("real upload lifecycle", () => {
  it("100% transferred stays importing until server admission resolves", async () => {
    const publish = vi.fn();
    let complete!: (value: string) => void;
    const c = new UploadController<string>(publish);
    const run = c.run(async (_signal, progress) => {
      progress({ loaded: 100, total: 100 });
      return new Promise((resolve) => (complete = resolve));
    });
    expect(publish.mock.calls.at(-1)?.[0].phase).toBe("importing");
    complete("accepted");
    await run;
    expect(publish.mock.calls.at(-1)?.[0]).toEqual({
      phase: "ready",
      result: "accepted",
    });
  });
  it("cancel prevents late admission from becoming ready and communicates server uncertainty", async () => {
    const publish = vi.fn();
    let complete!: (value: string) => void;
    const c = new UploadController<string>(publish);
    const run = c.run(() => new Promise((resolve) => (complete = resolve)));
    c.cancel();
    complete("late");
    await run;
    expect(publish.mock.calls.at(-1)?.[0].phase).toBe("cancelled");
    expect(publish.mock.calls.some(([s]) => s.phase === "ready")).toBe(false);
  });
  it("validates formats and limits independently", () => {
    expect(
      validateFiles(
        [
          new File(["1234"], "x.png", { type: "image/png" }),
          new File(["1234"], "x.txt"),
        ],
        ".txt",
        3,
      ).map((x) => x.error),
    ).toEqual([
      "x.png exceeds the 3 byte limit",
      "x.txt exceeds the 3 byte limit",
    ]);
    expect(
      validateFiles([new File(["x"], "bad.png")], ".txt")[0].error,
    ).toContain("unsupported");
  });
});
