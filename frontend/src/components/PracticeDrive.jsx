import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MapContainer, TileLayer, GeoJSON, Marker, Circle, useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import { useLang } from "../i18n.jsx";
import { StatusBadge } from "../RouteNotation.jsx";
import { buildFeaturePaths, snapToRoute, haversineM } from "./practiceGeo.js";
import { makeCentreIcon, routeLineStyle, casingStyle, splitAtGaps } from "./MapView.jsx";
import { simulationSpeed, makeSimulatedGeolocation } from "./simulatedGeolocation.js";

// Practice drive, Phase A of OntarioDriveTestMap-Mobile-Practice-Drive-
// Redesign.md: a truthful visual follower. Full-height map, blue position
// puck + accuracy halo, follow camera with Recenter, route drawn with its
// evidence styling, preview/permission/acquire/join/low-accuracy/off-route/
// paused/interrupted states, and a manual turn list.
//
// Deliberately NOT here (spec §1, §5, Phase B/C gates): turn-by-turn
// countdown, "next turn in 350 m", voice prompts, auto-advancing turns,
// completion scoring. Route steps in this app carry no geometry anchor
// (only per-leg distances, repeated on manual_youtube routes), so any of
// those would be a guess dressed up as guidance.
//
// Privacy: coordinates live only in this component's memory for the
// session. Nothing is sent to the server, logged, or persisted; End and
// unmount clear the watch.

// Starting thresholds from spec §6 -- to be tuned in field tests.
const ACC_GOOD_M = 30;
const ACC_POOR_M = 60;
const STALE_FIX_MS = 10000;
const OFF_ROUTE_MIN_M = 35;
const OFF_ROUTE_HOLD_MS = 10000;
const OFF_ROUTE_MIN_FIXES = 3;
const TELEPORT_M = 250;
const TELEPORT_WINDOW_MS = 5000;
const FIX_TIMEOUT_MS = 20000;
const WAKE_PREF_KEY = "odtm_practice_keep_awake";

const ACTIVE = new Set(["acquiring", "noFix", "joining", "following", "lowAccuracy", "offRoute", "away"]);

// ------------------------------------------------------------------
// Controller: one reducer, one status. UI derives everything from it.
// ------------------------------------------------------------------
function reducer(s, a) {
  switch (a.type) {
    case "START":
      return { ...s, status: "acquiring", fix: null, offSince: null, offFixes: 0 };
    case "DENIED":
      return { ...s, status: "denied", fix: null };
    case "UNAVAILABLE":
      return { ...s, status: "unavailable", fix: null };
    case "TIMEOUT":
      return s.status === "acquiring" ? { ...s, status: "noFix" } : s;
    case "PAUSE":
      return { ...s, status: "paused", offSince: null, offFixes: 0 };
    case "INTERRUPT":
      return ACTIVE.has(s.status) ? { ...s, status: "interrupted", fix: s.fix ? { ...s.fix, stale: true } : null } : s;
    case "PREVIEW":
      return { ...s, status: "preview", fix: null, offSince: null, offFixes: 0 };
    case "FIX": {
      if (!ACTIVE.has(s.status)) return s; // paused/interrupted/preview: ignore callbacks
      const { fix, deviationM } = a;
      const prev = s.fix && !s.fix.stale ? s.fix : null;
      // Reject implausible teleports; wait for corroborating fixes.
      if (prev && fix.t - prev.t < TELEPORT_WINDOW_MS && haversineM(prev.lonlat, fix.lonlat) > TELEPORT_M) return s;
      const next = { ...s, fix: { ...fix, deviationM } };
      if (fix.acc > ACC_POOR_M) return { ...next, status: "lowAccuracy", offSince: null, offFixes: 0 };
      const threshold = Math.max(OFF_ROUTE_MIN_M, 2 * fix.acc);
      const onRoute = deviationM <= threshold;
      if (s.status === "acquiring" || s.status === "noFix" || s.status === "lowAccuracy") {
        return { ...next, status: onRoute ? "following" : "joining", offSince: null, offFixes: 0 };
      }
      if (s.status === "joining" || s.status === "away") {
        if (onRoute) return { ...next, status: "following", offSince: null, offFixes: 0 };
        // Only call it "away" on a good fix; a degraded one just keeps checking.
        return { ...next, status: fix.acc <= ACC_GOOD_M ? "away" : "joining" };
      }
      // following / offRoute
      if (onRoute) return { ...next, status: "following", offSince: null, offFixes: 0 };
      if (fix.acc > ACC_GOOD_M) return next; // degraded fix: never the basis for an off-route call
      const offSince = s.offSince ?? fix.t;
      const offFixes = s.offFixes + 1;
      const confirmed = fix.t - offSince >= OFF_ROUTE_HOLD_MS && offFixes >= OFF_ROUTE_MIN_FIXES;
      return { ...next, offSince, offFixes, status: confirmed ? "offRoute" : s.status };
    }
    default:
      return s;
  }
}

function usePracticeDrive(lineFeatures, keepAwake, simSpeed) {
  const [state, dispatch] = useReducer(reducer, { status: "preview", fix: null, offSince: null, offFixes: 0 });
  const paths = useMemo(() => buildFeaturePaths(lineFeatures), [lineFeatures]);
  // Dev-only simulated GPS replaces the real provider; everything else
  // (reducer, gates, visibility handling) runs exactly as for real GPS.
  const geo = useMemo(
    () => (simSpeed ? makeSimulatedGeolocation(lineFeatures, simSpeed) : typeof navigator !== "undefined" ? navigator.geolocation : null),
    [lineFeatures, simSpeed]
  );
  const watchRef = useRef(null);
  const wakeRef = useRef(null);
  const [wake, setWake] = useState("off"); // off | on | unavailable | released
  const statusRef = useRef(state.status);
  statusRef.current = state.status;

  const stopWatch = useCallback(() => {
    if (watchRef.current != null && geo) geo.clearWatch(watchRef.current);
    watchRef.current = null;
  }, [geo]);

  const releaseWake = useCallback(() => {
    const s = wakeRef.current;
    wakeRef.current = null;
    if (s) s.release().catch(() => {});
  }, []);

  const requestWake = useCallback(async () => {
    if (!keepAwake) return setWake("off");
    if (!("wakeLock" in navigator)) return setWake("unavailable");
    try {
      const sentinel = await navigator.wakeLock.request("screen");
      wakeRef.current = sentinel;
      setWake("on");
      sentinel.addEventListener("release", () => {
        if (wakeRef.current === sentinel) {
          wakeRef.current = null;
          setWake("released");
        }
      });
    } catch {
      setWake("unavailable");
    }
  }, [keepAwake]);

  const startWatch = useCallback(() => {
    if (!geo || (!simSpeed && !window.isSecureContext)) {
      dispatch({ type: "UNAVAILABLE" });
      return;
    }
    stopWatch();
    dispatch({ type: "START" });
    watchRef.current = geo.watchPosition(
      (pos) => {
        if (document.visibilityState === "hidden") return; // never advance while hidden
        const { latitude, longitude, accuracy } = pos.coords;
        if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return;
        if (Date.now() - pos.timestamp > STALE_FIX_MS) return; // cached/stale fix
        const lonlat = [longitude, latitude];
        const snap = snapToRoute(paths, lonlat);
        dispatch({
          type: "FIX",
          fix: { lonlat, acc: accuracy ?? 999, t: pos.timestamp },
          deviationM: snap ? snap.deviationM : Infinity,
        });
      },
      (err) => {
        if (err.code === 1) {
          stopWatch();
          releaseWake();
          dispatch({ type: "DENIED" });
        } else if (err.code === 3) {
          dispatch({ type: "TIMEOUT" });
        } else {
          dispatch({ type: "TIMEOUT" });
        }
      },
      { enableHighAccuracy: true, maximumAge: 0, timeout: FIX_TIMEOUT_MS }
    );
  }, [paths, stopWatch, releaseWake, geo, simSpeed]);

  const start = useCallback(() => {
    startWatch();
    requestWake();
  }, [startWatch, requestWake]);

  const pause = useCallback(() => {
    stopWatch();
    releaseWake();
    setWake("off");
    dispatch({ type: "PAUSE" });
  }, [stopWatch, releaseWake]);

  const end = useCallback(() => {
    stopWatch();
    releaseWake();
    setWake("off");
    dispatch({ type: "PREVIEW" });
  }, [stopWatch, releaseWake]);

  // Page hidden -> interrupted: stop the watch and wake lock immediately.
  // Coming back does NOT resume on its own; the user rechecks location.
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "hidden" && ACTIVE.has(statusRef.current)) {
        stopWatch();
        releaseWake();
        setWake("off");
        dispatch({ type: "INTERRUPT" });
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [stopWatch, releaseWake]);

  useEffect(
    () => () => {
      stopWatch();
      releaseWake();
    },
    [stopWatch, releaseWake]
  );

  return { state, wake, start, pause, end, retry: start };
}

// ------------------------------------------------------------------
// Map pieces
// ------------------------------------------------------------------
const puckIcon = L.divIcon({
  className: "pd-puck",
  html: '<span class="pd-puck__dot"></span>',
  iconSize: [22, 22],
  iconAnchor: [11, 11],
});

function reducedMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

// Follow camera: north-up, throttled pans only on good fixes; never
// fitBounds per GPS callback. A user drag switches to explore mode.
function Camera({ fix, follow, setFollow, fitRequest, routeBounds, following }) {
  const map = useMap();
  const lastPan = useRef(0);
  useMapEvents({
    dragstart: () => setFollow(false),
  });
  // Fit AFTER re-measuring: the full-screen overlay's size isn't final on
  // the first frame (100dvh, safe areas), and fitting against the stale
  // size showed only the start of the route on a real iPhone.
  useEffect(() => {
    const t = setTimeout(() => {
      map.invalidateSize();
      if (routeBounds?.isValid()) map.fitBounds(routeBounds, { padding: [32, 32] });
    }, 60);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitRequest]);
  useEffect(() => {
    if (!fix || fix.stale || !follow || !following) return;
    const now = Date.now();
    if (now - lastPan.current < 900) return;
    lastPan.current = now;
    const target = [fix.lonlat[1], fix.lonlat[0]];
    if (map.getZoom() < 16) map.setView(target, 16, { animate: !reducedMotion() });
    else map.panTo(target, { animate: !reducedMotion(), duration: 0.6 });
  }, [fix, follow, following, map]);
  // The overlay's size settles after mount (100dvh, safe areas).
  useEffect(() => {
    const t = setTimeout(() => map.invalidateSize(), 50);
    return () => clearTimeout(t);
  }, [map]);
  return null;
}

// ------------------------------------------------------------------
// Screen
// ------------------------------------------------------------------
const BANNER = {
  preview: ["pdPreviewTitle", "pdPreviewSub"],
  acquiring: ["pdAcquiringTitle", "pdAcquiringSub"],
  noFix: ["pdNoFixTitle", "pdNoFixSub"],
  joining: ["pdJoiningTitle", "pdJoiningSub"],
  following: ["pdFollowingTitle", "pdFollowingSub"],
  lowAccuracy: ["pdLowAccTitle", "pdLowAccSub"],
  offRoute: ["pdOffRouteTitle", "pdOffRouteSub"],
  away: ["pdAwayTitle", "pdAwaySub"],
  paused: ["pdPausedTitle", "pdPausedSub"],
  interrupted: ["pdInterruptedTitle", "pdInterruptedSub"],
  denied: ["pdDeniedTitle", "pdDeniedSub"],
  unavailable: ["pdUnavailableTitle", "pdUnavailableSub"],
};

function gpsQuality(state) {
  if (!state.fix || state.fix.stale) return null;
  if (state.fix.acc <= ACC_GOOD_M) return "good";
  if (state.fix.acc <= ACC_POOR_M) return "fair";
  return "poor";
}

function readPref(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v == null ? fallback : v === "1";
  } catch {
    return fallback;
  }
}

export default function PracticeDrive({ lineFeatures, steps, routeLabel, centreName, centre, overall, onClose }) {
  const { t } = useLang();
  const [keepAwake, setKeepAwake] = useState(() => readPref(WAKE_PREF_KEY, true));
  const simSpeed = useMemo(() => simulationSpeed(), []);
  const { state, wake, start, pause, end } = usePracticeDrive(lineFeatures, keepAwake, simSpeed);
  const [setupOpen, setSetupOpen] = useState(true);
  const [follow, setFollow] = useState(true);
  const [fitRequest, setFitRequest] = useState(0);
  const [panelOpen, setPanelOpen] = useState(false);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const titleRef = useRef(null);
  const status = state.status;
  const active = ACTIVE.has(status);
  const wakeSupported = typeof navigator !== "undefined" && "wakeLock" in navigator;

  const routeData = useMemo(
    () => ({ type: "FeatureCollection", features: lineFeatures.flatMap(splitAtGaps) }),
    [lineFeatures]
  );
  const routeBounds = useMemo(() => {
    const b = L.geoJSON(routeData).getBounds();
    if (centre) b.extend(centre);
    return b;
  }, [routeData, centre]);

  useEffect(() => {
    titleRef.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  function exit() {
    end();
    onClose();
  }

  function onKeyDown(e) {
    if (e.key !== "Escape") return;
    if (confirmEnd) setConfirmEnd(false);
    else if (active) setConfirmEnd(true);
    else exit();
  }

  const [titleKey, subKey] = BANNER[status] || BANNER.preview;
  const quality = gpsQuality(state);
  const fix = state.fix;
  const puckVisible = fix && (status === "following" || status === "offRoute" || status === "away" || status === "joining" || status === "lowAccuracy" || status === "interrupted" || status === "paused");

  const primaryActions = (() => {
    switch (status) {
      case "preview":
        return (
          <button type="button" className="btn-primary pd-btn" onClick={() => setSetupOpen(true)}>
            {t("pdStartFollowing")}
          </button>
        );
      case "denied":
      case "unavailable":
        return (
          <button type="button" className="pd-btn" onClick={exit}>
            {t("pdStudyWithout")}
          </button>
        );
      case "paused":
        return (
          <>
            <button type="button" className="btn-primary pd-btn" onClick={start}>
              {t("pdResume")}
            </button>
            <button type="button" className="pd-btn" onClick={exit}>
              {t("pdEnd")}
            </button>
          </>
        );
      case "interrupted":
        return (
          <>
            <button type="button" className="btn-primary pd-btn" onClick={start}>
              {t("pdRecheck")}
            </button>
            <button type="button" className="pd-btn" onClick={exit}>
              {t("pdEnd")}
            </button>
          </>
        );
      case "noFix":
        return (
          <>
            <button type="button" className="btn-primary pd-btn" onClick={start}>
              {t("retry")}
            </button>
            <button type="button" className="pd-btn" onClick={exit}>
              {t("cancel")}
            </button>
          </>
        );
      case "acquiring":
        return (
          <button type="button" className="pd-btn" onClick={exit}>
            {t("cancel")}
          </button>
        );
      default:
        return (
          <>
            <button type="button" className="pd-btn" onClick={pause}>
              {t("pdPause")}
            </button>
            <button type="button" className="pd-btn pd-btn--end" onClick={() => setConfirmEnd(true)}>
              {t("pdEnd")}
            </button>
          </>
        );
    }
  })();

  return createPortal(
    <div className="pd" role="dialog" aria-modal="true" aria-labelledby="pd-title" onKeyDown={onKeyDown}>
      <header className={`pd-banner pd-banner--${status}`}>
        <div className="pd-banner__text" aria-live="polite">
          <h2 id="pd-title" ref={titleRef} tabIndex={-1}>
            {status === "following" ? t("pdFollowingTitle").replace("{route}", routeLabel) : t(titleKey)}
          </h2>
          <p>{t(subKey)}</p>
        </div>
        {!active && status !== "paused" && status !== "interrupted" && (
          <button type="button" className="pd-banner__close" onClick={exit} aria-label={t("pdClose")}>
            ✕
          </button>
        )}
      </header>

      <div className="pd-map">
        {centre ? (
          <MapContainer center={centre} zoom={13} className="pd-map__leaflet" zoomControl={false}>
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              className="ontario-tiles"
            />
            <GeoJSON data={routeData} style={casingStyle} interactive={false} />
            <GeoJSON data={routeData} style={(f) => routeLineStyle(f, null, false)} interactive={false} />
            <Marker position={centre} icon={makeCentreIcon()} interactive={false} keyboard={false} />
            {puckVisible && (
              <>
                <Circle
                  center={[fix.lonlat[1], fix.lonlat[0]]}
                  radius={Math.max(5, fix.acc)}
                  pathOptions={{
                    color: fix.stale ? "#8a97a3" : "#2467b6",
                    weight: 1,
                    fillColor: fix.stale ? "#8a97a3" : "#2467b6",
                    fillOpacity: 0.12,
                    dashArray: status === "lowAccuracy" ? "4 4" : null,
                  }}
                  interactive={false}
                />
                <Marker position={[fix.lonlat[1], fix.lonlat[0]]} icon={puckIcon} interactive={false} keyboard={false} opacity={fix.stale ? 0.45 : 1} />
              </>
            )}
            <Camera
              fix={fix}
              follow={follow}
              setFollow={setFollow}
              fitRequest={fitRequest}
              routeBounds={routeBounds}
              following={status === "following" || status === "offRoute" || status === "lowAccuracy"}
            />
          </MapContainer>
        ) : (
          <div className="map-canvas__fallback">
            <p>{t("mapUnavailable")}</p>
          </div>
        )}

        <div className="pd-controls">
          {active && fix && !follow && (
            <button type="button" className="pd-ctl" onClick={() => setFollow(true)}>
              {t("pdRecenter")}
            </button>
          )}
          <button
            type="button"
            className="pd-ctl"
            onClick={() => {
              setFollow(false);
              setFitRequest((n) => n + 1);
            }}
          >
            {t("fitRoute")}
          </button>
        </div>
      </div>

      <footer className="pd-panel">
        <div className="pd-panel__route">
          <p className="pd-panel__label">
            {routeLabel} · {centreName}
          </p>
          <div className="pd-panel__chips">
            {overall === "mixed" ? <StatusBadge status="inferred" size="sm">{t("statusMixed")}</StatusBadge> : <StatusBadge status={overall} size="sm" />}
            {quality && (
              <span className={`pd-gps pd-gps--${quality}`}>
                <span className="pd-gps__bars" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                {t(`pdGps_${quality}`)}
              </span>
            )}
            {simSpeed > 0 && <span className="pd-sim">{t("pdSimulated").replace("{x}", simSpeed)}</span>}
            {wake === "unavailable" && <span className="pd-note">{t("pdWakeUnavailable")}</span>}
            {wake === "released" && <span className="pd-note">{t("pdWakeReleased")}</span>}
          </div>
        </div>

        {confirmEnd ? (
          <div className="pd-confirm" role="alertdialog" aria-labelledby="pd-confirm-q">
            <p id="pd-confirm-q">{t("pdEndQuestion")}</p>
            <div className="pd-actions">
              <button type="button" className="btn-danger pd-btn" onClick={exit}>
                {t("pdEnd")}
              </button>
              <button type="button" className="pd-btn" onClick={() => setConfirmEnd(false)}>
                {t("pdKeepGoing")}
              </button>
            </div>
          </div>
        ) : (
          <div className="pd-actions">{primaryActions}</div>
        )}

        <button type="button" className="pd-panel__toggle" aria-expanded={panelOpen} aria-controls="pd-turns" onClick={() => setPanelOpen((o) => !o)}>
          {panelOpen ? t("pdHideTurns") : t("pdShowTurns")}
        </button>
        {panelOpen && (
          <div id="pd-turns" className="pd-turns">
            <p className="pd-turns__note">{t("pdTurnsManualNote")}</p>
            <ol>
              {steps.map((s, i) => (
                <li key={i} className={s.status === "inferred" ? "is-inferred" : undefined}>
                  <span className="pd-turns__n">{i + 1}</span>
                  <span>
                    {s.instruction}
                    {s.status === "inferred" && <StatusBadge status="inferred" size="sm" />}
                  </span>
                </li>
              ))}
            </ol>
            <p className="pd-turns__disclaimer">{t("footerDisclaimer")}</p>
          </div>
        )}
      </footer>

      {setupOpen && status === "preview" && (
        <div className="pd-setup" role="dialog" aria-modal="true" aria-labelledby="pd-setup-title">
          <div className="pd-setup__sheet">
            <h3 id="pd-setup-title">{t("pdSetupTitle")}</h3>
            <p>{t("pdSetupSafety")}</p>
            <p className="pd-setup__limits">{t("pdSetupLimits")}</p>
            {wakeSupported && (
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={keepAwake}
                  onChange={(e) => {
                    setKeepAwake(e.target.checked);
                    try {
                      localStorage.setItem(WAKE_PREF_KEY, e.target.checked ? "1" : "0");
                    } catch {
                      /* preference just won't persist */
                    }
                  }}
                />
                <span>
                  {t("pdKeepAwake")}
                  <span className="check-row__hint">{t("pdKeepAwakeHint")}</span>
                </span>
              </label>
            )}
            <p className="pd-setup__voice">{t("pdVoiceUnavailable")}</p>
            <div className="pd-actions">
              <button
                type="button"
                className="btn-primary pd-btn"
                onClick={() => {
                  setSetupOpen(false);
                  setFollow(true);
                  start();
                }}
              >
                {t("pdGotItStart")}
              </button>
              <button type="button" className="pd-btn" onClick={() => setSetupOpen(false)}>
                {t("pdJustLook")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>,
    document.body
  );
}
