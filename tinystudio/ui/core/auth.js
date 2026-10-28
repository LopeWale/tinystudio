import { $ } from "../ui/dom.js?v=c1757fa5eddd";
import { esc } from "../ui/format.js?v=c1757fa5eddd";

export function createAuthViews({ http }) {
  function authPage(title, body) {
    $("#app").innerHTML = `<div class="auth"><div class="auth-card"><div class="brand"><div class="logo" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 32 32"><path d="M8 22 L13 12 L18 18 L24 9" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg></div><div>tinystudio<small>Your model workspace</small></div></div><h1>${esc(title)}</h1>${body}</div></div>`;
    $("#app").setAttribute?.("aria-busy", "false");
    document.title = `${title} · tinystudio`;
    $("#tasks").hidden = true;
    return $("#app .auth-card");
  }
  function showLogin() {
    if ($("#loginForm")) return;
    const card = authPage("Sign in", `<p class="page-description">Continue to your projects and saved conversations.</p><form id="loginForm"><div class="field"><label for="lEmail">Email</label><input id="lEmail" type="email" autocomplete="username" required></div><div class="field"><label for="lPass">Password</label><input id="lPass" type="password" autocomplete="current-password" required></div><button class="btn primary auth-submit" type="submit">Sign in</button><div class="error" id="lErr" role="alert"></div></form>`);
    $("#loginForm", card).onsubmit = async (e) => {
      e.preventDefault();
      try { await http.post("/api/auth/login", { email: $("#lEmail", card).value, password: $("#lPass", card).value }); location.reload(); }
      catch (err) { $("#lErr", card).textContent = err.message; }
    };
  }
  function showSetup() {
    const card = authPage("First start: create the admin account", `<form id="setupForm"><div class="field"><label for="sName">Name</label><input id="sName" autocomplete="name"></div><div class="field"><label for="sEmail">Email</label><input id="sEmail" type="email" autocomplete="username" required></div><div class="field"><label for="sPass">Password (8+ characters)</label><input id="sPass" type="password" autocomplete="new-password" minlength="8" required></div><button class="btn primary auth-submit" type="submit">Create admin and continue</button><div class="error" id="sErr" role="alert"></div></form>`);
    $("#setupForm", card).onsubmit = async (e) => {
      e.preventDefault();
      try { await http.post("/api/auth/setup", { name: $("#sName", card).value, email: $("#sEmail", card).value, password: $("#sPass", card).value }); location.reload(); }
      catch (err) { $("#sErr", card).textContent = err.message; }
    };
  }
  function showUnavailable() {
    const card = authPage("Connection unavailable", `<p class="page-description">We couldn’t open your workspace. Check your connection and try again.</p><button class="btn primary auth-submit" id="reloadApp" type="button">Try again</button>`);
    $("#reloadApp", card).onclick = () => location.reload();
  }
  return { authPage, showLogin, showSetup, showUnavailable };
}
