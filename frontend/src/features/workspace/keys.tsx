import { DataTable } from "../../components/studio/data-table";
import { CollectionTemplate } from "../../components/studio/templates";
import "./admin-workspace.css";
import { WorkspaceDisclosure } from "../../components/studio/workspace";
import { useEffect, useState } from "react";
import { useStudio, useResource } from "../../app/StudioContext";
import {
  Page,
  Panel,
  Button,
  Modal,
  EmptyState,
} from "../../components/studio/primitives";
import { Input, ErrorNotice, useAction, enc } from "../data/common";
export function KeysPage() {
  const s = useStudio(),
    r = useResource<any[]>("/api/keys"),
    a = useAction();
  const [open, O] = useState(false),
    [name, N] = useState(""),
    [secret, S] = useState(""),
    [query, Q] = useState("");
  useEffect(() => {
    S("");
    O(false);
    N("");
    Q("");
  }, [s.project, s.user?.id]);
  const matching = (r.data || []).filter((key) =>
    key.name.toLowerCase().includes(query.trim().toLowerCase()),
  );
  return (
    <Page
      className="admin-workspace keys-workspace"
      title="API Keys"
      description="Connect applications and MCP clients with keys belonging to your account."
      actions={
        <Button
          variant="primary"
          onClick={() => {
            a.clearError();
            O(true);
          }}
        >
          Create API key
        </Button>
      }
    >
      <CollectionTemplate
        label="API key workspace"
        toolbar={
          <div className="admin-directory-toolbar">
            <Input
              label="Find an API key"
              type="search"
              value={query}
              onChange={Q}
            />
          </div>
        }
        summary={
          <div className="admin-scope">
            <strong>{r.data?.length ?? "—"} account keys</strong>
            <span>Owned by {s.user?.email || "your account"}</span>
            <span>Access follows account permissions</span>
          </div>
        }
      >
        <ErrorNotice error={r.error || a.error} />

        {r.loading ? (
          <p role="status">Loading API keys…</p>
        ) : (
          <Panel>
            <DataTable
              label="Your API keys"
              rows={matching}
              getRowId={(k) => k.id}
              scopeKey={s.scopeKey}
              columns={[
                {
                  id: "name",
                  label: "Name",
                  value: (k) => k.name,
                  hideable: false,
                  render: (k) => <>{k.name}</>,
                },
                {
                  id: "prefix",
                  label: "Prefix",
                  value: (k) => k.prefix,
                  render: (k) => <>{k.prefix}…</>,
                },
                {
                  id: "created",
                  label: "Created",
                  value: (k) => k.created,
                  render: (k) => <>{k.created || "—"}</>,
                },
                {
                  id: "lastused",
                  label: "Last used",
                  value: (k) => k.last_used,
                  render: (k) => <>{k.last_used || "—"}</>,
                },
                {
                  id: "actions",
                  label: "Actions",
                  value: (k) => k.id,
                  sortable: false,
                  filterable: false,
                  hideable: false,
                  render: (k) => (
                    <>
                      <Button
                        variant="danger"
                        size="sm"
                        aria-label={`Revoke ${k.name}`}
                        onClick={() =>
                          a.run(async () => {
                            if (
                              await s.confirm(
                                "Revoke this key? Clients using it will get 401.",
                                "Revoke key",
                              )
                            ) {
                              await s.api.del(`/api/keys/${enc(k.id)}`);
                              r.reload();
                            }
                          })
                        }
                      >
                        Revoke
                      </Button>
                    </>
                  ),
                },
              ]}
            />
            {!r.error && !!r.data?.length && !matching.length && (
              <EmptyState>
                <div className="records-empty-recovery">
                  <span>No API keys match this search.</span>
                  <Button onClick={() => Q("")}>Clear search</Button>
                </div>
              </EmptyState>
            )}
            {!r.error && !r.data?.length && (
              <EmptyState>
                No keys yet. Create a key for /v1 or /api; revoke access at any
                time.
              </EmptyState>
            )}
          </Panel>
        )}
        <WorkspaceDisclosure
          title="Using your key"
          summary="SDK configuration and request example"
          className="admin-key-help"
        >
          <p>Send it as a Bearer token with each request.</p>
          <pre>{`curl ${window.location.origin}/v1/chat/completions \\\n  -H "Authorization: Bearer ts-..." -H "content-type: application/json" \\\n  -d '${JSON.stringify({ model: `${s.project}/<job>/<n>`, messages: [{ role: "user", content: "hello" }] })}'`}</pre>
          <p>
            OpenAI SDK base_url: <code>{window.location.origin}/v1</code>. Use
            model IDs <code>&lt;project&gt;/&lt;job&gt;/&lt;n&gt;</code>, or
            send <code>X-Project: {s.project}</code> with{" "}
            <code>&lt;job&gt;/&lt;n&gt;</code>.
          </p>
        </WorkspaceDisclosure>
        {open && (
          <Modal
            open
            title="Create API key"
            onClose={() => O(false)}
            footer={
              <Button
                variant="primary"
                loading={a.busy}
                onClick={() =>
                  a.run(async () => {
                    if (!name.trim()) throw Error("Give the key a name");
                    const result = await s.api.post<any>("/api/keys", {
                      name: name.trim(),
                    });
                    if (!result.key)
                      throw Error("The server did not return the new key");
                    O(false);
                    N("");
                    S(result.key);
                    r.reload();
                  })
                }
              >
                Create
              </Button>
            }
          >
            <Input label="Name" value={name} onChange={N} />
            <ErrorNotice error={a.error} />
          </Modal>
        )}
        {secret && (
          <Modal
            open
            title="Your new API key"
            onClose={() => S("")}
            footer={
              <Button variant="primary" onClick={() => S("")}>
                Done
              </Button>
            }
          >
            <div data-sensitive="true">
              <p>
                Copy and store this key securely now. It is shown only once and
                grants access as your account.
              </p>
              <pre style={{ overflowWrap: "anywhere" }}>{secret}</pre>
              <Button
                onClick={() =>
                  a.run(async () => {
                    await navigator.clipboard.writeText(secret);
                    s.toast("API key copied");
                  })
                }
              >
                Copy key
              </Button>
            </div>
          </Modal>
        )}
      </CollectionTemplate>
    </Page>
  );
}
