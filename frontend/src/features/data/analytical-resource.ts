import { useEffect, useState } from "react";
import { useResource, useStudio } from "../../app/StudioContext";
/** Retains evidence during refresh, scoped to the authenticated project and path. */
export function useAnalyticalResource<T>(path: string) {
  const { scopeKey } = useStudio();
  const resource = useResource<T>(path);
  const key = JSON.stringify([scopeKey, path]);
  const [last, setLast] = useState<{
    key: string;
    data: T;
    updated: string;
  } | null>(null);
  useEffect(() => {
    if (resource.data !== undefined)
      setLast({
        key,
        data: resource.data,
        updated: new Date().toLocaleTimeString(),
      });
  }, [key, resource.data]);
  const retained = last?.key === key ? last : null;
  return {
    ...resource,
    data: resource.data ?? retained?.data,
    stale: resource.data === undefined && !!retained,
    updated: retained?.updated,
  };
}
