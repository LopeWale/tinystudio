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

it("identifies same-named checkpoints by frozen run and revision before attaching", async () => {
  const rows = [1, 2].map((revision) => ({
    id: `checkpoint-${revision}`,
    name: "checkpoint.pt",
    sha256: String(revision).repeat(64),
    size: 128,
    media_type: "application/octet-stream",
    origin: {
      definition_name: "Sequence fixture",
      revision,
      run_id: `run-${revision}`,
    },
  }));
  const controller = new AbortController();
  function Harness() {
    const [definition, setDefinition] = useState(
      makeDefinition("learning.language"),
    );
    return (
      <>
        <InputArtifacts
          definition={definition}
          onChange={(inputs) => setDefinition({ ...definition, inputs })}
          signal={() => controller.signal}
        />
        <output data-testid="selected-inputs">
          {JSON.stringify(definition.inputs)}
        </output>
      </>
    );
  }
  render(
    <StudioContext.Provider
      value={
        {
          api: {
            get: vi.fn(async (path) =>
              path === "/api/lab/inputs" ? rows : [],
            ),
          },
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
    await screen.findByText("Sequence fixture · r1 · run-1"),
  ).toBeVisible();
  expect(screen.getByText("Sequence fixture · r2 · run-2")).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", {
      name: "Attach checkpoint.pt from Sequence fixture revision 2 run run-2",
    }),
  );
  expect(
    JSON.parse(screen.getByTestId("selected-inputs").textContent!),
  ).toEqual([
    {
      artifact_id: "checkpoint-2",
      sha256: "2".repeat(64),
      role: "source",
      visibility: "model",
    },
  ]);
  expect(
    screen.getByRole("button", {
      name: "Attach checkpoint.pt from Sequence fixture revision 1 run run-1",
    }),
  ).toHaveAttribute("aria-pressed", "false");
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
