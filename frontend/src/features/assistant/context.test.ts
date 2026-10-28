import { describe, it, expect } from "vitest";
import { EvidenceRegistry } from "../../app/evidence";
import { capturePageContext } from "./context";
describe("committed page context ownership", () => {
  it("includes current GET records and excludes other routes, projects, accounts and credential APIs", () => {
    const evidence = new EvidenceRegistry();
    for (const record of [
      {
        id: "current",
        route: "/evaluations/review",
        project: "home",
        user: "alice",
        label: "GET /api/evals/review",
        data: {
          score: 0.75,
          api_key: "never-send",
          nested: { refresh_token: "also-private" },
        },
      },
      {
        id: "old-route",
        route: "/models",
        project: "home",
        user: "alice",
        label: "GET /api/models",
        data: { name: "old-route-secret" },
      },
      {
        id: "old-project",
        route: "/evaluations/review",
        project: "other",
        user: "alice",
        label: "GET /api/evals/other",
        data: { name: "other-project-secret" },
      },
      {
        id: "old-user",
        route: "/evaluations/review",
        project: "home",
        user: "bob",
        label: "GET /api/evals/private",
        data: { name: "other-account-secret" },
      },
      {
        id: "private-api",
        route: "/evaluations/review",
        project: "home",
        user: "alice",
        label: "GET /api/settings",
        data: { key: "private-settings" },
      },
    ])
      evidence.register(record);
    const context = capturePageContext({
      evidence,
      route: "/evaluations/review",
      project: "home",
      user: "alice",
    });
    expect(context).toMatchObject({
      route: "/evaluations/review",
      project: "home",
      user: "alice",
      api: [
        {
          path: "/api/evals/review",
          data: {
            score: 0.75,
            api_key: "[redacted]",
            nested: { refresh_token: "[redacted]" },
          },
        },
      ],
    });
    expect(context.api).toHaveLength(1);
    expect(JSON.stringify(context)).not.toMatch(
      /never-send|also-private|old-route-secret|other-project-secret|other-account-secret|private-settings/,
    );
    expect(context.omissions).toContain(
      "Records from another route, project or account were excluded.",
    );
  });
  it("keeps a private page route while omitting its page content", () => {
    const evidence = new EvidenceRegistry();
    evidence.register({
      id: "p",
      label: "GET /api/evals/review",
      route: "/settings",
      project: "home",
      user: "alice",
      data: { name: "must-not-be-shared" },
    });
    const context = capturePageContext({
      evidence,
      route: "/settings",
      project: "home",
      user: "alice",
      title: "Sensitive title",
    });
    expect(context.route).toBe("/settings");
    expect(context.api).toEqual([]);
    expect(JSON.stringify(context)).not.toMatch(
      /must-not-be-shared|Sensitive title/,
    );
  });
});
