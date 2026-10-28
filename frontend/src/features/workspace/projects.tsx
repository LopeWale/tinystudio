import { DataTable } from "../../components/studio/data-table";
import { CollectionTemplate } from "../../components/studio/templates";
import "./admin-workspace.css";
import { useEffect, useState } from "react";
import { useStudio, useResource } from "../../app/StudioContext";
import {
  Page,
  Panel,
  Button,
  Modal,
  Evidence,
  Badge,
  EmptyState,
} from "../../components/studio/primitives";
import { Input, Text, ErrorNotice, useAction, enc } from "../data/common";
export function ProjectsPage() {
  const s = useStudio(),
    r = useResource<any[]>("/api/projects"),
    a = useAction();
  const [open, O] = useState(false),
    [name, N] = useState(""),
    [goal, G] = useState(""),
    [query, Q] = useState(""),
    [inspected, I] = useState<string | null>(null);
  useEffect(() => {
    I(null);
    Q("");
    O(false);
    N("");
    G("");
  }, [s.user?.id]);
  const selected =
    (r.data || []).find((pr) => pr.slug === inspected) ||
    (r.data || []).find((pr) => pr.slug === s.project) ||
    r.data?.[0];
  const matching = (r.data || []).filter((pr) =>
    `${pr.name} ${pr.goal || ""}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  const enter = async (slug: string) => {
    s.selectProject(slug);
    s.navigate("#/datasets");
  };
  return (
    <Page
      className="projects-workspace admin-workspace"
      title="Projects"
      description="Keep each model goal together with its datasets, experiments, and results."
      actions={
        <Button
          variant="primary"
          onClick={() => {
            a.clearError();
            O(true);
          }}
        >
          New project
        </Button>
      }
    >
      <CollectionTemplate
        label="Project directory workspace"
        toolbar={
          <div className="admin-directory-toolbar">
            <Input
              label="Find a project"
              type="search"
              value={query}
              onChange={Q}
            />
          </div>
        }
        summary={
          <div className="admin-scope">
            <strong>{r.data?.length ?? "—"} projects</strong>
            <span>Current project: {s.project}</span>
            <span>Datasets · training · evaluations</span>
          </div>
        }
        inspector={
          selected ? (
            <section
              className="admin-directory-detail"
              aria-label="Project details"
            >
              <div>
                <h2>{selected.name}</h2>
                <span className="data-muted">{selected.slug}</span>
              </div>
              <Evidence label="Project goal" data={selected}>
                <p>
                  {selected.goal ||
                    "No goal has been supplied for this project."}
                </p>
              </Evidence>
              <dl>
                <dt>Datasets</dt>
                <dd>{selected.datasets ?? "—"}</dd>
                <dt>Training jobs</dt>
                <dd>{selected.jobs ?? "—"}</dd>
                <dt>Created</dt>
                <dd>{selected.created || "—"}</dd>
              </dl>
              <Button
                variant="primary"
                onClick={() => a.run(() => enter(selected.slug))}
              >
                Open project
              </Button>
              {s.user?.role === "admin" && (r.data?.length || 0) > 1 && (
                <Button
                  variant="danger"
                  size="sm"
                  aria-label={`Delete ${selected.name}`}
                  onClick={() =>
                    a.run(async () => {
                      if (
                        !(await s.confirm(
                          `Delete project ${selected.slug} with all its datasets, jobs, weights, evaluations, traces, and repositories?`,
                          "Delete project",
                        ))
                      )
                        return;
                      await s.api.del(`/api/p/${enc(selected.slug)}`);
                      await s.refreshProjects();
                      I(null);
                      r.reload();
                    })
                  }
                >
                  Delete project
                </Button>
              )}
            </section>
          ) : (
            <section className="admin-directory-detail">
              <h2>Project details</h2>
              <p>Select a project to inspect its goal and resources.</p>
            </section>
          )
        }
      >
        <ErrorNotice error={r.error || a.error} />
        {r.loading && <p role="status">Loading projects…</p>}

        <>
          <Panel title="Project directory">
            <DataTable
              label="Projects"
              rows={matching}
              getRowId={(pr) => pr.slug}
              scopeKey={s.scopeKey}
              renderRow={(pr, { key, ...props }, cells) => (
                <Evidence
                  key={key}
                  as="tr"
                  {...props}
                  label="Project"
                  data={pr}
                >
                  {cells}
                </Evidence>
              )}
              columns={[
                {
                  id: "name",
                  label: "Project",
                  value: (pr) => pr.name,
                  hideable: false,
                  render: (pr) => (
                    <>
                      <Button
                        variant="ghost"
                        aria-pressed={selected?.slug === pr.slug}
                        onClick={() => I(pr.slug)}
                      >
                        {pr.name}
                      </Button>
                      {s.project === pr.slug && <Badge>current</Badge>}
                    </>
                  ),
                },
                {
                  id: "datasets",
                  label: "Datasets",
                  value: (pr) => pr.datasets,
                  render: (pr) => <>{pr.datasets ?? "—"}</>,
                },
                {
                  id: "jobs",
                  label: "Jobs",
                  value: (pr) => pr.jobs,
                  render: (pr) => <>{pr.jobs ?? "—"}</>,
                },
                {
                  id: "created",
                  label: "Created",
                  value: (pr) => pr.created,
                  render: (pr) => <>{pr.created || "—"}</>,
                },
              ]}
            />
            {!r.loading && !!r.data?.length && !matching.length && (
              <EmptyState>
                <div className="records-empty-recovery">
                  <span>No projects match this search.</span>
                  <Button onClick={() => Q("")}>Clear search</Button>
                </div>
              </EmptyState>
            )}
          </Panel>
        </>
        {!r.loading && !r.error && !r.data?.length && (
          <EmptyState>
            No projects yet. Create a project and give your model a goal.
          </EmptyState>
        )}
        {open && (
          <Modal
            open
            title="New project"
            onClose={() => O(false)}
            footer={
              <Button
                variant="primary"
                loading={a.busy}
                onClick={() =>
                  a.run(async () => {
                    if (!name.trim()) throw Error("Give the project a name");
                    const pr = await s.api.post<any>("/api/projects", {
                      name: name.trim(),
                      goal: goal.trim(),
                    });
                    O(false);
                    await s.refreshProjects();
                    await enter(pr.slug);
                  })
                }
              >
                Create project
              </Button>
            }
          >
            <ErrorNotice error={a.error} />
            <Input label="Name" value={name} onChange={N} />
            <Text
              label="Goal (what the model should become good at)"
              value={goal}
              onChange={G}
            />
          </Modal>
        )}
      </CollectionTemplate>
    </Page>
  );
}
