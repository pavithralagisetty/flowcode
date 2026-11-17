import { useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, PauseCircle, Search } from "lucide-react";
import { fmt } from "../format.js";

function RunPanel({ job, go }) {
  const icons = {
    running: <Loader2 size={20} className="spin" />,
    done: <CheckCircle2 size={20} color="var(--green)" />,
    paused: <PauseCircle size={20} color="#b07d24" />,
    ambiguous: <AlertTriangle size={20} color="#b07d24" />,
    failed: <AlertTriangle size={20} color="var(--red)" />,
  };
  const label = {
    running: `Researching ${job.query}…`, done: `Done: ${job.query}`, paused: "Paused: waiting for Claude Code",
    ambiguous: "Need a more specific company", failed: "Run failed",
  }[job.status];
  return (
    <div className="run-panel">
      <div className="run-panel-head">{icons[job.status]} {label}</div>
      {job.status === "running" && (
        <div className="run-step">
          <span className="run-step-label">NOW</span>
          <span>{job.step}</span>
        </div>
      )}
      {job.message && <div className="msg">{job.message}</div>}
      {job.candidates?.length > 0 && (
        <div className="msg">
          Enter the website of the one you mean:
          <ul>{job.candidates.map((c) => <li key={c.name}><strong>{c.name}</strong> — {c.domain || "no domain"} {c.note}</li>)}</ul>
        </div>
      )}
      {job.status === "done" && job.slug && (
        <div className="msg"><button className="btn btn-dark" onClick={() => go(`/reports/${job.slug}`)}>Open report</button></div>
      )}
      {job.status !== "running" && job.run_id && (
        <div className="msg"><button className="btn btn-link" style={{ padding: 0 }} onClick={() => go("/usage")}>See tokens and cost for this run →</button></div>
      )}
      {job.logs.length > 0 && (
        <details className="log-wrap">
          <summary>Full log ({job.logs.length} lines)</summary>
          <div className="log">{job.logs.slice(-200).map((l, i) => <div key={i}>{l}</div>)}</div>
        </details>
      )}
    </div>
  );
}

export default function Home({ reports, go, job, startRun }) {
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const running = job?.status === "running";
  const top = reports.slice(0, 3);
  const best = reports.find((r) => r.top);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    try { await startRun(query); } catch (err) { setError(err.message); }
  };

  return (
    <>
      <section className="hero">
        <div className="hero-left">
          <p className="eyebrow">RESEARCH</p>
          <h1>Find where Flowcode fits, with a source behind every claim</h1>
          <p className="lede">
            Enter a company or website. The agent reads its current campaigns, launches, packaging, events and
            retail news, then ranks the best places for a Flowcode, with quotes, dates and links.
          </p>
          <form className="run-form" onSubmit={submit}>
            <input id="run-input" value={query} onChange={(e) => setQuery(e.target.value)}
                   placeholder="Liquid Death  or  https://liquiddeath.com" disabled={running} />
            <button className="btn btn-amber" disabled={running || !query.trim()}>
              {running ? <Loader2 size={20} className="spin" /> : <Search size={20} />} Run research
            </button>
          </form>
          {error && <p style={{ color: "var(--red)" }}>{error}</p>}
        </div>

        <div className="hero-right">
          <div className="mock">
            <div className="mock-head">
              <strong>Top accounts</strong>
            </div>
            <table>
              <thead><tr><th>Company</th><th>Opportunities</th><th>Score</th></tr></thead>
              <tbody>
                {top.map((r) => (
                  <tr key={r.slug} style={{ cursor: "pointer" }} onClick={() => go(`/reports/${r.slug}`)}>
                    <td>{r.name}</td><td>{r.opportunities}</td><td><span className="score-cell">{fmt(r.score)}</span></td>
                  </tr>
                ))}
                {top.length === 0 && <tr><td colSpan={3} className="muted">No reports yet</td></tr>}
              </tbody>
            </table>
          </div>
          {best && (
            <div className="float-card" role="button" onClick={() => go(`/reports/${best.slug}`)}>
              <div className="label">TOP PITCH · {best.name.toUpperCase()} · {fmt(best.top_score)}</div>
              <h4>{best.top}</h4>
            </div>
          )}
        </div>
      </section>

      {job && (
        <section className="run-section">
          <RunPanel job={job} go={go} />
        </section>
      )}

      <section className="band">
        <h3>RECENT REPORTS</h3>
        <div className="cards">
          {reports.slice(0, 4).map((r, i) => (
            <button key={r.slug} className="card" onClick={() => go(`/reports/${r.slug}`)}>
              <div className={`card-art tint-${i % 4}`}>
                <div className="inner">
                  <div className="sub">{r.domain}</div>
                  <div className="big">{fmt(r.score)}</div>
                  <div className="sub">{r.opportunities} opportunities · {r.strong} strong</div>
                </div>
              </div>
              <h5>{r.name}</h5>
              <p>{r.top ? `Top: ${r.top}` : "No current opportunities found"}</p>
            </button>
          ))}
        </div>
      </section>
    </>
  );
}
