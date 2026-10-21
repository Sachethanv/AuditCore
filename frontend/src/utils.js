export const fmtDate = s => s ? new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
export const fmtDay = s => s ? new Date(s).toLocaleDateString(undefined, { dateStyle: "medium" }) : "";
export const ago = s => {
  if (!s) return "";
  const m = Math.round((Date.now() - new Date(s)) / 60000);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : fmtDate(s);
};
export const repoName = u => (u || "").replace("https://github.com/", "");
export const fileB64 = f => new Promise((res, rej) => {
  const r = new FileReader();
  r.onload = () => res(String(r.result).split(",")[1]);
  r.onerror = () => rej(new Error("Could not read the file"));
  r.readAsDataURL(f);
});
export const toLocalInput = s => {
  if (!s) return "";
  const d = new Date(s), p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};
export const fromLocalInput = v => (v ? new Date(v).toISOString() : null);
export const parseMembers = str => (str || "").split(";").map(s => s.trim()).filter(Boolean).map(s => {
  const m = s.match(/^(.*?)\s*\(\s*@?([\w-]+)\s*\)$/);
  return m ? { name: m[1], github: m[2] } : { name: s };
});
export const membersStr = ms => (ms || []).map(m => m.name + (m.github ? ` (@${m.github})` : "")).join("; ");
export const initials = name => (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join("") || "?";
export const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
export const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;

/** Where an event is in its life, from its start and end times. */
export function phaseOf(ev, now = Date.now()) {
  const s = ev.hackathon_start ? new Date(ev.hackathon_start).getTime() : null;
  const e = ev.hackathon_end ? new Date(ev.hackathon_end).getTime() : null;
  if (s == null && e == null) return { key: "unset", label: "Dates not set", step: -1 };
  if (s != null && now < s) return { key: "upcoming", label: "Upcoming", step: 0 };
  if (e != null && now > e) return { key: "closed", label: "Submissions closed", step: 2 };
  return { key: "live", label: "Live", step: 1 };
}
export function download(text, name, type = "text/csv") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
export const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage may be blocked */ } },
};
