// Read-model and form state shared by pages; render lifecycle belongs to the router.
export function createAppState() {
  return {
    overview: null,
    tasks: [],
    user: null,
    project: null,
    projects: [],
    pageContext: null,
    domContext: null,
  };
}
