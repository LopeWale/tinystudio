export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const fmtPct = (v) => (v === null || v === undefined || Number.isNaN(v)) ? "–" : `${Math.round(v * 100)}%`;
export const fmtNum = (v, d = 3) => (v === null || v === undefined || Number.isNaN(v)) ? "–" : Number(v).toFixed(d);
export const fmtDate = (iso) => iso ? new Date(iso).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "–";
export const fmtParams = (n) => !n ? "–" : n >= 1e9 ? `${(n / 1e9).toFixed(2)}B` : `${Math.round(n / 1e6)}M`;
export const pctClass = (v) => v >= 0.75 ? "hi" : v >= 0.4 ? "mid" : "lo";
export const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)"];
export const slug = (id) => id.replace("/", "~");
