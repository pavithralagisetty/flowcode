import { Fragment, useEffect, useState } from "react";
import { ChevronDown, ChevronUp, RefreshCw } from "lucide-react";
import { api } from "../api.js";
import { titleCase } from "../format.js";

const num = (n) => (n || 0).toLocaleString();
const money = (c) => (c == null ? "set prices" : `$${c.toFixed(4)}`);
function Cost({ cost }) {
  return <span>{money(cost)}</span>;
}
const STEP_LABEL = {
  resolve: "Identify company", moments: "Read pages", merge: "Merge moments", match: "Match playbook",
  verify: "Verify claims", score: "Score", search: "Web search", other: "Other",
};

function Detail({ row }) {
  if (row.kind !== "llm") {
    return (
      <>
        <strong>“{row.query}”</strong>
        <span className="muted"> · {row.results} links{row.kind === "search_cached" ? " (from today's cache, free)" : ""}</span>
      </>
    );
  }
  const pages = row.pages || [];
  return (
    <>
      {row.opportunity && <div><strong>{row.opportunity}</strong></div>}
      {row.batch && <div className="muted">Batch {row.batch}</div>}
      {row.moments != null && <div className="muted">{row.moments} moments</div>}
      {row.opportunities != null && <div className="muted">{row.opportunities} opportunities</div>}
      {pages.length > 0 && (
        <ul className="page-list">
          {pages.map((p, i) => {
            const url = typeof p === "string" ? p : p.url;
            return (
              <li key={i}>
                <a href={url} target="_blank" rel="noreferrer">{url.replace(/^https?:\/\/(www\.)?/, "").slice(0, 70)}</a>
                {p.chars_sent != null && <span className="muted"> · {num(p.chars_sent)} chars</span>}
              </li>
            );
          })}
        </ul>
      )}
      {row.attempt > 1 && <div className="chip amber">retry after invalid JSON</div>}
    </>
  );
}

function Calls({ session }) {
  const [rows, setRows] = useState(null);
  useEffect(() => { api.calls(session).then(setRows).catch(() => setRows([])); }, [session]);
  if (!rows) return <p className="muted" style={{ padding: 16 }}>Loading calls…</p>;
  return (
    <table className="table calls">
      <thead>
        <tr><th>Time</th><th>Step</th><th>What was sent</th><th>Model</th><th>In tokens</th><th>Out tokens</th>
          <th>Web searches</th><th>Cost</th></tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className={r.kind === "search_cached" ? "dim" : ""}>
            <td className="muted">{r.time.slice(11)}</td>
            <td><span className="chip">{STEP_LABEL[r.step || (r.kind.startsWith("search") ? "search" : "other")]}</span></td>
            <td style={{ maxWidth: 520 }}><Detail row={r} /></td>
            <td className="muted">{r.model || r.provider}</td>
            <td className="num">{num(r.input_tokens)}</td>
            <td className="num">{num(r.output_tokens)}</td>
            <td className="num">{num(r.web_searches)}</td>
            <td className="num">{r.kind === "search_cached" ? "–" : <Cost cost={r.cost} />}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StepBars({ byStep }) {
  const entries = Object.entries(byStep).sort((a, b) => b[1].input_tokens - a[1].input_tokens);
  const max = Math.max(1, ...entries.map(([, s]) => s.input_tokens + s.output_tokens));
  return (
    <div className="step-bars">
      {entries.map(([step, s]) => (
        <div className="weight-row" key={step}>
          <span>{STEP_LABEL[step] || titleCase(step)} <span className="muted">· {s.calls} calls</span></span>
          <span className="weight-bar"><i style={{ width: `${((s.input_tokens + s.output_tokens) / max) * 100}%` }} /></span>
          <strong style={{ textAlign: "right", whiteSpace: "nowrap" }}>{num(s.input_tokens + s.output_tokens)}</strong>
        </div>
      ))}
    </div>
  );
}

export default function Usage() {
  const [runs, setRuns] = useState(null);
  const [open, setOpen] = useState(null);
  const load = () => api.usage().then(setRuns).catch(() => setRuns([]));
  useEffect(() => { load(); }, []);

  const total = (runs || []).reduce((t, r) => ({
    input: t.input + r.input_tokens, output: t.output + r.output_tokens, web: t.web + r.web_searches,
    cost: t.cost + (r.cost || 0),
    unpriced: t.unpriced || r.cost == null,
  }), { input: 0, output: 0, web: 0, cost: 0, unpriced: false });

  return (
    <div className="page">
      <div className="page-head">
        <h1>Usage</h1>
        <button className="btn btn-link" onClick={load}><RefreshCw size={18} /> Refresh</button>
      </div>
      <p className="muted" style={{ fontSize: 18, marginTop: 10 }}>
        Every AI call and web search, with the pages sent and the tokens used.
        {" "}Costs are estimates at Anthropic list prices.
        {total.unpriced && " Some calls use a model with no price set."}
      </p>

      {runs && runs.length > 0 && (
        <div className="usage-totals">
          <div><span className="lbl">Input tokens</span><span className="val">{num(total.input)}</span></div>
          <div><span className="lbl">Output tokens</span><span className="val">{num(total.output)}</span></div>
          <div><span className="lbl">Web searches</span><span className="val">{num(total.web)}</span></div>
          <div>
            <span className="lbl">Estimated cost</span>
            <span className="val">{money(total.cost)}</span>
          </div>
        </div>
      )}

      {runs && runs.length === 0 && (
        <div className="empty"><h2>No calls logged yet</h2><p>Run research on a company and its calls will show up here.</p></div>
      )}

      {runs && runs.length > 0 && (
        <table className="table">
          <thead>
            <tr><th>Started</th><th>Company</th><th>AI calls</th><th>Searches</th><th>Web searches</th>
              <th>In tokens</th><th>Out tokens</th><th>Cost</th><th /></tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <Fragment key={r.session}>
                <tr className="clickable" onClick={() => setOpen(open === r.session ? null : r.session)}>
                  <td className="muted">{r.started.replace("T", " ")}</td>
                  <td><strong style={{ fontWeight: 600 }}>{r.company}</strong>{r.mode === "refresh" && <span className="chip" style={{ marginLeft: 8 }}>refresh</span>}</td>
                  <td className="num">{r.ai_calls}</td>
                  <td className="num">{r.searches}{r.cached_searches > 0 && <span className="muted"> (+{r.cached_searches} cached)</span>}</td>
                  <td className="num">{num(r.web_searches)}</td>
                  <td className="num">{num(r.input_tokens)}</td>
                  <td className="num">{num(r.output_tokens)}</td>
                  <td className="num"><Cost cost={r.cost} /></td>
                  <td>{open === r.session ? <ChevronUp /> : <ChevronDown />}</td>
                </tr>
                {open === r.session && (
                  <tr className="expanded">
                    <td colSpan={9}>
                      <h3 className="sub-h">Tokens by step</h3>
                      <StepBars byStep={r.by_step} />
                      <h3 className="sub-h">Every call</h3>
                      <Calls session={r.session} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
