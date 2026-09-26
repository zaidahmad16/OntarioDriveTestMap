import { useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from "react";
import { MapContainer, TileLayer, GeoJSON, Marker, Tooltip, Pane, useMap } from "react-leaflet";
import L from "leaflet";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";
import { formatTrafficControl, formatSpeedLimit, inferManeuver } from "../format.js";
import LoadingScreen from "../LoadingScreen.jsx";
import { useSeo, breadcrumbList } from "../seo.js";
import { StatusBadge, RouteLegend, LineSample } from "../RouteNotation.jsx";
import { buildTrack, PlaybackLayer, PlaybackBar } from "./RoutePlayback.jsx";
import { useSignIn } from "../SignIn.jsx";
import {
  InfoIcon,
  ChevronDownIcon,
  LayersIcon,
  TurnLeftIcon,
  TurnRightIcon,
  StraightIcon,
  MergeIcon,
  RoundaboutIcon,
  DestinationIcon,
} from "../Icons.jsx";

// Lazy: practice drive is mobile-only and opt-in -- a visitor who never
// starts one never downloads its code.
const PracticeDrive = lazy(() => import("./PracticeDrive.jsx"));

// Icons only where the instruction's own wording names the maneuver
// (inferManeuver reads the text) -- never a guessed direction.
const MANEUVER_ICONS = {
  left: TurnLeftIcon,
  right: TurnRightIcon,
  straight: StraightIcon,
  merge: MergeIcon,
  roundabout: RoundaboutIcon,
  destination: DestinationIcon,
};

// Real DriveTest centre coordinates, from osm.db's centres table (the
// OSM way for the actual DriveTest building/lot).
export const CENTRE_COORDS = {
  walkley: [45.376145807017544, -75.64758859649123],
  canotek: [45.4528876, -75.5883806],
  smithsfalls: [44.8827581, -76.0150536],
  winchester: [45.0851023, -75.3712133],
};

// Map colours come from the live CSS tokens so a token edit in App.css
// can never silently desync the map from the badges and legend.
export function cssVar(name, fallback) {
  if (typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export function makeCentreIcon() {
  return L.divIcon({
    className: "centre-pin",
    html: '<span class="centre-pin__dot"></span>',
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

// ------------------------------------------------------------------
// Route status: one typed mapping from backend fields to the product's
// notation. Never derived from CSS or from the old red/blue visuals.
//   predicted          -> inferred (road-snapped guess between real points)
//   below_threshold    -> inferred (real evidence, weaker than the
//                         published threshold -- the centre index already
//                         counts these outside "confirmed")
//   neither            -> confirmed
//   client-split gap   -> gap (straight beeline, not a road)
// ------------------------------------------------------------------
export function lineStatus(props) {
  if (props.kind === "route_gap") return "gap";
  if (props.predicted || props.below_threshold) return "inferred";
  if (props.predicted === false && props.below_threshold === false) return "confirmed";
  return "unknown";
}

// OSRM returns a straight beeline when two consecutive junctions sit on
// disconnected pieces of the road graph. Drawn as confirmed road it
// would assert a road that doesn't exist, so split it out as a `gap`.
// Threshold sits above the largest real sparse-road stretch (~383 m).
const GAP_THRESHOLD_M = 400;

function haversine(a, b) {
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLon = toRad(b[0] - a[0]);
  const s =
    Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

export function splitAtGaps(feature) {
  if (feature.properties.kind !== "route_line" || feature.geometry.type !== "LineString") return [feature];
  const coords = feature.geometry.coordinates;
  const pieces = [[]];
  const gaps = [];
  for (let i = 0; i < coords.length; i++) {
    if (i > 0) {
      const d = haversine(coords[i - 1], coords[i]);
      if (d > GAP_THRESHOLD_M) {
        gaps.push({ from: coords[i - 1], to: coords[i], dist: d });
        pieces.push([]);
      }
    }
    pieces[pieces.length - 1].push(coords[i]);
  }
  if (!gaps.length) return [feature];
  const out = pieces
    .filter((pc) => pc.length >= 2)
    .map((pc) => ({ ...feature, geometry: { type: "LineString", coordinates: pc } }));
  gaps.forEach((g) => {
    out.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: [g.from, g.to] },
      properties: { ...feature.properties, kind: "route_gap", gap_m: Math.round(g.dist) },
    });
  });
  return out;
}

// A route line that draws nothing: < 2 points or under ~5 m long.
export function isDegenerateRouteLine(feature) {
  if (feature.properties.kind !== "route_line") return false;
  const c = feature.geometry?.coordinates || [];
  if (c.length < 2) return true;
  let len = 0;
  for (let i = 1; i < c.length; i++) len += haversine(c[i - 1], c[i]);
  return len < 5;
}

// Selected route styling (spec §5.3). Status decides colour AND dash;
// selection only changes weight/opacity, never status notation.
export function routeLineStyle(feature, selectedLineId, linkable) {
  const p = feature.properties;
  const status = lineStatus(p);
  const dimmed = linkable && selectedLineId != null && p.route_line_id !== selectedLineId;
  const emphasised = linkable && selectedLineId != null && p.route_line_id === selectedLineId;
  const base = { opacity: dimmed ? 0.4 : 1, lineCap: "round", lineJoin: "round" };
  if (status === "gap") {
    return { ...base, color: cssVar("--neutral-line", "#6a7a86"), weight: 3, dashArray: "2 8", lineCap: "butt" };
  }
  if (status === "inferred") {
    return { ...base, color: cssVar("--inferred", "#97531b"), weight: emphasised ? 7 : 5, dashArray: "10 9", lineCap: "butt" };
  }
  if (status === "confirmed") {
    return { ...base, color: cssVar("--brand", "#0b756b"), weight: emphasised ? 7 : 5 };
  }
  return { ...base, color: cssVar("--neutral-line", "#6a7a86"), weight: 3 };
}

// Pale casing under every drawn section so the route stays legible over
// busy streets without recolouring the third-party tiles themselves.
export function casingStyle(feature) {
  const status = lineStatus(feature.properties);
  return {
    color: "#ffffff",
    weight: status === "gap" ? 6 : 10,
    opacity: status === "gap" ? 0.6 : 0.85,
    lineCap: "round",
    lineJoin: "round",
    interactive: false,
  };
}

function weightColor(w) {
  const c = Math.max(0, Math.min(1, w));
  return `hsl(${170 - (1 - c) * 130}, 55%, ${36 + (1 - c) * 12}%)`;
}

// Evidence points (spec §5.3): off by default, small quiet circles when
// on. A point is one scored junction observation -- not a turn, not a
// route stop, not an independently verified claim.
function makePointToLayer(colourBySupport) {
  return function pointToLayer(feature, latlng) {
    const authors = feature.properties.authors || 1;
    const w = feature.properties.weight ?? 1;
    const colour = colourBySupport ? weightColor(w) : cssVar("--ink-soft", "#536675");
    return L.circleMarker(latlng, {
      pane: "evidence",
      radius: 3 + Math.min(authors, 4),
      fillColor: colour,
      color: "#ffffff",
      weight: 1.5,
      fillOpacity: colourBySupport ? 0.85 : Math.max(0.35, Math.min(0.8, 0.35 + w * 0.4)),
    });
  };
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function makeOnEachFeature(t) {
  return function onEachFeature(feature, layer) {
    const p = feature.properties;
    if (p.kind === "segment") {
      layer.bindPopup(
        `<p class="map-popup__title">${escapeHtml(p.streets.join(" × "))}</p>` +
          `<p class="map-popup__body">${escapeHtml(
            t("evidencePointPopup")
              .replace("{authors}", p.authors)
              .replace("{video}", p.video_count)
              .replace("{text}", p.text_count)
          )}</p>` +
          (p.last_seen ? `<p class="map-popup__meta">${escapeHtml(t("lastSeen"))}: ${escapeHtml(p.last_seen)}</p>` : "")
      );
      return;
    }
    const status = lineStatus(p);
    if (status === "gap") {
      layer.bindPopup(
        `<p class="map-popup__title">${escapeHtml(t("statusGap"))}</p>` +
          `<p class="map-popup__body">${escapeHtml(t("gapPopup").replace("{m}", p.gap_m))}</p>`
      );
    } else if (status === "inferred") {
      layer.bindPopup(
        `<p class="map-popup__title">${escapeHtml(t("statusInferred"))}</p>` +
          `<p class="map-popup__body">${escapeHtml(p.predicted ? t("inferredPopup") : t("lowSupportPopup"))}</p>`
      );
    } else if (status === "confirmed") {
      layer.bindPopup(
        `<p class="map-popup__title">${escapeHtml(t("statusConfirmed"))}</p>` +
          `<p class="map-popup__body">${escapeHtml(
            p.source === "manual_youtube"
              ? t("provenanceVideo")
              : t("provenanceTraces").replace("{traces}", p.trace_count).replace("{authors}", p.authors)
          )}</p>`
      );
    }
  };
}

// Auto-fit ONLY when the route (or centre) changes -- never on a turn
// click, a layer toggle or an unrelated rerender (spec §5.3). The fit
// function is also exposed to the "Fit route" control.
function MapController({ frameKey, frameBounds, fitRef, focusBounds }) {
  const map = useMap();
  const fit = useCallback(() => {
    if (!frameBounds || !frameBounds.isValid()) return;
    map.invalidateSize();
    map.fitBounds(frameBounds, { padding: [36, 36] });
  }, [map, frameBounds]);

  useEffect(() => {
    fitRef.current = fit;
  }, [fit, fitRef]);

  useEffect(() => {
    fit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frameKey]);

  // Keep Leaflet's cached size honest when the rail/page reflows.
  useEffect(() => {
    const el = map.getContainer();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(el);
    return () => ro.disconnect();
  }, [map]);

  // Selecting a section pans only if it's actually off-screen.
  useEffect(() => {
    if (!focusBounds || !focusBounds.isValid()) return;
    if (!map.getBounds().contains(focusBounds)) {
      map.fitBounds(focusBounds, { padding: [48, 48], maxZoom: map.getZoom() });
    }
  }, [focusBounds, map]);

  return null;
}

// ------------------------------------------------------------------
// Distance / duration formatting (locale-aware, never synthesized)
// ------------------------------------------------------------------
function currentLang() {
  if (typeof document === "undefined") return "en";
  return document.documentElement.lang === "fr" ? "fr-CA" : "en-CA";
}

export function formatDistance(m, locale = currentLang()) {
  if (m == null) return "";
  if (m < 1000) return `${new Intl.NumberFormat(locale).format(Math.round(m))} m`;
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1, minimumFractionDigits: 1 }).format(m / 1000)} km`;
}

function formatDuration(s) {
  if (s == null) return "";
  if (s < 60) return `${Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}

// A real "unknown" stays null instead of becoming a displayed "0 m".
function sumOrNull(values) {
  const known = values.filter((v) => v != null);
  if (!known.length) return null;
  return known.reduce((a, v) => a + v, 0);
}

// manual_youtube transcript lines repeat the same real leg's
// distance/duration on every line covering it -- count each leg once.
function sumUniqueLegDurationS(rows) {
  let total = 0;
  let lastKey = null;
  for (const r of rows) {
    if (!r.duration_s) continue;
    const key = `${r.distance_m}|${r.duration_s}`;
    if (key !== lastKey) total += r.duration_s;
    lastKey = key;
  }
  return total;
}

// ------------------------------------------------------------------
// Turn feed (spec §5.4)
// ------------------------------------------------------------------
function ReportTurn({ routeLineId, stepOrder, number }) {
  const { t } = useLang();
  const { requireSignIn } = useSignIn();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [state, setState] = useState("idle"); // idle | sending | sent | failed
  const inputRef = useRef(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  if (state === "sent")
    return (
      <p className="turn__reported" role="status">
        {t("reportErrorSent")}
      </p>
    );
  if (!open)
    return (
      <button
        type="button"
        className="turn__report link-btn"
        aria-label={t("reportTurnAria").replace("{n}", number)}
        onClick={() => requireSignIn("report", () => setOpen(true))}
      >
        {t("reportIssue")}
      </button>
    );
  return (
    <form
      className="turn__report-form"
      onSubmit={(e) => {
        e.preventDefault();
        setState("sending");
        api
          .reportError(routeLineId, stepOrder, note.trim() || null)
          .then(() => setState("sent"))
          .catch(() => setState("failed"));
      }}
    >
      <label htmlFor={`report-${routeLineId}-${stepOrder}`}>{t("reportTurnLabel").replace("{n}", number)}</label>
      <input
        ref={inputRef}
        id={`report-${routeLineId}-${stepOrder}`}
        value={note}
        maxLength={500}
        onChange={(e) => setNote(e.target.value)}
        placeholder={t("reportErrorPrompt")}
      />
      <div className="turn__report-actions">
        <button type="submit" className="btn-primary" disabled={state === "sending"}>
          {state === "sending" ? t("sending") : t("submit")}
        </button>
        <button type="button" onClick={() => setOpen(false)}>
          {t("cancel")}
        </button>
      </div>
      {state === "failed" && (
        <p className="field-error" role="alert">
          {t("reportFailed")}
        </p>
      )}
    </form>
  );
}

function TurnRow({ step, number, showStatus, selectable, selected, onSelect }) {
  const { t } = useLang();
  const maneuver = inferManeuver(step.instruction);
  const Icon = maneuver && MANEUVER_ICONS[maneuver];
  const junction = formatTrafficControl(step.traffic_control);
  const speed = formatSpeedLimit(step.speed_limit);
  const meta = [];
  if (step.distance_m) meta.push(["distance", formatDistance(step.distance_m)]);
  if (step.duration_s) meta.push(["time", formatDuration(step.duration_s)]);
  if (junction) meta.push(["control", junction]);
  if (speed) meta.push(["speed", speed.replace(" km/h", " km/h")]);

  const body = (
    <>
      <span
        className={`turn__num${step.anchor ? " turn__num--mapped" : ""}`}
        aria-hidden="true"
        title={step.anchor ? undefined : t("turnNotMapped")}
      >
        {number}
      </span>
      <span className="turn__icon" aria-hidden="true">
        {Icon && <Icon />}
      </span>
      <span className="turn__text">
        <span className="turn__instruction">
          <span className="visually-hidden">{t("turnN").replace("{n}", number)}: </span>
          {step.instruction}
        </span>
        {(meta.length > 0 || showStatus) && (
          <span className="turn__meta">
            {showStatus && <StatusBadge status={step.status} size="sm" />}
            {meta.map(([k, v]) => (
              <span key={k} className="turn__fact">
                <span className="visually-hidden">{t(`fact_${k}`)}: </span>
                <span className={k === "distance" || k === "time" || k === "speed" ? "data" : undefined}>{v}</span>
              </span>
            ))}
          </span>
        )}
      </span>
    </>
  );

  return (
    <li
      id={`turn-row-${number}`}
      className={`turn${selected ? " turn--selected" : ""}${step.status === "inferred" ? " turn--inferred" : ""}`}
    >
      {selectable ? (
        <button
          type="button"
          className="turn__main"
          aria-pressed={selected}
          aria-describedby={step.anchor ? "turn-map-note" : undefined}
          onClick={onSelect}
        >
          {body}
        </button>
      ) : (
        <div className="turn__main">{body}</div>
      )}
      {step.routeLineId != null && (
        <div className="turn__aside no-print">
          <ReportTurn routeLineId={step.routeLineId} stepOrder={step.step_order} number={number} />
        </div>
      )}
    </li>
  );
}

function TurnFeed({ steps, mixed, linkable, selectedStep, onSelectStep }) {
  const { t, tn } = useLang();
  const mapped = steps.filter((s) => s.anchor).length;
  if (!steps.length) {
    return (
      <section className="route-section" id="turns" aria-labelledby="turns-title">
        <h3 id="turns-title" className="route-section__title">{t("turns")}</h3>
        <p className="state-panel">{t("noTurnData")}</p>
      </section>
    );
  }
  return (
    <section className="route-section" id="turns" aria-labelledby="turns-title">
      <div className="route-section__head">
        <h3 id="turns-title" className="route-section__title">{t("turns")}</h3>
        <span className="route-section__meta">{tn("turnsN", steps.length).replace("{n}", steps.length)}</span>
      </div>
      {mapped > 0 ? (
        <p className="route-section__hint" id="turn-map-note">
          <span className="turn-pin-sample" aria-hidden="true">
            1
          </span>{" "}
          {t("turnsMappedNote").replace("{n}", mapped).replace("{total}", steps.length)}
        </p>
      ) : (
        <p className="route-section__hint">{t("turnsNoneMapped")}</p>
      )}
      <ol className="turn-list">
        {steps.map((s, i) => (
          <TurnRow
            key={`${s.routeLineId}-${s.step_order}-${i}`}
            step={s}
            number={i + 1}
            showStatus={mixed}
            selectable={!!s.anchor || (linkable && s.routeLineId != null)}
            selected={selectedStep === i}
            onSelect={() => onSelectStep(i)}
          />
        ))}
      </ol>
    </section>
  );
}

// ------------------------------------------------------------------
// Routes: one selectable route per (test_class, family)
// ------------------------------------------------------------------
export function buildRoutes(geojson) {
  const byKey = new Map();
  for (const f of geojson.features) {
    if (f.properties.kind !== "route_line") continue;
    if (isDegenerateRouteLine(f)) continue;
    const cls = f.properties.test_class || "unknown";
    const fam = f.properties.family;
    const key = `${cls}|${fam}`;
    if (!byKey.has(key)) byKey.set(key, { key, cls, fam, features: [], traces: 0, authors: 0, manualVerified: false });
    const r = byKey.get(key);
    r.features.push(f);
    r.traces = Math.max(r.traces, f.properties.trace_count || 0);
    r.authors = Math.max(r.authors, f.properties.authors || 0);
    if (f.properties.source === "manual_youtube") r.manualVerified = true;
  }
  return [...byKey.values()];
}

// A route with an undetermined class is real geometry -- listed under
// both classes rather than hidden. Most-corroborated first.
export function routesForClass(routes, cls) {
  return routes
    .filter((r) => r.cls === cls || r.cls === "unknown")
    .sort((a, b) => b.traces - a.traces || a.fam - b.fam);
}

function routeDistance(route) {
  return sumOrNull(route.features.map((f) => f.properties.distance_m));
}

// Section composition, never a single route-wide claim (spec §5.2).
function composition(lineFeatures, gapCount) {
  const counts = { confirmed: 0, inferred: 0, unknown: 0 };
  lineFeatures.forEach((f) => {
    const s = lineStatus(f.properties);
    counts[s] = (counts[s] || 0) + 1;
  });
  let overall = "unknown";
  if (counts.confirmed && !counts.inferred && !counts.unknown) overall = "confirmed";
  else if (counts.inferred && !counts.confirmed && !counts.unknown) overall = "inferred";
  else if (counts.confirmed || counts.inferred) overall = "mixed";
  return { ...counts, gaps: gapCount, overall };
}

function downloadGpx(route, lineFeatures, centreId) {
  const points = lineFeatures.flatMap((f) => f.geometry.coordinates);
  const trkpts = points.map(([lon, lat]) => `<trkpt lat="${lat}" lon="${lon}"></trkpt>`).join("\n      ");
  const gpx =
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<gpx version="1.1" creator="OntarioDriveTestMap">\n` +
    `  <trk><name>${centreId}-${route.cls}-${route.fam}</name><trkseg>\n      ${trkpts}\n` +
    `  </trkseg></trk>\n</gpx>\n`;
  const blob = new Blob([gpx], { type: "application/gpx+xml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${centreId}-${route.cls}-route${route.fam}.gpx`;
  a.click();
  URL.revokeObjectURL(url);
}

function Popover({ label, icon, className, children, align = "start" }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const btnRef = useRef(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => rootRef.current && !rootRef.current.contains(e.target) && setOpen(false);
    const onKey = (e) => {
      if (e.key === "Escape") {
        setOpen(false);
        btnRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className={`mini-popover ${className || ""}`} ref={rootRef}>
      <button ref={btnRef} type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {icon}
        <span className="mini-popover__label">{label}</span>
        <ChevronDownIcon />
      </button>
      {open && (
        <div className={`popover-panel mini-popover__panel mini-popover__panel--${align}`}>
          {typeof children === "function" ? children(() => setOpen(false)) : children}
        </div>
      )}
    </div>
  );
}

function RouteSummary({ route, routeLabel, comp, totalDist, totalDur, lineFeatures, centreId, classFilter }) {
  const { t, tn } = useLang();
  const { requireSignIn } = useSignIn();
  const worst = lineFeatures
    .filter((f) => f.properties.difficulty_score != null)
    .reduce((a, b) => (!a || b.properties.difficulty_score > a.properties.difficulty_score ? b : a), null);

  return (
    <div className="route-summary">
      <div className="route-summary__head">
        <h2 className="route-summary__title">{routeLabel}</h2>
        {comp.overall === "mixed" ? (
          <StatusBadge status="inferred">{t("statusMixed")}</StatusBadge>
        ) : (
          <StatusBadge status={comp.overall} />
        )}
      </div>

      <p className="route-summary__facts">
        {totalDist != null && <span className="data">{formatDistance(totalDist)}</span>}
        {totalDur > 0 && (
          <span>
            {t("aboutMinutes").replace("{n}", Math.max(1, Math.round(totalDur / 60)))}
            <span className="route-summary__approx"> ({t("estimated")})</span>
          </span>
        )}
      </p>

      <p className="route-summary__provenance">
        {route.manualVerified
          ? t("provenanceVideo")
          : t("provenanceTraces").replace("{traces}", route.traces).replace("{authors}", route.authors)}
      </p>

      {(comp.inferred > 0 || comp.gaps > 0) && (
        <ul className="route-summary__composition">
          {comp.confirmed > 0 && (
            <li>
              <LineSample status="confirmed" width={24} /> {tn("confirmedSectionsN", comp.confirmed).replace("{n}", comp.confirmed)}
            </li>
          )}
          {comp.inferred > 0 && (
            <li>
              <LineSample status="inferred" width={24} /> {tn("inferredSectionsCountN", comp.inferred).replace("{n}", comp.inferred)}
            </li>
          )}
          {comp.gaps > 0 && (
            <li>
              <LineSample status="gap" width={24} /> {tn("gapsN", comp.gaps).replace("{n}", comp.gaps)}
            </li>
          )}
        </ul>
      )}
      {comp.overall === "confirmed" && <p className="route-summary__note">{t("studyAidNote")}</p>}

      <div className="route-summary__actions no-print">
        <a className="btn" href="#evidence">
          {t("viewEvidence")}
        </a>
        <a
          className="btn"
          href={`/discussion.html?centre=${encodeURIComponent(centreId)}&route=${encodeURIComponent(
            lineFeatures[0]?.properties.route_line_id || ""
          )}&type=${encodeURIComponent(classFilter)}&compose=1`}
          onClick={(e) => {
            // Posting needs an account: ask here, with the route context
            // kept in the link, then continue to the composer.
            const href = e.currentTarget.href;
            e.preventDefault();
            requireSignIn("discuss", () => window.location.assign(href));
          }}
        >
          {t("discussRoute")}
        </a>
        <Popover label={t("exportLabel")} align="end">
          {(close) => (
            <div role="menu">
              <button
                type="button"
                role="menuitem"
                className="popover-row"
                onClick={() => {
                  downloadGpx(route, lineFeatures, centreId);
                  close();
                }}
              >
                {t("exportGpx")}
              </button>
              <button
                type="button"
                role="menuitem"
                className="popover-row"
                onClick={() => {
                  close();
                  window.print();
                }}
              >
                {t("exportPdf")}
              </button>
            </div>
          )}
        </Popover>
      </div>

      <details className="disclosure">
        <summary>
          <InfoIcon /> {t("aboutThisRoute")} <ChevronDownIcon className="disclosure__chevron" />
        </summary>
        <p>{t("routeReconstructedNote")}</p>
        <p>{t("confidenceExplainerShort")}</p>
        {worst && (
          <p>
            <strong>{t("difficultyEstimate")}:</strong> {t(`difficulty_${worst.properties.difficulty_label}`)}.{" "}
            {t("difficultyDefinition")}
          </p>
        )}
      </details>
    </div>
  );
}

function RoutePicker({ routes, classRoutes, classFilter, routeKey, onClass, onRoute }) {
  const { t } = useLang();
  const idx = classRoutes.findIndex((r) => r.key === routeKey);
  return (
    <div className="route-picker">
      <div className="route-picker__class" role="group" aria-label={t("testClass")}>
        {["G", "G2"].map((c) => {
          const n = routesForClass(routes, c).length;
          return (
            <button key={c} type="button" onClick={() => onClass(c)} disabled={!n} aria-pressed={classFilter === c}>
              {c}
              <span className="visually-hidden"> — {n ? t("routesCount").replace("{n}", n) : t("noRoutesForClass")}</span>
            </button>
          );
        })}
      </div>
      {classRoutes.length > 0 && (
        <div className="route-picker__routes">
          <p className="route-picker__label" id="route-picker-label">
            {t("routeXofN").replace("{x}", idx + 1).replace("{n}", classRoutes.length)}
          </p>
          <div className="route-picker__options" role="group" aria-labelledby="route-picker-label">
            {classRoutes.map((r, i) => {
              const d = routeDistance(r);
              return (
                <button
                  key={r.key}
                  type="button"
                  aria-pressed={r.key === routeKey}
                  onClick={() => onRoute(r.key)}
                  className="route-option"
                >
                  <span className="route-option__name">
                    {t("route")} {i + 1}
                  </span>
                  {d != null && <span className="route-option__meta data">{formatDistance(d)}</span>}
                  {r.cls === "unknown" && <span className="route-option__meta">{t("classUnknown")}</span>}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function MapLayersMenu({ showEvidence, setShowEvidence, colourBySupport, setColourBySupport, evidenceCount }) {
  const { t } = useLang();
  return (
    <Popover label={t("layers")} icon={<LayersIcon />} className="map-tool" align="end">
      <fieldset className="layers-menu">
        <legend>{t("mapLayers")}</legend>
        <label className="check-row">
          <input type="checkbox" checked={showEvidence} onChange={(e) => setShowEvidence(e.target.checked)} disabled={!evidenceCount} />
          <span>
            {t("evidencePoints")}
            <span className="check-row__hint">{t("evidencePointsHint").replace("{n}", evidenceCount)}</span>
          </span>
        </label>
        <label className="check-row check-row--nested">
          <input
            type="checkbox"
            checked={colourBySupport}
            disabled={!showEvidence}
            onChange={(e) => setColourBySupport(e.target.checked)}
          />
          <span>
            {t("colourBySupport")}
            <span className="check-row__hint">{t("colourBySupportHint")}</span>
          </span>
        </label>
      </fieldset>
    </Popover>
  );
}

// Bold numbered points for turns matched to a real junction ON the route
// (backend turn_anchors.py). Turns that share a junction share one pin
// ("7, 10"). Unmatched turns get no pin -- never a guessed position.
function TurnPins({ steps, selectedStep, onSelect }) {
  const { t } = useLang();
  const groups = useMemo(() => {
    const out = [];
    steps.forEach((s, i) => {
      if (!s.anchor) return;
      const g = out.find((x) => haversine([x.lon, x.lat], [s.anchor.lon, s.anchor.lat]) < 25);
      if (g) g.idx.push(i);
      else out.push({ lat: s.anchor.lat, lon: s.anchor.lon, idx: [i] });
    });
    return out;
  }, [steps]);

  return groups.map((g) => {
    const nums = g.idx.map((i) => i + 1);
    const label = nums.length > 3 ? `${nums.slice(0, 2).join(", ")}…` : nums.join(", ");
    const selected = g.idx.includes(selectedStep);
    const icon = L.divIcon({
      className: `turn-pin${selected ? " turn-pin--selected" : ""}${nums.length > 1 ? " turn-pin--multi" : ""}`,
      html: `<span>${label}</span>`,
      iconSize: null,
    });
    return (
      <Marker
        key={g.idx.join("-") + (selected ? "-s" : "")}
        position={[g.lat, g.lon]}
        icon={icon}
        zIndexOffset={selected ? 900 : 500}
        keyboard={false}
        title={nums.map((n) => `${t("turnN").replace("{n}", n)}: ${steps[n - 1].instruction}`).join(" · ")}
        eventHandlers={{ click: () => onSelect(g.idx[0]) }}
      />
    );
  });
}

function RouteWorkspace({ centreId, centreName, classFilter, geojson, route, routeIndex, routes, classRoutes, onClass, onRoute }) {
  const { t } = useLang();
  const [showEvidence, setShowEvidence] = useState(false);
  const [colourBySupport, setColourBySupport] = useState(false);
  const [practiceOpen, setPracticeOpen] = useState(false);
  const [selectedLineId, setSelectedLineId] = useState(null);
  const [selectedStep, setSelectedStep] = useState(null);
  const [tileError, setTileError] = useState(false);
  const [tileKey, setTileKey] = useState(0);
  const fitRef = useRef(null);
  const center = CENTRE_COORDS[centreId] || null;

  // Route playback (a dot travelling the drawn route). Reset per route.
  const [playing, setPlaying] = useState(false);
  const [playDist, setPlayDist] = useState(0);
  const [playActive, setPlayActive] = useState(false);
  const [playSpeed, setPlaySpeed] = useState(1);
  const playPosRef = useRef(0);

  useEffect(() => {
    setSelectedLineId(null);
    setSelectedStep(null);
    setPlaying(false);
    setPlayDist(0);
    playPosRef.current = 0;
    setPlayActive(false);
    setPracticeOpen(false);
  }, [route]);

  const { lineFeatures, routeData, evidenceData, comp, steps, totalDur, totalDist, frameBounds, statusesShown, track } = useMemo(() => {
    const lineFeatures = route ? route.features.filter((f) => !isDegenerateRouteLine(f)) : [];
    const drawn = lineFeatures.flatMap(splitAtGaps);
    const routeData = { type: "FeatureCollection", features: drawn };
    const evidenceData = {
      type: "FeatureCollection",
      features: geojson.features.filter((f) => f.properties.kind === "segment"),
    };
    const gapCount = drawn.filter((f) => f.properties.kind === "route_gap").length;
    const comp = composition(lineFeatures, gapCount);

    const steps = [];
    lineFeatures.forEach((f) => {
      (f.properties.steps || []).forEach((s) =>
        steps.push({ ...s, status: lineStatus(f.properties), routeLineId: f.properties.route_line_id })
      );
    });
    // A mid-route "Arrive at destination" is where one collected piece
    // ended, not the end of the drive.
    const cleanSteps = steps.filter((r, i) => r.instruction !== "Arrive at destination" || i === steps.length - 1);

    const totalDur = route && route.manualVerified
      ? sumUniqueLegDurationS(cleanSteps)
      : cleanSteps.reduce((a, s) => a + (s.duration_s || 0), 0);
    const totalDist = route && route.manualVerified
      ? sumOrNull(lineFeatures.map((f) => f.properties.distance_m))
      : sumOrNull(cleanSteps.map((s) => s.distance_m));

    // Frame on the route (+ centre); only a centre with no route at all
    // falls back to its evidence points so there's something to see.
    const framing = drawn.length ? drawn : evidenceData.features;
    let frameBounds = framing.length ? L.geoJSON({ type: "FeatureCollection", features: framing }).getBounds() : null;
    if (frameBounds && center) frameBounds.extend(center);
    if (!frameBounds && center) frameBounds = L.latLngBounds([center, center]);

    const statusesShown = ["confirmed", "inferred", "gap", "unknown"].filter((s) =>
      drawn.some((f) => lineStatus(f.properties) === s)
    );
    const track = buildTrack(lineFeatures);
    return { lineFeatures, routeData, evidenceData, comp, steps: cleanSteps, totalDur, totalDist, frameBounds, statusesShown, track };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route, geojson]);

  // Turn <-> map linking only means something when a route is made of
  // several distinct sections; with one section every turn would light
  // up the same whole line (spec: never invent correspondences).
  const linkable = lineFeatures.length > 1;
  const focusBounds = useMemo(() => {
    const a = selectedStep != null ? steps[selectedStep]?.anchor : null;
    if (a) return L.latLngBounds([a.lat, a.lon], [a.lat, a.lon]);
    if (!linkable || selectedLineId == null) return null;
    const f = lineFeatures.find((x) => x.properties.route_line_id === selectedLineId);
    return f ? L.geoJSON(f).getBounds() : null;
  }, [linkable, selectedLineId, lineFeatures, selectedStep, steps]);

  function selectStep(i, fromMap) {
    const next = selectedStep === i && !fromMap ? null : i;
    setSelectedStep(next);
    setSelectedLineId(next != null && linkable ? steps[next]?.routeLineId ?? null : null);
    if (fromMap && next != null) {
      document.getElementById(`turn-row-${next + 1}`)?.scrollIntoView({
        block: "nearest",
        behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      });
    }
  }

  const routeLabel = route ? `${classFilter} ${t("route")} ${routeIndex + 1}` : t("noRouteTitle");
  const onEachFeature = useMemo(() => makeOnEachFeature(t), [t]);

  useSeo(
    route
      ? {
          title: `${centreName} — ${routeLabel} — OntarioDriveTestMap`,
          description: `Study ${centreName} ${routeLabel}: turn-by-turn directions with confirmed and inferred sections labelled, and the evidence behind them.`,
          path: `/?centre=${encodeURIComponent(centreId)}`,
          breadcrumbJsonLd: breadcrumbList([
            { name: t("centres"), path: "/" },
            { name: centreName, path: `/?centre=${encodeURIComponent(centreId)}` },
            { name: routeLabel },
          ]),
        }
      : undefined
  );

  return (
    <section className="route-workspace" aria-label={t("routeWorkspace")}>
      <div className="route-workspace__picker">
        <RoutePicker
          routes={routes}
          classRoutes={classRoutes}
          classFilter={classFilter}
          routeKey={route?.key}
          onClass={onClass}
          onRoute={onRoute}
        />
      </div>

      <div className="route-workspace__summary">
        {route ? (
          <RouteSummary
            route={route}
            routeLabel={routeLabel}
            comp={comp}
            totalDist={totalDist}
            totalDur={totalDur}
            lineFeatures={lineFeatures}
            centreId={centreId}
            classFilter={classFilter}
          />
        ) : (
          <div className="route-summary">
            <h2 className="route-summary__title">{t("noRouteTitle")}</h2>
            <p>{t("noRoute")}</p>
          </div>
        )}
      </div>

      <div className="route-workspace__map no-print">
        <div className="map-frame">
          <div className="map-toolbar">
            <p className="map-toolbar__title">
              <span className="map-toolbar__centre">{centreName} · </span>
              {route ? routeLabel : t("noRouteTitle")}
            </p>
            {route && (
              <span className="map-toolbar__status">
                {comp.overall === "mixed" ? (
                  <StatusBadge status="inferred" size="sm">{t("statusMixed")}</StatusBadge>
                ) : (
                  <StatusBadge status={comp.overall} size="sm" />
                )}
              </span>
            )}
            <div className="map-toolbar__tools">
              <button type="button" className="map-tool" onClick={() => fitRef.current?.()}>
                {t("fitRoute")}
              </button>
              <MapLayersMenu
                showEvidence={showEvidence}
                setShowEvidence={setShowEvidence}
                colourBySupport={colourBySupport}
                setColourBySupport={setColourBySupport}
                evidenceCount={evidenceData.features.length}
              />
            </div>
          </div>
          <div className="map-canvas">
            {center ? (
              <MapContainer center={center} zoom={13} className="map-canvas__leaflet" scrollWheelZoom>
                <TileLayer
                  key={tileKey}
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                  className="ontario-tiles"
                  eventHandlers={{ tileerror: () => setTileError(true), load: () => setTileError(false) }}
                />
                <Marker position={center} icon={makeCentreIcon()} keyboard={false}>
                  <Tooltip direction="top" offset={[0, -10]}>
                    {t("legend_centre")}
                  </Tooltip>
                </Marker>
                {/* Own pane under the route overlay: evidence never
                    paints over the route line it's explaining. */}
                <Pane name="evidence" style={{ zIndex: 390 }}>
                  {showEvidence && (
                    <GeoJSON
                      key={`ev-${colourBySupport}`}
                      data={evidenceData}
                      pointToLayer={makePointToLayer(colourBySupport)}
                      onEachFeature={onEachFeature}
                    />
                  )}
                </Pane>
                <GeoJSON key={`case-${route?.key || "none"}`} data={routeData} style={casingStyle} interactive={false} />
                <GeoJSON
                  key={`route-${route?.key || "none"}-${selectedLineId}`}
                  data={routeData}
                  style={(f) => routeLineStyle(f, selectedLineId, linkable)}
                  onEachFeature={onEachFeature}
                  eventHandlers={
                    linkable
                      ? {
                          click: (e) => {
                            const id = e.layer?.feature?.properties?.route_line_id;
                            if (id != null) setSelectedLineId(id);
                          },
                        }
                      : undefined
                  }
                />
                <TurnPins steps={steps} selectedStep={selectedStep} onSelect={(i) => selectStep(i, true)} />
                <MapController frameKey={`${centreId}|${route?.key}`} frameBounds={frameBounds} fitRef={fitRef} focusBounds={focusBounds} />
                <PlaybackLayer
                  track={track}
                  active={playActive}
                  playing={playing}
                  dist={playDist}
                  posRef={playPosRef}
                  speed={playSpeed}
                  onProgress={setPlayDist}
                  onEnd={() => setPlaying(false)}
                />
              </MapContainer>
            ) : (
              <div className="map-canvas__fallback">
                <p>{t("mapUnavailable")}</p>
              </div>
            )}
            {tileError && (
              <div className="map-alert" role="alert">
                <span>{t("tilesFailed")}</span>
                <button
                  type="button"
                  onClick={() => {
                    setTileError(false);
                    setTileKey((k) => k + 1);
                  }}
                >
                  {t("retry")}
                </button>
              </div>
            )}
            <div className="map-legend">
              <RouteLegend statuses={statusesShown.length ? statusesShown : ["confirmed"]} showCentre />
              {steps.some((st) => st.anchor) && (
                <p className="map-legend__extra">
                  <span className="legend-turn-pin" aria-hidden="true">
                    <span className="turn-pin-sample">1</span>
                  </span>
                  {t("legend_turn")}
                </p>
              )}
              {showEvidence && (
                <p className="map-legend__extra">
                  <span className="legend-evidence-dot" aria-hidden="true" /> {t("legend_evidence")}
                </p>
              )}
            </div>
          </div>
          {route && center && (
            <PlaybackBar
              track={track}
              dist={playDist}
              playing={playing}
              speed={playSpeed}
              onSpeed={setPlaySpeed}
              formatDistance={formatDistance}
              onPlay={(restart) => {
                if (restart) {
                  playPosRef.current = 0;
                  setPlayDist(0);
                }
                setPlayActive(true);
                setPlaying(true);
              }}
              onPause={() => {
                setPlaying(false);
                setPlayDist(playPosRef.current);
              }}
              onScrub={(d) => {
                playPosRef.current = d;
                setPlaying(false);
                setPlayActive(true);
                setPlayDist(d);
              }}
            />
          )}
        </div>
      </div>

      <div className="route-workspace__turns">
        {route && lineFeatures.length > 0 && (
          <section className="practice-entry mobile-only no-print" aria-labelledby="practice-entry-title">
            <div>
              <h3 id="practice-entry-title">{t("practiceDriveTitle")}</h3>
              <p>{t("practiceEntryBody")}</p>
            </div>
            <button type="button" className="btn-primary" onClick={() => setPracticeOpen(true)}>
              {t("startDriveAlong")}
            </button>
          </section>
        )}
        {practiceOpen && route && (
          <Suspense fallback={null}>
            <PracticeDrive
              lineFeatures={lineFeatures}
              steps={steps}
              routeLabel={routeLabel}
              centreName={centreName}
              centre={center}
              overall={comp.overall}
              onClose={() => setPracticeOpen(false)}
            />
          </Suspense>
        )}
        <nav className="section-jump mobile-only no-print" aria-label={t("onThisPage")}>
          <a href="#turns">{t("turns")}</a>
          <a href="#evidence">{t("evidenceNav")}</a>
          <a href="#centre-tips">{t("tipsNav")}</a>
        </nav>
        <TurnFeed
          steps={steps}
          mixed={comp.overall === "mixed"}
          linkable={linkable}
          selectedStep={selectedStep}
          onSelectStep={(i) => selectStep(i, false)}
        />
      </div>
    </section>
  );
}

function readRouteParams() {
  const p = new URLSearchParams(window.location.search);
  return { cls: p.get("class"), idx: Number(p.get("route")) || null };
}

function writeRouteParams(cls, idx) {
  const p = new URLSearchParams(window.location.search);
  if (cls) p.set("class", cls);
  else p.delete("class");
  if (idx) p.set("route", String(idx));
  else p.delete("route");
  const next = `${window.location.pathname}?${p.toString()}`;
  if (next !== window.location.pathname + window.location.search) window.history.replaceState(null, "", next);
}

export default function MapView({ centreId, centreName }) {
  const { t } = useLang();
  const [geojson, setGeojson] = useState(null);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [classFilter, setClassFilter] = useState(null);
  const [routeKey, setRouteKey] = useState(null);
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    let stale = false;
    setGeojson(null);
    setError(null);
    setNotice(null);
    api
      .getMap(centreId)
      .then((data) => {
        if (stale) return;
        setGeojson(data);
        const rs = buildRoutes(data);
        const wanted = readRouteParams();
        // Deep link (?class=G&route=2) wins when it points at a real
        // route; an invalid one falls back WITH a notice, never silently.
        if (wanted.cls && (wanted.cls === "G" || wanted.cls === "G2")) {
          const forClass = routesForClass(rs, wanted.cls);
          const pick = forClass[(wanted.idx || 1) - 1];
          if (pick) {
            setClassFilter(wanted.cls);
            setRouteKey(pick.key);
            return;
          }
          setNotice(t("deepLinkFallback"));
        }
        const best = rs.slice().sort((a, b) => b.traces - a.traces)[0];
        const defClass = best && (best.cls === "G" || best.cls === "G2") ? best.cls : "G2";
        setClassFilter(defClass);
        const forClass = routesForClass(rs, defClass);
        setRouteKey(forClass.length ? forClass[0].key : null);
      })
      .catch((err) => {
        if (!stale) setError(err.message);
      });
    return () => {
      stale = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [centreId, reloadKey]);

  const routes = useMemo(() => (geojson ? buildRoutes(geojson) : []), [geojson]);
  const classRoutes = classFilter ? routesForClass(routes, classFilter) : [];
  const selected = routes.find((r) => r.key === routeKey) || null;
  const routeIndex = selected ? Math.max(0, classRoutes.findIndex((r) => r.key === selected.key)) : 0;

  useEffect(() => {
    if (!geojson) return;
    writeRouteParams(classFilter, selected ? routeIndex + 1 : null);
  }, [geojson, classFilter, selected, routeIndex]);

  if (error)
    return (
      <div className="page">
        <div className="state-panel state-panel--error" role="alert">
          <p>
            {t("mapLoadFailed")} ({error})
          </p>
          <button type="button" onClick={() => setReloadKey((k) => k + 1)}>
            {t("retry")}
          </button>
        </div>
      </div>
    );
  if (!geojson) return <LoadingScreen label={t("loadingMap")} />;

  function selectClass(c) {
    setClassFilter(c);
    const forClass = routesForClass(routes, c);
    setRouteKey(forClass.length ? forClass[0].key : null);
  }

  return (
    <>
      {notice && (
        <div className="page page--notice">
          <p className="notice-banner" role="status">
            {notice}
          </p>
        </div>
      )}
      <RouteWorkspace
        centreId={centreId}
        centreName={centreName}
        classFilter={classFilter}
        geojson={geojson}
        route={selected}
        routeIndex={routeIndex}
        routes={routes}
        classRoutes={classRoutes}
        onClass={selectClass}
        onRoute={setRouteKey}
      />
    </>
  );
}
