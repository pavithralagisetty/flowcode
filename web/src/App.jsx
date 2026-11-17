import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import TopBar from "./components/TopBar.jsx";
import Rail from "./components/Rail.jsx";
import Home from "./pages/Home.jsx";
import Report from "./pages/Report.jsx";
import Ranking from "./pages/Ranking.jsx";
import Settings from "./pages/Settings.jsx";
import Usage from "./pages/Usage.jsx";

/** Tiny hash router: #/home, #/reports, #/reports/<slug>, #/usage, #/settings */
function useRoute() {
  const read = () => {
    const [, section = "home", arg] = window.location.hash.replace(/^#/, "").split("/");
    return { section, arg };
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onHash = () => setRoute(read());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const go = useCallback((path) => { window.location.hash = path; }, []);
  return [route, go];
}

export default function App() {
  const [{ section, arg }, go] = useRoute();
  const [reports, setReports] = useState([]);
  const [settings, setSettings] = useState(null);
  const [job, setJob] = useState(null); // the run started from Home, polled until it finishes
  const [mode, setMode] = useState(null);
  useEffect(() => { api.mode().then(setMode).catch(() => {}); }, []);
  const changeMode = async (m) => {
    setMode(await api.setMode(m));
    api.settings().then(setSettings).catch(() => {});
  };

  const refreshReports = useCallback(() => api.reports().then(setReports).catch(() => {}), []);
  useEffect(() => { refreshReports(); api.settings().then(setSettings).catch(() => {}); }, [refreshReports]);

  useEffect(() => {
    if (!job || job.status !== "running") return;
    const t = setInterval(async () => {
      const next = await api.run(job.id).catch(() => null);
      if (!next) return;
      setJob(next);
      if (next.status !== "running") refreshReports();
    }, 1500);
    return () => clearInterval(t);
  }, [job, refreshReports]);

  const startRun = async (query) => setJob(await api.startRun(query));

  let page;
  if (section === "reports" && arg) page = <Report slug={arg} go={go} />;
  else if (section === "reports") page = <Ranking reports={reports} go={go} />;
  else if (section === "settings") page = <Settings settings={settings} />;
  else if (section === "usage") page = <Usage />;
  else page = <Home reports={reports} go={go} job={job} startRun={startRun} />;

  return (
    <div className="app">
      <TopBar reports={reports} go={go} mode={mode} onMode={changeMode} running={job?.status === "running"} />
      <Rail section={section} go={go} />
      <main className="main">{page}</main>
    </div>
  );
}
