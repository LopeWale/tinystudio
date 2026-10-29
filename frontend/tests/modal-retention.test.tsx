import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { Modal } from "../src/components/studio/primitives";

afterEach(cleanup);

function Session({ retained }: { retained: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open editor</button>
      <Modal
        open={open}
        keepMounted={retained}
        title="Retained editor"
        onClose={() => setOpen(false)}
      >
        <label>
          Raw number
          <input defaultValue="0" />
        </label>
        <label>
          Local recording
          <input type="file" />
        </label>
      </Modal>
    </>
  );
}

it("retains an incomplete edit and file while releasing the closed modal's focus and background lease", async () => {
  const user = userEvent.setup();
  render(<Session retained />);
  const opener = screen.getByRole("button", { name: "Open editor" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(document.body.style.overflow).not.toBe("hidden");

  await user.click(opener);
  const dialog = screen.getByRole("dialog", { name: "Retained editor" });
  const raw = screen.getByLabelText("Raw number");
  const file = screen.getByLabelText("Local recording") as HTMLInputElement;
  const recording = new File(["retained observation"], "recording.jsonl");
  await user.clear(raw);
  await user.type(raw, "-1e");
  await user.upload(file, recording);
  expect(dialog).toContainElement(document.activeElement as HTMLElement);
  expect(opener.closest("[inert]")).not.toBeNull();

  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(raw).toHaveValue("-1e");
  expect(file.files?.[0]).toBe(recording);
  expect(dialog.closest(".modal-overlay")).toHaveAttribute("hidden");
  expect(dialog.closest(".modal-overlay")).toHaveAttribute("inert");
  expect(document.activeElement).toBe(opener);
  expect(opener.closest("[inert]")).toBeNull();
  expect(document.body.style.overflow).not.toBe("hidden");

  await user.click(opener);
  expect(screen.getByRole("dialog")).toBe(dialog);
  expect(screen.getByLabelText("Raw number")).toBe(raw);
  expect(raw).toHaveValue("-1e");
  expect(file.files?.[0]).toBe(recording);
  expect(dialog).toContainElement(document.activeElement as HTMLElement);
  fireEvent.mouseDown(dialog.closest(".modal-overlay")!);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(document.activeElement).toBe(opener);
});

it("keeps the existing teardown behavior for dialogs that do not opt into retention", async () => {
  const user = userEvent.setup();
  render(<Session retained={false} />);
  await user.click(screen.getByRole("button", { name: "Open editor" }));
  const raw = screen.getByLabelText("Raw number");
  await user.clear(raw);
  await user.type(raw, "-1e");
  await user.click(screen.getByRole("button", { name: "Close dialog" }));
  expect(raw).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Open editor" }));
  expect(screen.getByLabelText("Raw number")).toHaveValue("0");
});

it("hands a sibling dialog to a retained editor without restoring its old hidden-background lease over the new active state", async () => {
  function Handoff() {
    const [transfer, setTransfer] = useState(false);
    const [editor, setEditor] = useState(false);
    return (
      <>
        <button onClick={() => setTransfer(true)}>Open import</button>
        <Modal
          open={transfer}
          title="Import"
          onClose={() => setTransfer(false)}
        >
          <button
            onClick={() => {
              setTransfer(false);
              setEditor(true);
            }}
          >
            Edit imported definition
          </button>
        </Modal>
        <Modal
          open={editor}
          keepMounted
          title="Imported experiment"
          onClose={() => setEditor(false)}
        >
          <input aria-label="Imported name" defaultValue="Retained import" />
        </Modal>
      </>
    );
  }
  const user = userEvent.setup();
  render(<Handoff />);
  const opener = screen.getByRole("button", { name: "Open import" });
  await user.click(opener);
  await user.click(
    screen.getByRole("button", { name: "Edit imported definition" }),
  );
  const editor = screen.getByRole("dialog", { name: "Imported experiment" });
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(editor.closest("[inert]")).toBeNull();
  expect(editor.closest("[aria-hidden='true']")).toBeNull();
  expect(editor).toContainElement(document.activeElement as HTMLElement);
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(document.activeElement).toBe(opener);
  expect(opener.closest("[inert]")).toBeNull();
});
