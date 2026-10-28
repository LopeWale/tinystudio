import { StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  cleanup,
  act,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { StudioContext } from "../../app/StudioContext";
import { EvidenceRegistry } from "../../app/evidence";
import { AssistantProvider, AssistantWidget } from "./index";
import { AssistantScrollArea } from "./scrollArea";

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", () => ({
    matches: true,
    addEventListener() {},
    removeEventListener() {},
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function setup(injectedFetcher?: typeof fetch) {
  let saved: any = {
    revision: 1,
    history: [],
    provider: "anthropic",
    model: "claude-opus-5-5",
  };
  const api = {
    get: vi.fn(async (path: string) =>
      path.endsWith("/models")
        ? {
            anthropic: {
              available: true,
              default: "claude-opus-5-5",
              models: [{ id: "claude-opus-5-5" }],
            },
            openai: {
              available: true,
              default: null,
              models: [{ id: "gpt-4.1-mini" }],
            },
          }
        : path.endsWith("/config")
          ? { max_bytes: 1024, accept: ".txt" }
          : structuredClone(saved),
    ),
    put: vi.fn(
      async (_path: any, body: any) =>
        (saved = { ...body, revision: body.revision + 1 }),
    ),
    upload: vi.fn(),
    post: vi.fn(),
  };
  const fetcher = vi.fn(
    async (_url: any, _options: any) =>
      new Response(
        `data: ${JSON.stringify({ type: "conversation_saved", revision: saved.revision + 1 })}\n\n`,
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  const studio = {
    api,
    route: "/evaluations/review",
    project: "home",
    scopeKey: "alice:home",
    user: { id: "alice" },
    evidence: new EvidenceRegistry(),
    toast: vi.fn(),
    confirm: vi.fn(async () => true),
    navigate: vi.fn(),
  } as any;
  render(
    <StrictMode>
      <MemoryRouter initialEntries={["/evaluations/review"]}>
        <StudioContext.Provider value={studio}>
          <AssistantProvider fetcher={injectedFetcher}>
            <AssistantWidget />
          </AssistantProvider>
        </StudioContext.Provider>
      </MemoryRouter>
    </StrictMode>,
  );
  // The user requires one omnipresent dock; exercise its real open control on research.
  fireEvent.click(screen.getByRole("button", { name: "Open assistant" }));
  return { api, fetcher, studio };
}
async function ready() {
  await waitFor(() =>
    expect(screen.getByText(/Saved · revision 1/)).toBeInTheDocument(),
  );
}
function enter(text: string) {
  fireEvent.change(screen.getByLabelText("Message"), {
    target: { value: text },
  });
}
function file(name: string) {
  return new File(["fixture"], name, { type: "text/plain", lastModified: 1 });
}
function uploaded(name: string) {
  return {
    id: name === "a.txt" ? "a".repeat(32) : "b".repeat(32),
    name,
    size: 7,
    sha256: "hash",
    project_id: "p",
    user_id: "alice",
    extracted_chars: 7,
    retained_chars: 7,
    status: "ready",
  };
}
async function selectFiles(files: File[]) {
  await userEvent.click(
    await screen.findByRole("button", { name: "Attach files" }),
  );
  fireEvent.change(
    screen.getByLabelText("Assistant files", { selector: "input" }),
    { target: { files } },
  );
}

describe("actual assistant controls preserve turn invariants", () => {
  it("routes a real admitted send through the injected stream transport without falling back to global fetch", async () => {
    const denyStream = vi.fn(
      async (
        _input: RequestInfo | URL,
        _init?: RequestInit,
      ): Promise<Response> => {
        throw Error("Isolated preview stream transport is disabled");
      },
    );
    const { api, fetcher } = setup(denyStream);
    await ready();
    enter("Exercise isolated preview send");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(denyStream).toHaveBeenCalledOnce());
    // Persistence succeeds: the transport boundary itself must enforce isolation.
    expect(api.put).toHaveBeenCalled();
    expect(denyStream.mock.calls[0][0]).toBe("/api/assistant/chat");
    expect(denyStream.mock.calls[0][1]?.method).toBe("POST");
    await waitFor(() =>
      expect(
        screen
          .getAllByRole("alert")
          .some((alert) =>
            alert.textContent?.includes(
              "Isolated preview stream transport is disabled",
            ),
          ),
      ).toBe(true),
    );
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("keeps a live response attached to the same conversation while the floating card is closed", async () => {
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    const transport = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              stream = controller;
            },
          }),
        ),
    );
    const { api } = setup(transport);
    await ready();
    enter("Keep working while I inspect this page");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(transport).toHaveBeenCalledOnce());
    const signal = transport.mock.calls[0][1]?.signal;
    const textarea = screen.getByLabelText("Message");
    fireEvent.click(screen.getByRole("button", { name: "Close assistant" }));
    expect(signal?.aborted).toBe(false);
    await act(async () => {
      stream.enqueue(
        new TextEncoder().encode(
          'data: {"type":"text","delta":"Retained live answer"}\n\n',
        ),
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "Open assistant" }));
    expect(await screen.findByText("Retained live answer")).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBe(textarea);
    expect(transport).toHaveBeenCalledOnce();
    const read = api.get.getMockImplementation()!;
    // A conversation_saved event denotes a durable server revision; subsequent
    // reads must return that revision rather than the pre-stream fixture.
    api.get.mockImplementation(async (path) =>
      path.endsWith("/conversation")
        ? {
            revision: 3,
            provider: "anthropic",
            model: "claude-opus-5-5",
            history: [
              {
                role: "user",
                content: "Keep working while I inspect this page",
              },
              { role: "assistant", content: "Retained live answer" },
            ],
          }
        : read(path),
    );
    await act(async () => {
      stream.enqueue(
        new TextEncoder().encode(
          'data: {"type":"conversation_saved","revision":3}\n\n',
        ),
      );
      stream.close();
    });
    await screen.findByText(/Saved · revision 3/);
  });
  it("opens secondary controls only on request and lets nested select Escape dismiss the select before the card", async () => {
    setup();
    await ready();
    expect(
      screen.queryByRole("region", { name: "Assistant settings" }),
    ).toBeNull();
    expect(
      screen.queryByRole("checkbox", { name: "Provider web search" }),
    ).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Assistant model settings" }),
    );
    await userEvent.click(screen.getByRole("button", { name: /Provider$/ }));
    await screen.findByRole("option", { name: "openai" });
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(
      screen.getByRole("region", { name: "Assistant settings" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open assistant" }),
    ).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(screen.getByRole("button", { name: /Provider$/ }), {
      key: "Escape",
    });
    expect(
      screen.queryByRole("region", { name: "Assistant settings" }),
    ).toBeNull();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Assistant model settings" }),
    );
    expect(
      screen.getByRole("button", { name: "Open assistant" }),
    ).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByRole("button", { name: "Turn options" }));
    expect(
      screen.getByRole("checkbox", { name: "Provider web search" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", {
        name: "Allow actions / deletions this turn",
      }),
    ).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText("Message"), { key: "Escape" });
    expect(screen.queryByRole("region", { name: "Turn options" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Open assistant" }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Turn options" }),
    );
  });
  it("sends the explicitly selected provider/model even after synchronized history refresh", async () => {
    const { api, fetcher } = setup();
    await ready();
    await userEvent.click(
      screen.getByRole("button", { name: "Assistant model settings" }),
    );
    await userEvent.click(screen.getByRole("button", { name: /Provider$/ }));
    await userEvent.click(
      await screen.findByRole("option", { name: "openai" }),
    );
    await userEvent.click(screen.getByRole("button", { name: /Model$/ }));
    await userEvent.click(
      await screen.findByRole("option", { name: "gpt-4.1-mini" }),
    );
    window.dispatchEvent(new Event("focus"));
    await waitFor(() =>
      expect(
        api.get.mock.calls.filter(([path]) => path.endsWith("/conversation"))
          .length,
      ).toBeGreaterThan(1),
    );
    enter("Review this page");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({
      provider: "openai",
      model: "gpt-4.1-mini",
    });
    expect(api.put.mock.calls[0][1]).toMatchObject({
      provider: "openai",
      model: "gpt-4.1-mini",
    });
  });
  it("keeps a provider failure visible after successful persistence and later refresh", async () => {
    const { api, fetcher } = setup();
    fetcher.mockImplementation(
      async () =>
        new Response(
          'data: {"type":"error","message":"Provider rejected these credentials"}\n\ndata: {"type":"conversation_saved","revision":3}\n\n',
        ),
    );
    await ready();
    enter("Review this page");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Provider rejected these credentials");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument(),
    );
    const reads = api.get.mock.calls.filter(([path]) =>
      path.endsWith("/conversation"),
    ).length;
    window.dispatchEvent(new Event("focus"));
    await waitFor(() =>
      expect(
        api.get.mock.calls.filter(([path]) => path.endsWith("/conversation"))
          .length,
      ).toBeGreaterThan(reads),
    );
    expect(
      screen.getByText("Provider rejected these credentials"),
    ).toBeInTheDocument();
  });
  it("preserves the next draft and focused textarea while the durable user write is pending", async () => {
    const { api, fetcher } = setup();
    let complete!: (body: any) => void;
    api.put.mockImplementation(
      (_path: any, body: any) =>
        new Promise(
          (resolve) => (complete = () => resolve({ ...body, revision: 2 })),
        ),
    );
    await ready();
    enter("Submitted question");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(complete).toBeTypeOf("function"));
    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    textarea.focus();
    enter("New unsent question");
    textarea.setSelectionRange(3, 7);
    await act(async () => complete(undefined));
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    expect(screen.getByLabelText("Message")).toBe(textarea);
    expect(textarea.value).toBe("New unsent question");
    expect(document.activeElement).toBe(textarea);
    expect(textarea.selectionStart).toBe(3);
    expect(
      JSON.parse(fetcher.mock.calls[0][1].body).messages.at(-1).content,
    ).toBe("Submitted question");
  });
  it("rejects an oversized prompt before any durable write or provider request", async () => {
    const { api, fetcher, studio } = setup();
    await ready();
    enter("x".repeat(61000));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(studio.toast).toHaveBeenCalledWith(
        expect.stringContaining("60 KB"),
        true,
      ),
    );
    expect(api.put).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
    expect(
      (screen.getByLabelText("Message") as HTMLTextAreaElement).value,
    ).toHaveLength(61000);
  });
  it("cancels the whole two-file batch and retains both unsent files without starting the second transfer", async () => {
    const { api } = setup();
    api.upload.mockImplementation(
      (_path: any, _body: any, { signal }: any) =>
        new Promise((_resolve, reject) =>
          signal.addEventListener(
            "abort",
            () => reject(new DOMException("Cancelled", "AbortError")),
            { once: true },
          ),
        ),
    );
    await ready();
    await selectFiles([file("a.txt"), file("b.txt")]);
    fireEvent.click(
      screen.getByRole("button", { name: "Upload selected files" }),
    );
    await waitFor(() => expect(api.upload).toHaveBeenCalledOnce());
    expect(
      screen.getByRole("button", { name: "Upload selected files" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await screen.findByRole("button", { name: "Retry" });
    expect(api.upload).toHaveBeenCalledOnce();
    expect(
      screen.getByRole("button", { name: "Remove a.txt" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Remove b.txt" }),
    ).toBeInTheDocument();
  });
  it("retries only the failed file and never duplicates an already accepted upload", async () => {
    const { api } = setup();
    api.upload
      .mockResolvedValueOnce(uploaded("a.txt"))
      .mockRejectedValueOnce(new Error("Second transfer failed"))
      .mockResolvedValueOnce(uploaded("b.txt"));
    await ready();
    await selectFiles([file("a.txt"), file("b.txt")]);
    fireEvent.click(
      screen.getByRole("button", { name: "Upload selected files" }),
    );
    await screen.findByRole("button", { name: "Retry" });
    expect(api.upload).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "Remove a.txt" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Remove b.txt" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(api.upload).toHaveBeenCalledTimes(3));
    await screen.findByRole("button", { name: "Remove b.txt from message" });
    expect(
      api.upload.mock.calls.map(([, body]) =>
        (body as FormData).get("file") instanceof File
          ? ((body as FormData).get("file") as File).name
          : "missing",
      ),
    ).toEqual(["a.txt", "b.txt", "b.txt"]);
    expect(
      screen.getAllByRole("button", { name: "Remove a.txt from message" }),
    ).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Remove b.txt" })).toBeNull();
  });
  it("ramps each progressive scroll overlay independently and resets it at the top", () => {
    render(
      <AssistantScrollArea data-testid="scroll">
        <p>Evidence</p>
      </AssistantScrollArea>,
    );
    const area = screen.getByTestId("scroll"),
      layers = area.querySelectorAll(".assistant-scroll-fade span");
    expect(
      Array.from(layers).map((layer) => (layer as HTMLElement).style.opacity),
    ).toEqual(["0", "0", "0"]);
    fireEvent.scroll(area, { target: { scrollTop: 12 } });
    expect(
      Array.from(layers).map((layer) => (layer as HTMLElement).style.opacity),
    ).toEqual(["0.5", "0.5", "0.5"]);
    fireEvent.scroll(area, { target: { scrollTop: 0 } });
    expect(
      Array.from(layers).map((layer) => (layer as HTMLElement).style.opacity),
    ).toEqual(["0", "0", "0"]);
  });
  it.each([
    { competing: false, includeAdmission: true },
    { competing: false, includeAdmission: false },
    { competing: true, includeAdmission: true },
  ])(
    "uses admitted CAS recovery; competing=$competing admission event=$includeAdmission",
    async ({ competing, includeAdmission }) => {
      const { api, fetcher } = setup();
      let revision = 1;
      api.put.mockImplementation(async (_path: any, body: any) => {
        if (body.revision !== revision)
          throw Object.assign(
            new Error("A competing writer changed the conversation"),
            { status: 409 },
          );
        return { ...body, revision: ++revision };
      });
      fetcher.mockImplementation(async () => {
        revision = 3;
        return new Response(
          (includeAdmission
            ? 'data: {"type":"conversation_admitted","revision":3}\n\n'
            : "") +
            'data: {"type":"text","delta":"Generated answer retained for recovery"}\n\ndata: {"type":"conversation_error","revision":3,"conflict":false,"message":"Final persistence unavailable"}\n\n',
        );
      });
      await ready();
      enter("Generate this answer");
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
      await screen.findByText("Final persistence unavailable");
      await screen.findByRole("button", { name: "Retry save" });
      expect(
        screen.getByText("Generated answer retained for recovery"),
      ).toBeInTheDocument();
      if (competing) revision = 4;
      fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
      await waitFor(() => expect(api.put).toHaveBeenCalledTimes(2));
      expect(api.put.mock.calls[1][1]).toMatchObject({
        revision: 3,
        history: expect.arrayContaining([
          expect.objectContaining({
            role: "assistant",
            content: "Generated answer retained for recovery",
          }),
        ]),
      });
      if (competing) {
        await screen.findByText("A competing writer changed the conversation");
        expect(revision).toBe(4);
        expect(screen.queryByRole("button", { name: "Retry save" })).toBeNull();
      } else {
        await screen.findByText(/Saved · revision 4/);
        expect(screen.queryByText("Final persistence unavailable")).toBeNull();
      }
      expect(
        screen.getByText("Generated answer retained for recovery"),
      ).toBeInTheDocument();
      expect(fetcher).toHaveBeenCalledOnce();
    },
  );
  it("gives the actual free selects explicit accessible typeahead text and an unambiguous default that still sends an empty model", async () => {
    const warn = vi.spyOn(console, "warn"),
      error = vi.spyOn(console, "error");
    const { fetcher } = setup();
    await ready();
    await userEvent.click(
      screen.getByRole("button", { name: "Assistant model settings" }),
    );
    await userEvent.click(screen.getByRole("button", { name: /Provider$/ }));
    await screen.findByRole("option", { name: "openai" });
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: /Model$/ }));
    const defaultOption = await screen.findByRole("option", {
      name: "Provider default · claude-opus-5-5",
    });
    expect(
      screen.getAllByRole("option", { name: "claude-opus-5-5" }),
    ).toHaveLength(1);
    await userEvent.click(defaultOption);
    enter("Use the configured provider default");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    expect(JSON.parse(fetcher.mock.calls[0][1].body).model).toBeNull();
    expect(
      [...warn.mock.calls, ...error.mock.calls]
        .flat()
        .filter((message) => String(message).includes("textValue")),
    ).toEqual([]);
  });
});
