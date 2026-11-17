import { dotColor, fmt, initials, scoreTone } from "../format.js";

function Table({ rows, go }) {
  return (
    <table className="table">
      <thead>
        <tr><th>#</th><th>Company</th><th>Score</th><th>Opportunities</th><th>Strong</th><th>Top pitch</th><th>Run</th></tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={r.slug} className="clickable" onClick={() => go(`/reports/${r.slug}`)}>
            <td className="muted">{i + 1}</td>
            <td>
              <div className="company-cell">
                <span className="logo-dot" style={{ background: dotColor(r.name) }}>{initials(r.name)}</span>
                <div><strong style={{ fontWeight: 600 }}>{r.name}</strong><div className="muted" style={{ fontSize: 14 }}>{r.industry}</div></div>
              </div>
            </td>
            <td><span className={`score-box chip ${scoreTone(r.score)}`} style={{ borderRadius: 2 }}>{fmt(r.score)}</span></td>
            <td>{r.opportunities}</td>
            <td>{r.strong}</td>
            <td style={{ maxWidth: 420 }}>{r.top ? <>{r.top} <span className="muted">({fmt(r.top_score)})</span></> : <span className="muted">None</span>}</td>
            <td className="muted">{r.run_date}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Ranking({ reports, go }) {
  const prospects = reports.filter((r) => !r.customer_tier);
  const customers = reports.filter((r) => r.customer_tier);
  return (
    <div className="page">
      <div className="page-head"><h1>Reports</h1></div>
      <p className="muted" style={{ fontSize: 18, marginTop: 10 }}>Companies sorted by company score. Existing Flowcode customers are listed separately.</p>
      {reports.length === 0 && <p className="muted">No reports yet.</p>}
      {prospects.length > 0 && <Table rows={prospects} go={go} />}
      {customers.length > 0 && (
        <>
          <h2 style={{ fontWeight: 500, fontSize: 28, marginTop: 48 }}>Existing customers</h2>
          <Table rows={customers} go={go} />
        </>
      )}
    </div>
  );
}
