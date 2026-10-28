import { useLayoutEffect, useRef, useState } from "react";
import { useStudio } from "../../app/StudioContext";
import { DataTable } from "../../components/studio/data-table";
import { CollectionToolbar } from "../../components/studio/collection-toolbar";
import { CollectionTemplate } from "../../components/studio/templates";
import {
  Page,
  Button,
  Modal,
  Evidence,
  Badge,
  EmptyState,
} from "../../components/studio/primitives";
import { Input } from "../../components/boardui/base/input/input";
import { Textarea } from "../../components/boardui/base/textarea/textarea";
import { cx } from "../../components/boardui/utils/cx";
import {
  RecordsInspector,
  useRetainedResource,
} from "../data/records-workspace";
import { ErrorNotice, enc } from "../data/common";
import "../data/records-workspace.css";
import "../data/affect-data.css";
import "../../components/studio/form-controls.css";
import "./projects-workspace.css";

export function ProjectsPage() {
  const s = useStudio();
  return (
    <ProjectDirectory
      key={JSON.stringify([s.scopeKey, s.project, s.user?.id, s.user?.role])}
    />
  );
}

function ProjectDirectory() {
  const s = useStudio(),
    r = useRetainedResource<any[]>("/api/projects");
  const [open, setOpen] = useState(false),
    [name, setName] = useState(""),
    [goal, setGoal] = useState("");
  const [query, setQuery] = useState(""),
    [inspected, setInspected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [dialogError, setDialogError] = useState("");
  const mounted = useRef(true),
    pending = useRef(false),
    generation = useRef(0),
    draftRevision = useRef(0);
  const search = useRef<HTMLInputElement>(null),
    returnFocus = useRef<HTMLButtonElement | null>(null);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const selected = (r.data || []).find((pr) => pr.slug === inspected);
  const matching = (r.data || []).filter((pr) =>
    `${pr.name} ${pr.goal || ""}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  function closeDialog() {
    generation.current++;
    setOpen(false);
    setDialogError("");
  }
  function showDialog() {
    generation.current++;
    setDialogError("");
    setOpen(true);
  }
  function closeInspector() {
    setInspected(null);
    const button = returnFocus.current;
    if (button?.isConnected) button.focus();
    else search.current?.focus();
  }
  function enter(slug: string) {
    s.selectProject(slug);
    s.navigate("#/datasets");
  }
  async function create() {
    if (pending.current) return;
    if (!name.trim()) {
      setDialogError("Give the project a name");
      return;
    }
    pending.current = true;
    setBusy(true);
    setDialogError("");
    const owner = generation.current,
      revision = draftRevision.current;
    let created = false;
    try {
      const project = await s.api.post<any>("/api/projects", {
        name: name.trim(),
        goal: goal.trim(),
      });
      created = true;
      if (!mounted.current) return;
      // A completed creation must not become a repeatable failed submission if refresh fails.
      const shouldEnter =
        generation.current === owner && draftRevision.current === revision;
      if (shouldEnter) {
        closeDialog();
        setName("");
        setGoal("");
      }
      r.reload();
      await s.refreshProjects();
      if (!mounted.current) return;
      if (shouldEnter && generation.current === owner + 1) enter(project.slug);
      else s.toast("Project created");
    } catch (failure: any) {
      if (!mounted.current) return;
      const message = failure.message || String(failure);
      if (created)
        setError(
          `Project created, but the directory could not refresh. ${message}`,
        );
      else if (generation.current === owner) setDialogError(message);
      else setError(message);
    } finally {
      if (mounted.current) {
        pending.current = false;
        setBusy(false);
      }
    }
  }
  async function remove() {
    if (
      pending.current ||
      !selected ||
      s.user?.role !== "admin" ||
      (r.data?.length || 0) < 2
    )
      return;
    pending.current = true;
    setBusy(true);
    setError("");
    const project = selected;
    try {
      const approved = await s.confirm(
        `Delete project ${project.slug} with all its datasets, jobs, weights, evaluations, traces, and repositories?`,
        "Delete project",
      );
      if (!approved || !mounted.current) return;
      await s.api.del(`/api/p/${enc(project.slug)}`);
      if (!mounted.current) return;
      closeInspector();
      r.reload();
      await s.refreshProjects();
    } catch (failure: any) {
      if (mounted.current) setError(failure.message || String(failure));
    } finally {
      if (mounted.current) {
        pending.current = false;
        setBusy(false);
      }
    }
  }
  return (
    <Page
      className="projects-workspace data-workspace records-page collection-page"
      title="Projects"
      description="Keep each model goal together with its datasets, experiments, and results."
      actions={
        <Button variant="primary" onClick={showDialog}>
          New project
        </Button>
      }
    >
      <ErrorNotice error={error} />
      <CollectionTemplate
        className="data-template"
        label="Project directory workspace"
      >
        <div
          className={cx(
            "records-workbench",
            selected && "records-workbench-inspecting",
          )}
        >
          <div className="records-table-host">
            {r.error && (
              <div className="projects-load-error">
                <ErrorNotice error={r.error} />
                <Button onClick={r.reload}>Retry projects</Button>
              </div>
            )}
            <DataTable
              label="Projects"
              rows={matching}
              getRowId={(pr) => pr.slug}
              scopeKey={s.scopeKey}
              layout="fill"
              pagination="client"
              toolbarContent={
                <>
                  <CollectionToolbar
                    label="Find a project"
                    query={query}
                    onQueryChange={setQuery}
                    searchRef={search}
                  />
                  <span className="projects-count">
                    {r.data?.length ?? "—"} projects
                  </span>
                </>
              }
              rowProps={(pr) => ({
                "aria-selected": selected?.slug === pr.slug,
                className:
                  selected?.slug === pr.slug ? "records-inspected" : undefined,
              })}
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
              empty={
                <EmptyState>
                  {r.loading ? (
                    <span role="status">Loading projects…</span>
                  ) : r.error ? (
                    "Project directory unavailable."
                  ) : r.data?.length ? (
                    <div className="records-empty-recovery">
                      <span>No projects match this search.</span>
                      <Button onClick={() => setQuery("")}>Clear search</Button>
                    </div>
                  ) : (
                    "No projects yet. Create a project and give your model a goal."
                  )}
                </EmptyState>
              }
              columns={[
                {
                  id: "name",
                  label: "Project",
                  value: (pr) => pr.name,
                  hideable: false,
                  render: (pr) => (
                    <div className="projects-identity">
                      <Button
                        variant="ghost"
                        aria-pressed={selected?.slug === pr.slug}
                        onClick={(event) => {
                          returnFocus.current = event.currentTarget;
                          setInspected(pr.slug);
                        }}
                      >
                        {pr.name}
                      </Button>
                      {s.project === pr.slug && <Badge>current</Badge>}
                    </div>
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
          </div>
          {selected && (
            <RecordsInspector title="Project details" onClose={closeInspector}>
              <div className="projects-detail">
                <div>
                  <h3>{selected.name}</h3>
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
                <div className="projects-detail-actions">
                  <Button
                    variant="primary"
                    onClick={() => enter(selected.slug)}
                  >
                    Open project
                  </Button>
                  {s.user?.role === "admin" && (r.data?.length || 0) > 1 && (
                    <Button
                      variant="danger"
                      loading={busy}
                      aria-label={`Delete ${selected.name}`}
                      onClick={remove}
                    >
                      Delete project
                    </Button>
                  )}
                </div>
              </div>
            </RecordsInspector>
          )}
        </div>
      </CollectionTemplate>
      <Modal
        open={open}
        title="New project"
        className="projects-create-dialog"
        onClose={closeDialog}
        footer={
          <Button variant="primary" loading={busy} onClick={create}>
            Create project
          </Button>
        }
      >
        <ErrorNotice error={dialogError} />
        <div className="projects-create-fields">
          <Input
            size="small"
            fieldClassName="studio-form-control"
            label="Name"
            value={name}
            onChange={(value) => {
              draftRevision.current++;
              setName(value);
            }}
          />
          <Textarea
            size="small"
            fieldClassName="studio-form-control"
            label="Goal (what the model should become good at)"
            rows={3}
            value={goal}
            onChange={(value) => {
              draftRevision.current++;
              setGoal(value);
            }}
          />
        </div>
      </Modal>
    </Page>
  );
}
