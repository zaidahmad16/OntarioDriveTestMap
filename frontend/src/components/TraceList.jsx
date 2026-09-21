import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";

function TraceDetail({ traceId, onClose, isAdmin }) {
  const { t } = useLang();
  const [trace, setTrace] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let stale = false;
    setTrace(null);
    setError(null);
    api
      .getTrace(traceId)
      .then((data) => {
        if (!stale) setTrace(data);
      })
      .catch((err) => {
        if (!stale) setError(err.message);
      });
    return () => {
      stale = true;
    };
  }, [traceId]);

  if (error)
    return (
      <div className="card scale-in" style={{ marginTop: 10 }}>
        <button onClick={onClose}>{t("close")}</button>
        <p style={{ color: "var(--confirmed)" }}>{t("couldntLoadSource")}: {error}</p>
      </div>
    );
  if (!trace) return <p style={{ color: "var(--ink-muted)" }}>{t("loading")}</p>;

  // Human-readable label instead of the raw ingestion id (e.g.
  // "reddit:jt7gwng#route1") -- the real category is the only thing a
  // normal user needs; the raw id stays available to admins only
  // (TraceList threads isAdmin down from App.jsx's own user.is_admin).
  const isVideo = trace.source_id.startsWith("youtube");
  const label = isVideo ? t("videoTranscriptions") : t("communityReports");

  return (
    <div className="card scale-in" style={{ marginTop: 10 }}>
      <button onClick={onClose}>{t("close")}</button>
      <h4 style={{ marginTop: 10 }}>{isAdmin ? trace.source_id : label}</h4>
      <p style={{ fontSize: "0.875rem", color: "var(--ink-muted)" }}>
        <span className="trust-badge trust-badge--gap">{trace.test_class || "?"}</span>{" "}
        reliability <span className="data">{trace.reliability}</span> · observed{" "}
        <span className="data">{trace.observed_at}</span> · status {trace.status || "unsnapped"}
      </p>
      <h4 style={{ fontSize: "0.875rem" }}>{t("turns")}</h4>
      <ol style={{ paddingLeft: 20, margin: "0 0 var(--space-md)" }}>
        {trace.turns.map((tn, i) => (
          <li key={i} style={{ fontSize: "0.875rem" }}>
            {tn.direction} → {tn.street}
          </li>
        ))}
      </ol>
      <h4 style={{ fontSize: "0.875rem" }}>{t("resolvedWaypoints")}</h4>
      <ol style={{ paddingLeft: 20, margin: 0 }}>
        {trace.waypoints.map((w, i) => (
          <li key={i} style={{ fontSize: "0.875rem" }}>
            {w.pair_street_a} × {w.pair_street_b} —{" "}
            {/* A failed/partial trace can carry a waypoint row that never
                resolved to real coordinates; guard so one null doesn't
                crash the whole detail view. */}
            {w.lat != null && w.lon != null ? (
              <span className="data">
                ({w.lat.toFixed(5)}, {w.lon.toFixed(5)})
              </span>
            ) : (
              <span style={{ color: "var(--ink-faint)" }}>{t("unresolved")}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

export default function TraceList({ centreId, isAdmin }) {
  const { t } = useLang();
  const [traces, setTraces] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let stale = false;
    setTraces([]);
    setOpenId(null);
    setError(null);
    setExpanded(false);
    api
      .getTraces(centreId)
      .then((data) => {
        if (!stale) setTraces(data);
      })
      .catch((err) => {
        if (!stale) setError(err.message);
      });
    return () => {
      stale = true;
    };
  }, [centreId]);

  if (error) return <p className="error-banner">{t("error")}: {error}</p>;

  // Real, derivable categories -- video transcriptions vs. community
  // reports -- the same distinction segment popups already show
  // ("N video, N text"), not an invented taxonomy.
  const videoCount = traces.filter((tr) => tr.source_id.startsWith("youtube")).length;
  const reportCount = traces.length - videoCount;

  return (
    <div className="card evidence-summary" style={{ marginTop: 20 }}>
      <h3 style={{ marginTop: 0 }}>{t("evidenceSources")}</h3>
      {!traces.length ? (
        <p style={{ color: "var(--ink-muted)" }}>{t("noSourcesYet")}</p>
      ) : (
        <>
          <p>
            <span className="data">{traces.length}</span> {t("observationsContributed")}
          </p>
          <ul className="evidence-summary__breakdown">
            {reportCount > 0 && (
              <li>
                <span className="data">{reportCount}</span> {t("communityReports")}
              </li>
            )}
            {videoCount > 0 && (
              <li>
                <span className="data">{videoCount}</span> {t("videoTranscriptions")}
              </li>
            )}
          </ul>
          <button onClick={() => setExpanded((e) => !e)} aria-expanded={expanded}>
            {expanded ? t("hideSources") : t("viewSources")}
          </button>
        </>
      )}
      {expanded && (
        <div className="evidence-summary__pills">
          {traces.map((tr, i) => {
            const isVideo = tr.source_id.startsWith("youtube");
            return (
              <button
                key={tr.id}
                onClick={() => setOpenId(tr.id)}
                className={`rise-in${openId === tr.id ? " btn-primary" : ""}`}
                style={{ fontSize: "0.8125rem", animationDelay: `${Math.min(i, 20) * 15}ms` }}
              >
                {isVideo ? t("videoTranscriptions") : t("communityReports")} — {tr.test_class},{" "}
                <span className="data">{tr.observed_at}</span>
              </button>
            );
          })}
        </div>
      )}
      {openId && <TraceDetail traceId={openId} onClose={() => setOpenId(null)} isAdmin={isAdmin} />}
    </div>
  );
}
