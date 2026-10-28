import {
  createContext,
  useContext,
  useLayoutEffect,
  useRef,
  type ReactNode,
} from "react";
import { useStudio } from "../../app/StudioContext";
import {
  ComponentActionRegistry,
  type ComponentActionDefinition,
} from "../../controllers/componentActions";
const Context = createContext<ComponentActionRegistry | null>(null);
export function ComponentActionsProvider({
  registry,
  children,
}: {
  registry: ComponentActionRegistry;
  children: ReactNode;
}) {
  return <Context.Provider value={registry}>{children}</Context.Provider>;
}
export function useComponentActionRegistry() {
  return useContext(Context);
}
export function useComponentAction(definition: ComponentActionDefinition) {
  const registry = useContext(Context),
    studio = useStudio();
  const registration = useRef<ReturnType<
    ComponentActionRegistry["register"]
  > | null>(null);
  useLayoutEffect(() => {
    if (!registry) return;
    const handle = registry.register(definition);
    registration.current = handle;
    return () => {
      handle.dispose();
      if (registration.current === handle) registration.current = null;
    };
  }, [registry, studio.scopeKey, studio.route, definition.id, definition.kind]);
  useLayoutEffect(() => {
    registration.current?.update(definition);
  });
}
