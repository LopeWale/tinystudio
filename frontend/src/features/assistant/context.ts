import {
  canCaptureApiEvidence,
  sanitizeEvidence,
  type EvidenceRegistry,
} from "../../app/evidence";
import type { PageContext } from "../../controllers/conversation";

/** Capture only committed records from one rendered route and identity. No raw DOM text or form values. */
export function capturePageContext({
  evidence,
  route,
  project,
  user,
  title = "",
}: {
  evidence: EvidenceRegistry;
  route: string;
  project: string;
  user: string;
  title?: string;
}): PageContext {
  const currentRoute = route.replace(/^#/, "") || "/";
  const omissions: string[] = [];
  const snapshot = evidence.snapshot();
  const current = snapshot.filter(
    (record) =>
      record.route.replace(/^#/, "") === currentRoute &&
      record.project === project &&
      record.user === user,
  );
  if (current.length !== snapshot.length)
    omissions.push(
      "Records from another route, project or account were excluded.",
    );
  const api = new Map<
    string,
    { path: string; data: unknown; record_id: string }
  >();
  const privatePage = /^\/(?:api-keys|settings|users)(?:[/?]|$)/.test(
    currentRoute,
  );
  if (privatePage)
    omissions.push("Credential and account page content was omitted.");
  else
    for (const record of current) {
      if (!record.label.startsWith("GET ")) continue;
      const path = record.label.slice(4);
      if (canCaptureApiEvidence(path))
        api.set(path, {
          path,
          data: sanitizeEvidence(record.data),
          record_id: record.id,
        });
    }
  const records = Array.from(api.values());
  if (records.length > 8)
    omissions.push(
      `${records.length - 8} additional current rendered GET sources omitted; pick a narrower element to include it explicitly.`,
    );
  return {
    route: currentRoute,
    project,
    user,
    title: privatePage ? "Private workspace page" : title,
    api: records.slice(0, 8),
    omissions,
  };
}
