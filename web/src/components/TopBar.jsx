import { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import { fmt } from "../format.js";

function ModeToggle({ mode, onMode, running }) {
  if (!mode) return null;
  const options = [
    { id: "api", label: "API key", note: "Uses your Anthropic API credits", ok: mode.has_api_key },
    { id: "claude_code", label: "Claude Code", note: "Uses your Claude Code login, no API credits", ok: mode.claude_code_installed },
  ];
  const current = options.find((o) => o.id === mode.mode);
  return (
    <div className="mode">
      <div className="mode-toggle" role="radiogroup" aria-label="Run research with">
        {options.map((o) => (
          <button key={o.id} role="radio" aria-checked={mode.mode === o.id} disabled={running || !o.ok}
                  className={mode.mode === o.id ? "on" : ""} onClick={() => mode.mode !== o.id && onMode(o.id)}
                  title={o.ok ? o.note : o.id === "api" ? "No ANTHROPIC_API_KEY in .env" : "The claude command is not installed"}>
            {o.label}
          </button>
        ))}
      </div>
      <span className="mode-note">{running ? "Can't switch during a run" : current ? current.note : "Custom providers in config.yaml"}</span>
    </div>
  );
}

export default function TopBar({ reports, go, mode, onMode, running }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const input = useRef(null);

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); input.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const matches = q.trim()
    ? reports.filter((r) => `${r.name} ${r.domain}`.toLowerCase().includes(q.toLowerCase())).slice(0, 6)
    : [];
  const pick = (slug) => { go(`/reports/${slug}`); setQ(""); setOpen(false); input.current?.blur(); };

  return (
    <header className="topbar">
      <div className="brand">
        <span className="wordmark">FLOWCODE</span>
        <span className="tag">RESEARCH</span>
      </div>
      <div className="search">
        <label className="search-box">
          <Search size={22} />
          <input
            ref={input}
            value={q}
            placeholder="Search reports [⌘K]"
            onChange={(e) => { setQ(e.target.value); setOpen(true); }}
            onFocus={() => setOpen(true)}
            onBlur={() => setTimeout(() => setOpen(false), 150)}
            onKeyDown={(e) => { if (e.key === "Enter" && matches[0]) pick(matches[0].slug); }}
          />
        </label>
        {open && matches.length > 0 && (
          <div className="search-results">
            {matches.map((r) => (
              <button key={r.slug} onMouseDown={() => pick(r.slug)}>
                <span>{r.name} <span className="muted">· {r.domain}</span></span>
                <strong>{fmt(r.score)}</strong>
              </button>
            ))}
          </div>
        )}
      </div>
      <ModeToggle mode={mode} onMode={onMode} running={running} />
    </header>
  );
}
