import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";
import { useSignIn } from "../SignIn.jsx";

// Source type is derived from the real ingestion id prefix -- the same
// split the map's evidence popups use ("N video, N text"), not an
// invented taxonomy. Video + community always sum to the total because
// they partition one list.
function sourceType(tr) {
  return tr.source_id.startsWith("youtube") ? "video" : "community";
}

// Raw pipeline values ("ambiguous", null) never reach the UI verbatim.
function classLabel(t, value) {
  return value === "G" || value === "G2" ? value : t("classUnclear");
}

function formatDate(value, lang) {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat(lang === "fr" ? "fr-CA" : "en-CA", { year: "numeric", month: "short", day: "numeric" }).format(d);
}

function TraceDetail({ traceId, isAdmin, onBack }) {
  const { t, lang } = useLang();
  const [trace, setTrace] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let stale = false;
    setTrace(null);
    setError(null);
    api
      .getTrace(traceId)
      .then((data) => !stale && setTrace(data))
      .catch((err) => !stale && setError(err.message));
    return () => {
      stale = true;
    };
  }, [traceId]);

  return (
    <div className="source-detail">
      <button type="button" className="link-btn source-detail__back" onClick={onBack}>
        ← {t("allSources")}
      </button>
      {error && (
        <p className="field-error" role="alert">
          {t("couldntLoadSource")}: {error}
        </p>
      )}
      {!trace && !error && <p className="muted">{t("loading")}</p>}
      {trace && (
        <>
          <h3>{isAdmin ? trace.source_id : t(`sourceType_${sourceType(trace)}`)}</h3>
          <dl className="source-detail__facts">
            <div>
              <dt>{t("testClass")}</dt>
              <dd>{classLabel(t, trace.test_class)}</dd>
            </div>
            {trace.observed_at && (
              <div>
                <dt>{t("observed")}</dt>
                <dd>{formatDate(trace.observed_at, lang)}</dd>
              </div>
            )}
            {trace.status && (
              <div>
                <dt>{t("processing")}</dt>
                <dd>{trace.status}</dd>
              </div>
            )}
          </dl>
          {trace.turns?.length > 0 && (
            <>
              <h4>{t("reportedTurns")}</h4>
              <ol className="source-detail__list">
                {trace.turns.map((tn, i) => (
                  <li key={i}>
                    {tn.direction} → {tn.street}
                  </li>
                ))}
              </ol>
            </>
          )}
          {trace.waypoints?.length > 0 && (
            <>
              <h4>{t("resolvedJunctions")}</h4>
              <ol className="source-detail__list">
                {trace.waypoints.map((w, i) => (
                  <li key={i}>
                    {w.pair_street_a} × {w.pair_street_b}
                    {(w.lat == null || w.lon == null) && <span className="muted"> — {t("unresolved")}</span>}
                  </li>
                ))}
              </ol>
            </>
          )}
        </>
      )}
    </div>
  );
}

// Side drawer on desktop, full-height sheet on mobile (CSS). Modal
// pattern: focus moves in, Escape closes, focus returns to the trigger.
function EvidenceDrawer({ traces, error, isAdmin, centreName, onClose }) {
  const { t, lang } = useLang();
  const [filter, setFilter] = useState("all");
  const [openId, setOpenId] = useState(null);
  const panelRef = useRef(null);
  const closeRef = useRef(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && panelRef.current) {
        const f = panelRef.current.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (!f.length) return;
        const first = f[0];
        const last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  const list = traces || [];
  const types = ["video", "community"].filter((ty) => list.some((tr) => sourceType(tr) === ty));
  const shown = filter === "all" ? list : list.filter((tr) => sourceType(tr) === filter);

  return (
    <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" ref={panelRef}>
        <div className="drawer__head">
          <div>
            <p className="overline">{centreName}</p>
            <h2 id="drawer-title">{t("sourcesForCentre")}</h2>
          </div>
          <button ref={closeRef} type="button" onClick={onClose}>
            {t("close")}
          </button>
        </div>
        {openId ? (
          <TraceDetail traceId={openId} isAdmin={isAdmin} onBack={() => setOpenId(null)} />
        ) : (
          <>
            <p className="drawer__scope">{t("centreScopeNote")}</p>
            {error && (
              <p className="field-error" role="alert">
                {t("evidenceLoadFailed")} ({error})
              </p>
            )}
            {!traces && !error && <p className="muted">{t("loading")}</p>}
            {types.length > 1 && (
              <div className="filter-chips" role="group" aria-label={t("filterSources")}>
                {["all", ...types].map((ty) => (
                  <button key={ty} type="button" aria-pressed={filter === ty} onClick={() => setFilter(ty)}>
                    {ty === "all" ? t("filterAll") : t(`sourceTypePlural_${ty}`)}
                  </button>
                ))}
              </div>
            )}
            <ul className="source-list">
              {shown.map((tr) => (
                <li key={tr.id}>
                  <button type="button" className="source-row" onClick={() => setOpenId(tr.id)}>
                    <span className="source-row__type">{t(`sourceType_${sourceType(tr)}`)}</span>
                    <span className="source-row__meta">
                      {classLabel(t, tr.test_class)}
                      {tr.observed_at && <> · {formatDate(tr.observed_at, lang)}</>}
                    </span>
                    <span aria-hidden="true" className="source-row__go">
                      →
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

// Evidence section (spec §5.5). Everything here is explicitly
// CENTRE-wide. Counts come from the public /evidence-summary endpoint;
// the source records themselves (ingestion ids, author hashes) load only
// when a signed-in user opens the drawer -- guests are asked to sign in
// at that moment, and the drawer opens once they have.
export default function TraceList({ centreId, centreName, isAdmin, signedIn }) {
  const { t, tn } = useLang();
  const { requireSignIn } = useSignIn();
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);
  const [traces, setTraces] = useState(null);
  const [tracesError, setTracesError] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [reload, setReload] = useState(0);
  const triggerRef = useRef(null);

  useEffect(() => {
    let stale = false;
    setSummary(null);
    setError(null);
    setTraces(null);
    setDrawerOpen(false);
    api
      .getEvidenceSummary(centreId)
      .then((data) => !stale && setSummary(data))
      .catch((err) => !stale && setError(err.message));
    return () => {
      stale = true;
    };
  }, [centreId, reload]);

  useEffect(() => {
    if (!drawerOpen || traces) return;
    let stale = false;
    setTracesError(null);
    api
      .getTraces(centreId)
      .then((data) => !stale && setTraces(data))
      .catch((err) => !stale && setTracesError(err.message));
    return () => {
      stale = true;
    };
  }, [drawerOpen, traces, centreId]);

  const total = summary?.total ?? 0;
  const videoCount = summary?.video ?? 0;
  const communityCount = summary?.community ?? 0;

  return (
    <section className="page-section" id="evidence" aria-labelledby="evidence-title">
      <div className="page-section__head">
        <h2 id="evidence-title">{t("evidenceForCentre")}</h2>
        <p className="page-section__lede">{t("evidenceScopeLede")}</p>
      </div>

      {error ? (
        <div className="state-panel state-panel--error" role="alert">
          <p>
            {t("evidenceLoadFailed")} ({error})
          </p>
          <button type="button" onClick={() => setReload((r) => r + 1)}>
            {t("retry")}
          </button>
        </div>
      ) : summary === null ? (
        <div className="evidence-grid" aria-busy="true">
          <div className="skeleton-block" />
          <div className="skeleton-block" />
        </div>
      ) : total === 0 ? (
        <p className="state-panel">{t("noSourcesYet")}</p>
      ) : (
        <div className="evidence-grid">
          <dl className="evidence-counts">
            <div className="evidence-counts__total">
              <dt>{t("sourceRecords")}</dt>
              <dd className="data">{total}</dd>
            </div>
            {videoCount > 0 && (
              <div>
                <dt>{tn("videoTranscriptionsN", videoCount)}</dt>
                <dd className="data">{videoCount}</dd>
              </div>
            )}
            {communityCount > 0 && (
              <div>
                <dt>{tn("communityReportsN", communityCount)}</dt>
                <dd className="data">{communityCount}</dd>
              </div>
            )}
          </dl>
          <div className="evidence-method">
            <p>{t("evidenceHowConfirmed")}</p>
            <div className="evidence-method__actions">
              <button ref={triggerRef} type="button" onClick={() => requireSignIn("sources", () => setDrawerOpen(true))}>
                {t("inspectSources")}
              </button>
              <a href="/about.html#confidence">{t("readMethod")}</a>
            </div>
            {!signedIn && <p className="evidence-method__note">{t("sourcesNeedSignIn")}</p>}
          </div>
        </div>
      )}

      {drawerOpen && (
        <EvidenceDrawer
          traces={traces}
          error={tracesError}
          isAdmin={isAdmin}
          centreName={centreName}
          onClose={() => {
            setDrawerOpen(false);
            triggerRef.current?.focus();
          }}
        />
      )}
    </section>
  );
}
