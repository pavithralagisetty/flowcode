"""Simple Streamlit frontend. Run with: .venv/bin/streamlit run app.py"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from flowcode_agent.batch import BatchItem, run_many, write_ranking
from flowcode_agent.config import load_config
from flowcode_agent.pipeline import build_context, run_company
from flowcode_agent.providers.handoff import NeedsAnswer
from flowcode_agent.steps.resolve import AmbiguousCompany

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Flowcode Research Agent", page_icon="🔎", layout="wide")


@st.cache_resource
def get_config():
    return load_config(ROOT / "config.yaml")


class StatusHandler(logging.Handler):
    """Streams pipeline log lines into a Streamlit status box."""

    def __init__(self, box):
        super().__init__(level=logging.INFO)
        self.box = box

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("flowcode_agent"):
            self.box.write(f"`{record.levelname.lower()}` {record.getMessage()}")


def with_log_box(label: str):
    box = st.status(label, expanded=True)
    handler = StatusHandler(box)
    logger = logging.getLogger("flowcode_agent")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return box, handler, logger


def load_reports(out_dir: Path) -> dict[str, dict]:
    reports = {}
    for path in sorted(out_dir.glob("*/report.json")):
        try:
            reports[path.parent.name] = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
    return reports


def show_report(data: dict, md_path: Path) -> None:
    company, cs = data["company"], data["company_score"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Company score", f"{cs['score']:.2f}")
    c2.metric("Opportunities", len(data["opportunities"]))
    c3.metric("Existing customer", company.get("customer_tier") or "no")
    c4.metric("Run date", data["run_date"])
    st.caption(f"**Industry:** {company['industry']} · **Brands:** {', '.join(company['brands']) or 'n/a'} · "
               f"**Channels covered:** {', '.join(data['channels_covered']) or 'none'}")
    if cs.get("snapshot"):
        st.info(cs["snapshot"])

    tab_opps, tab_md, tab_factors = st.tabs(["Opportunities", "Full report", "Company score"])
    with tab_opps:
        if not data["opportunities"]:
            st.write("No verified, current opportunities found.")
        for i, o in enumerate(data["opportunities"], 1):
            m = o["moment"]
            new = " 🆕" if o.get("new") and data["mode"] == "refresh" else ""
            with st.expander(f"{i}. {m['title']} — score {o['score']:.2f}{new}", expanded=i <= 2):
                st.markdown(f"**What's happening:** {m['summary']}  \n"
                            f"*Timing: {m['timing'].get('date_text') or 'not stated'} ({m['timing']['status']})*")
                st.markdown(f"**Where a code goes:** {', '.join(m['physical_touchpoints']) or 'not stated'}")
                st.markdown(f"**Why Flowcode fits:** {o['why_flowcode_fits']}")
                if o.get("similar_story"):
                    s = o["similar_story"]
                    st.markdown(f"**Similar story:** [{s['name']}]({s['url']}) — {s['result']}")
                st.markdown(f"**Suggested first pitch:** {o['suggested_first_pitch']}")
                ratings = o.get("factor_ratings", {})
                if ratings:
                    st.dataframe(pd.DataFrame([{"factor": k, "rating": v["rating"], "reason": v["reason"]}
                                               for k, v in ratings.items()]), hide_index=True, width="stretch")
                st.markdown("**Sources**")
                for src in o["sources"]:
                    st.markdown(f"- {src['publisher']}, {src['date']}: “{src['quote']}” [link]({src['url']})")
    with tab_md:
        md = md_path.read_text() if md_path.exists() else ""
        st.download_button("Download report.md", md, file_name=f"{md_path.parent.name}-report.md")
        st.markdown(md)
    with tab_factors:
        st.dataframe(pd.DataFrame([{"factor": k, "rating": v["rating"], "reason": v["reason"]}
                                   for k, v in cs["factors"].items()]), hide_index=True, width="stretch")


cfg = get_config()
out_dir = cfg.path("outputs")

with st.sidebar:
    st.title("🔎 Flowcode Research")
    page = st.radio("Page", ["Research a company", "Past reports", "Batch ranking"])
    st.divider()
    main, cheap = cfg.llm.models()
    search_model = cfg.search.claude_model if cfg.search.provider == "claude" else cfg.search.gemini_model
    st.caption(f"Search: **{cfg.search.provider}** ({search_model})")
    st.caption(f"AI: **{cfg.llm.provider}** ({main} / {cheap})")
    providers = {cfg.search.provider, cfg.llm.provider}
    needed = [k for k, used in (("ANTHROPIC_API_KEY", providers & {"claude", "anthropic"}),
                                ("GEMINI_API_KEY", "gemini" in providers)) if used]
    for key in needed:
        st.caption(("✅ " if os.environ.get(key) else "❌ ") + key)

if page == "Research a company":
    st.header("Research a company")
    with st.form("run"):
        query = st.text_input("Company name or website", placeholder="Liquid Death  or  https://liquiddeath.com")
        submitted = st.form_submit_button("Run research", type="primary")
    if submitted and query.strip():
        box, handler, logger = with_log_box(f"Researching {query}…")
        try:
            ctx = build_context(cfg)
            result = run_company(ctx, query.strip())
            box.update(label=f"Done: {result.company.name}", state="complete", expanded=False)
            u = result.usage
            st.caption(f"Usage: {u['search_requests']} searches, {u['llm_input_tokens']:,} input tokens, "
                       f"{u['llm_output_tokens']:,} output tokens")
            data = json.loads((result.report_dir / "report.json").read_text())
            show_report(data, result.report_dir / "report.md")
        except AmbiguousCompany as exc:
            box.update(label="Need a more specific company", state="error")
            st.warning(f"'{exc.query}' could mean several companies. Enter the website of the one you mean:")
            for c in exc.candidates:
                st.markdown(f"- **{c.name}** — {c.domain or 'no domain'} {c.note or ''}")
        except NeedsAnswer as exc:
            box.update(label="Paused: waiting for Claude Code", state="running", expanded=False)
            st.info(f"No API key mode: this run needs an answer in `{exc.path}`. Ask Claude Code to answer the "
                    "pending handoff requests, then click Run research again to continue.")
        except Exception as exc:  # noqa: BLE001 - show any failure in the UI
            box.update(label="Run failed", state="error")
            st.error(f"{type(exc).__name__}: {exc}")
        finally:
            logger.removeHandler(handler)

elif page == "Past reports":
    st.header("Past reports")
    reports = load_reports(out_dir)
    if not reports:
        st.write("No reports yet. Run a company first.")
    else:
        ranked = sorted(reports.items(), key=lambda kv: kv[1]["company_score"]["score"], reverse=True)
        labels = {f"{d['company']['name']} ({d['company_score']['score']:.2f})": slug for slug, d in ranked}
        choice = st.selectbox("Company", list(labels))
        slug = labels[choice]
        show_report(reports[slug], out_dir / slug / "report.md")

else:
    st.header("Batch ranking")
    uploaded = st.file_uploader("CSV with a `name` column and an optional `domain` column", type="csv")
    default_csv = ROOT / "companies.csv"
    if st.button("Run batch", type="primary"):
        text = uploaded.getvalue().decode() if uploaded else default_csv.read_text()
        rows = list(csv.DictReader(io.StringIO(text)))
        items = [BatchItem(name=r["name"].strip(), domain=(r.get("domain") or "").strip() or None)
                 for r in rows if (r.get("name") or "").strip()]
        box, handler, logger = with_log_box(f"Running {len(items)} companies…")
        progress = st.progress(0.0)
        done: list[BatchItem] = []

        def on_done(item: BatchItem) -> None:
            done.append(item)
            progress.progress(len(done) / len(items))

        try:
            # Log lines from worker threads can't reach the UI, so progress is shown per company.
            run_many(cfg, lambda db: build_context(cfg, db=db), items, on_done=on_done)
            write_ranking(out_dir, items)
            box.update(label="Batch finished", state="complete", expanded=False)
            for i in items:
                if i.error:
                    st.warning(f"{i.name}: {i.error}")
        except Exception as exc:  # noqa: BLE001
            box.update(label="Batch failed", state="error")
            st.error(f"{type(exc).__name__}: {exc}")
        finally:
            logger.removeHandler(handler)
    ranking = out_dir / "ranking.csv"
    if ranking.exists():
        st.subheader("Latest ranking")
        st.dataframe(pd.read_csv(ranking), hide_index=True, width="stretch")
        st.download_button("Download ranking.csv", ranking.read_text(), file_name="ranking.csv")
    else:
        st.write("No ranking yet.")
