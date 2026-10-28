import { useEffect, useRef, useState } from "react";
import { useStudio, useResource } from "../../app/StudioContext";
import { DataTable } from "../../components/studio/data-table";
import { CollectionToolbar } from "../../components/studio/collection-toolbar";
import { CollectionTemplate } from "../../components/studio/templates";
import {
  Page,
  Button,
  Modal,
  Evidence,
  EmptyState,
  Badge,
} from "../../components/studio/primitives";
import { Input } from "../../components/boardui/base/input/input";
import { Checkbox } from "../../components/boardui/base/checkbox/checkbox";
import {
  Select,
  SelectItem,
} from "../../components/boardui/base/select/select";
import { ErrorNotice, enc } from "../data/common";
import "../data/records-workspace.css";
import "../data/affect-data.css";
import "../../components/studio/form-controls.css";
import "./users-workspace.css";

const emptyDraft = () => ({ email: "", name: "", password: "", admin: false });
export function UsersPage() {
  const s = useStudio(),
    admin = s.user?.role === "admin";
  const r = useResource<any[]>(admin ? "/api/users" : null);
  const context = JSON.stringify([s.user?.id, admin, s.scopeKey]);
  const currentContext = useRef(context);
  const contextVersion = useRef(0);
  if (currentContext.current !== context) {
    contextVersion.current++;
    currentContext.current = context;
  }
  const mounted = useRef(true),
    dialogGeneration = useRef(0),
    draftRevision = useRef(0);
  const tableOperation = useRef<object | null>(null);
  // Admission belongs to the request, so dismissal cannot admit a competing write.
  const pendingSaves = useRef(new Map<string, object>());
  const [, renderSettlement] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(""),
    [dialogError, setDialogError] = useState("");
  const [open, setOpen] = useState(""),
    [target, setTarget] = useState<any>(null);
  const [query, setQuery] = useState(""),
    [roleFilter, setRoleFilter] = useState("all");
  const [draft, setDraft] = useState(emptyDraft);
  const field =
    (key: keyof ReturnType<typeof emptyDraft>) => (value: string | boolean) => {
      draftRevision.current++;
      setDraft((p) => ({ ...p, [key]: value }));
    };
  const saveKey = JSON.stringify([
    context,
    contextVersion.current,
    open,
    open === "create" ? "create" : target?.id,
  ]);
  const saving = pendingSaves.current.has(saveKey);
  function close() {
    dialogGeneration.current++;

    setOpen("");
    setTarget(null);
    setDraft(emptyDraft());
    setDialogError("");
  }
  function show(kind: string, user: any = null) {
    close();
    setTarget(user);
    setOpen(kind);
  }
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    close();
    setQuery("");
    setRoleFilter("all");
    setError("");
    setBusy(false);
    tableOperation.current = null;
  }, [context]);
  const ownsContext = (owner: string, version: number) =>
    mounted.current &&
    currentContext.current === owner &&
    contextVersion.current === version;
  async function mutate(action: (active: () => boolean) => Promise<void>) {
    if (!admin || tableOperation.current) return;
    const operation = {},
      owner = context,
      version = contextVersion.current;
    tableOperation.current = operation;
    setBusy(true);
    setError("");
    const active = () =>
      ownsContext(owner, version) && tableOperation.current === operation;
    try {
      await action(active);
    } catch (failure: any) {
      if (active()) setError(failure.message || String(failure));
    } finally {
      if (active()) {
        tableOperation.current = null;
        setBusy(false);
      }
    }
  }
  async function save() {
    if (!admin || pendingSaves.current.has(saveKey)) return;
    const operation = {},
      generation = dialogGeneration.current,
      revision = draftRevision.current,
      owner = context,
      version = contextVersion.current,
      requestKey = saveKey;
    pendingSaves.current.set(requestKey, operation);
    renderSettlement((value) => value + 1);
    setDialogError("");
    const ownsDialog = () =>
      ownsContext(owner, version) && dialogGeneration.current === generation;
    const active = () => ownsDialog() && draftRevision.current === revision;
    try {
      if (draft.password.length < 8)
        throw Error("Password must have at least 8 characters");
      if (open === "create") {
        if (!draft.email.trim()) throw Error("Enter an email");
        await s.api.post("/api/users", {
          email: draft.email.trim(),
          name: draft.name,
          password: draft.password,
          role: draft.admin ? "admin" : "member",
        });
        // Reconcile deployment accounts even when the submitting dialog was dismissed.
        if (ownsContext(owner, version)) r.reload();
      } else
        await s.api.post(`/api/users/${enc(target.id)}/password`, {
          password: draft.password,
        });
      if (!active()) return;
      s.toast(open === "create" ? "User created" : "Password set");
      close();
    } catch (failure: any) {
      if (active()) setDialogError(failure.message || String(failure));
    } finally {
      if (pendingSaves.current.get(requestKey) === operation)
        pendingSaves.current.delete(requestKey);
      if (ownsContext(owner, version)) renderSettlement((value) => value + 1);
    }
  }
  const matching = (r.data || []).filter(
    (user) =>
      `${user.email} ${user.name || ""}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()) &&
      (roleFilter === "all" || user.role === roleFilter),
  );
  return (
    <Page
      className="users-workspace data-workspace records-page collection-page"
      title="Users"
      description="Manage deployment accounts and access."
      actions={
        admin && (
          <Button variant="primary" onClick={() => show("create")}>
            Add user
          </Button>
        )
      }
    >
      {!admin ? (
        <p role="alert">Administrator access is required to manage users.</p>
      ) : (
        <>
          <ErrorNotice error={error} />
          <CollectionTemplate
            className="data-template"
            label="User directory workspace"
          >
            <div className="records-workbench">
              <div className="records-table-host">
                {r.loading ? (
                  <p role="status">Loading users…</p>
                ) : r.error ? (
                  <div className="users-load-error">
                    <ErrorNotice error={r.error} />
                    <Button onClick={r.reload}>Retry</Button>
                  </div>
                ) : (
                  <DataTable<any>
                    label="User accounts"
                    rows={matching}
                    getRowId={(user) => user.id}
                    scopeKey={context}
                    layout="fill"
                    pagination="client"
                    toolbarContent={
                      <>
                        <CollectionToolbar
                          label="Find a user"
                          query={query}
                          onQueryChange={setQuery}
                        />
                        <Select
                          size="sm"
                          className="users-role-filter"
                          aria-label="Account role"
                          selectedKey={roleFilter}
                          onSelectionChange={(key) =>
                            setRoleFilter(String(key))
                          }
                        >
                          <SelectItem id="all">All roles</SelectItem>
                          <SelectItem id="admin">Administrators</SelectItem>
                          <SelectItem id="member">Members</SelectItem>
                        </Select>
                        <span className="users-account-count">
                          {r.data?.length ?? 0} accounts
                        </span>
                      </>
                    }
                    empty={
                      <EmptyState>
                        {r.data?.length ? (
                          <div className="records-empty-recovery">
                            <span>No users match these filters.</span>
                            <Button
                              onClick={() => {
                                setQuery("");
                                setRoleFilter("all");
                              }}
                            >
                              Clear filters
                            </Button>
                          </div>
                        ) : (
                          "No accounts to show. Add a user to grant access."
                        )}
                      </EmptyState>
                    }
                    renderRow={(user, { key, ...props }, cells) => (
                      <Evidence
                        key={key}
                        as="tr"
                        {...props}
                        label="User account"
                        data={{
                          id: user.id,
                          email: user.email,
                          name: user.name,
                          role: user.role,
                          disabled: user.disabled,
                          last_login: user.last_login,
                        }}
                      >
                        {cells}
                      </Evidence>
                    )}
                    columns={[
                      {
                        id: "email",
                        label: "Email",
                        value: (user) => user.email,
                        hideable: false,
                        render: (user) => (
                          <span className="users-identity">
                            {user.email}
                            {user.id === s.user.id && <Badge>You</Badge>}
                          </span>
                        ),
                      },
                      {
                        id: "name",
                        label: "Name",
                        value: (user) => user.name,
                        hideable: false,
                        render: (user) => <>{user.name || "—"}</>,
                      },
                      {
                        id: "role",
                        label: "Role",
                        value: (user) => user.role,
                        render: (user) => (
                          <Select
                            size="sm"
                            aria-label={`Role for ${user.email}`}
                            selectedKey={user.role}
                            isDisabled={user.id === s.user.id || busy}
                            onSelectionChange={(role) =>
                              mutate(async (active) => {
                                await s.api.patch(
                                  `/api/users/${enc(user.id)}`,
                                  { role: String(role) },
                                );
                                if (active()) {
                                  s.toast("Role updated");
                                  r.reload();
                                }
                              })
                            }
                          >
                            <SelectItem id="member">Member</SelectItem>
                            <SelectItem id="admin">Admin</SelectItem>
                          </Select>
                        ),
                      },
                      {
                        id: "login",
                        label: "Last login",
                        value: (user) => user.last_login,
                        render: (user) => <>{user.last_login || "—"}</>,
                      },
                      {
                        id: "status",
                        label: "Status",
                        value: (user) =>
                          user.disabled ? "disabled" : "active",
                        render: (user) => (
                          <Badge>{user.disabled ? "Disabled" : "Active"}</Badge>
                        ),
                      },
                      {
                        id: "actions",
                        label: "Account actions",
                        value: (user) => user.id,
                        sortable: false,
                        filterable: false,
                        hideable: false,
                        render: (user) => (
                          <div className="data-actions">
                            <Button
                              size="sm"
                              aria-label={`Set password for ${user.email}`}
                              onClick={() => show("password", user)}
                            >
                              Set password
                            </Button>
                            {user.id !== s.user.id && (
                              <>
                                <Button
                                  size="sm"
                                  disabled={busy}
                                  aria-label={`${user.disabled ? "Enable" : "Disable"} ${user.email}`}
                                  onClick={() =>
                                    mutate(async (active) => {
                                      await s.api.patch(
                                        `/api/users/${enc(user.id)}`,
                                        { disabled: !user.disabled },
                                      );
                                      if (active()) r.reload();
                                    })
                                  }
                                >
                                  {user.disabled ? "Enable" : "Disable"}
                                </Button>
                                <Button
                                  variant="danger"
                                  size="sm"
                                  disabled={busy}
                                  aria-label={`Delete ${user.email}`}
                                  onClick={() =>
                                    mutate(async (active) => {
                                      const confirmed = await s.confirm(
                                        "Delete this account and its API keys?",
                                        "Delete user",
                                      );
                                      if (!confirmed || !active()) return;
                                      await s.api.del(
                                        `/api/users/${enc(user.id)}`,
                                      );
                                      if (active()) r.reload();
                                    })
                                  }
                                >
                                  Delete
                                </Button>
                              </>
                            )}
                          </div>
                        ),
                      },
                    ]}
                  />
                )}
              </div>
            </div>
          </CollectionTemplate>
          {open && (
            <Modal
              open
              className="users-dialog"
              title={
                open === "create"
                  ? "Add user"
                  : `Set password · ${target?.email}`
              }
              onClose={close}
              footer={
                <Button variant="primary" loading={saving} onClick={save}>
                  {open === "create" ? "Create" : "Save password"}
                </Button>
              }
            >
              <div data-sensitive="true" className="users-form">
                <ErrorNotice error={dialogError} />
                {open === "create" && (
                  <div className="users-form-identity">
                    <Input
                      size="small"
                      label="Email"
                      type="email"
                      value={draft.email}
                      onChange={field("email")}
                      fieldClassName="studio-form-control"
                    />
                    <Input
                      size="small"
                      label="Name"
                      value={draft.name}
                      onChange={field("name")}
                      fieldClassName="studio-form-control"
                    />
                  </div>
                )}
                <Input
                  size="small"
                  label="Password (8+ characters)"
                  type="password"
                  autoComplete="new-password"
                  value={draft.password}
                  onChange={field("password")}
                  fieldClassName="studio-form-control"
                />
                {open === "create" && (
                  <Checkbox isSelected={draft.admin} onChange={field("admin")}>
                    Give administrator access
                  </Checkbox>
                )}
              </div>
            </Modal>
          )}
        </>
      )}
    </Page>
  );
}
