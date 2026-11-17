import { useEffect, useState } from "react";
import { marked } from "marked";
import { ArrowLeft, BookOpen, ChevronDown, ChevronUp, ExternalLink, MapPin } from "lucide-react";
import { api } from "../api.js";
import { fmt, scoreTone, titleCase } from "../format.js";

function Dots({ value }) {
  return <span className="dots">{[1, 2, 3, 4, 5].map((n) => <i key={n} className={n <= value ? "on" : ""} />)}</span>;
}

function FactorList({ factors, mini }) {
  return (
    <div className={mini ? "mini-factors factors" : "factors"} style={mini ? { padding: 0 } : undefined}>
      {Object.entries(factors).map(([k, v]) => (
        <div className="factor" key={k}>
          <span className="name">{titleCase(k)}</span>
          <span style={{ display: "flex", alignItems: "center", gap: 10 }}><Dots value={v.rating} /> {v.rating}</span>
          <span className="reason">{v.reason}</span>
        </div>
      ))}
    </div>
  );
}

function Opportunity({ o, rank, open, onToggle, isNew }) {
  const m = o.moment;
  const statusTone = m.timing.status === "upcoming" ? "green" : m.timing.status === "ongoing" ? "amber" : "";
  return (
    <article className="opp">
      <button className="opp-head" onClick={onToggle}>
        <span className="rank">{rank}</span>
        <div>
          <p className="opp-title">{m.title}</p>
          <div className="opp-tags">
            <span className="chip">{titleCase(m.type)}</span>
            <span className={`chip ${statusTone}`}>{m.timing.date_text || "Date not stated"} · {m.timing.status}</span>
            {o.play_id && <span className="chip">Play: {titleCase(o.play_id)}</span>}
            {isNew && <span className="chip green">New</span>}
          </div>
        </div>
        <span className="score-pill">
          <span className={`chip ${scoreTone(o.score)}`} style={{ fontSize: 22, padding: "6px 14px" }}>{fmt(o.score)}</span>
          {open ? <ChevronUp /> : <ChevronDown />}
        </span>
      </button>
      {open && (
        <div className="opp-body">
          <section>
            <div className="kv"><h6>What's happening</h6><p>{m.summary}</p></div>
            {m.physical_touchpoints.length > 0 && (
              <div className="kv">
                <h6>Where a code goes</h6>
                <div className="opp-tags">{m.physical_touchpoints.map((t) => <span className="chip" key={t}><MapPin size={13} />{t}</span>)}</div>
              </div>
            )}
            <div className="kv"><h6>Why Flowcode fits</h6><p>{o.why_flowcode_fits}</p></div>
            <div className="kv"><h6>Suggested first pitch</h6></div>
            <p className="pitch">“{o.suggested_first_pitch}”</p>
            {o.similar_story && (
              <a className="story" href={o.similar_story.url} target="_blank" rel="noreferrer">
                <BookOpen size={22} />
                <div><strong>Similar story: {o.similar_story.name}</strong><span>{o.similar_story.result}</span></div>
                <ExternalLink size={16} style={{ marginLeft: "auto" }} />
              </a>
            )}
            {o.flowcode_products?.length > 0 && (
              <div className="kv"><h6>Products</h6><div className="opp-tags">{o.flowcode_products.map((p) => <span className="chip" key={p}>{p}</span>)}</div></div>
            )}
          </section>
          <section>
            <div className="kv"><h6>Score breakdown</h6></div>
            <FactorList factors={o.factor_ratings} mini />
            <div className="kv" style={{ marginTop: 24 }}>
              <h6>Sources · {o.verification.quotes_passed}/{o.verification.quotes_checked} quotes verified on page</h6>
            </div>
            <ul className="sources">
              {o.sources.map((s, i) => (
                <li key={i}>
                  <div className="src-meta"><strong>{s.publisher}</strong> · {s.date || "date unknown"}</div>
                  “{s.quote}” <a href={s.url} target="_blank" rel="noreferrer">link <ExternalLink size={12} /></a>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </article>
  );
}

export default function Report({ slug, go }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("opps");
  const [open, setOpen] = useState({ 0: true });

  useEffect(() => {
    if (!slug) return;
    setData(null); setError(""); setOpen({ 0: true });
    api.report(slug).then(setData).catch((e) => setError(e.message));
  }, [slug]);

  if (!slug) {
    return (
      <div className="page empty">
        <h2>No reports yet</h2>
        <p>Research a company to see where Flowcode could help.</p>
        <button className="btn btn-dark" onClick={() => go("/home")}>Start research</button>
      </div>
    );
  }
  if (error) return <div className="page"><p>{error}</p></div>;
  if (!data) return <div className="page"><p className="muted">Loading…</p></div>;

  const r = data.report;
  const cs = r.company_score;
  const opps = r.opportunities;

  return (
    <div className="page">
      <button className="back" onClick={() => go("/reports")}><ArrowLeft size={18} /> All reports</button>
      <div className="page-head"><h1>{r.company.name}: where Flowcode could help</h1></div>
      <div className="report-meta">
        <span className="chip">{r.company.domain}</span>
        <span className="chip">{r.company.industry}</span>
        <span className="chip">Run {r.run_date}</span>
        <span className={`chip ${r.company.customer_tier ? "amber" : ""}`}>
          Existing customer: {r.company.customer_tier || "no"}
        </span>
        {r.channels_covered.map((c) => <span className="chip green" key={c}>{c}</span>)}
      </div>

      <div className="scoreboard">
        <div className="score-big">
          <div className="num">{fmt(cs.score)}</div>
          <div className="lbl">Company score · {r.opportunities.length} opportunities</div>
        </div>
        <FactorList factors={cs.factors} />
      </div>
      {cs.snapshot && <p className="snapshot">{cs.snapshot}</p>}

      <div className="tabs">
        <button className={`tab ${tab === "opps" ? "on" : ""}`} onClick={() => setTab("opps")}>Opportunities</button>
        <button className={`tab ${tab === "md" ? "on" : ""}`} onClick={() => setTab("md")}>Full report</button>
      </div>

      {tab === "opps" ? (
        <>
          <div style={{ height: 24 }} />
          {opps.length === 0 && <p className="muted">No verified, current opportunities.</p>}
          {opps.map((o, i) => (
            <Opportunity key={o.id ?? i} o={o} rank={i + 1} open={!!open[i]}
                         isNew={o.new && r.mode === "refresh"} onToggle={() => setOpen({ ...open, [i]: !open[i] })} />
          ))}
        </>
      ) : (
        <div className="md" dangerouslySetInnerHTML={{ __html: marked.parse(data.markdown) }} />
      )}
    </div>
  );
}
