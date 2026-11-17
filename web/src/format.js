export const fmt = (n) => (n == null ? "–" : Number(n).toFixed(2));

export const titleCase = (s) =>
  (s || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()).replace(/\bTv\b/g, "TV");

const DOT_COLORS = ["#3a8a50", "#b98f8e", "#6f86d6", "#a77bbd", "#b08f4f", "#2f2f2f"];
export const dotColor = (name) =>
  DOT_COLORS[[...(name || "")].reduce((a, c) => a + c.charCodeAt(0), 0) % DOT_COLORS.length];

export const initials = (name) =>
  (name || "?").split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

export const scoreTone = (s) => (s >= 3.5 ? "green" : s >= 2.5 ? "amber" : "red");
