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
  EmptyState,
  Badge,
} from "../../components/studio/primitives";
import {
  Input,
  Check,
  Select,
  ErrorNotice,
  useAction,
  enc,
} from "../data/common";
export function UsersPage() {
  const s = useStudio(),
    admin = s.user?.role === "admin",
    r = useResource<any[]>(admin ? "/api/users" : null),
    a = useAction();
  const [open, O] = useState(""),
    [target, T] = useState<any>(null),
    [query, Q] = useState(""),
    [roleFilter, F] = useState(""),
    [v, V] = useState({ email: "", name: "", password: "", admin: false });
  const field = (k: string) => (value: any) => V((p) => ({ ...p, [k]: value }));
  function close() {
    O("");
    T(null);
    V({ email: "", name: "", password: "", admin: false });
  }
  useEffect(() => {
    close();
    Q("");
    F("");
  }, [s.user?.id, admin]);
  const matching = (r.data || []).filter(
    (user) =>
      `${user.email} ${user.name || ""}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()) &&
      (!roleFilter || user.role === roleFilter),
  );
  return (
    <Page
      className="users-workspace admin-workspace"
      title="Users"
      description={`Manage deployment access. ${r.data?.length ?? "…"} accounts can sign in.`}
      actions={
        admin && (
          <Button
            variant="primary"
            onClick={() => {
              a.clearError();
              close();
              O("create");
            }}
          >
            Add user
          </Button>
        )
      }
    >
      {!admin ? (
        <p role="alert">Administrator access is required to manage users.</p>
      ) : (
        <>
          <ErrorNotice error={r.error || a.error} />
          <CollectionTemplate
            label="User directory workspace"
            toolbar={
              <div className="admin-directory-toolbar">
                <Input
                  label="Find a user"
                  type="search"
                  value={query}
                  onChange={Q}
                />
                <Select
                  label="Account role"
                  value={roleFilter}
                  onChange={F}
                  options={[
                    { value: "", label: "All roles" },
                    "admin",
                    "member",
                  ]}
                />
              </div>
            }
            summary={
              <div className="admin-scope">
                <strong>{r.data?.length ?? "—"} deployment accounts</strong>
                <span>
                  {(r.data || []).filter((u) => u.role === "admin").length}{" "}
                  administrators
                </span>
                <span>
                  {(r.data || []).filter((u) => u.disabled).length} disabled
                </span>
              </div>
            }
          >
            {r.loading ? (
              <p role="status">Loading users…</p>
            ) : (
              <Panel>
                <DataTable
                  label="User accounts"
                  rows={matching}
                  getRowId={(u) => u.id}
                  scopeKey={s.scopeKey}
                  renderRow={(u, { key, ...props }, cells) => (
                    <Evidence
                      key={key}
                      as="tr"
                      {...props}
                      label="User account"
                      data={{
                        id: u.id,
                        email: u.email,
                        name: u.name,
                        role: u.role,
                        disabled: u.disabled,
                        last_login: u.last_login,
                      }}
                    >
                      {cells}
                    </Evidence>
                  )}
                  columns={[
                    {
                      id: "email",
                      label: "Email",
                      value: (u) => u.email,
                      hideable: false,
                      render: (u) => (
                        <>
                          {u.email}
                          {u.id === s.user.id && <Badge>You</Badge>}
                        </>
                      ),
                    },
                    {
                      id: "name",
                      label: "Name",
                      value: (u) => u.name,
                      hideable: false,
                      render: (u) => <>{u.name}</>,
                    },
                    {
                      id: "role",
                      label: "Role",
                      value: (u) => u.role,
                      render: (u) => (
                        <>
                          <Select
                            label={`Role for ${u.email}`}
                            value={u.role}
                            disabled={u.id === s.user.id || a.busy}
                            options={["member", "admin"]}
                            onChange={(role: string) =>
                              a.run(async () => {
                                await s.api.patch(`/api/users/${enc(u.id)}`, {
                                  role,
                                });
                                s.toast("Role updated");
                                r.reload();
                              })
                            }
                          />
                        </>
                      ),
                    },
                    {
                      id: "login",
                      label: "Last login",
                      value: (u) => u.last_login,
                      render: (u) => <>{u.last_login || "—"}</>,
                    },
                    {
                      id: "status",
                      label: "Status",
                      value: (u) => (u.disabled ? "disabled" : "active"),
                      render: (u) => <>{u.disabled ? "disabled" : "active"}</>,
                    },
                    {
                      id: "actions",
                      label: "Account actions",
                      value: (u) => u.id,
                      sortable: false,
                      filterable: false,
                      hideable: false,
                      render: (u) => (
                        <>
                          <div className="data-actions">
                            <Button
                              size="sm"
                              aria-label={`Set password for ${u.email}`}
                              onClick={() => {
                                a.clearError();
                                T(u);
                                O("password");
                              }}
                            >
                              Set password
                            </Button>
                            {u.id !== s.user.id && (
                              <>
                                <Button
                                  size="sm"
                                  aria-label={`${u.disabled ? "Enable" : "Disable"} ${u.email}`}
                                  onClick={() =>
                                    a.run(async () => {
                                      await s.api.patch(
                                        `/api/users/${enc(u.id)}`,
                                        {
                                          disabled: !u.disabled,
                                        },
                                      );
                                      r.reload();
                                    })
                                  }
                                >
                                  {u.disabled ? "Enable" : "Disable"}
                                </Button>
                                <Button
                                  variant="danger"
                                  size="sm"
                                  aria-label={`Delete ${u.email}`}
                                  onClick={() =>
                                    a.run(async () => {
                                      if (
                                        await s.confirm(
                                          "Delete this account and its API keys?",
                                          "Delete user",
                                        )
                                      ) {
                                        await s.api.del(
                                          `/api/users/${enc(u.id)}`,
                                        );
                                        r.reload();
                                      }
                                    })
                                  }
                                >
                                  Delete
                                </Button>
                              </>
                            )}
                          </div>
                        </>
                      ),
                    },
                  ]}
                />
                {!r.error && !!r.data?.length && !matching.length && (
                  <EmptyState>
                    <div className="records-empty-recovery">
                      <span>No users match these filters.</span>
                      <Button
                        onClick={() => {
                          Q("");
                          F("");
                        }}
                      >
                        Clear filters
                      </Button>
                    </div>
                  </EmptyState>
                )}
                {!r.error && !r.data?.length && (
                  <EmptyState>
                    No accounts to show. Add a user to grant access.
                  </EmptyState>
                )}
              </Panel>
            )}
          </CollectionTemplate>
          {open && (
            <Modal
              open
              title={
                open === "create"
                  ? "Add user"
                  : `Set password · ${target?.email}`
              }
              onClose={close}
              footer={
                <Button
                  variant="primary"
                  loading={a.busy}
                  onClick={() =>
                    a.run(async () => {
                      if (v.password.length < 8)
                        throw Error("Password must have at least 8 characters");
                      if (open === "create") {
                        if (!v.email.trim()) throw Error("Enter an email");
                        await s.api.post("/api/users", {
                          email: v.email.trim(),
                          name: v.name,
                          password: v.password,
                          role: v.admin ? "admin" : "member",
                        });
                        r.reload();
                      } else
                        await s.api.post(
                          `/api/users/${enc(target.id)}/password`,
                          { password: v.password },
                        );
                      close();
                      s.toast(
                        open === "create" ? "User created" : "Password set",
                      );
                    })
                  }
                >
                  {open === "create" ? "Create" : "Save password"}
                </Button>
              }
            >
              <div data-sensitive="true">
                <ErrorNotice error={a.error} />
                {open === "create" && (
                  <>
                    <Input
                      label="Email"
                      type="email"
                      value={v.email}
                      onChange={field("email")}
                    />
                    <Input
                      label="Name"
                      value={v.name}
                      onChange={field("name")}
                    />
                  </>
                )}
                <Input
                  label="Password (8+ characters)"
                  type="password"
                  autoComplete="new-password"
                  value={v.password}
                  onChange={field("password")}
                />
                {open === "create" && (
                  <Check
                    label="Give administrator access"
                    checked={v.admin}
                    onChange={field("admin")}
                  />
                )}
              </div>
            </Modal>
          )}
        </>
      )}
    </Page>
  );
}
