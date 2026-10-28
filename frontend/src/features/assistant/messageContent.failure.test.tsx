import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
vi.mock("./markdownBody", () => {
  throw new Error("Controlled Markdown chunk load failure");
});
import { AssistantMessageContent } from "./messageContent";
afterEach(cleanup);
it("retains complete current plaintext and composer identity when the lazy formatter cannot load", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const view = render(
    <>
      <textarea aria-label="Draft" defaultValue="Unsent draft" />
      <AssistantMessageContent role="assistant" content="**Retained answer**" />
    </>,
  );
  const textarea = screen.getByLabelText("Draft") as HTMLTextAreaElement;
  textarea.focus();
  textarea.setSelectionRange(2, 5);
  await screen.findByText("Formatting unavailable. Plain text is retained.");
  expect(screen.getByText("**Retained answer**")).toBeInTheDocument();
  view.rerender(
    <>
      <textarea aria-label="Draft" defaultValue="Unsent draft" />
      <AssistantMessageContent
        role="assistant"
        content="**Retained answer** plus the next streamed text"
      />
    </>,
  );
  expect(
    screen.getByText("**Retained answer** plus the next streamed text"),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Draft")).toBe(textarea);
  expect(document.activeElement).toBe(textarea);
  expect([textarea.selectionStart, textarea.selectionEnd]).toEqual([2, 5]);
});
