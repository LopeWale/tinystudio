import { describe, it, expect, vi } from "vitest";
import {
  ConversationController,
  prepareRequest,
  readEvents,
} from "./conversation";
const snapshot = {
  revision: 2,
  history: [{ role: "user", content: "old" }],
  provider: "anthropic",
  model: "claude-opus-5-5",
};
const syncFactory = () => ({
  start: vi.fn(),
  stop: vi.fn(),
  requestCheck: vi.fn(),
  notifySaved: vi.fn(),
});
describe("durable conversation ownership", () => {
  it("does not adopt a late read after the scope stops", async () => {
    let complete!: (value: any) => void;
    const api = {
      get: vi.fn(() => new Promise((resolve) => (complete = resolve))),
    } as any;
    const c = new ConversationController({
      api,
      project: "a",
      user: "alice",
      syncFactory,
    });
    const read = c.refresh();
    await vi.waitFor(() => expect(api.get).toHaveBeenCalled());
    c.stop();
    complete(snapshot);
    await read;
    expect(c.getSnapshot().loaded).toBe(false);
  });
  it("remote updates preserve focused draft; dirty conflict never overwrites local turns", async () => {
    const api = {
      get: vi.fn(async () => snapshot),
      put: vi.fn(async () => {
        throw Object.assign(new Error("conflict"), { status: 409 });
      }),
    } as any;
    const c = new ConversationController({
      api,
      project: "a",
      user: "alice",
      syncFactory,
    });
    await c.refresh();
    c.setDraft("keep this draft");
    await c.refresh();
    expect(c.getSnapshot().draft).toBe("keep this draft");
    await expect(c.clear()).rejects.toThrow("conflict");
    await c.refresh();
    expect(c.getSnapshot().history).toEqual([]);
    expect(c.getSnapshot().conflict).toBe(true);
  });
  it("keeps server model IDs exactly and rejects oversized last requests", () => {
    expect(() =>
      prepareRequest({
        messages: [{ role: "user", content: "a".repeat(61000) }],
      }),
    ).toThrow("60 KB");
    expect(
      prepareRequest({
        model: "claude-haiku-5-5",
        messages: [{ role: "user", content: "hi" }],
      }).model,
    ).toBe("claude-haiku-5-5");
  });
  it("parses split UTF8 and CRLF frames without losing real tool events", async () => {
    const seen: any[] = [];
    const bytes = new TextEncoder().encode(
      'data: {"type":"tool_call","id":"a","name":"résumé"}\r\n\r\ndata: {"type":"tool_result","id":"a","output":"done"}\r\n\r\n',
    );
    await readEvents(
      new ReadableStream({
        start(c) {
          c.enqueue(bytes.slice(0, 45));
          c.enqueue(bytes.slice(45));
          c.close();
        },
      }),
      (e) => {
        seen.push(e);
      },
      new AbortController().signal,
    );
    expect(seen.map((e) => e.type)).toEqual(["tool_call", "tool_result"]);
    expect(seen[0].name).toBe("résumé");
  });
  it("does not start a queued write after its owner stops", async () => {
    const api = { get: vi.fn(async () => snapshot), put: vi.fn() } as any;
    const c = new ConversationController({
      api,
      project: "a",
      user: "alice",
      syncFactory,
    });
    await c.refresh();
    const write = c.clear();
    c.stop();
    await write;
    expect(api.put).not.toHaveBeenCalled();
  });
  it("cancels a pending stream read without waiting for another byte", async () => {
    const cancel = vi.fn();
    const controller = new AbortController();
    const reading = readEvents(
      new ReadableStream({ cancel }),
      () => {},
      controller.signal,
    );
    controller.abort();
    await expect(reading).rejects.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("persists the user first and publishes only real stream work with immutable snapshots", async () => {
    const order: string[] = [];
    const api = {
      get: vi.fn(async () => snapshot),
      put: vi.fn(async (_path, body) => {
        order.push("saved-user");
        return { ...body, revision: 3 };
      }),
    } as any;
    const events = [
      {
        type: "tool_call",
        id: "t",
        name: "read_evaluation",
        input: { name: "review" },
      },
      { type: "tool_result", id: "t", output: "actual metrics" },
      { type: "search", id: "s", state: "completed", query: "provider query" },
      {
        type: "source",
        url: "https://example.org/research",
        title: "Research",
      },
      { type: "text", delta: "Result" },
      { type: "conversation_saved", revision: 4 },
    ];
    const fetcher = vi.fn(async (_url: any, options: any) => {
      order.push("provider");
      expect(JSON.parse(options.body).conversation_revision).toBe(3);
      return new Response(
        new ReadableStream({
          start(controller) {
            for (const event of events)
              controller.enqueue(
                new TextEncoder().encode(
                  "data: " + JSON.stringify(event) + "\n\n",
                ),
              );
            controller.close();
          },
        }),
      );
    }) as any;
    const c = new ConversationController({
      api,
      fetcher,
      project: "a",
      user: "alice",
      syncFactory,
    });
    c.start();
    await c.refresh();
    c.setDraft("Review these results");
    let initialReply: any;
    c.subscribe(() => {
      const last = c.getSnapshot().history.at(-1);
      if (last?.role === "assistant" && !initialReply) initialReply = last;
    });
    await c.send();
    expect(order).toEqual(["saved-user", "provider"]);
    expect(c.getSnapshot()).toMatchObject({
      busy: false,
      dirty: false,
      revision: 4,
      draft: "",
    });
    expect(c.getSnapshot().history.at(-1)).toMatchObject({
      content: "Result",
      calls: [{ id: "t", output: "actual metrics" }],
      sources: [{ url: "https://example.org/research" }],
      searches: [{ query: "provider query" }],
    });
    expect(initialReply.content).toBe("");
    c.stop();
  });

  it("rejects a picked record when its rendered data version changed on the same route", async () => {
    const api = { get: vi.fn(async () => snapshot), put: vi.fn() } as any;
    const fetcher = vi.fn() as any;
    const c = new ConversationController({
      api,
      fetcher,
      project: "a",
      user: "alice",
      syncFactory,
      validateSelection: () => false,
    });
    await c.refresh();
    c.setDraft("inspect this");
    c.setOptions({
      selection: {
        id: "row1",
        project: "a",
        user: "alice",
        route: location.hash.replace(/^#/, ""),
        data: { revision: 1 },
      },
    });
    await expect(c.send()).rejects.toThrow("stale");
    expect(api.put).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("captures unpicked page evidence before asynchronous refresh and does not mix a later route or data version", async () => {
    let release!: (value: any) => void;
    let reads = 0;
    const context = {
      route: "/evaluations/review",
      project: "a",
      user: "alice",
      api: [
        {
          path: "/api/evals/review",
          data: { score: 0.75, api_key: "never-send" },
        },
      ],
    };
    const api = {
      get: vi.fn(async () =>
        ++reads === 1
          ? snapshot
          : new Promise((resolve) => (release = resolve)),
      ),
      put: vi.fn(async (_path: any, body: any) => ({ ...body, revision: 3 })),
    } as any;
    const fetcher = vi.fn(
      async () =>
        new Response('data: {"type":"conversation_saved","revision":4}\n\n'),
    ) as any;
    const controller = new ConversationController({
      api,
      fetcher,
      project: "a",
      user: "alice",
      captureContext: () => context,
      syncFactory,
    });
    await controller.refresh();
    controller.setDraft("Review the open evaluation");
    const sending = controller.send();
    await vi.waitFor(() => expect(release).toBeTypeOf("function"));
    context.route = "/models";
    context.api[0].data.score = 0.1;
    controller.setDraft("A new unsent draft written while saving");
    release(snapshot);
    await sending;
    const body = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(body.context).toMatchObject({
      route: "/evaluations/review",
      project: "a",
      user: "alice",
      api: [
        {
          path: "/api/evals/review",
          data: { score: 0.75, api_key: "[redacted]" },
        },
      ],
    });
    expect(body.context.selected_data).toBeUndefined();
    expect(body.messages.at(-1).content).toBe("Review the open evaluation");
    expect(controller.getSnapshot().draft).toBe(
      "A new unsent draft written while saving",
    );
  });
  it("refuses a context callback from another account or project before any model request", async () => {
    for (const context of [
      { route: "/evaluations/review", project: "other", user: "alice" },
      { route: "/evaluations/review", project: "a", user: "bob" },
    ]) {
      const api = { get: vi.fn(async () => snapshot), put: vi.fn() } as any,
        fetcher = vi.fn() as any;
      const controller = new ConversationController({
        api,
        fetcher,
        project: "a",
        user: "alice",
        captureContext: () => context,
        syncFactory,
      });
      await controller.refresh();
      controller.setDraft("Review");
      await expect(controller.send()).rejects.toThrow(
        "another account or project",
      );
      expect(fetcher).not.toHaveBeenCalled();
      expect(api.put).not.toHaveBeenCalled();
    }
  });
  it("compacts API context with explicit omission markers while preserving a small exact selection and original data", () => {
    const data = {
      rows: Array.from({ length: 200 }, (_, id) => ({
        id,
        text: "x".repeat(12000),
      })),
    };
    const selected = { id: "picked-row", data: { metric: 0.875, example: 42 } };
    const body = {
      messages: [{ role: "user", content: "Review the selected score" }],
      context: {
        route: "/evaluations/review",
        project: "a",
        user: "alice",
        selected_data: selected,
        api: [{ path: "/api/evals/review", data }],
        omissions: ["Existing capture omission"],
      },
    };
    const outgoing = prepareRequest(body);
    expect(outgoing.context.selected_data).toEqual(selected);
    expect(outgoing.messages).toEqual(body.messages);
    expect(
      new TextEncoder().encode(JSON.stringify(outgoing)).length,
    ).toBeLessThan(240000);
    expect(JSON.stringify(outgoing.context.api)).toContain("omitted");
    expect(outgoing.context.omissions).toContain("Existing capture omission");
    expect(outgoing.context.omissions).toContain(
      "API source compacted for size: /api/evals/review",
    );
    expect(data.rows).toHaveLength(200);
    expect(data.rows[0].text).toHaveLength(12000);
  });
  it.each(["anthropic", "openai"])(
    "adopts a clean remote empty model default for provider %s",
    async (provider) => {
      let remote = { ...snapshot };
      const api = {
        get: vi.fn(async () => remote),
        put: vi.fn(async (_path: any, body: any) => ({
          ...body,
          revision: body.revision + 1,
        })),
      } as any;
      const fetcher = vi.fn(
        async () =>
          new Response('data: {"type":"conversation_saved","revision":5}\n\n'),
      ) as any;
      const controller = new ConversationController({
        api,
        fetcher,
        project: "a",
        user: "alice",
        syncFactory,
      });
      await controller.refresh();
      remote = { ...snapshot, revision: 3, provider, model: "" };
      await controller.refresh();
      expect(controller.getSnapshot()).toMatchObject({ provider, model: "" });
      controller.setDraft("Use the provider default");
      await controller.send();
      expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({
        provider,
        model: null,
      });
    },
  );
  it("preserves an outstanding local choice when a remote writer resets its provider/model default", async () => {
    let remote = { ...snapshot };
    const api = { get: vi.fn(async () => remote) } as any;
    const controller = new ConversationController({
      api,
      project: "a",
      user: "alice",
      syncFactory,
    });
    await controller.refresh();
    controller.setOptions({ provider: "openai", model: "gpt-4.1-mini" });
    remote = { ...snapshot, revision: 3, model: "" };
    await controller.refresh();
    expect(controller.getSnapshot()).toMatchObject({
      provider: "openai",
      model: "gpt-4.1-mini",
    });
  });
  it("publishes stable deeply frozen snapshots and defensively owns server data, selections and even shallow-frozen caller collections", async () => {
    const remote = {
      ...snapshot,
      history: [
        {
          role: "assistant",
          content: "Existing answer",
          calls: [
            {
              id: "call",
              name: "get_evaluation",
              input: { filters: { dataset: "home" } },
            },
          ],
        },
      ],
    };
    const api = { get: vi.fn(async () => remote) } as any;
    const controller = new ConversationController({
      api,
      project: "a",
      user: "alice",
      syncFactory,
    });
    await controller.refresh();
    const first = controller.getSnapshot();
    expect(controller.getSnapshot()).toBe(first);
    expect(Object.isFrozen(first)).toBe(true);
    expect(() => {
      (first as any).revision = 999;
    }).toThrow(TypeError);
    expect(() => {
      (first.history as any).push({ role: "user", content: "injected" });
    }).toThrow(TypeError);
    expect(() => {
      (first.history[0].calls![0].input as any).filters.dataset = "injected";
    }).toThrow(TypeError);
    remote.history[0].content = "Changed outside";
    remote.history[0].calls[0].input.filters.dataset = "outside";
    expect(first.history[0].content).toBe("Existing answer");
    expect((first.history[0].calls![0].input as any).filters.dataset).toBe(
      "home",
    );
    const attachment = {
      id: "a".repeat(32),
      name: "original.txt",
      size: 1,
      sha256: "hash",
      project_id: "p",
      user_id: "alice",
      extracted_chars: 1,
      retained_chars: 1,
      status: "ready" as const,
    };
    const supplied = Object.freeze([attachment]);
    controller.setAttachments(supplied);
    attachment.name = "changed-outside.txt";
    const selection = {
      route: "/",
      project: "a",
      user: "alice",
      data: { score: 0.75, rows: [{ id: 42 }] },
    };
    controller.setOptions({ selection });
    selection.data.score = 0;
    selection.data.rows[0].id = 999;
    const current = controller.getSnapshot(),
      notified = vi.fn();
    controller.subscribe(notified);
    expect(current.attachments[0].name).toBe("original.txt");
    expect(current.selection!.data).toEqual({
      score: 0.75,
      rows: [{ id: 42 }],
    });
    expect(() => {
      (current.attachments as any).pop();
    }).toThrow(TypeError);
    expect(() => {
      (current.attachments[0] as any).name = "bypass";
    }).toThrow(TypeError);
    expect(() => {
      (current.selection!.data as any).rows[0].id = 100;
    }).toThrow(TypeError);
    expect(notified).not.toHaveBeenCalled();
    expect(controller.getSnapshot()).toBe(current);
    expect(controller.getSnapshot().revision).toBe(2);
  });
  it.each(["anthropic", "openai"])(
    "uses a clean remote %s default discovered DURING send refresh",
    async (provider) => {
      const api = {
        get: vi
          .fn()
          .mockResolvedValueOnce(snapshot)
          .mockResolvedValue({ ...snapshot, revision: 3, provider, model: "" }),
        put: vi.fn(async (_path: any, body: any) => ({
          ...body,
          revision: body.revision + 1,
        })),
      } as any;
      const fetcher = vi.fn(
        async () =>
          new Response('data: {"type":"conversation_saved","revision":5}\n\n'),
      ) as any;
      const controller = new ConversationController({
        api,
        fetcher,
        project: "a",
        user: "alice",
        syncFactory,
      });
      await controller.refresh();
      controller.setDraft("Use the remotely selected provider default");
      await controller.send();
      expect(api.put.mock.calls[0][1]).toMatchObject({ provider, model: "" });
      expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({
        provider,
        model: null,
      });
    },
  );
  it("preserves explicit dirty local provider/model choices against a remote default discovered DURING send refresh", async () => {
    const api = {
      get: vi
        .fn()
        .mockResolvedValueOnce(snapshot)
        .mockResolvedValue({
          ...snapshot,
          revision: 3,
          provider: "anthropic",
          model: "",
        }),
      put: vi.fn(async (_path: any, body: any) => ({
        ...body,
        revision: body.revision + 1,
      })),
    } as any;
    const fetcher = vi.fn(
      async () =>
        new Response('data: {"type":"conversation_saved","revision":5}\n\n'),
    ) as any;
    const controller = new ConversationController({
      api,
      fetcher,
      project: "a",
      user: "alice",
      syncFactory,
    });
    await controller.refresh();
    controller.setOptions({ provider: "openai", model: "gpt-4.1-mini" });
    controller.setDraft("Use my explicit model");
    await controller.send();
    expect(api.put.mock.calls[0][1]).toMatchObject({
      provider: "openai",
      model: "gpt-4.1-mini",
    });
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({
      provider: "openai",
      model: "gpt-4.1-mini",
    });
  });
});
