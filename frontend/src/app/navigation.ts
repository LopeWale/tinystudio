import {
  RiDatabase2Line,
  RiFolder3Line,
  RiFlaskLine,
  RiBarChartLine,
  RiCpuLine,
  RiChat3Line,
  RiSettings3Line,
  RiKey2Line,
  RiGroupLine,
  RiApps2Line,
} from "@remixicon/react";
/** One route vocabulary for the rail, page finder and assistant actions. */
export const destinations = [
  [
    "Data",
    [
      ["Datasets", "/datasets"],
      ["Repositories", "/repositories"],
    ],
  ],
  [
    "Experiments",
    [
      ["Fine tuning", "/finetuning"],
      ["Evaluations", "/evaluations"],
      ["Metrics", "/metrics"],
      ["Laboratory", "/lab"],
    ],
  ],
  [
    "Explore",
    [
      ["Interpretability", "/interp"],
      ["Models", "/models"],
      ["Playground", "/playground"],
      ["Traces", "/traces"],
    ],
  ],
  [
    "Workspace",
    [
      ["Projects", "/projects"],
      ["API keys", "/api-keys"],
      ["Stats", "/stats"],
      ["Settings", "/settings"],
      ["Users", "/users"],
    ],
  ],
] as const;
export function visibleDestinations(role?: string) {
  return destinations.map(([group, items]) => ({
    group,
    items: items.filter(([label]) => label !== "Users" || role === "admin"),
  }));
}
const icons: Record<string, typeof RiDatabase2Line> = {
  "/datasets": RiDatabase2Line,
  "/repositories": RiFolder3Line,
  "/finetuning": RiCpuLine,
  "/evaluations": RiFlaskLine,
  "/metrics": RiBarChartLine,
  "/lab": RiFlaskLine,
  "/interp": RiBarChartLine,
  "/models": RiCpuLine,
  "/playground": RiChat3Line,
  "/traces": RiBarChartLine,
  "/projects": RiFolder3Line,
  "/api-keys": RiKey2Line,
  "/stats": RiBarChartLine,
  "/settings": RiSettings3Line,
  "/users": RiGroupLine,
};
export function destinationFor(route: string) {
  const path = route.split("?")[0];
  return destinations
    .flatMap(([, items]) => [...items])
    .find(([, url]) => path === url || path.startsWith(url + "/"));
}
export function destinationIcon(route: string) {
  return icons[destinationFor(route)?.[1] || ""] || RiApps2Line;
}
export function locationTitle(route: string) {
  const path = route.split("?")[0],
    dest = destinationFor(route);
  let name = path.split("/").filter(Boolean).at(-1) || "Workspace";
  try {
    name = decodeURIComponent(name);
  } catch {
    /* Malformed links still need a location label. */
  }
  return dest
    ? path === dest[1]
      ? dest[0]
      : `${dest[0]} / ${name}`
    : "Workspace";
}
