import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { ReusableTemplateGallery } from "./templates";

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function choose(name: string) {
  fireEvent.click(screen.getByRole("radio", { name }));
}
function mount() {
  const request = vi.fn(() => {
    throw Error("The template gallery must stay local");
  });
  vi.stubGlobal("fetch", request);
  render(<ReusableTemplateGallery />);
  return request;
}
describe("interactive reusable template gallery", () => {
  it("keeps checked identities independent of inspection and restores filtered authored rows", () => {
    const request = mount();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Select Sample support" }),
    );
    expect(screen.getByText("3 authored datasets · 1 selected")).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Inspect Sample instructions" }),
    );
    const inspector = screen.getByRole("region", {
      name: "Sample dataset inspection",
    });
    expect(within(inspector).getByText("856")).toBeTruthy();
    expect(
      screen.getByRole("checkbox", { name: "Select Sample instructions" }),
    ).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    fireEvent.click(
      within(
        screen.getByRole("dialog", {
          name: "Sample dataset collection filters",
        }),
      ).getByRole("button", { name: /^Kind/ }),
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "Instructions" }));
    expect(
      within(screen.getByRole("table")).queryByText("Sample support"),
    ).toBeNull();
    fireEvent.change(
      screen.getByRole("searchbox", { name: "Search sample datasets" }),
      { target: { value: "missing" } },
    );
    expect(screen.getByText(/No sample datasets match/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reset search" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(
      screen.getByRole("checkbox", { name: "Select Sample support" }),
    ).toBeChecked();
    expect(within(inspector).getByText("856")).toBeTruthy();
    expect(request).not.toHaveBeenCalled();
  });
  it("shows simple, interactive and advanced variants with shared exact row actions and local view controls", () => {
    const request = mount();
    const table = screen.getByRole("table", {
      name: "Sample dataset collection",
    });
    fireEvent.click(within(table).getByRole("button", { name: "Rows" }));
    expect(within(table).getAllByRole("row")[1].textContent).toContain(
      "Sample instructions",
    );
    fireEvent.click(screen.getByRole("radio", { name: "Spacious" }));
    expect(table.closest(".data-table")).toHaveAttribute(
      "data-density",
      "spacious",
    );
    fireEvent.click(screen.getByRole("button", { name: "Columns" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Kind" }));
    expect(
      within(table).queryByRole("columnheader", { name: /Kind/ }),
    ).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Inspect Sample knowledge" }),
    );
    expect(
      within(
        screen.getByRole("region", { name: "Sample dataset inspection" }),
      ).getByText("2,410"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Simple" }));
    expect(screen.queryByRole("button", { name: "Filters" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Columns" })).toBeNull();
    expect(screen.getByRole("table").closest(".data-table")).toHaveAttribute(
      "data-table-variant",
      "simple",
    );
    fireEvent.click(screen.getByRole("radio", { name: "Interactive" }));
    expect(screen.getByRole("button", { name: "Filters" })).toBeTruthy();
    expect(screen.getByRole("table").closest(".data-table")).toHaveAttribute(
      "data-table-variant",
      "standard",
    );
    expect(request).not.toHaveBeenCalled();
  });
  it("synchronizes record navigation, goal, and metadata with honest boundary controls", () => {
    const request = mount();
    choose("Detail");
    expect(
      screen.getByRole("button", { name: "Previous sample" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Next sample" }));
    expect(
      screen.getByRole("heading", { name: "Sample instructions" }),
    ).toBeTruthy();
    expect(
      screen.getByText("Follow short, specific instructions."),
    ).toBeTruthy();
    expect(screen.getByText("856")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next sample" }));
    expect(screen.getByRole("button", { name: "Next sample" })).toBeDisabled();
    expect(screen.getByText("Explain the supplied knowledge.")).toBeTruthy();
    expect(request).not.toHaveBeenCalled();
  });
  it("uses fixed request units, a real three-day subset, and selectable zero observations", () => {
    const request = mount();
    choose("Analysis");
    expect(screen.getByText("15 requests")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Day 1: 2 requests" }));
    fireEvent.click(screen.getByRole("radio", { name: "Last 3 days" }));
    expect(screen.getByText("6 requests")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Day 1: 2 requests" }),
    ).toBeNull();
    expect(screen.queryByText("Day 1: 2 requests")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Day 6: 0 requests" }));
    expect(screen.getByRole("status").textContent).toBe("Day 6: 0 requests");
    expect(request).not.toHaveBeenCalled();
  });
  it("keeps failure evidence explicit and publication unstarted until the success fixture is selected", () => {
    const request = mount();
    choose("Run");
    expect(screen.getByRole("status").textContent).toContain(
      "validation error",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Publish Not started" }),
    );
    expect(screen.getByRole("status").textContent).toContain("not attempted");
    fireEvent.click(
      screen.getByRole("button", { name: "Show success scenario" }),
    );
    expect(screen.getByRole("status").textContent).toContain(
      "3 output records were published",
    );
    expect(request).not.toHaveBeenCalled();
  });
  it("validates, saves locally, and resets a settings draft to its last local save", () => {
    const request = mount();
    choose("Settings");
    const input = screen.getByRole("textbox", {
      name: "Example workspace name",
    });
    fireEvent.change(input, { target: { value: "  " } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save example settings" }),
    );
    expect(screen.getByRole("alert").textContent).toBe(
      "Enter a workspace name.",
    );
    fireEvent.change(input, { target: { value: "Local rename" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save example settings" }),
    );
    expect(screen.getByText(/Saved locally in this example/)).toBeTruthy();
    fireEvent.change(input, { target: { value: "Unsaved replacement" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Reset example draft" }),
    );
    expect(input).toHaveValue("Local rename");
    expect(request).not.toHaveBeenCalled();
  });
  it("retains setup drafts when returning from review and confirms without creating a project", () => {
    const request = mount();
    choose("Setup");
    fireEvent.click(screen.getByRole("button", { name: "Review example" }));
    expect(screen.getByRole("alert").textContent).toContain(
      "both a name and a goal",
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Example project name" }),
      { target: { value: "Research" } },
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Example project goal" }),
      { target: { value: "Review support answers" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Review example" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Back to description" }),
    );
    expect(
      screen.getByRole("textbox", { name: "Example project goal" }),
    ).toHaveValue("Review support answers");
    fireEvent.click(screen.getByRole("button", { name: "Review example" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm local example" }),
    );
    expect(screen.getByRole("status").textContent).toBe(
      "Local example confirmed: Research. No project was created.",
    );
    expect(request).not.toHaveBeenCalled();
  });
  it("appends the exact local message without inventing a model reply", () => {
    const request = mount();
    choose("Conversation");
    expect(
      screen.getByRole("button", { name: "Add local message" }),
    ).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "Example message" }), {
      target: { value: "Inspect the selected dataset" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add local message" }));
    expect(screen.getByText("Inspect the selected dataset")).toBeTruthy();
    expect(
      screen.getByRole("textbox", { name: "Example message" }),
    ).toHaveValue("");
    expect(screen.queryByText(/Assistant ·/)).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Clear local messages" }),
    );
    expect(screen.queryByText("Inspect the selected dataset")).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });
});
