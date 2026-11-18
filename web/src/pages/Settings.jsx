import { ExternalLink } from "lucide-react";
import { titleCase } from "../format.js";
import { FACTOR_HELP } from "../factors.js";

const TINTS = ["var(--mauve)", "var(--periwinkle)", "var(--lilac)", "var(--tan)", "#cfe0c3", "#e7d3b5"];

function Weights({ title, weights }) {
  return (
    <div className="box">
      <h3>{title}</h3>
      {Object.entries(weights).map(([k, w]) => (
        <div className="weight-row" key={k}>
          <span className="has-tip" tabIndex={0}>
            {titleCase(k)}
            {FACTOR_HELP[k] && <span className="tip" role="tooltip">{FACTOR_HELP[k]}</span>}
          </span>
          <span className="weight-bar"><i style={{ width: `${w * 100}%` }} /></span>
          <strong style={{ textAlign: "right" }}>{Math.round(w * 100)}%</strong>
        </div>
      ))}
    </div>
  );
}

export default function Settings({ settings: s }) {
  if (!s) return <div className="page"><p className="muted">Loading…</p></div>;
  return (
    <div className="page">
      <div className="page-head"><h1>Manage</h1></div>

      <div className="grid-2" id="providers">
        <div className="box">
          <h3>Providers</h3>
          <div className="weight-row"><span>Search</span><strong>{s.search_provider}</strong></div>
          <div className="weight-row"><span>AI</span><strong>{s.llm_provider}</strong></div>
          <div className="weight-row"><span>Models</span><span>{s.main_model} / {s.cheap_model}</span></div>
          <p className="muted" style={{ fontSize: 15 }}>Change providers in config.yaml, then restart the API.</p>
        </div>
        <div className="box">
          <h3>Research rules</h3>
          <div className="weight-row"><span>Fresh window</span><strong>{s.recency_days} days</strong></div>
          <div className="weight-row"><span>Max pages</span><strong>{s.max_pages} per company</strong></div>
          <div className="weight-row"><span>Strong opportunity</span><strong>{s.strong_threshold}+</strong></div>
          <div className="weight-row"><span>Strong industries</span><span>{s.strong_industries.join(", ")}</span></div>
        </div>
      </div>

      <div className="grid-2" id="weights">
        <Weights title="Opportunity score weights" weights={s.opportunity_weights} />
        <Weights title="Company score weights" weights={s.company_weights} />
      </div>

      <h2 id="playbook" style={{ fontWeight: 500, fontSize: 30, marginTop: 48 }}>Playbook</h2>
      <div className="plays">
        {s.plays.map((p, i) => (
          <div className="play" key={p.id} style={{ background: TINTS[i % TINTS.length] }}>
            <h4>{p.name}</h4>
            <p>{p.idea}</p>
            <p style={{ fontSize: 14 }}>Signals: {p.signals.join(", ")}</p>
            <a href={p.proof.url} target="_blank" rel="noreferrer" style={{ fontWeight: 600, fontSize: 15 }}>
              {p.proof.name}: {p.proof.result} <ExternalLink size={13} />
            </a>
          </div>
        ))}
      </div>
    </div>
  );
}
