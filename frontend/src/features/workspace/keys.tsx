import { DataTable } from "../../components/studio/data-table";
import { CollectionTemplate } from "../../components/studio/templates";
import "../data/records-workspace.css";
import "../data/affect-data.css";
import "../../components/studio/form-controls.css";
import "./keys-workspace.css";
import { CollectionToolbar } from "../../components/studio/collection-toolbar";
import { Input } from "../../components/boardui/base/input/input";
import { useEffect, useRef, useState } from "react";
import { useStudio, useResource } from "../../app/StudioContext";
import {
  Page,
  Button,
  Modal,
  EmptyState,
} from "../../components/studio/primitives";
import { ErrorNotice, useAction, enc } from "../data/common";
export function KeysPage() {
  const s = useStudio();
  // Account and scope changes end this operation session, even on a later return.
  return (
    <KeysSession
      key={JSON.stringify([
        s.user?.id,
        s.user?.role,
        s.project,
        s.scopeKey,
        s.route,
      ])}
    />
  );
}
function KeysSession() {
  const s = useStudio(),
    r = useResource<any[]>("/api/keys"),
    a = useAction();
  const [open, O] = useState(false),
    [name, N] = useState(""),
    [secret, S] = useState(""),
    [query, Q] = useState("");
  const [help, H] = useState(false),
    [creating, C] = useState(false);
  const lifetime = useRef(0),
    pending = useRef(false),
    draftRevision = useRef(0);
  useEffect(() => {
    lifetime.current++;
    return () => {
      lifetime.current++;
    };
  }, []);
  const create = () => {
    if (pending.current) return;
    const submitted = name.trim();
    const owner = lifetime.current,
      revision = draftRevision.current;
    if (!submitted) {
      void a.run(async () => {
        throw Error("Give the key a name");
      });
      return;
    }
    pending.current = true;
    C(true);
    void a.run(async () => {
      try {
        const result = await s.api.post<any>("/api/keys", { name: submitted });
        if (owner !== lifetime.current) return;
        if (!result.key) throw Error("The server did not return the new key");
        O(false);
        if (revision === draftRevision.current) N("");
        S(result.key);
        r.reload();
      } finally {
        if (owner === lifetime.current) {
          pending.current = false;
          C(false);
        }
      }
    });
  };
  const matching = (r.data || []).filter((key) =>
    key.name.toLowerCase().includes(query.trim().toLowerCase()),
  );
  return (
    <Page
      className="collection-page data-workspace records-page keys-workspace"
      title="API Keys"
      description={`Keys owned by ${s.user?.email || "your account"}. Access follows your account permissions.`}
      actions={
        <>
          <Button onClick={() => H(true)}>Using your key</Button>
          <Button
            variant="primary"
            onClick={() => {
              a.clearError();
              O(true);
            }}
          >
            Create API key
          </Button>
        </>
      }
    >
      <CollectionTemplate label="API key workspace" className="data-template">
        <ErrorNotice error={r.error || (!open && a.error)} />

        <div className="records-workbench">
          <div className="records-table-host">
            <DataTable
              layout="fill"
              pagination="client"
              toolbarContent={
                <CollectionToolbar
                  label="Find an API key"
                  query={query}
                  onQueryChange={Q}
                />
              }
              empty={
                r.loading ? (
                  "Loading API keys…"
                ) : r.error ? (
                  <div className="records-empty-recovery">
                    <span>API keys could not be loaded.</span>
                    <Button onClick={r.reload}>Retry API keys</Button>
                  </div>
                ) : query ? (
                  <EmptyState>
                    <div className="records-empty-recovery">
                      <span>No API keys match this search.</span>
                      <Button onClick={() => Q("")}>Clear search</Button>
                    </div>
                  </EmptyState>
                ) : (
                  <EmptyState>
                    No keys yet. Create a key for /v1 or /api; revoke access at
                    any time.
                  </EmptyState>
                )
              }
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
                        disabled={a.busy}
                        onClick={() => {
                          const owner = lifetime.current;
                          void a.run(async () => {
                            const accepted = await s.confirm(
                              "Revoke this key? Clients using it will get 401.",
                              "Revoke key",
                            );
                            if (!accepted || owner !== lifetime.current) return;
                            await s.api.del(`/api/keys/${enc(k.id)}`);
                            if (owner === lifetime.current) r.reload();
                          });
                        }}
                      >
                        Revoke
                      </Button>
                    </>
                  ),
                },
              ]}
            />
          </div>
        </div>
        {help && (
          <Modal
            open
            title="Using your key"
            className="keys-help-dialog"
            onClose={() => H(false)}
          >
            <p>Send it as a Bearer token with each request.</p>
            <pre>{`curl ${window.location.origin}/v1/chat/completions \\\n  -H "Authorization: Bearer ts-..." -H "content-type: application/json" \\\n  -d '${JSON.stringify({ model: `${s.project}/<job>/<n>`, messages: [{ role: "user", content: "hello" }] })}'`}</pre>
            <p>
              OpenAI SDK base_url: <code>{window.location.origin}/v1</code>. Use
              model IDs <code>&lt;project&gt;/&lt;job&gt;/&lt;n&gt;</code>, or
              send <code>X-Project: {s.project}</code> with{" "}
              <code>&lt;job&gt;/&lt;n&gt;</code>.
            </p>
          </Modal>
        )}
        {open && (
          <Modal
            open
            title="Create API key"
            className="keys-create-dialog"
            onClose={() => O(false)}
            footer={
              <Button variant="primary" loading={creating} onClick={create}>
                Create
              </Button>
            }
          >
            <p>Name this key for the application or client that will use it.</p>
            <Input
              size="small"
              fieldClassName="studio-form-control"
              label="Name"
              value={name}
              onChange={(value) => {
                draftRevision.current++;
                N(value);
              }}
            />
            {creating && (
              <p role="status">
                Creating your key. You can close this dialog; the key will
                appear when ready in this session.
              </p>
            )}
            <ErrorNotice error={a.error} />
          </Modal>
        )}
        {secret && (
          <Modal
            open
            title="Your new API key"
            className="keys-secret-dialog"
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
              <pre>{secret}</pre>
              <Button
                onClick={() => {
                  const owner = lifetime.current;
                  void a.run(async () => {
                    await navigator.clipboard.writeText(secret);
                    if (owner === lifetime.current) s.toast("API key copied");
                  });
                }}
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
