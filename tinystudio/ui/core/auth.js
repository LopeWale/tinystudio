import { $ } from "../ui/dom.js?v=c96cb305d835";
import { esc } from "../ui/format.js?v=c96cb305d835";

export function createAuthViews({ http }) {
  function authPage(title, body) {
    $("#app").innerHTML = `<div class="auth"><div class="auth-card"><div class="brand" style="padding:0 0 10px"><div class="logo"><svg width="16" height="16" viewBox="0 0 32 32"><path d="M8 22 L13 12 L18 18 L24 9" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg></div><div>tinystudio<small>${esc(title)}</small></div></div>${body}</div></div>`;
    $("#tasks").hidden = true;
    return $("#app .auth-card");
  }
  function showLogin() {
    if ($("#loginForm")) return;
    const card = authPage("Sign in", `<form id="loginForm"><div class="field"><label>Email</label><input id="lEmail" type="email" autocomplete="username" required></div><div class="field"><label>Password</label><input id="lPass" type="password" autocomplete="current-password" required></div><button class="btn primary" style="width:100%;justify-content:center" type="submit">Sign in</button><div class="muted" id="lErr" style="margin-top:8px"></div></form>`);
    $("#loginForm", card).onsubmit = async (e) => {
      e.preventDefault();
      try { await http.post("/api/auth/login", { email: $("#lEmail", card).value, password: $("#lPass", card).value }); location.reload(); }
      catch (err) { $("#lErr", card).textContent = err.message; }
    };
  }
  function showSetup() {
    const card = authPage("First start: create the admin account", `<form id="setupForm"><div class="field"><label>Name</label><input id="sName"></div><div class="field"><label>Email</label><input id="sEmail" type="email" autocomplete="username" required></div><div class="field"><label>Password (8+ characters)</label><input id="sPass" type="password" autocomplete="new-password" required></div><button class="btn primary" style="width:100%;justify-content:center" type="submit">Create admin and continue</button><div class="muted" id="sErr" style="margin-top:8px"></div></form>`);
    $("#setupForm", card).onsubmit = async (e) => {
      e.preventDefault();
      try { await http.post("/api/auth/setup", { name: $("#sName", card).value, email: $("#sEmail", card).value, password: $("#sPass", card).value }); location.reload(); }
      catch (err) { $("#sErr", card).textContent = err.message; }
    };
  }
  return { authPage, showLogin, showSetup };
}
