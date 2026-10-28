import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
import {
  AssistantActivity,
  AssistantAttachments,
  AssistantSources,
} from "./display";
afterEach(cleanup);
it("presents actual operation states, returned sources and attachment ownership callbacks", () => {
  const remove = vi.fn(),
    del = vi.fn();
  render(
    <>
      <AssistantActivity
        calls={[
          { id: "a", name: "read_evaluation" },
          { id: "b", name: "get_neuron", output: '{"error":"missing"}' },
          { id: "c", name: "get_job", output: { state: "succeeded" } },
        ]}
        searches={[
          { id: "s", state: "completed", query: "actual provider query" },
        ]}
      />
      <AssistantSources
        sources={[
          {
            url: "https://developers.cloudflare.com/r2/pricing/",
            title: "Cloudflare R2 pricing",
          },
          { url: "javascript:alert(1)", title: "Unsafe" },
        ]}
      />
      <AssistantAttachments
        attachments={[
          {
            id: "private1",
            name: "evidence.txt",
            size: 24,
            sha256: "hash",
            project_id: "p",
            user_id: "u",
            extracted_chars: 24,
            retained_chars: 24,
            status: "ready",
          },
        ]}
        onRemove={remove}
        onDelete={del}
      />
    </>,
  );
  expect(
    screen.getByText("Tool · read_evaluation · running"),
  ).toBeInTheDocument();
  expect(screen.getByText("Tool · get_neuron · failed")).toBeInTheDocument();
  expect(screen.getByText("Tool · get_job · completed")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Cloudflare R2 pricing" }),
  ).toHaveAttribute("href", "https://developers.cloudflare.com/r2/pricing/");
  expect(screen.queryByText("Unsafe")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Remove evidence.txt from message" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Delete upload evidence.txt" }),
  );
  expect(remove).toHaveBeenCalledWith("private1");
  expect(del).toHaveBeenCalledWith("private1");
});
