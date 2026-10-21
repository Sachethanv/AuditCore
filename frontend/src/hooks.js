import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Load data, and optionally poll while `shouldPoll(data)` is true.
 * Polling never replaces data while `paused()` says someone is typing.
 */
export function useLoad(fn, deps = [], { shouldPoll, interval = 2500 } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const alive = useRef(true);
  const load = useCallback(async (quiet) => {
    if (!quiet) setLoading(true);
    try {
      const d = await fnRef.current();
      if (alive.current) { setData(d); setError(null); }
      return d;
    } catch (e) {
      if (alive.current) setError(e);
    } finally {
      if (alive.current && !quiet) setLoading(false);
    }
  }, []);
  useEffect(() => { alive.current = true; setData(null); load(); return () => { alive.current = false; }; }, deps); // eslint-disable-line
  const poll = shouldPoll ? shouldPoll(data) : false;
  useEffect(() => {
    if (!poll) return;
    const id = setInterval(() => { if (!document.hidden) load(true); }, interval);
    return () => clearInterval(id);
  }, [poll, interval, load]);
  return { data, error, loading, reload: load, setData };
}

export function useDebounced(value, ms = 200) {
  const [v, setV] = useState(value);
  useEffect(() => { const id = setTimeout(() => setV(value), ms); return () => clearTimeout(id); }, [value, ms]);
  return v;
}

export function useTheme() {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme || "");
  const toggle = () => {
    const dark = theme ? theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("ac_theme", next); } catch { /* ignore */ }
    setTheme(next);
  };
  return [theme, toggle];
}
