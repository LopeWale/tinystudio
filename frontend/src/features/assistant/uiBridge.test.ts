import { afterEach, describe, it, expect, vi } from "vitest";
import { AssistantUiBridge } from "./uiBridge";
import { EvidenceRegistry } from "../../app/evidence";
import { ComponentActionRegistry } from "../../controllers/componentActions";
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

describe("current DOM control availability", () => {
  function fixture() {
    location.hash = "#/datasets";
    document.body.innerHTML =
      '<main><section><button>Import</button><input aria-label="Dataset title" value="original"/><select aria-label="Category"><option value="one">One</option><option value="two">Two</option></select><a href="#/models">Models</a></section></main>';
    for (const element of document.querySelectorAll("button,input,select,a"))
      element.getClientRects = () => [{ width: 1 }] as any;
    const navigate = vi.fn();
    const bridge = new AssistantUiBridge({
      navigate,
      evidence: new EvidenceRegistry(),
    });
    return { bridge, navigate, pane: document.querySelector("section")! };
  }

  it.each([
    "hidden",
    "inert",
    "aria-hidden",
    "display",
    "visibility",
    "geometry",
  ])(
    "rejects cached refs after %s changes and omits unavailable controls on reread",
    async (mode) => {
      const { bridge, navigate, pane } = fixture();
      const controls = bridge.snapshot().controls!;
      expect(controls).toHaveLength(4);
      const handler = vi.fn((event: Event) => event.preventDefault());
      for (const element of pane.querySelectorAll("button,input,select,a")) {
        element.addEventListener("click", handler);
        element.addEventListener("input", handler);
        element.addEventListener("change", handler);
      }
      if (mode === "display") pane.style.display = "none";
      else if (mode === "visibility") pane.style.visibility = "hidden";
      else if (mode === "geometry") {
        for (const element of pane.children)
          element.getClientRects = () => [] as any;
      } else pane.setAttribute(mode, mode === "aria-hidden" ? "true" : "");
      for (const [label, action, value] of [
        ["Import", "click", undefined],
        ["Dataset title", "fill", "changed"],
        ["Category", "select", "two"],
        ["Models", "click", undefined],
      ]) {
        const ref = controls.find((control) => control.label === label)!.ref;
        await expect(
          bridge.perform(action!, { ref, value }, true),
        ).rejects.toThrow("unavailable");
      }
      expect(handler).not.toHaveBeenCalled();
      expect(navigate).not.toHaveBeenCalled();
      expect(location.hash).toBe("#/datasets");
      expect(pane.querySelector("input")!.value).toBe("original");
      expect(pane.querySelector("select")!.value).toBe("one");
      expect(bridge.snapshot().controls).toEqual([]);
    },
  );

  it.each(["fieldset", "aria-disabled"])(
    "reports and rejects controls disabled by %s after capture",
    async (mode) => {
      const { bridge, pane } = fixture();
      const ref = bridge
        .snapshot()
        .controls!.find((control) => control.label === "Dataset title")!.ref;
      if (mode === "fieldset") {
        const fieldset = document.createElement("fieldset");
        pane.replaceWith(fieldset);
        fieldset.append(pane);
        fieldset.disabled = true;
      } else pane.setAttribute("aria-disabled", "true");
      await expect(
        bridge.perform("fill", { ref, value: "changed" }, true),
      ).rejects.toThrow("unavailable");
      expect(pane.querySelector("input")!.value).toBe("original");
      expect(
        bridge
          .snapshot()
          .controls!.find((control) => control.label === "Dataset title")!
          .disabled,
      ).toBe(true);
    },
  );

  it("keeps visible controls operable and read-only registry actions available without mutation opt-in", async () => {
    const { bridge, pane } = fixture();
    const controls = (await bridge.perform("read_page", {}, false)).controls;
    const input = controls.find(
      (control: { label: string }) => control.label === "Dataset title",
    );
    await bridge.perform("fill", { ref: input.ref, value: "updated" }, true);
    expect(pane.querySelector("input")!.value).toBe("updated");
    const actions = new ComponentActionRegistry();
    actions.setScope({ user: "user", project: "project", route: "/datasets" });
    const run = vi.fn();
    actions.register({
      id: "dataset.filter",
      label: "Filter datasets",
      kind: "filter",
      run,
    });
    const registryBridge = new AssistantUiBridge({
      navigate: vi.fn(),
      evidence: new EvidenceRegistry(),
      actions,
    });
    const listed = await registryBridge.perform("list_actions", {}, false);
    await registryBridge.perform(
      "execute_action",
      { ref: listed.actions[0].ref },
      false,
    );
    expect(run).toHaveBeenCalledOnce();
  });
});
