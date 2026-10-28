import { useEffect, useRef } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { EvidenceRegistry } from "../../app/evidence";
import { StudioContext } from "../../app/StudioContext";
import { SettingsPage } from "./settings";

// Make the first edit at the newly loaded field's earliest passive lifecycle.
// A parent passive reset runs after this child effect and used to erase it.
vi.mock("../data/common", async (importOriginal) => {
  const original = await importOriginal<typeof import("../data/common")>();
  return {
    ...original,
    Input: (props: any) => {
      const edited = useRef(false);
      useEffect(() => {
        if (props.label === "Provider key" && !edited.current) {
          edited.current = true;
          props.onChange("first-edit");
        }
      }, [props]);
      return <original.Input {...props} />;
    },
  };
});
afterEach(cleanup);
it("keeps the first draft edit after settings arrive", async () => {
  const api = {
    get: vi.fn(async (path: string) =>
      path === "/api/settings"
        ? {
            settings: [
              {
                key: "API_SECRET",
                label: "Provider key",
                group: "openai",
                secret: true,
                set: false,
                source: "default",
              },
            ],
          }
        : {},
    ),
    put: vi.fn(async () => ({})),
  };
  const studio = {
    api,
    scopeKey: "self:fixture",
    project: "fixture",
    user: { id: "self", role: "admin" },
    route: "/settings",
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
    refreshOverview: vi.fn(async () => {}),
  } as any;
  render(
    <StudioContext.Provider value={studio}>
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </StudioContext.Provider>,
  );
  await waitFor(() =>
    expect(
      (screen.getByLabelText("Provider key") as HTMLInputElement).value,
    ).toBe("first-edit"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(api.put).toHaveBeenCalledWith("/api/settings", {
      values: { API_SECRET: "first-edit" },
    }),
  );
});
