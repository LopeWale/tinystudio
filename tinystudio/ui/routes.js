// The route inventory is separate from navigation lifecycle and page rendering.
export function createRoutes(pages, go) {
  return [
    { name: "home", pattern: /^#\/?$/, run: () => go("#/datasets") },
    { name: "datasets", pattern: /^#\/datasets$/, run: () => pages.pageDatasets() },
    { name: "repositories", pattern: /^#\/repositories$/, run: () => pages.pageRepositories() },
    { name: "dataset", pattern: /^#\/datasets\/([^/]+)$/, run: (m) => pages.pageDataset(decodeURIComponent(m[1])) },
    { name: "finetuning", pattern: /^#\/finetuning$/, run: () => pages.pageFinetuning() },
    { name: "job", pattern: /^#\/finetuning\/([^/]+)$/, run: (m) => pages.pageJob(decodeURIComponent(m[1])) },
    { name: "evaluations", pattern: /^#\/evaluations$/, run: () => pages.pageEvaluations() },
    { name: "evaluation", pattern: /^#\/evaluations\/([^/]+)$/, run: (m) => pages.pageEval(decodeURIComponent(m[1])) },
    { name: "metrics", pattern: /^#\/metrics$/, run: () => pages.pageMetrics() },
    { name: "playground", pattern: /^#\/playground(?:\?model=(.+))?$/, run: (m) => pages.pagePlayground(m[1] ? decodeURIComponent(m[1]) : null) },
    { name: "models", pattern: /^#\/models$/, run: () => pages.pageModels() },
    { name: "traces", pattern: /^#\/traces$/, run: () => pages.pageTraces() },
    { name: "stats", pattern: /^#\/stats$/, run: () => pages.pageStats() },
    { name: "keys", pattern: /^#\/api-keys$/, run: () => pages.pageKeys() },
    { name: "assistant", pattern: /^#\/assistant$/, run: () => pages.pageAssistant() },
    { name: "settings", pattern: /^#\/settings$/, run: () => pages.pageSettings() },
    { name: "users", pattern: /^#\/users$/, run: () => pages.pageUsers() },
    { name: "projects", pattern: /^#\/projects$/, run: () => pages.pageProjects() },
    { name: "interpretability", pattern: /^#\/interp$/, run: () => pages.pageInterp() },
    { name: "capture", pattern: /^#\/interp\/c\/([^/?]+)(?:\?(.*))?$/, run: (m) => pages.pageCapture(decodeURIComponent(m[1]), new URLSearchParams(m[2] || "")) },
    { name: "diff", pattern: /^#\/interp\/d\/([^/?]+)(?:\?(.*))?$/, run: (m) => pages.pageDiff(decodeURIComponent(m[1]), new URLSearchParams(m[2] || "")) },
  ];
}
