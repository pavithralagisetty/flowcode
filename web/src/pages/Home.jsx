import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Search } from "lucide-react";
import { fmt } from "../format.js";

const TYPICAL_RUN_SECONDS = 8 * 60; // recent full runs took 7.5 to 9 minutes
// The 7 pipeline steps and the percent each starts at (matches the percents the server sends).
const STEPS = [[0, "Find the company"], [3, "Search"], [62, "Find moments"], [78, "Match to Flowcode"],
  [83, "Check the facts"], [96, "Score"], [99, "Write the report"]];

/** Progress bar with the current step and a rough time left (from typical run length, then from the pace so far). */
function RunProgress({ job }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const elapsed = Math.max(0, (now - new Date(job.started_at).getTime()) / 1000);
  const percent = Math.min(99, job.percent || 0);
  const left = percent >= 15 ? (elapsed * (100 - percent)) / percent : TYPICAL_RUN_SECONDS - elapsed;
  const leftText = left < 60 ? "Almost done" : `About ${Math.round(left / 60)} min left`;
  const stepIndex = STEPS.findLastIndex(([start]) => percent >= start);
  return (
    <div className="run-progress">
      <div className="run-progress-step">Step {stepIndex + 1} of {STEPS.length}: {STEPS[stepIndex][1]}</div>
      <div className="bar"><div style={{ width: `${Math.max(percent, 2)}%` }} /></div>
      <div className="run-progress-text">
        <span>{leftText}</span>
      </div>
    </div>
  );
}

function RunPanel({ job, go }) {
  const icons = {
    running: <Loader2 size={20} className="spin" />,
    done: <CheckCircle2 size={20} color="var(--green)" />,
    ambiguous: <AlertTriangle size={20} color="#b07d24" />,
    not_found: <AlertTriangle size={20} color="#b07d24" />,
    failed: <AlertTriangle size={20} color="var(--red)" />,
    interrupted: <AlertTriangle size={20} color="#b07d24" />,
  };
  const label = {
    running: `Researching ${job.query}…`, done: `Done: ${job.query}`,
    ambiguous: "Need a more specific company", not_found: "No company found", failed: "Run failed", interrupted: "Run interrupted",
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
      {job.status === "running" && <RunProgress job={job} />}
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
              <strong>Top prospects</strong>
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
              <div className="label">TOP PITCH</div>
              <div className="company" title={best.name}>{best.name}</div>
              <h4 title={best.top}>{best.top}</h4>
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
