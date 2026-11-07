import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ScrollPane } from "./scroll-pane";
afterEach(cleanup);
it("keeps a named keyboard scroll region and ramps each stationary fade layer independently", () => {
  const { container } = render(
    <ScrollPane label="Selected evidence">
      <button>Inspect record</button>
    </ScrollPane>,
  );
  const body = screen.getByRole("region", { name: "Selected evidence" });
  expect(body.tabIndex).toBe(0);
  const edge = container.querySelector(".studio-scroll-pane__edge")!;
  expect(body.contains(edge)).toBe(false);
  expect(edge.getAttribute("aria-hidden")).toBe("true");
  for (const [scrollTop, opacity] of [
    [0, "0"],
    [12, "0.5"],
    [60, "1"],
    [0, "0"],
    [-10, "0"],
  ] as const) {
    fireEvent.scroll(body, { target: { scrollTop } });
    for (const layer of edge.children)
      expect((layer as HTMLElement).style.opacity).toBe(opacity);
    expect((edge as HTMLElement).style.opacity).toBe("");
  }
});
