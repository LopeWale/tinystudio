import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { SettingsPage, type SettingSpec } from "./settings";
afterEach(cleanup);
const effortKey = "TINYSTUDIO_ASSISTANT_EFFORT";
function setup(
  value: string | null = "medium",
  put?: ReturnType<typeof vi.fn>,
  ok = true,
) {
  const settings: SettingSpec[] = [
    {
      key: effortKey,
      label: "Claude effort (low, medium, high)",
      group: "anthropic",
      secret: false,
      value,
      set: !!value,
      source: value ? "settings" : "default",
    },
    {
      key: "TINYSTUDIO_ANTHROPIC_MODEL",
      label: "Default Claude model",
      group: "anthropic",
      secret: false,
      value: "custom/model:v2",
      set: true,
      source: "settings",
    },
    {
      key: "TINYSTUDIO_LLM_BASE_URL",
      label: "Helper LLM base URL",
      group: "helper",
      secret: false,
      value: "https://models.example/v1",
      set: true,
      source: "environment",
    },
  ];
  const api = {
    get: vi.fn(async (path: string) =>
      path === "/api/settings" ? { settings } : {},
    ),
    put: put || vi.fn(async () => ({ settings })),
    post: vi.fn(async () => ({
      ok,
      detail: "Provider response: exact detail / endpoint",
    })),
  };
  const studio = {
    api,
    scopeKey: "self:controls",
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
  return { api, settings, user: userEvent.setup() };
}
async function choose(
  user: ReturnType<typeof userEvent.setup>,
  option: string,
) {
  await user.click(
    await screen.findByRole("button", { name: /Claude effort/ }),
  );
  await user.click(await screen.findByRole("option", { name: option }));
}
it("offers supported effort choices and saves them together with arbitrary model edits", async () => {
  const { api, user } = setup();
  await user.click(
    await screen.findByRole("button", { name: /Claude effort/ }),
  );
  expect(
    screen.getAllByRole("option").map((option) => option.textContent),
  ).toEqual(["Use deployment default", "Low", "Medium", "High"]);
  await user.click(screen.getByRole("option", { name: "High" }));
  fireEvent.change(
    screen.getByLabelText("Default Claude model", { selector: "input" }),
    { target: { value: "my-unlisted/model:next" } },
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(api.put).toHaveBeenCalledWith("/api/settings", {
      values: {
        [effortKey]: "high",
        TINYSTUDIO_ANTHROPIC_MODEL: "my-unlisted/model:next",
      },
    }),
  );
});
it("removes an effort override via the deployment default option and resets the draft", async () => {
  const { api, user } = setup("high");
  await choose(user, "Use deployment default");
  expect(
    screen.getByText("1 unsaved change across providers"),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Reset draft" }));
  expect(
    screen.getByRole("button", { name: /Claude effort/ }),
  ).toHaveTextContent("High");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  await choose(user, "Use deployment default");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(api.put).toHaveBeenCalledWith("/api/settings", {
      values: { [effortKey]: null },
    }),
  );
});
it("shows the default without making an unset effort dirty", async () => {
  setup(null);
  expect(
    await screen.findByRole("button", { name: /Claude effort/ }),
  ).toHaveTextContent("Use deployment default");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
});
it("preserves unknown existing effort through other edits and after changing choices", async () => {
  const { api, user } = setup("future-effort");
  expect(
    await screen.findByRole("button", { name: /Claude effort/ }),
  ).toHaveTextContent("Current value: future-effort");
  await choose(user, "Low");
  await choose(user, "Current value: future-effort");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Helper LLM" }));
  fireEvent.change(
    screen.getByLabelText("Helper LLM base URL", { selector: "input" }),
    { target: { value: "http://localhost:1234/custom/v1" } },
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(api.put).toHaveBeenCalledWith("/api/settings", {
      values: { TINYSTUDIO_LLM_BASE_URL: "http://localhost:1234/custom/v1" },
    }),
  );
});
it.each([
  ["medium", "High"],
  ["future-effort", "Current value: future-effort"],
])(
  "keeps a newer effort selection after a pending save completes (%s)",
  async (initial, newer) => {
    let resolve!: (result: any) => void;
    const pending = new Promise((yes) => {
      resolve = yes;
    });
    const { settings, user } = setup(
      initial,
      vi.fn(() => pending),
    );
    await choose(user, "Low");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await choose(user, newer);
    await act(async () =>
      resolve({
        settings: settings.map((spec) =>
          spec.key === effortKey ? { ...spec, value: "low" } : spec,
        ),
      }),
    );
    expect(
      screen.getByRole("button", { name: /Claude effort/ }),
    ).toHaveTextContent(newer);
    expect(
      screen.getByText("1 unsaved change across providers"),
    ).toBeInTheDocument();
  },
);
it.each([true, false])(
  "labels the connection outcome and preserves its detail when ok=%s",
  async (ok) => {
    const { user } = setup("medium", undefined, ok);
    await user.click(
      await screen.findByRole("button", { name: "Test anthropic connection" }),
    );
    const badge = await screen.findByText(
      ok ? "Connected" : "Connection failed",
    );
    expect(badge.closest('[role="status"]')).toHaveTextContent(
      "Provider response: exact detail / endpoint",
    );
    expect(badge).toHaveAttribute("data-state", ok ? "success" : "error");
  },
);
