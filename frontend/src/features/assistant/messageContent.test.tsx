import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { AssistantMessageContent } from "./messageContent";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const review =
  '## Evaluation review\n\n**Model A** leads on `exact`.\n\n| Model | exact |\n| --- | ---: |\n| Model A | 100% |\n| Model B | 16.7% |\n\n- Inspect held-out failures.\n- Check training/validation overlap.\n\n```json\n{"score":1}\n```\n\n[Official reference](https://developers.cloudflare.com/r2/pricing/)';
describe("safe lazy assistant formatting", () => {
  it("renders actual GFM tables, emphasis, inline and fenced code, lists and safe external links", async () => {
    const { container } = render(
      <AssistantMessageContent role="assistant" content={review} />,
    );
    await screen.findByRole("table");
    expect(
      screen.getByRole("heading", { name: "Evaluation review" }),
    ).toBeInTheDocument();
    expect(container.querySelector("strong")?.textContent).toBe("Model A");
    expect(
      screen.getByRole("columnheader", { name: "exact" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "16.7%" })).toBeInTheDocument();
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("pre code")?.textContent).toBe(
      '{"score":1}\n',
    );
    expect(container.querySelector("p code")?.textContent).toBe("exact");
    expect(
      screen.getByRole("link", { name: "Official reference" }),
    ).toHaveAttribute("href", "https://developers.cloudflare.com/r2/pricing/");
    expect(
      screen.getByRole("link", { name: "Official reference" }),
    ).toHaveAttribute("rel", "noopener noreferrer");
  });
  it("keeps user prompts literal rather than interpreting their markdown or HTML", () => {
    const content =
      '**User text** <img src="https://invalid.example/pixel">\n| A | B |';
    const { container } = render(
      <AssistantMessageContent role="user" content={content} />,
    );
    expect(container.textContent).toBe(content);
    expect(container.querySelector("strong,table,img")).toBeNull();
  });
  it("never creates raw HTML, image fetches or links with unsafe/relative/credential URL schemes", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const content =
      '[JS](javascript:alert%281%29) [Data](data:text/html,hello) [Mail](mailto:private@example.org) [Relative](/api/settings) [Protocol relative](//invalid.example) [Credentials](https://user:password@example.org/)\n\n![tracking](https://invalid.example/pixel.png)\n\n<script>window.markdownInjected=true</script>\n\n<img src="https://invalid.example/raw-pixel" onerror="alert(1)">\n\n<iframe src="https://invalid.example"></iframe>';
    const { container } = render(
      <AssistantMessageContent role="assistant" content={content} />,
    );
    await screen.findByText("[Image omitted: tracking]");
    expect(
      container.querySelector("script,img,iframe,form,object,embed"),
    ).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
    expect(container.innerHTML).not.toContain(
      "https://invalid.example/pixel.png",
    );
    expect(container.innerHTML).not.toContain("onerror");
  });
  it("updates streamed content without remounting or disturbing the adjacent composer", async () => {
    const view = render(
      <>
        <textarea aria-label="Draft" defaultValue="Keep my unsent question" />
        <AssistantMessageContent
          role="assistant"
          content="## Evaluation review"
        />
      </>,
    );
    await screen.findByRole("heading", { name: "Evaluation review" });
    const textarea = screen.getByLabelText("Draft") as HTMLTextAreaElement;
    textarea.focus();
    textarea.setSelectionRange(3, 8);
    view.rerender(
      <>
        <textarea aria-label="Draft" defaultValue="Keep my unsent question" />
        <AssistantMessageContent role="assistant" content={review} />
      </>,
    );
    await screen.findByRole("table");
    expect(screen.getByLabelText("Draft")).toBe(textarea);
    expect(document.activeElement).toBe(textarea);
    expect(textarea.value).toBe("Keep my unsent question");
    expect([textarea.selectionStart, textarea.selectionEnd]).toEqual([3, 8]);
  });
});
