import { afterEach, describe, it, expect, vi } from "vitest";
import { AssistantUiBridge } from "./uiBridge";
import { EvidenceRegistry } from "../../app/evidence";
afterEach(() => {
  document.body.innerHTML = "";
  location.hash = "";
});
describe("assistant UI safety boundaries", () => {
  it("requires opt-in, operates an actually captured control, and rejects stale references", async () => {
    location.hash = "#/datasets";
    document.body.innerHTML =
      '<main><button>Import</button><input aria-label="Password" type="password" value="must-not-appear"/><input aria-label="Dataset title"/></main>';
    for (const element of document.querySelectorAll("button,input"))
      element.getClientRects = () => [{ width: 1 }] as any;
    const bridge = new AssistantUiBridge({
      navigate: vi.fn(),
      evidence: new EvidenceRegistry(),
    });
    const snapshot = bridge.snapshot();
    expect(JSON.stringify(snapshot)).not.toContain("must-not-appear");
    expect((snapshot.controls as any[]).map((c) => c.label)).toEqual([
      "Import",
      "Dataset title",
    ]);
    const click = vi.fn();
    document.querySelector("button")!.addEventListener("click", click);
    await expect(
      bridge.perform("click", { ref: "control_1" }, false),
    ).rejects.toThrow("opt-in");
    expect(click).not.toHaveBeenCalled();
    await bridge.perform("click", { ref: "control_1" }, true);
    expect(click).toHaveBeenCalledOnce();
    location.hash = "#/models";
    await expect(
      bridge.perform("click", { ref: "control_1" }, true),
    ).rejects.toThrow("stale");
  });
  it("omits credential pages and refuses direct credential mutations", async () => {
    location.hash = "#/api-keys";
    const bridge = new AssistantUiBridge({
      navigate: vi.fn(),
      evidence: new EvidenceRegistry(),
    });
    expect(bridge.snapshot()).not.toHaveProperty("controls");
    await expect(
      bridge.perform("fill", { ref: "anything", value: "unsafe" }, true),
    ).rejects.toThrow("directly");
  });
});
