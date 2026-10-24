import { $, $$ } from "../ui/dom.js?v=82e74d4b5ecb";
import { esc, fmtDate } from "../ui/format.js?v=82e74d4b5ecb";

// Owns this feature's page rendering and dialogs; application services stay at explicit boundaries.
export function createUsersPages({ state, http, feedback, dialogs, shell, context, ensureOverview }) {
  const api = (...args) => http.api(...args);
  const post = (...args) => http.post(...args);
  const patch = (...args) => http.patch(...args);
  const del = (...args) => http.del(...args);
  const toast = (...args) => feedback.toast(...args);
  const guard = (...args) => feedback.guard(...args);
  const modal = (...args) => dialogs.modal(...args);
  const confirmModal = (...args) => dialogs.confirmModal(...args);
  const layout = (...args) => shell.layout(...args);
  const dataContext = (...args) => context.dataContext(...args);
  const assertRender = (...args) => context.assertRender(...args);

  async function pageUsers() {
    const view = state.pageContext;
    await ensureOverview(true); assertRender(view);
    const users = await api("/api/users");
    assertRender(view);
    const content = layout("users", `<div class="page-head"><h1>Users</h1><span class="meta">${users.length} account${users.length === 1 ? "" : "s"}</span><div class="actions"><button class="btn primary" id="newUser">+ Add user</button></div></div>
      <div class="panel"><table><thead><tr><th>Email</th><th>Name</th><th>Role</th><th>Last login</th><th>Status</th><th></th></tr></thead><tbody>${users.map((u) => `<tr ${dataContext("User account", u)}><td><b>${esc(u.email)}</b></td><td>${esc(u.name)}</td><td><select data-role="${u.id}" ${u.id === state.user.id ? "disabled" : ""}><option ${u.role === "member" ? "selected" : ""}>member</option><option ${u.role === "admin" ? "selected" : ""}>admin</option></select></td><td>${fmtDate(u.last_login)}</td><td><span class="pill ${u.disabled ? "bad" : "good"}">${u.disabled ? "disabled" : "active"}</span></td><td style="text-align:right;white-space:nowrap"><button class="btn sm" data-pw="${u.id}">Set password</button> ${u.id !== state.user.id ? `<button class="btn sm" data-toggle-user="${u.id}" data-disabled="${u.disabled}">${u.disabled ? "Enable" : "Disable"}</button> <button class="btn sm danger" data-del-user="${u.id}">Delete</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`, [{ label: "Users" }]);
    $("#newUser", content).onclick = () => {
      const m = modal({ title: "Add user", body: `<div class="field"><label>Email</label><input id="uEmail" type="email"></div><div class="field"><label>Name</label><input id="uName"></div><div class="field"><label>Password (8+ characters)</label><input id="uPass" type="password" autocomplete="new-password"></div><label class="chk"><input type="checkbox" id="uAdmin"> admin</label>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Create</button>` });
      $("[data-cancel]", m.el).onclick = m.close;
      $("[data-ok]", m.el).onclick = guard(async () => { await post("/api/users", { email: $("#uEmail", m.el).value, name: $("#uName", m.el).value, password: $("#uPass", m.el).value, role: $("#uAdmin", m.el).checked ? "admin" : "member" }); m.close(); pageUsers(); });
    };
    $$("[data-role]", content).forEach((s) => s.onchange = guard(async () => { await patch(`/api/users/${s.dataset.role}`, { role: s.value }); toast("role updated"); }));
    $$("[data-toggle-user]", content).forEach((b) => b.onclick = guard(async () => { await patch(`/api/users/${b.dataset.toggleUser}`, { disabled: b.dataset.disabled !== "true" }); pageUsers(); }));
    $$("[data-del-user]", content).forEach((b) => b.onclick = () => confirmModal("Delete user", "Delete this account and its API keys?", async () => { await del(`/api/users/${b.dataset.delUser}`); pageUsers(); }));
    $$("[data-pw]", content).forEach((b) => b.onclick = () => {
      const m = modal({ title: "Set password", body: `<div class="field"><label>New password</label><input id="pw" type="password" autocomplete="new-password"></div>`, footer: `<button class="btn" data-cancel>Cancel</button><button class="btn primary" data-ok>Save</button>` });
      $("[data-cancel]", m.el).onclick = m.close;
      $("[data-ok]", m.el).onclick = guard(async () => { await post(`/api/users/${b.dataset.pw}/password`, { password: $("#pw", m.el).value }); m.close(); toast("password set"); });
    });
  }

  return { pageUsers };
}
