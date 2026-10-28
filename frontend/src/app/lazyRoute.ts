import { lazy, type ComponentType } from "react";
/** React caches a lazy import rejection. It needs a full document reload rather
 * than remounting the same rejected component and claiming to retry a download.
 * Source: https://react.dev/reference/react/lazy */
export class RouteModuleError extends Error {
  constructor(public readonly cause: unknown) {
    super("Page code could not be downloaded.");
    this.name = "RouteModuleError";
  }
}
export function lazyRoute(load: () => Promise<ComponentType>) {
  return lazy(async () => {
    try {
      return { default: await load() };
    } catch (cause) {
      throw new RouteModuleError(cause);
    }
  });
}
