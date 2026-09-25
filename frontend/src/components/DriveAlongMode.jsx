import { useEffect, useRef, useState } from "react";
import { Marker, Circle } from "react-leaflet";
import L from "leaflet";
import { useLang } from "../i18n.jsx";
import { formatDistance, cssVar } from "./MapView.jsx";
import { LiveDotIcon } from "../Icons.jsx";

// Notion "Frontend Design" backlog, Hard tier: "Live GPS drive-along
// practice mode." Honest scope limit, stated up front: this is
// FOREGROUND tracking only (the tab/screen must stay on) -- a real web
// app has no access to true background GPS the way a native app would
// (Page Visibility / battery-saving throttling pauses or slows
// watchPosition once the tab is backgrounded on every mobile browser
// tested against). Building a fake "keeps tracking in the background"
// claim would be worse than not having the feature; this says so in the
// UI instead (driveAlongDisclaimer).

// A route's real GPS accuracy is a few metres; a person walking away
// from an intersection to a nearby parking spot can drift further than
// that without having left the route. 60m matches roughly one
// residential block-width buffer -- wide enough to not nag at every
// stop sign, narrow enough to still mean something when it fires.
const OFF_ROUTE_M = 60;

function haversineM(a, b) {
  // a, b = [lon, lat]
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLon = toRad(b[0] - a[0]);
  const la1 = toRad(a[1]);
  const la2 = toRad(b[1]);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

// Projects point p onto segment [a,b] (all [lon, lat]). Flat-plane
// approximation, longitude scaled by cos(latitude) at the segment's own
// midpoint -- accurate enough at the scale of one drive-test route (a
// few km), the same tolerance the rest of this app already accepts for
// road-snapped geometry (see consensus_geometry.py's own haversine use).
function projectToSegment(p, a, b) {
  const lat0 = (a[1] + b[1]) / 2;
  const kx = Math.cos((lat0 * Math.PI) / 180) || 1;
  const ax = a[0] * kx, ay = a[1];
  const bx = b[0] * kx, by = b[1];
  const px = p[0] * kx, py = p[1];
  const dx = bx - ax, dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  let t = lenSq === 0 ? 0 : ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  const qx = ax + t * dx, qy = ay + t * dy;
  const point = [qx / kx, qy];
  return { point, distM: haversineM(p, point), t };
}

// One ordered path per line feature, each with cumulative distance --
// kept SEPARATE per feature rather than one flattened path, matching
// InstructionsTable's own established reasoning (see its comment: a
// route is grouped by originating route_line segment, never flattened
// into one fake-continuous list -- a lesson already learned once on
// this exact data shape).
export function buildFeaturePaths(lineFeatures) {
  return lineFeatures.map((f) => {
    const coords = f.geometry.coordinates;
    const cum = [0];
    for (let i = 1; i < coords.length; i++) {
      cum.push(cum[i - 1] + haversineM(coords[i - 1], coords[i]));
    }
    return { feature: f, coords, cum, total: cum[cum.length - 1] || 0 };
  });
}

// Snaps a live GPS point to the closest point across every feature's
// geometry. Returns which feature it's on, how far along that feature
// (metres), and how far the raw GPS point actually is from the road
// (metres) -- the off-route signal.
export function snapToRoute(paths, gpsPoint) {
  let best = null;
  paths.forEach((fp, featureIndex) => {
    for (let i = 1; i < fp.coords.length; i++) {
      const { point, distM, t } = projectToSegment(gpsPoint, fp.coords[i - 1], fp.coords[i]);
      if (!best || distM < best.deviationM) {
        const alongFeature = fp.cum[i - 1] + t * (fp.cum[i] - fp.cum[i - 1]);
        best = { featureIndex, alongFeature, deviationM: distM, point };
      }
    }
  });
  return best;
}

const liveIcon = L.divIcon({
  className: "",
  html:
    '<div style="width:16px;height:16px;border-radius:50%;background:var(--accent);' +
    'border:3px solid #fff;box-shadow:0 0 0 1px var(--accent),0 1px 4px rgba(0,0,0,.5);"></div>',
  iconSize: [16, 16],
  iconAnchor: [8, 8],
});

// Rendered as a child of the same <MapContainer> as the route itself --
// react-leaflet's Marker/Circle need the map's React context, which only
// exists inside that tree, so this can't live in the control panel below
// the map even though it's logically part of the same feature.
export function DriveAlongMarker({ live }) {
  if (!live?.position) return null;
  const { position, deviationM } = live;
  return (
    <>
      <Marker position={[position[1], position[0]]} icon={liveIcon} />
      {deviationM > OFF_ROUTE_M && (
        <Circle
          center={[position[1], position[0]]}
          radius={position[2] || 20}
          pathOptions={{ color: cssVar("--warning", "#956400"), fillOpacity: 0.12 }}
        />
      )}
    </>
  );
}

// Foreground-only live tracking + map-matching control panel, rendered
// outside the map (buttons, progress, current-segment instructions).
// Lifts the live GPS/snap result up via onUpdate so DriveAlongMarker
// (inside the map) can draw it -- the two halves share state through the
// parent (RoutePanel) because they can't be siblings in the same JSX
// subtree without breaking react-leaflet's context requirement.
export default function DriveAlongControls({ lineFeatures, onUpdate }) {
  const { t } = useLang();
  const [tracking, setTracking] = useState(false);
  const [live, setLive] = useState(null);
  const [error, setError] = useState(null);
  const watchIdRef = useRef(null);
  const wakeLockRef = useRef(null);
  const pathsRef = useRef([]);

  useEffect(() => {
    pathsRef.current = buildFeaturePaths(lineFeatures);
  }, [lineFeatures]);

  useEffect(() => {
    return () => stop(); // eslint-disable-line react-hooks/exhaustive-deps
  }, []);

  async function start() {
    setError(null);
    if (!("geolocation" in navigator)) {
      setError("unsupported");
      return;
    }
    setTracking(true);
    setLive({ position: null });

    // Best-effort: keeps the screen from sleeping mid-practice. Not
    // supported in every browser (notably not Safari as of this
    // writing) -- failing silently here is correct, the feature still
    // works, it's just easier for the screen to lock.
    try {
      wakeLockRef.current = await navigator.wakeLock?.request("screen");
    } catch {
      wakeLockRef.current = null;
    }

    watchIdRef.current = navigator.geolocation.watchPosition(
      (pos) => {
        const gps = [pos.coords.longitude, pos.coords.latitude];
        const snap = snapToRoute(pathsRef.current, gps);
        const next = {
          position: [gps[0], gps[1], pos.coords.accuracy],
          deviationM: snap ? snap.deviationM : null,
          featureIndex: snap ? snap.featureIndex : null,
          alongFeature: snap ? snap.alongFeature : null,
        };
        setLive(next);
        onUpdate?.(next);
      },
      () => setError("denied"),
      { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 }
    );
  }

  function stop() {
    if (watchIdRef.current != null) {
      navigator.geolocation.clearWatch(watchIdRef.current);
      watchIdRef.current = null;
    }
    wakeLockRef.current?.release?.().catch(() => {});
    wakeLockRef.current = null;
    setTracking(false);
    setLive(null);
    onUpdate?.(null);
  }

  const paths = pathsRef.current;
  const totalM = paths.reduce((a, p) => a + p.total, 0);
  const traveledM =
    live?.featureIndex != null
      ? paths.slice(0, live.featureIndex).reduce((a, p) => a + p.total, 0) + live.alongFeature
      : 0;
  const remainingM = totalM ? Math.max(0, totalM - traveledM) : null;
  const activeFeature = live?.featureIndex != null ? paths[live.featureIndex]?.feature : null;
  const percent = totalM ? Math.min(100, Math.round((traveledM / totalM) * 100)) : 0;

  return (
    <div className="no-print mobile-only" style={{ marginBottom: "var(--space-md)", fontSize: "0.875rem" }}>
      {!tracking ? (
        <button onClick={start} className="btn-primary">
          {t("startDriveAlong")}
        </button>
      ) : (
        <button onClick={stop}>
          <span className="live-dot" style={{ marginRight: 6, display: "inline-flex", color: "var(--confirmed)" }}>
            <LiveDotIcon />
          </span>
          {t("stopDriveAlong")}
        </button>
      )}

      {tracking && error === "denied" && <p className="error-banner" style={{ marginTop: "var(--space-sm)" }}>{t("driveAlongDenied")}</p>}
      {tracking && error === "unsupported" && (
        <p className="error-banner" style={{ marginTop: "var(--space-sm)" }}>{t("driveAlongUnsupported")}</p>
      )}

      {tracking && !error && (
        <div className="card anim-rise" style={{ marginTop: "var(--space-sm)" }}>
          <p style={{ color: "var(--ink-muted)", fontSize: "0.8125rem" }}>{t("driveAlongDisclaimer")}</p>
          {!live?.position ? (
            <p style={{ margin: 0 }}>
              <span className="spin" style={{ display: "inline-block", marginRight: 6 }}>
                ◌
              </span>
              {t("driveAlongLocating")}
            </p>
          ) : (
            <>
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${percent}%` }} />
              </div>
              {remainingM != null && (
                <p style={{ marginTop: "var(--space-sm)", marginBottom: 0 }}>
                  {t("driveAlongRemaining").replace(
                    "{dist}",
                    formatDistance(Math.round(remainingM))
                  )}
                </p>
              )}
              {live.deviationM > OFF_ROUTE_M && (
                <p className="fade-in" style={{ marginTop: "var(--space-sm)", marginBottom: 0 }}>
                  <span className="trust-badge trust-badge--warning">
                    {t("driveAlongOffRoute").replace(
                      "{dist}",
                      formatDistance(Math.round(live.deviationM))
                    )}
                  </span>
                </p>
              )}
              {activeFeature?.properties?.steps?.length > 0 && (
                <div style={{ marginTop: "var(--space-md)" }}>
                  <p style={{ fontWeight: 600, margin: "0 0 var(--space-xs)" }}>{t("driveAlongOnSegment")}</p>
                  <ul style={{ margin: 0, paddingLeft: 18, maxHeight: 120, overflowY: "auto" }}>
                    {activeFeature.properties.steps.map((s, i) => (
                      <li key={i}>{s.instruction}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
