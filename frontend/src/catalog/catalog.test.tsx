import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StudioContext } from "../app/StudioContext";
import { EvidenceRegistry } from "../app/evidence";
import { ComponentCatalogPage } from "./index";
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);
afterEach(cleanup);
function mount() {
  const request = vi.fn(() => {
    throw Error("Catalog must not request network");
  });
  vi.stubGlobal("fetch", request);
  const s = {
    api: { get: request, post: request, put: request, upload: request },
    project: "illustrative",
    user: { id: "illustrative" },
    route: "/dev/catalog",
    evidence: new EvidenceRegistry(),
  } as any;
  render(
    <StudioContext.Provider value={s}>
      <ComponentCatalogPage />
    </StudioContext.Provider>,
  );
  return { request, s };
}
describe("actual product component catalog", () => {
  it("labels examples, allows local input and activity state without requesting HTTP", () => {
    const { request } = mount();
    expect(
      screen.getByText(
        /All records, work states, and counters here are illustrative/,
      ),
    ).toBeTruthy();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Illustrative name" }),
      { target: { value: "Local edit" } },
    );
    expect(screen.getByText("Local input: Local edit")).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Show illustrative pending state" }),
    );
    expect(
      screen.getByText("Illustrative action pending. No request exists."),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Show illustrative activity" }),
    );
    expect(screen.getByLabelText("Illustrative active request")).toBeTruthy();
    expect(request).not.toHaveBeenCalled();
  });
  it("demonstrates measured/indeterminate transfer and separate import, cancel, retry and failure", () => {
    const { request } = mount();
    fireEvent.click(screen.getByRole("button", { name: "Example: uploading" }));
    expect(
      screen
        .getByRole("progressbar", { name: "Bytes transferred" })
        .getAttribute("value"),
    ).toBe("25");
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Known illustrative byte total" }),
    );
    expect(
      screen
        .getByRole("progressbar", { name: "Bytes transferred" })
        .hasAttribute("value"),
    ).toBe(false);
    fireEvent.click(
      screen.getByRole("button", { name: "Cancel", exact: true } as any),
    );
    expect(
      screen.getByText(/Illustrative cancellation; no actual transfer/),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Retry", exact: true } as any),
    );
    expect(
      (
        screen.getByRole("slider", {
          name: "Illustrative bytes transferred",
        }) as HTMLInputElement
      ).value,
    ).toBe("0");
    fireEvent.click(screen.getByRole("button", { name: "Example: importing" }));
    expect(screen.getByText("Transferred · importing on server")).toBeTruthy();
    expect(screen.queryByRole("progressbar")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Example: failed" }));
    expect(screen.getByText("Illustrative connection failure")).toBeTruthy();
    expect(request).not.toHaveBeenCalled();
  });
  it("uses the real shared upload selection/removal surface without transferring files", () => {
    const { request } = mount();
    const input = screen.getByLabelText("Illustrative source files", {
      selector: "input",
    });
    fireEvent.change(input, {
      target: { files: [new File(["notes"], "example.md")] },
    });
    expect(
      screen.getByRole("button", { name: "Remove example.md" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove example.md" }));
    expect(
      screen.queryByRole("button", { name: "Remove example.md" }),
    ).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });
  it("links actual map selection and detail and retains undefined record selection honestly", () => {
    const { request } = mount();
    expect(
      screen.getByText("1 undefined coordinates omitted", { exact: false }),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: /Explore neuron 433:/ }),
    );
    expect(
      screen.getByRole("heading", { name: "Selected neuron 433" }),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Share of 4 distinct retained datapoints. A datapoint can carry several labels.",
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /support.*50/ }));
    expect(
      screen.getByText(/Illustrative label selected: support/),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Select illustrative neuron 920" }),
    );
    expect(
      screen.getByRole("heading", { name: "Selected neuron 920" }),
    ).toBeTruthy();
    expect(
      screen.getByText(/plot omits its undefined coordinate/),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Explore neuron 920:/ }),
    ).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });
  it("reuses assistant patterns for local attachment removal and safe links", () => {
    const { request } = mount();
    expect(
      screen
        .getByRole("link", {
          name: "Illustrative source link · BoardUI installation",
        })
        .getAttribute("href"),
    ).toBe("https://www.boardui.com/installation");
    expect(
      screen.queryByRole("link", { name: "Unsafe illustrative URL omitted" }),
    ).toBeNull();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Remove illustrative-notes.md from message",
      }),
    );
    expect(screen.getByText(/No stored file was deleted/)).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Restore illustrative attachment" }),
    );
    expect(
      screen.getByRole("button", {
        name: "Delete upload illustrative-notes.md",
      }),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Illustrative operation: failed" }),
    );
    expect(
      screen.getByText("Tool · illustrative_inspect_records · failed"),
    ).toBeTruthy();
    expect(request).not.toHaveBeenCalled();
  });
});
