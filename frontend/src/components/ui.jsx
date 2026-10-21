import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { clamp, initials, store } from "../utils.js";

/* ---------- toasts ---------- */
const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const push = useCallback((msg, err) => {
    const id = Math.random();
    setItems(a => [...a, { id, msg, err }]);
    setTimeout(() => setItems(a => a.filter(x => x.id !== id)), err ? 9000 : 3800);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map(t => <div key={t.id} className={"toast" + (t.err ? " err" : "")} role={t.err ? "alert" : "status"}>{t.msg}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}

/* ---------- small display parts ---------- */
export const Pill = ({ kind = "", children, title }) => <span className={"pill " + kind} title={title}>{children}</span>;

export function StatusPill({ e }) {
  if (!e) return <Pill>Not evaluated</Pill>;
  if (e.status === "queued") return <Pill kind="run live">Queued</Pill>;
  if (e.status === "running") return <Pill kind="run live">Analysing</Pill>;
  if (e.status === "failed") return <Pill kind="high" title={e.error}>Failed</Pill>;
  return null;
}

export const FlagPills = ({ high, medium }) =>
  high || medium ? (
    <span className="row" style={{ gap: 4 }}>
      {high ? <Pill kind="high">{high} high</Pill> : null}
      {medium ? <Pill kind="medium">{medium} medium</Pill> : null}
    </span>
  ) : <span className="muted tiny">none</span>;

export function ScoreCell({ value, warn }) {
  if (value == null) return <span className="muted">–</span>;
  const tone = value < 40 ? "bad" : value < 60 ? "warn" : "";
  return (
    <div className="scorecell">
      <span>{value}</span>
      <div className={"bar " + (warn ? "warn" : tone)}><b style={{ width: clamp(value, 0, 100) + "%" }} /></div>
    </div>
  );
}

/** Circular score: a number out of `max`, drawn as a ring. */
export function Ring({ value, max = 100, size = 120, stroke = 11, label, tone }) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r;
  const pct = value == null ? 0 : clamp(value / max, 0, 1);
  const color = tone || (pct < 0.4 ? "var(--flag)" : pct < 0.6 ? "var(--amber)" : "var(--teal)");
  return (
    <span className="ring" style={{ width: size, height: size, "--ring-font": size / 4.4 + "px" }} role="img" aria-label={`${label || "Score"} ${value ?? "not available"} out of ${max}`}>
      <svg width={size} height={size} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--sunk)" strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - pct)} style={{ transition: "stroke-dashoffset .6s ease" }} />
      </svg>
      <span className="v">{value ?? "–"}{label ? <small>{label}</small> : null}</span>
    </span>
  );
}

export const Avatar = ({ name }) => <span className="avatar" aria-hidden="true">{initials(name)}</span>;
export const Empty = ({ title, children }) => <div className="empty"><b>{title}</b>{children}</div>;
export const Skeleton = ({ h = 20, w = "100%", style }) => <div className="skeleton" style={{ height: h, width: w, ...style }} />;
export function PageSkeleton() {
  return <div aria-busy="true" aria-label="Loading"><Skeleton h={34} w="40%" /><Skeleton h={16} w="65%" style={{ marginTop: 14 }} /><Skeleton h={220} style={{ marginTop: 26 }} /></div>;
}
export const ErrorNote = ({ error }) => <div className="notice err" role="alert">{error?.message || String(error)}</div>;

export function Field({ label, help, children, small }) {
  return (
    <div className="field">
      {label ? <label>{label}{small ? <> <small>{small}</small></> : null}{children}</label> : children}
      {help ? <div className="help">{help}</div> : null}
    </div>
  );
}

export function CopyBox({ value, label }) {
  const toast = useToast();
  const ref = useRef();
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); toast("Link copied"); }
    catch { ref.current?.select(); toast("Press Ctrl+C to copy the selected link"); }
  };
  return (
    <div className="linkbox">
      <input ref={ref} readOnly value={value} aria-label={label || "Link"} onFocus={e => e.target.select()} />
      <button type="button" className="small quiet" onClick={copy}>Copy</button>
    </div>
  );
}

/* ---------- dialog ---------- */
export function Modal({ open, onClose, title, children, wide, className = "" }) {
  const ref = useRef();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className={(wide ? "wide " : "") + className} onClose={onClose} onClick={e => { if (e.target === ref.current) onClose(); }}>
      {open ? (<>{title ? <h3 style={{ fontSize: "1.25rem", marginBottom: 12 }}>{title}</h3> : null}{children}</>) : null}
    </dialog>
  );
}

/* ---------- access token (only when the server asks for one) ---------- */
export function TokenGate() {
  const [open, setOpen] = useState(false);
  const [v, setV] = useState("");
  useEffect(() => { const h = () => setOpen(true); window.addEventListener("ac-auth", h); return () => window.removeEventListener("ac-auth", h); }, []);
  return (
    <Modal open={open} onClose={() => setOpen(false)} title="This server needs an access token">
      <p className="muted">Ask whoever runs AuditCore for the token (the AUDITCORE_TOKEN setting).</p>
      <form onSubmit={e => { e.preventDefault(); store.set("ac_token", v.trim()); location.reload(); }}>
        <Field label="Access token"><input type="password" autoComplete="off" required value={v} onChange={e => setV(e.target.value)} autoFocus /></Field>
        <p><button>Continue</button></p>
      </form>
    </Modal>
  );
}
