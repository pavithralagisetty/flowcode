async function request(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export const api = {
  reports: () => request("/reports"),
  report: (slug) => request(`/reports/${slug}`),
  runs: () => request("/runs"),
  run: (id) => request(`/runs/${id}`),
  startRun: (query) => request("/runs", { method: "POST", body: JSON.stringify({ query }) }),
  settings: () => request("/settings"),
};
