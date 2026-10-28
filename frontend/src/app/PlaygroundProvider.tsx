import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  type ReactNode,
} from "react";
import { PlaygroundController } from "../controllers/playground";
import { useStudio } from "./StudioContext";
const PlaygroundContext = createContext<PlaygroundController | null>(null);
export function PlaygroundProvider({
  children,
  fetcher,
}: {
  children: ReactNode;
  fetcher?: typeof fetch;
}) {
  const studio = useStudio();
  const controller = useMemo(
    () =>
      new PlaygroundController({
        project: studio.project,
        fetcher,
        user: String(studio.user?.id ?? ""),
        onUnauthorized: () => studio.onSessionExpired(),
      }),
    [studio.project, studio.user?.id, fetcher],
  );
  useEffect(() => () => controller.deactivate(), [controller]);
  return (
    <PlaygroundContext.Provider value={controller}>
      {children}
    </PlaygroundContext.Provider>
  );
}
export function usePlaygroundController() {
  const controller = useContext(PlaygroundContext);
  if (!controller) throw new Error("PlaygroundProvider is required");
  return controller;
}
