import { useEffect, useMemo, useState, lazy, Suspense } from "react";
import { MapContainer, TileLayer, GeoJSON, Marker, Tooltip, useMap } from "react-leaflet";
import L from "leaflet";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";
import { formatTrafficControl, formatSpeedLimit, inferManeuver } from "../format.js";
import LoadingScreen, { Spinner } from "../LoadingScreen.jsx";

// Lazy: quiz mode and drive-along tracking are real features but not
// needed for the initial route-detail paint -- keeping them out of the
// main bundle means a visitor who never opens either never downloads
// their code.
const DriveAlongControls = lazy(() => import("./DriveAlongMode.jsx"));
const DriveAlongMarker = lazy(() =>
  import("./DriveAlongMode.jsx").then((m) => ({ default: m.DriveAlongMarker }))
);
import { useSeo, breadcrumbList } from "../seo.js";
import Breadcrumbs from "../Breadcrumbs.jsx";
import {
  FlagIcon,
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

const MANEUVER_ICONS = {
  left: TurnLeftIcon,
  right: TurnRightIcon,
  straight: StraightIcon,
  merge: MergeIcon,
  roundabout: RoundaboutIcon,
  destination: DestinationIcon,
};

// Real DriveTest centre coordinates, from osm.db's centres table (the
// OSM way for the actual DriveTest building/lot) -- not an approximate
// guess. Every route starts and ends here; a user needs to find this
// point on the map before anything else makes sense.
const CENTRE_COORDS = {
  walkley: [45.376145807017544, -75.64758859649123],
  canotek: [45.4528876, -75.5883806],
  smithsfalls: [44.8827581, -76.0150536],
  winchester: [45.0851023, -75.3712133],
};

// Hallmark audit finding (gate 58, token drift): these used to be hex
// literals duplicating --confirmed/--gap/--accent, which already drifted
// out of sync with the real tokens once this session (caught by hand).
// Reading the live custom property instead means a future token edit in
// App.css can never silently break the map's colors again. Read lazily
// (not at module-eval time) so this never races the stylesheet's own
// load -- by the time any of these run, the app has already painted
// using these same tokens, so the stylesheet is guaranteed present.
export function cssVar(name, fallback) {
  if (typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function makeCentreIcon() {
  const ring = cssVar("--accent", "#14532d");
  return L.divIcon({
    className: "",
    html:
      `<div style="width:12px;height:12px;border-radius:50%;background:#111;` +
      `border:3px solid ${ring};box-shadow:0 0 0 1px #111,0 1px 4px rgba(0,0,0,.6);"></div>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
  });
}

// Class color-coding (G orange / G2 blue) was dropped by explicit
// request in favour of one uniform color for all real, sourced route
// data -- test_class is still filterable (buttons below) and still
// shown in each line's popup text, just no longer color-coded. Real
// data (confirmed AND below-threshold) is one solid red; nothing about
// this touches the one tier that still MUST stay visually separate.
function realColor() {
  return cssVar("--confirmed", "#c0392b");
}

// A route line's road geometry comes from OSRM. When two consecutive
// junctions sit on disconnected pieces of the road graph, OSRM still
// answers "Ok" but returns a straight beeline for that leg -- a fake
// straight segment across whatever is between them (confirmed on the
// Walkley airport routes: an 778 m jump straight across greenspace).
// Drawn as part of the solid confirmed line it silently asserts a road
// that does not exist. GAP_THRESHOLD_M is set above the largest real
// sparse-road stretch in the data (~383 m) so only true beelines split.
const GAP_THRESHOLD_M = 400;
function gapColor() {
  return cssVar("--gap", "#6b7680");
}

function haversine(a, b) {
  // a, b are [lon, lat]
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLon = toRad(b[0] - a[0]);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

// Split one route_line feature at any beeline gap: the real road pieces
// come back as route_line features (styled normally), and each gap comes
// back as its own `route_gap` feature so it renders honestly (dashed,
// gray, labeled) instead of masquerading as confirmed road. Features
// without a gap pass straight through untouched.
function splitAtGaps(feature) {
  if (
    feature.properties.kind !== "route_line" ||
    feature.geometry.type !== "LineString"
  ) {
    return [feature];
  }
  const coords = feature.geometry.coordinates;
  const pieces = [[]];
  const gaps = [];
  for (let i = 0; i < coords.length; i++) {
    if (i > 0) {
      const d = haversine(coords[i - 1], coords[i]);
      if (d > GAP_THRESHOLD_M) {
        gaps.push({ from: coords[i - 1], to: coords[i], dist: d });
        pieces.push([]); // start a new contiguous piece after the gap
      }
    }
    pieces[pieces.length - 1].push(coords[i]);
  }
  if (!gaps.length) return [feature];

  const out = pieces
    .filter((pc) => pc.length >= 2)
    .map((pc) => ({
      ...feature,
      geometry: { type: "LineString", coordinates: pc },
    }));
  gaps.forEach((g) => {
    out.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: [g.from, g.to] },
      properties: {
        ...feature.properties,
        kind: "route_gap",
        gap_m: Math.round(g.dist),
      },
    });
  });
  return out;
}

// A route line that draws nothing: fewer than 2 distinct points, or a
// total length under ~5 m (a bridge whose two ends coincide).
function isDegenerateRouteLine(feature) {
  if (feature.properties.kind !== "route_line") return false;
  const c = feature.geometry?.coordinates || [];
  if (c.length < 2) return true;
  let len = 0;
  for (let i = 1; i < c.length; i++) len += haversine(c[i - 1], c[i]);
  return len < 5;
}

// selectedLineId (a real route_line_id, set by clicking a turn in the
// sidebar) makes that one segment visually dominant and dims its
// siblings -- the map<->turn interaction from the 2026-09-21 spec. Never
// touches trust color, only weight/opacity, so confirmed/predicted/gap
// meaning stays exactly as before regardless of what's selected.
function routeLineStyle(feature, selectedLineId) {
  const p = feature.properties;
  if (p.kind === "route_gap") {
    // Not a road: a straight, unrouted jump. Dashed + gray so it never
    // reads as confirmed driven geometry.
    const dimmed = selectedLineId != null;
    return { color: gapColor(), weight: 3, dashArray: "4, 8", opacity: dimmed ? 0.5 : 0.9 };
  }
  if (p.kind !== "route_line") return {};
  // Confirmed, below-threshold, and predicted all render the same solid
  // red line now -- see onEachFeature for how predicted stays labeled.
  if (selectedLineId != null) {
    const isSelected = p.route_line_id === selectedLineId;
    return {
      color: realColor(),
      weight: isSelected ? 7 : 4,
      opacity: isSelected ? 1 : 0.45,
    };
  }
  return { color: realColor(), weight: 4 };
}

// weight ranges roughly 0-1 in this app's data (consensus_geometry.py);
// clamp defensively since a heatmap color must always be well-defined.
function weightColor(w) {
  const c = Math.max(0, Math.min(1, w));
  // red (low corroboration) -> yellow -> green (high corroboration)
  const hue = c * 120;
  return `hsl(${hue}, 75%, 45%)`;
}

// Confidence heatmap (backlog: "visually weight each street segment by
// how many independent traces confirm it") reuses the same weight/
// authors data already scored by consensus_geometry.py and already
// returned per junction point -- no new data collection, just a second
// color encoding of what's already on the map.
function makePointToLayer(heatmap) {
  return function pointToLayer(feature, latlng) {
    const authors = feature.properties.authors || 1;
    const w = feature.properties.weight ?? 1;
    if (heatmap) {
      return L.circleMarker(latlng, {
        radius: 5 + Math.min(authors, 6),
        fillColor: weightColor(w),
        color: weightColor(w),
        weight: 1,
        fillOpacity: 0.75,
      });
    }
    // Fade weakly-supported junctions so they read as secondary to the
    // strongly-corroborated ones on the actual routes -- a single-author,
    // weight-0.3 junction 6 km out shouldn't look as solid as a
    // multi-author junction on a confirmed route.
    const fillOpacity = Math.max(0.2, Math.min(0.7, 0.2 + w * 0.35));
    return L.circleMarker(latlng, {
      radius: 4 + Math.min(authors, 6),
      fillColor: "#3388ff",
      color: "#3388ff",
      weight: 1,
      fillOpacity,
    });
  };
}

function onEachFeature(feature, layer) {
  const p = feature.properties;
  if (p.kind === "route_gap") {
    layer.bindPopup(
      `<b style="color:var(--gap)">⚠ Unrouted gap -- not a road</b><br/>` +
        `The route data jumps ~${p.gap_m} m straight here: the two ends sit ` +
        `on road segments that don't connect in the map data, so this ` +
        `stretch is a straight line across the gap, <b>not a driven road</b>.<br/>` +
        `Family ${p.family}. The road pieces on either side are real.`
    );
    return;
  }
  if (p.kind === "segment") {
    layer.bindPopup(
      `<b>${p.streets.join(" × ")}</b><br/>` +
        `${p.authors} author(s), weight ${p.weight.toFixed(2)}<br/>` +
        `${p.video_count} video, ${p.text_count} text<br/>` +
        `last seen: ${p.last_seen || "unknown"}`
    );
  } else if (p.kind === "route_line") {
    if (p.predicted) {
      // Line color/weight matches confirmed data now. Permanent on-map
      // tooltips were tried and dropped -- with several predicted
      // segments close together (e.g. clustered near the centre) they
      // overlapped into unreadable clutter. Per-segment identification
      // now lives off the map instead: the instructions table below
      // tags each predicted row inline, and this popup still gives full
      // detail on click. The ratio banner covers the "at a glance"
      // case without needing a label physically on every line.
      layer.bindPopup(
        `<b style="color:var(--predicted)">⚠ PREDICTED -- not sourced</b><br/>` +
          `Road-snapped guess connecting two real, confirmed points that no ` +
          `trace directly walked between. Family ${p.family}, ~${p.distance_m}m.<br/>` +
          `<b>Do not treat this as a confirmed turn-by-turn instruction.</b>`
      );
      return;
    }
    const classLabel = p.mixed_classes
      ? `${p.test_class || "class unknown"} (mixed -- traces disagree)`
      : p.test_class || "class unknown";
    const confidenceNote = p.below_threshold
      ? "<br/><i>Below the standard confidence threshold -- real, same-family " +
        "evidence, just weaker corroboration than a fully published route.</i>"
      : "";
    // manual_youtube routes are hand-transcribed street-by-street from a
    // real drive-test video by a person watching it, not crowdsourced/
    // clustered like everything else here. "1 trace, 1 author" would
    // undersell that (reads identical to one anonymous, unreliable Reddit
    // post) -- and per the same principle that keeps predicted data from
    // looking as strong as confirmed data, different provenance should
    // never look the same as another kind just because a count matches.
    const provenanceLine =
      p.source === "manual_youtube"
        ? `<b style="color:var(--success)">&#10003; Hand-verified from a real DriveTest video</b><br/>`
        : `${p.trace_count} traces, ${p.authors} authors<br/>`;
    layer.bindPopup(
      `<b>${classLabel} route -- family ${p.family}</b><br/>` +
        provenanceLine +
        `${p.distance_m}m${confidenceNote}`
    );
  }
}

// Route lines only -- consensus_segments (the dots) carry no test_class
// at all, so a class filter has nothing to say about them one way or
// the other. They stay visible regardless of which button is active.
function matchesFilter(feature, filter) {
  // route_gap features carry their parent line's class, so filter them
  // the same way -- otherwise a gap could show while its route is hidden.
  if (
    feature.properties.kind !== "route_line" &&
    feature.properties.kind !== "route_gap"
  )
    return true;
  // A route_line whose dominant class couldn't be determined (test_class
  // null) is still REAL, validated geometry -- 4 of Walkley's 8 confirmed
  // consensus routes are unlabeled this way. It has no class to contradict
  // either view, so show it under both buttons rather than hiding real
  // route data off the map entirely (its popup already says "class
  // unknown"). Hiding it was a latent bug: with only G/G2 buttons, a
  // null-class line matched neither and never rendered.
  if (feature.properties.test_class == null) return true;
  return feature.properties.test_class === filter;
}

// Frames the view on whatever real geometry actually exists for this
// centre, instead of a hardcoded guess-coordinate at a fixed zoom.
// Real routes read as "finished" when the map opens already looking at
// them, not when a user has to pan/zoom to find a thin line somewhere
// in a wide default view.
//
// Frame on the ROUTES (+ the centre), not on every consensus point:
// some scored junctions are weak, single-author, far-flung outliers
// (real Orleans/outer-Ottawa junctions 4-7 km out that never joined a
// route). Including them in the bounds zoomed the whole map out until
// the actual routes were a tiny knot in the middle. The outlier dots
// still render -- they just don't get to hijack the opening view. Only
// when a centre has no routes at all (Smiths Falls) do we fall back to
// framing on the points so there's still something to look at.
function FitToData({ geojson, centreLatLng }) {
  const map = useMap();
  useEffect(() => {
    if (!geojson || !geojson.features.length) return;
    const routeish = geojson.features.filter(
      (f) => f.properties.kind === "route_line" || f.properties.kind === "route_gap"
    );
    const framingSet = routeish.length ? routeish : geojson.features;
    const bounds = L.geoJSON({ type: "FeatureCollection", features: framingSet }).getBounds();
    if (centreLatLng) bounds.extend(centreLatLng); // never crop the centre out of view
    if (bounds.isValid()) {
      // Sidebar content height changes per route (gap-notice box, turn
      // count, etc.), but that reflow doesn't touch the map container's
      // own dimensions -- Leaflet still sometimes fits against a stale
      // cached size after a route switch, producing a zoom/pan that
      // doesn't actually contain the drawn line until the user manually
      // interacts with the map. invalidateSize() forces a fresh
      // measurement immediately before fitting.
      map.invalidateSize();
      map.fitBounds(bounds, { padding: [30, 30] });
    }
  }, [geojson, centreLatLng, map]);
  return null;
}

export function formatDistance(m) {
  if (m == null) return "";
  return m < 1000 ? `${m} m` : `${(m / 1000).toFixed(2)} km`;
}

function formatDuration(s) {
  if (s == null) return "";
  // Flooring every nonzero duration up to "1 min" looked fine per row
  // but silently added ~20 minutes of pure rounding error once a route
  // has 30+ short connector segments (a 5-second turn showing "1 min"
  // 30 times over). Show real seconds under a minute instead.
  if (s < 60) return `${Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}

function formatDurationTotal(totalSec) {
  const mins = Math.floor(totalSec / 60);
  const secs = Math.round(totalSec % 60);
  return mins > 0 ? `${mins} min ${secs} s` : `${secs} s`;
}

function sumDuration(rows) {
  return formatDurationTotal(rows.reduce((a, r) => a + (r.duration_s || 0), 0));
}

// A route whose OSRM call genuinely failed still gets inserted (with a
// synthetic straight-line geometry) but distance_m=NULL -- `|| 0` on
// that turns a real "unknown" into a displayed "0 m", which is more
// misleading than showing nothing in an app whose whole point is
// accurate distances. Null only when EVERY value is null; a real 0
// among real numbers still sums normally.
function sumOrNull(values) {
  const known = values.filter((v) => v != null);
  if (!known.length) return null;
  return known.reduce((a, v) => a + v, 0);
}

// manual_youtube routes' rows are the owner's own transcript lines, and
// align_transcript() (manual_routes.py) stamps the SAME real leg's
// distance/duration onto every line covering it -- the turn-onto line
// plus every following "continue on X" line. Summing every row double-
// (or triple-, quadruple-) counts that leg's time. Consecutive rows
// sharing the exact same (distance_m, duration_s) pair are the same
// real leg repeated, not two different legs that happen to match --
// dedupe by only counting a value the first time it appears in a run.
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

// Real OSM tags (extract_traffic_data.py) -- absent for most segments
// simply because most streets in this extract aren't tagged with
// either (checked directly: ~4.7% of ways have a maxspeed at all).
// Blank cell means "not tagged," never a guessed default.
// ONE turn-by-turn list for the whole route. lines is a single route's
// pieces (one family); flatten them into one continuous numbered list.
// A mid-route "Arrive at destination" is just where one collected piece
// ended, not the end of the drive, so only the very last one is kept.
function ReportErrorButton({ routeLineId, stepOrder }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(false);

  if (sent) return <span style={{ fontSize: "0.85em", color: "var(--success)" }}>{t("reportErrorSent")}</span>;
  if (!open)
    return (
      <button
        onClick={() => setOpen(true)}
        title={t("reportError")}
        aria-label={t("reportError")}
        style={{ border: "none", background: "none", cursor: "pointer", color: "var(--ink-muted)" }}
      >
        <FlagIcon />
      </button>
    );
  return (
    <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder={t("reportErrorPrompt")}
        style={{ fontSize: "0.85em", width: 140 }}
      />
      <button
        onClick={() =>
          api
            .reportError(routeLineId, stepOrder, note || null)
            .then(() => setSent(true))
        }
        style={{ fontSize: "0.85em" }}
      >
        {t("submit")}
      </button>
      <button onClick={() => setOpen(false)} style={{ fontSize: "0.85em" }}>
        {t("cancel")}
      </button>
    </span>
  );
}

// One vertical row per turn -- the row shape every real navigation app
// uses (a step number/icon, the instruction, a compact meta line under
// it), replacing the old dense data-grid table that forced horizontal
// scrolling inside the sidebar. Still real semantic list markup
// (<ol>/<li>) for accessibility; a field only renders when the
// underlying data actually has it (never an empty "At junction" cell
// reserved just because the table used to have that column).
function TurnFeedRow({ step, index, selected, onSelect }) {
  const maneuver = inferManeuver(step.instruction);
  const Icon = maneuver && MANEUVER_ICONS[maneuver];
  const junction = formatTrafficControl(step.traffic_control);
  const speed = formatSpeedLimit(step.speed_limit);
  const metaParts = [];
  if (step.distance_m) metaParts.push(formatDistance(step.distance_m));
  if (step.duration_s) metaParts.push(formatDuration(step.duration_s));
  // A step's parent route_line is the finest granularity the map data
  // carries (multiple steps can share one line) -- selecting a turn
  // highlights that whole segment, not the single sub-slice of road this
  // exact turn happens on, which the underlying geometry doesn't
  // separately track.
  const clickable = step.routeLineId != null && onSelect;

  return (
    <li
      className={`turn-feed__row${step.predicted ? " turn-feed__row--predicted" : ""}${
        clickable ? " turn-feed__row--clickable" : ""
      }${selected ? " turn-feed__row--selected" : ""}`}
      onClick={clickable ? () => onSelect(step.routeLineId) : undefined}
      aria-selected={selected || undefined}
    >
      <span className="turn-feed__marker">
        {Icon ? <Icon /> : <span className="turn-feed__index">{index + 1}</span>}
      </span>
      <span className="turn-feed__body">
        <span className="turn-feed__instruction">
          {step.instruction}
          {step.predicted && (
            <span className="trust-badge trust-badge--predicted turn-feed__badge">predicted</span>
          )}
        </span>
        {(metaParts.length > 0 || junction || speed) && (
          <span className="turn-feed__meta">
            {metaParts.length > 0 && <span className="data">{metaParts.join(" · ")}</span>}
            {junction && <span>{junction}</span>}
            {speed && <span className="data">{speed}</span>}
          </span>
        )}
      </span>
      {step.routeLineId != null && (
        <span className="turn-feed__flag no-print">
          <ReportErrorButton routeLineId={step.routeLineId} stepOrder={step.step_order} />
        </span>
      )}
    </li>
  );
}

function TurnFeed({ lines, selectedLineId, onSelectLine }) {
  const rows = [];
  for (const l of lines) {
    const predicted = l.properties.predicted;
    const routeLineId = l.properties.route_line_id;
    for (const s of l.properties.steps || []) {
      rows.push({ ...s, predicted, routeLineId });
    }
  }
  const steps = rows.filter(
    (r, i) => r.instruction !== "Arrive at destination" || i === rows.length - 1
  );
  if (!steps.length) return null;

  // manual_youtube routes' steps are the owner's own transcript lines,
  // each aligned to the real OSRM leg that covers the same street where
  // possible -- but not every real leg gets a matching line (short
  // unnamed connectors near the centre, mainly), so summing steps
  // UNDERCOUNTS the true distance. Use the route's own real, whole-line
  // distance_m instead so this total agrees with the summary above it.
  const isManual = lines.some((l) => l.properties.source === "manual_youtube");
  const totalDistanceM = isManual
    ? sumOrNull(lines.map((l) => l.properties.distance_m))
    : steps.reduce((a, r) => a + (r.distance_m || 0), 0);
  const hasRealDuration = steps.some((r) => r.duration_s);
  const totalDurS = isManual ? sumUniqueLegDurationS(steps) : steps.reduce((a, r) => a + (r.duration_s || 0), 0);
  const durationText = hasRealDuration && totalDurS > 0 ? `, ${formatDurationTotal(totalDurS)}` : "";

  return (
    <div id="turn-feed" style={{ marginTop: "var(--space-md)" }}>
      <p className="turn-feed__total">
        <b>
          Total: <span className="data">{formatDistance(totalDistanceM)}</span>
          {durationText}
        </b>{" "}
        for this route.
      </p>
      <ol className="turn-feed">
        {steps.map((r, i) => (
          <TurnFeedRow
            key={i}
            step={r}
            index={i}
            selected={selectedLineId != null && r.routeLineId === selectedLineId}
            onSelect={onSelectLine}
          />
        ))}
      </ol>
    </div>
  );
}

// A driving test is ONE route. Group route_lines into selectable routes,
// one per (test_class, family) -- so the app can show a single route at a
// time instead of every variant and every inferred fragment piled on one
// map (which read as "this doesn't look like one route" and summed a
// nonsense 50-minute duration across unrelated routes). Popularity is the
// family's trace_count: the most-corroborated route sorts first and is the
// default.
function buildRoutes(geojson) {
  const byKey = new Map();
  for (const f of geojson.features) {
    if (f.properties.kind !== "route_line") continue;
    if (isDegenerateRouteLine(f)) continue;
    const cls = f.properties.test_class || "unknown";
    const fam = f.properties.family;
    const key = `${cls}|${fam}`;
    if (!byKey.has(key))
      byKey.set(key, { key, cls, fam, features: [], traces: 0, authors: 0, manualVerified: false });
    const r = byKey.get(key);
    r.features.push(f);
    r.traces = Math.max(r.traces, f.properties.trace_count || 0);
    r.authors = Math.max(r.authors, f.properties.authors || 0);
    if (f.properties.source === "manual_youtube") r.manualVerified = true;
  }
  return [...byKey.values()];
}

// Routes shown under a class button: that class, plus any still-unclassified
// route (real geometry whose class isn't determined -- shown under both so
// it's never hidden). Most-corroborated first.
function routesForClass(routes, cls) {
  return routes
    .filter((r) => r.cls === cls || r.cls === "unknown")
    .sort((a, b) => b.traces - a.traces || a.fam - b.fam);
}

// One trust-tier badge, reused everywhere a route's provenance is shown
// (route-selector chip + panel summary) so the tiering can never drift
// between the two spots the way the old duplicated inline JSX could.
// Never blurs predicted/below-threshold into looking like confirmed --
// same principle CentreList already follows for the confirmed count.
function TrustBadge({ route }) {
  const { t } = useLang();
  if (route.manualVerified) {
    return <span className="trust-badge trust-badge--success">✓ {t("verified")}</span>;
  }
  return (
    <span className="trust-badge trust-badge--gap">
      {route.traces} {t("src")}
      {route.authors ? ` · ${route.authors} ${route.authors === 1 ? "author" : "authors"}` : ""}
    </span>
  );
}

function DifficultyBadge({ lineFeatures }) {
  const { t } = useLang();
  const scored = lineFeatures.filter((f) => f.properties.difficulty_score != null);
  if (!scored.length) return null;
  // one route can be several stitched line pieces; take the hardest
  // piece's label rather than averaging away a genuinely hard stretch.
  const worst = scored.reduce((a, b) =>
    b.properties.difficulty_score > a.properties.difficulty_score ? b : a
  );
  const tier = { Easy: "success", Moderate: "warning", Hard: "confirmed" }[worst.properties.difficulty_label];
  return (
    <span style={{ marginLeft: 8 }}>
      {t("difficulty")}:{" "}
      <span className={`trust-badge trust-badge--${tier}`}>{worst.properties.difficulty_label}</span>
    </span>
  );
}

// GPX is plain XML over geometry already fetched for the map -- no
// backend endpoint needed, built client-side from the same coordinates
// Leaflet is already drawing.
function downloadGpx(route, lineFeatures, centreId) {
  const points = lineFeatures.flatMap((f) => f.geometry.coordinates);
  const trkpts = points
    .map(([lon, lat]) => `<trkpt lat="${lat}" lon="${lon}"></trkpt>`)
    .join("\n      ");
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

// Groups GPX/PDF behind one "Export ▾" trigger instead of two buttons
// with the same visual weight as the primary practice-drive action.
// Only the two export formats the app actually supports -- no invented
// options.
function ExportMenu({ onGpx, onPdf }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  return (
    <div className="export-menu no-print">
      <button onClick={() => setOpen((o) => !o)} aria-haspopup="true" aria-expanded={open}>
        {t("exportLabel")} <ChevronDownIcon />
      </button>
      {open && (
        <div className="popover popover-panel export-menu__panel" role="menu">
          <button
            role="menuitem"
            className="popover-row"
            onClick={() => {
              onGpx();
              setOpen(false);
            }}
          >
            {t("exportGpx")}
          </button>
          <button
            role="menuitem"
            className="popover-row"
            onClick={() => {
              onPdf();
              setOpen(false);
            }}
          >
            {t("exportPdf")}
          </button>
        </div>
      )}
    </div>
  );
}

// A small floating control over the map, matching how real mapping
// apps surface optional overlays -- only the one layer this app
// actually supports (the confidence heatmap encoding already built
// from consensus_geometry.py's own weight/author data), not a menu of
// invented toggles.
function LayersControl({ heatmap, onChange }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  return (
    <div className="map-layers-control no-print">
      <button onClick={() => setOpen((o) => !o)} aria-haspopup="true" aria-expanded={open} title={t("layers")}>
        <LayersIcon /> {t("layers")}
      </button>
      {open && (
        <div className="popover popover-panel map-layers-control__panel">
          <label className="map-layers-control__row">
            <input type="checkbox" checked={heatmap} onChange={(e) => onChange(e.target.checked)} />
            {t("confidenceView")}
          </label>
        </div>
      )}
    </div>
  );
}

// Static app copy explaining the app's own real, already-implemented
// trust tiers -- not a data fetch, so nothing here can drift out of
// sync with a live value; it's just naming what the colors already
// mean everywhere else in the UI (DESIGN.md's Never-Blur Rule).
function ConfidenceInfoModal({ onClose }) {
  const { t } = useLang();
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-panel card" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>{t("confidenceModalTitle")}</h3>
        <p>
          <span className="trust-badge trust-badge--confirmed">confirmed</span>
          <br />
          <span style={{ fontSize: "0.875rem", color: "var(--ink-muted)" }}>{t("confidenceConfirmedExplainer")}</span>
        </p>
        <p>
          <span className="trust-badge trust-badge--predicted">predicted</span>
          <br />
          <span style={{ fontSize: "0.875rem", color: "var(--ink-muted)" }}>{t("confidencePredictedExplainer")}</span>
        </p>
        <p>
          <span className="trust-badge trust-badge--gap">gap</span>
          <br />
          <span style={{ fontSize: "0.875rem", color: "var(--ink-muted)" }}>{t("confidenceGapExplainer")}</span>
        </p>
        <button onClick={onClose}>{t("close")}</button>
      </div>
    </div>
  );
}

function RoutePanel({ centreId, centreName, classFilter, routeIndex, geojson, route, center, selector }) {
  const { t } = useLang();
  const [heatmap, setHeatmap] = useState(false);
  const [driveAlongLive, setDriveAlongLive] = useState(null);
  const [confidenceInfoOpen, setConfidenceInfoOpen] = useState(false);
  const [selectedLineId, setSelectedLineId] = useState(null);
  const [sheetExpanded, setSheetExpanded] = useState(false);

  // A turn selection belongs to the route it was made on -- switching
  // routes (or the class filter) should never leave a stale segment
  // highlighted on the new route's map.
  useEffect(() => {
    setSelectedLineId(null);
  }, [route]);
  // Derived purely from `route`/`geojson` props -- memoized so unrelated
  // state changes (heatmap toggle, confidence modal, and especially
  // driveAlongLive, which updates on every GPS tick during a live
  // practice drive) don't re-run these filters/flatMaps on every render.
  const { lineFeatures, mapData, gapCount, predictedCount, steps, totalDur, totalDist } = useMemo(() => {
    const lineFeatures = route ? route.features.filter((f) => !isDegenerateRouteLine(f)) : [];
    // consensus junction dots stay visible regardless of which route is
    // selected -- they carry no class/family, they're the raw evidence layer.
    const segPoints = geojson.features.filter((f) => f.properties.kind === "segment");
    // split THIS route's lines at beeline gaps for the map; keep them un-split
    // for the instructions table so a line's OSRM steps aren't duplicated.
    const mapData = {
      type: "FeatureCollection",
      features: [...lineFeatures.flatMap(splitAtGaps), ...segPoints],
    };
    const gapCount = mapData.features.filter((f) => f.properties.kind === "route_gap").length;
    const predictedCount = lineFeatures.filter((f) => f.properties.predicted).length;

    // per-ROUTE distance/duration (one route, not every route summed).
    const steps = lineFeatures.flatMap((f) => f.properties.steps || []);
    // manual_youtube steps repeat a leg's real duration on every
    // transcript line covering it -- summing every row multiplies the
    // real driving time by however many lines describe that leg (caught
    // live: a real ~5min route was showing "~14 min driving"). Dedupe
    // consecutive repeats the same way totalDist's sibling column does.
    const totalDur = route && route.manualVerified
      ? sumUniqueLegDurationS(steps)
      : steps.reduce((a, s) => a + (s.duration_s || 0), 0);
    // manual_youtube routes show the owner's own transcript lines as
    // instructions (see manual_routes.py's TRANSCRIPT), which don't carry
    // a real per-step distance -- summing steps would silently show "0 m"
    // for a route that's actually several km. The route's own real,
    // OSRM-measured distance_m (always present regardless of step
    // wording) is the honest total here.
    const totalDist = route && route.manualVerified
      ? sumOrNull(lineFeatures.map((f) => f.properties.distance_m))
      : steps.reduce((a, s) => a + (s.distance_m || 0), 0);

    return { lineFeatures, mapData, gapCount, predictedCount, steps, totalDur, totalDist };
  }, [route, geojson]);

  const routeLabel = route ? `${classFilter} ${t("route")} ${routeIndex + 1}` : null;
  useSeo(
    route
      ? {
          title: `${centreName} — ${classFilter} ${t("route")} ${routeIndex + 1} — OntarioDriveTestMap`,
          description: `Study ${centreName} ${classFilter} Route ${routeIndex + 1} with turn-by-turn instructions, route confidence information and community-backed evidence.`,
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
    <div className="route-shell">
      <div className={`route-sidebar${sheetExpanded ? " route-sidebar--expanded" : ""}`}>
        <button
          type="button"
          className="route-sidebar__handle no-print"
          aria-label={sheetExpanded ? t("collapseRouteSheet") : t("expandRouteSheet")}
          aria-expanded={sheetExpanded}
          onClick={() => setSheetExpanded((v) => !v)}
        />
        {route && (
          <Breadcrumbs
            items={[
              { name: t("centres"), href: "/" },
              { name: centreName, href: `/?centre=${encodeURIComponent(centreId)}` },
              { name: routeLabel },
            ]}
          />
        )}
        <div className="route-title">
          {centreName && (
            <p className="route-title__eyebrow">
              {centreName} · {classFilter} {t("roadTest")}
            </p>
          )}
          <h2 className="route-title__heading">
            {t("route")} {route ? routeIndex + 1 : ""}
          </h2>
        </div>

        {route && (
          <div className="route-status">
            <div className="route-status__badges">
              <TrustBadge route={route} />
              <DifficultyBadge lineFeatures={lineFeatures} />
            </div>
            <p className="route-status__meta">
              <span className="data">{formatDistance(totalDist)}</span>
              {totalDur ? (
                <>
                  {" · about "}
                  <span className="data">{Math.round(totalDur / 60)} min</span>
                </>
              ) : null}
            </p>
            <p className="route-status__evidence">
              <button className="link-btn" onClick={() => setConfidenceInfoOpen(true)}>
                {t("howConfidenceWorks")}
              </button>
            </p>
          </div>
        )}

        {route && (
          <details className="disclosure">
            <summary>
              <InfoIcon /> {t("aboutThisRoute")} <ChevronDownIcon className="disclosure__chevron" />
            </summary>
            <p>{t("routeReconstructedNote")}</p>
          </details>
        )}

        {gapCount > 0 && (
          <p className="trust-banner trust-banner--gap">
            <span>
              {gapCount} <strong>unrouted gap{gapCount === 1 ? "" : "s"}</strong> shown{" "}
              <strong>dashed gray</strong>: the data jumps straight where the two ends don't
              connect on the road map -- not a driven road.
            </span>
          </p>
        )}
        {predictedCount > 0 && (
          <p className="trust-banner trust-banner--predicted">
            <span>
              Parts of this route are <strong>predicted</strong> -- a road-snapped guess
              connecting two real points no single source drove between, tagged in the
              instructions below. Not a confirmed turn-by-turn.
            </span>
          </p>
        )}

        {route && lineFeatures.length > 0 && (
          <Suspense fallback={<div className="suspense-fallback"><Spinner /></div>}>
            <DriveAlongControls
              key={route.key}
              lineFeatures={lineFeatures}
              onUpdate={setDriveAlongLive}
            />
          </Suspense>
        )}

        {route && (
          <div className="action-row no-print">
            <ExportMenu
              onGpx={() => downloadGpx(route, lineFeatures, centreId)}
              onPdf={() => window.print()}
            />
          </div>
        )}

        {route && (
          <div className="card discussion-link-card" style={{ marginBottom: "var(--space-md)" }}>
            <span className="discussion-link-card__text">{t("discussionAboutRoute")}</span>
            <a
              className="link-btn"
              href={`/discussion.html?centre=${encodeURIComponent(centreId)}&route=${encodeURIComponent(
                lineFeatures[0]?.properties.route_line_id || ""
              )}&type=${encodeURIComponent(classFilter)}&compose=1`}
            >
              {t("askAboutRoute")}
            </a>
          </div>
        )}

        <div className="route-alternatives">{selector}</div>

        <TurnFeed
          lines={lineFeatures}
          selectedLineId={selectedLineId}
          onSelectLine={(id) => setSelectedLineId((cur) => (cur === id ? null : id))}
        />

        {confidenceInfoOpen && <ConfidenceInfoModal onClose={() => setConfidenceInfoOpen(false)} />}
      </div>

      <div className="route-map-pane no-print">
        <LayersControl heatmap={heatmap} onChange={setHeatmap} />
        <MapContainer
          key={centreId}
          center={center}
          zoom={13}
          style={{ height: "600px", width: "100%" }}
        >
          {/* className applies to the tile container -- a restrained CSS
              desaturation so the base map recedes and the route line
              dominates, without touching any trust color (2026-09-21
              spec: "slightly reduce the visual intensity of the base
              OSM map without harming readability"). */}
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution="&copy; OpenStreetMap contributors"
            className="ontario-tiles"
          />
          <Marker position={center} icon={makeCentreIcon()}>
            <Tooltip permanent direction="top" offset={[0, -12]}>
              <b>DriveTest Centre</b>
            </Tooltip>
          </Marker>
          <GeoJSON
            key={route ? route.key : "none"}
            data={mapData}
            style={(feature) => routeLineStyle(feature, selectedLineId)}
            pointToLayer={makePointToLayer(heatmap)}
            onEachFeature={onEachFeature}
            eventHandlers={{ click: () => setSelectedLineId(null) }}
          />
          <FitToData geojson={mapData} centreLatLng={center} />
          <Suspense fallback={null}>
            <DriveAlongMarker live={driveAlongLive} />
          </Suspense>
        </MapContainer>
      </div>
      <style>{"@media print { .no-print { display: none !important; } }"}</style>
    </div>
  );
}

export default function MapView({ centreId, centreName }) {
  const { t } = useLang();
  const [geojson, setGeojson] = useState(null);
  const [error, setError] = useState(null);
  const [classFilter, setClassFilter] = useState(null);
  const [routeKey, setRouteKey] = useState(null);

  useEffect(() => {
    let stale = false;
    setGeojson(null);
    setError(null);
    setClassFilter(null);
    setRouteKey(null);
    api
      .getMap(centreId)
      .then((data) => {
        if (stale) return;
        setGeojson(data);
        // default: the single most-corroborated route across both classes
        // decides the class shown; then that class's top route is selected.
        const rs = buildRoutes(data);
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
  }, [centreId]);

  if (error) return <p style={{ color: "red" }}>{t("error")}: {error}</p>;
  if (!geojson) return <LoadingScreen label={t("loadingMap")} />;

  const center = CENTRE_COORDS[centreId] || [45, -76];
  const routes = buildRoutes(geojson);
  const classRoutes = classFilter ? routesForClass(routes, classFilter) : [];
  const selected = routes.find((r) => r.key === routeKey) || null;

  function selectClass(c) {
    setClassFilter(c);
    const forClass = routesForClass(routes, c);
    setRouteKey(forClass.length ? forClass[0].key : null);
  }

  if (!routes.length) {
    // e.g. Smiths Falls today: junctions collected but no route reconstructed
    // yet. Show the evidence points and say so plainly rather than a blank map.
    return (
      <div>
        <p style={{ fontSize: "0.9em", color: "var(--ink-muted)" }}>{t("noRoute")}</p>
        <RoutePanel
          centreId={centreId}
          centreName={centreName}
          classFilter={classFilter}
          routeIndex={0}
          geojson={geojson}
          route={null}
          center={center}
        />
      </div>
    );
  }

  // Segmented G/G2 control + a Google-Maps-directions-style list of route
  // alternatives -- rendered once, passed into RoutePanel's sidebar so it
  // sits above the route summary instead of floating above the whole
  // shell (map included) the way two separately-stacked blocks used to.
  const selector = (
    <>
      <div className="segmented">
        {["G", "G2"].map((c) => {
          const n = routesForClass(routes, c).length;
          return (
            <button
              key={c}
              onClick={() => selectClass(c)}
              disabled={!n}
              title={n ? `${n} route${n === 1 ? "" : "s"}` : "no routes"}
              aria-pressed={classFilter === c}
              style={{ cursor: n ? "pointer" : "not-allowed", opacity: n ? 1 : 0.4 }}
            >
              {c}
            </button>
          );
        })}
      </div>
      {classRoutes.map((r, i) => (
        <button
          key={r.key}
          onClick={() => setRouteKey(r.key)}
          className={`route-alt${routeKey === r.key ? " route-alt--selected" : ""}`}
        >
          <span className="route-alt__headline">
            {t("route")} {i + 1}
            {i === 0 ? " ★" : ""}
          </span>
          <div className="route-alt__meta">
            {r.manualVerified ? (
              <span className="trust-badge trust-badge--success">✓ {t("verified")}</span>
            ) : (
              <span className="trust-badge trust-badge--gap">
                {r.traces} {t("src")}
              </span>
            )}
            {r.cls === "unknown" ? ` · ${t("classUnknown")}` : ""}
          </div>
        </button>
      ))}
    </>
  );

  const routeIndex = selected ? classRoutes.findIndex((r) => r.key === selected.key) : 0;

  return (
    <RoutePanel
      centreId={centreId}
      centreName={centreName}
      classFilter={classFilter}
      routeIndex={routeIndex < 0 ? 0 : routeIndex}
      geojson={geojson}
      route={selected}
      center={center}
      selector={selector}
    />
  );
}