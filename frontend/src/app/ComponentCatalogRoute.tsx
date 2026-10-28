import { useEffect, useState } from "react";
import { ComponentCatalogPage } from "../catalog";
import {
  StudioContext,
  type StudioRuntime,
  type ApiClient,
} from "./StudioContext";
import { EvidenceRegistry } from "./evidence";
/** The development artifact has no authenticated user, project data or network
 * transport. Accidental API/model operations fail rather than reaching a server. */
export function createIllustrativeCatalogRuntime(): StudioRuntime {
  const blocked = () => {
    throw new Error(
      "ILLUSTRATIVE component catalog: network and workspace operations are disabled.",
    );
  };
  const request = async () => blocked();
  const api: ApiClient = {
    get: request,
    post: request,
    put: request,
    patch: request,
    del: request,
    upload: request,
    download: request,
  };
  return {
    api,
    user: null,
    project: "ILLUSTRATIVE_LOCAL_ONLY",
    overview: null,
    route: "/__components",
    scopeKey: "ILLUSTRATIVE_LOCAL_ONLY",
    evidence: new EvidenceRegistry(),
    projects: [],
    refreshOverview: request,
    refreshProjects: request,
    signOut: request,
    navigate: blocked,
    toast: blocked,
    confirm: request,
    trackTask: blocked,
    showTasks: blocked,
    selectProject: blocked,
    onSessionExpired: blocked,
  };
}
export function ComponentCatalogRoute() {
  const [runtime] = useState(createIllustrativeCatalogRuntime);
  useEffect(() => () => runtime.evidence.clear(), [runtime]);
  return (
    <StudioContext.Provider value={runtime}>
      <div
        className="studio-page"
        style={{ padding: "1.5rem", maxWidth: "100rem", margin: "auto" }}
      >
        <p role="note">
          ILLUSTRATIVE development catalog · No account or project is connected.
          Network and model operations are disabled.
        </p>
        <ComponentCatalogPage />
      </div>
    </StudioContext.Provider>
  );
}
