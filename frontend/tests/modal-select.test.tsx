import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Modal } from "../src/components/studio/primitives";
import {
  Select,
  SelectItem,
} from "../src/components/boardui/base/select/select";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
describe("real BoardUI Select nested in a TinyStudio modal", () => {
  it("keeps its portal interactive and consumes Escape before the parent dialog", async () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    const close = vi.fn(),
      user = userEvent.setup();
    function Demo() {
      const [open, setOpen] = useState(true),
        [value, setValue] = useState("first");
      return (
        <>
          <button>Background action</button>
          <Modal
            open={open}
            title="Choose provider"
            onClose={() => {
              close();
              setOpen(false);
            }}
          >
            <Select
              aria-label="Provider"
              selectedKey={value}
              onSelectionChange={(key) => setValue(String(key))}
            >
              <SelectItem id="first">First provider</SelectItem>
              <SelectItem id="second">Second provider</SelectItem>
            </Select>
            <button>Dialog action</button>
          </Modal>
        </>
      );
    }
    render(<Demo />);
    await user.click(screen.getByRole("button", { name: /Provider/i }));
    const listbox = await screen.findByRole("listbox", { hidden: true });
    expect(listbox.closest("[inert]")).toBeNull();
    const second = screen.getByRole("option", { name: "Second provider" });
    second.focus();
    expect(second).toHaveFocus();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole("dialog", { name: "Choose provider" }),
    ).toBeInTheDocument();
    expect(close).not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    expect(close).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
