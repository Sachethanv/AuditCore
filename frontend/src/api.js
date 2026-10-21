import { store } from "./utils.js";

export const getToken = () => store.get("ac_token") || "";

async function request(base, path, { method = "GET", body, auth } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && getToken()) headers.Authorization = "Bearer " + getToken();
  const r = await fetch(base + path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  if (r.status === 401 && auth) {
    window.dispatchEvent(new CustomEvent("ac-auth"));
    throw new Error("This server needs an access token.");
  }
  if (r.status === 204) return null;
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json().catch(() => ({})) : await r.text();
  if (!r.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map(x => x.msg).join("; ") : "Something went wrong. Try again.");
  }
  return data;
}

/** Organizer API (needs the access token when the server has one). */
export const api = (path, opts) => request("/api", path, { ...opts, auth: true });
/** Private-link APIs for teams, judges and the public results page. */
export const pub = (path, opts) => request("/public", path, opts);

export async function downloadBlob(path, filename) {
  const r = await fetch("/api" + path, { headers: getToken() ? { Authorization: "Bearer " + getToken() } : {} });
  if (!r.ok) throw new Error("Could not download the file");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await r.blob());
  a.download = filename;
  a.click();
}
