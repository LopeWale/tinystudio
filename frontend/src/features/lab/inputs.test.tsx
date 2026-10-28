import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { InputArtifacts } from "./inputs";
import { EvidenceRegistry } from "../../app/evidence";
import { makeDefinition } from "./definition";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("keeps a retained binding visible and detachable when its artifact is absent from the library response", async () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  const controller = new AbortController();
  const get = vi.fn(async () => []);
  function Harness() {
    const [definition, setDefinition] = useState(() => ({
      ...makeDefinition(),
      inputs: [
        {
          artifact_id: "retained-missing",
          sha256: "retained-checksum",
          role: "source",
          visibility: "model" as const,
        },
      ],
    }));
    return (
      <InputArtifacts
        definition={definition}
        onChange={(inputs) =>
          setDefinition({ ...definition, inputs } as typeof definition)
        }
        signal={() => controller.signal}
      />
    );
  }
  render(
    <StudioContext.Provider
      value={
        {
          api: { get },
          evidence: new EvidenceRegistry(),
          scopeKey: "test",
          route: "/lab",
          project: "test",
          user: { id: "test" },
        } as unknown as StudioRuntime
      }
    >
      <Harness />
    </StudioContext.Provider>,
  );
  expect(
    await screen.findByRole("button", { name: "Detach retained-missing" }),
  ).toBeVisible();
  expect(screen.getByText("1 attached")).toBeVisible();
  expect(
    screen.getByText("Metadata unavailable · Retained input"),
  ).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", { name: "Detach retained-missing" }),
  );
  expect(screen.getByText("0 attached")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Detach retained-missing" }),
  ).toBeNull();
});
