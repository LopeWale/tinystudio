import { useState } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StudioContext, type StudioRuntime } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { InputArtifacts } from "./inputs";
import { makeDefinition } from "./definition";

const sha256 =
  "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
beforeEach(() => localStorage.clear());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup({ local = false, losePut = false, autoVerify = true } = {}) {
  const controller = new AbortController();
  let intent = {
    name: "authored.bin",
    size: 3,
    sha256,
    media_type: "application/octet-stream",
    role: "source",
    visibility: "model",
  };
  let state = "uploading",
    accepted = false,
    verified = autoVerify;
  const row = () => ({
    id: "session-one",
    state,
    intent,
    plan: {
      size: 3,
      part_bytes: 5 * 1024 ** 2,
      part_count: 1,
      zero: false,
      mode: "multipart",
    },
    operation_id: state === "verifying" ? "verify-one" : null,
    operation_state: state === "verifying" ? "queued" : null,
    artifact: state === "verified" ? { id: "uploaded-one", ...intent } : null,
  });
  const get = vi.fn(async (path: string) => {
    if (path === "/api/lab/inputs") return [];
    if (path.endsWith("transfer-policy"))
      return {
        backend: local ? "local" : "r2",
        resumable: !local,
        policy: {
          per_file: { max_bytes: null, max_files: null },
          geometry: { concurrency: 1 },
        },
      };
    if (path.endsWith("/parts"))
      return {
        transfer: row(),
        parts: accepted ? [{ number: 1, size: 3, etag: "server-etag" }] : [],
      };
    if (path.endsWith("session-one")) {
      if (state === "verifying" && verified) state = "verified";
      return row();
    }
    return [];
  });
  const post = vi.fn(async (path: string, body: any) => {
    if (path === "/api/lab/transfers") {
      const { idempotency_key: _, ...selected } = body;
      intent = selected;
      return row();
    }
    if (path.endsWith("/parts/1"))
      return {
        url: "https://objects.invalid/file?signed-private-value",
        headers: { "Content-Length": "3" },
        expires_in: 30,
        part_number: 1,
        offset: 0,
        size: 3,
      };
    if (path.endsWith("/complete")) {
      state = "verifying";
      return row();
    }
    if (path.endsWith("/abort")) {
      state = "aborted";
      return row();
    }
    return {};
  });
  const upload = vi.fn(async (_path: string, _body: FormData) => ({
    id: "uploaded-one",
    ...intent,
  }));
  const fetcher = vi.fn(async () => {
    accepted = true;
    if (losePut) {
      losePut = false;
      throw Error(
        "network failed at https://objects.invalid/?signed-private-value",
      );
    }
    return new Response(null, { status: 200 });
  });
  vi.stubGlobal("fetch", fetcher);
  const changed = vi.fn(),
    pending = vi.fn();
  const api = { get, post, upload };
  function Harness() {
    const [definition, setDefinition] = useState(makeDefinition());
    return (
      <>
        <InputArtifacts
          definition={definition}
          signal={() => controller.signal}
          onPendingChange={pending}
          onChange={(inputs) => {
            changed(inputs);
            setDefinition({ ...definition, inputs });
          }}
        />
        <button
          onClick={() =>
            setDefinition({
              ...definition,
              inputs: [
                ...definition.inputs,
                {
                  artifact_id: "manual-choice",
                  sha256: "f".repeat(64),
                  role: "source",
                  visibility: "truth",
                },
              ],
            })
          }
        >
          Add other input while waiting
        </button>
        <output data-testid="selected">
          {JSON.stringify(definition.inputs)}
        </output>
      </>
    );
  }
  const tree = (project = "owned") => (
    <StudioContext.Provider
      value={
        {
          api,
          project,
          scopeKey: project,
          route: "/lab",
          user: { id: "researcher" },
          evidence: new EvidenceRegistry(),
        } as unknown as StudioRuntime
      }
    >
      <Harness />
    </StudioContext.Provider>
  );
  const view = () => render(tree());
  const choose = (container: HTMLElement) =>
    fireEvent.change(container.querySelector('input[type="file"]')!, {
      target: {
        files: [
          new File(["abc"], "authored.bin", {
            type: "application/octet-stream",
          }),
        ],
      },
    });
  return {
    view,
    tree,
    choose,
    changed,
    pending,
    get,
    post,
    upload,
    fetcher,
    verify: () => {
      verified = true;
    },
  };
}

it("attaches only verified metadata and preserves inputs edited during verification", async () => {
  const f = setup({ autoVerify: false });
  const view = f.view();
  f.choose(view.container);
  fireEvent.click(
    screen.getByRole("button", { name: "Import and attach input" }),
  );
  expect(
    await screen.findByText("Server verification · not yet attached"),
  ).toBeVisible();
  expect(f.changed).not.toHaveBeenCalled();
  expect(f.pending).toHaveBeenLastCalledWith(true);
  fireEvent.click(
    screen.getByRole("button", { name: "Add other input while waiting" }),
  );
  f.verify();
  await waitFor(() => expect(f.changed).toHaveBeenCalledOnce(), {
    timeout: 3000,
  });
  expect(JSON.parse(screen.getByTestId("selected").textContent!)).toEqual([
    {
      artifact_id: "manual-choice",
      sha256: "f".repeat(64),
      role: "source",
      visibility: "truth",
    },
    {
      artifact_id: "uploaded-one",
      sha256,
      role: "source",
      visibility: "model",
    },
  ]);
  expect(f.pending).toHaveBeenLastCalledWith(false);
  expect(f.upload).not.toHaveBeenCalled();
});

it("retains a failed upload across remount and resumes confirmed parts after file reselection", async () => {
  const f = setup({ losePut: true });
  const first = f.view();
  f.choose(first.container);
  fireEvent.click(
    screen.getByRole("button", { name: "Import and attach input" }),
  );
  expect(
    await screen.findByRole("button", { name: "Resume / retry import" }),
  ).toBeVisible();
  expect(screen.getByRole("alert")).not.toHaveTextContent(
    "signed-private-value",
  );
  expect(f.post.mock.calls.some(([path]) => path.endsWith("/abort"))).toBe(
    false,
  );
  first.unmount();
  const second = f.view();
  expect(
    await screen.findByText(/Saved transfers remain available/),
  ).toBeVisible();
  f.choose(second.container);
  fireEvent.click(
    screen.getByRole("button", { name: "Import and attach input" }),
  );
  await waitFor(() => expect(f.changed).toHaveBeenCalledOnce(), {
    timeout: 3000,
  });
  expect(f.fetcher).toHaveBeenCalledOnce();
  expect(
    f.post.mock.calls.filter(([path]) => path === "/api/lab/transfers"),
  ).toHaveLength(1);
  expect(localStorage.getItem(localStorage.key(0)!)).not.toContain(
    "signed-private-value",
  );
});

it("aborts a saved server session only through the explicit abort action", async () => {
  const f = setup({ losePut: true });
  const view = f.view();
  f.choose(view.container);
  fireEvent.click(
    screen.getByRole("button", { name: "Import and attach input" }),
  );
  const abort = await screen.findByRole("button", {
    name: "Abort transfer for authored.bin",
  });
  expect(f.post.mock.calls.some(([path]) => path.endsWith("/abort"))).toBe(
    false,
  );
  fireEvent.click(abort);
  expect(
    await screen.findByText("Transfer aborted. No input was attached."),
  ).toBeVisible();
  expect(f.post.mock.calls.at(-1)?.[0]).toBe(
    "/api/lab/transfers/session-one/abort",
  );
  expect(f.changed).not.toHaveBeenCalled();
});

it("keeps local storage uploads on the existing authenticated proxy path", async () => {
  const f = setup({ local: true });
  const view = f.view();
  f.choose(view.container);
  fireEvent.click(
    screen.getByRole("button", { name: "Import and attach input" }),
  );
  await waitFor(() => expect(f.changed).toHaveBeenCalledOnce());
  expect(f.upload.mock.calls[0][0]).toBe("/api/lab/inputs");
  expect(f.fetcher).not.toHaveBeenCalled();
  expect(f.post).not.toHaveBeenCalled();
});

it("does not publish late upload state into a newly selected project", async () => {
  const f = setup();
  let finish!: (response: Response) => void;
  f.fetcher.mockImplementationOnce(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  const view = f.view();
  f.choose(view.container);
  fireEvent.click(
    screen.getByRole("button", { name: "Import and attach input" }),
  );
  await waitFor(() => expect(f.fetcher).toHaveBeenCalledOnce());
  view.rerender(f.tree("other-project"));
  finish(new Response(null, { status: 200 }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Import and attach input" }),
    ).toBeDisabled(),
  );
  expect(screen.queryByLabelText("Saved input transfers")).toBeNull();
  expect(screen.queryByText(/Import paused/)).toBeNull();
  expect(f.changed).not.toHaveBeenCalled();
  expect(f.post.mock.calls.some(([path]) => path.endsWith("/abort"))).toBe(
    false,
  );
});
