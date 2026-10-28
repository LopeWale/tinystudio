import { fireEvent, render, screen, cleanup } from "@testing-library/react";
import { useState } from "react";
import { afterEach, it, expect, vi } from "vitest";
import { FileUploadField, UploadStatus } from "./uploads";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("uses BoardUI selection without simulated upload and rejects/removes actual files", () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  function Form() {
    const [files, setFiles] = useState<File[]>([]);
    return (
      <FileUploadField
        label="Training files"
        accept=".txt"
        files={files}
        multiple
        onChange={setFiles}
        maxBytes={100}
      />
    );
  }
  render(<Form />);
  const input = screen.getByLabelText("Training files", { selector: "input" });
  fireEvent.change(input, {
    target: {
      files: [
        new File(["hello"], "evidence.txt"),
        new File(["x"], "image.png"),
      ],
    },
  });
  expect(screen.getByText(/image.png is unsupported/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Remove evidence.txt" }),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Uploading/)).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Remove evidence.txt" }));
  expect(
    screen.queryByRole("button", { name: "Remove evidence.txt" }),
  ).toBeNull();
});
it("shows unknown totals as indeterminate and does not turn import into completion", () => {
  const view = render(
    <UploadStatus phase="uploading" progress={{ loaded: 1024 }} />,
  );
  expect(screen.getByRole("progressbar")).not.toHaveAttribute("value");
  view.rerender(
    <UploadStatus phase="importing" progress={{ loaded: 2048, total: 2048 }} />,
  );
  expect(
    screen.getByText("Transferred · importing on server"),
  ).toBeInTheDocument();
  expect(screen.queryByText("ready")).toBeNull();
});
it("keeps compact chooser keyboard, drop validation, removal and disabled behavior", () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  function Form({ disabled = false }: { disabled?: boolean }) {
    const [files, setFiles] = useState<File[]>([]);
    return (
      <FileUploadField
        presentation="compact"
        label="Dataset"
        accept=".txt"
        files={files}
        multiple
        maxBytes={10}
        disabled={disabled}
        onChange={setFiles}
      />
    );
  }
  const view = render(<Form />);
  const chooser = screen.getByRole("button", { name: "Dataset" });
  const input = screen.getByLabelText("Dataset", { selector: "input" });
  const click = vi.spyOn(input, "click").mockImplementation(() => {});
  fireEvent.keyDown(chooser, { key: "Enter" });
  fireEvent.keyDown(chooser, { key: " " });
  expect(click).toHaveBeenCalledTimes(2);
  fireEvent.drop(chooser, {
    dataTransfer: {
      files: [
        new File(["text"], "notes.txt"),
        new File(["image"], "photo.png"),
        new File(["over ten bytes"], "oversized.txt"),
      ],
    },
  });
  expect(
    screen.getByRole("button", { name: "Remove notes.txt" }),
  ).toBeVisible();
  expect(screen.getByText(/photo.png is unsupported/)).toBeVisible();
  expect(screen.getByText(/oversized.txt exceeds/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Remove notes.txt" }));
  expect(screen.queryByRole("button", { name: "Remove notes.txt" })).toBeNull();

  view.rerender(<Form disabled />);
  fireEvent.keyDown(chooser, { key: "Enter" });
  fireEvent.drop(chooser, {
    dataTransfer: { files: [new File(["text"], "blocked.txt")] },
  });
  expect(click).toHaveBeenCalledTimes(2);
  expect(input).toBeDisabled();
  expect(chooser).toHaveAttribute("aria-disabled", "true");
  expect(
    screen.queryByRole("button", { name: "Remove blocked.txt" }),
  ).toBeNull();
});
