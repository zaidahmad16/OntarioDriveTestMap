import { useEffect, useState } from "react";
import { MapContainer, TileLayer, GeoJSON, Marker, Tooltip, useMap } from "react-leaflet";
import L from "leaflet";
import { api } from "../api.js";

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

const centreIcon = L.divIcon({
  className: "",
  html:
    '<div style="width:12px;height:12px;border-radius:50%;background:#111;' +
    'border:3px solid #ffd400;box-shadow:0 0 0 1px #111,0 1px 4px rgba(0,0,0,.6);"></div>',
  iconSize: [12, 12],
  iconAnchor: [6, 6],
});

// Class color-coding (G orange / G2 blue) was dropped by explicit
// request in favour of one uniform color for all real, sourced route
// data -- test_class is still filterable (buttons below) and still
// shown in each line's popup text, just no longer color-coded. Real
// data (confirmed AND below-threshold) is one solid red; nothing about
// this touches the one tier that still MUST stay visually separate.
const REAL_COLOR = "#e41a1c";

// predicted: this is NOT sourced evidence -- it's a road-snapped guess
// bridging two independently real, same-family points that no single
// trace ever connected directly (see predict_family_bridges.py). Line
// color/weight matches confirmed data exactly, by request. A permanent
// on-map label per segment was tried and dropped -- with several
// predicted segments clustered together it overlapped into unreadable
// clutter. Identification now lives off the map: the ratio banner, the
// instructions table's inline tag, and this color reserved for text/
// tags rather than the line itself.
const PREDICTED_COLOR = "#984ea3";

// A route line's road geometry comes from OSRM. When two consecutive
// junctions sit on disconnected pieces of the road graph, OSRM still
// answers "Ok" but returns a straight beeline for that leg -- a fake
// straight segment across whatever is between them (confirmed on the
// Walkley airport routes: an 778 m jump straight across greenspace).
// Drawn as part of the solid confirmed line it silently asserts a road
// that does not exist. GAP_THRESHOLD_M is set above the largest real
// sparse-road stretch in the data (~383 m) so only true beelines split.
const GAP_THRESHOLD_M = 400;
const GAP_COLOR = "#7f8c8d";

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

function routeLineStyle(feature) {
  const p = feature.properties;
  if (p.kind === "route_gap") {
    // Not a road: a straight, unrouted jump. Dashed + gray so it never
    // reads as confirmed driven geometry.
    return { color: GAP_COLOR, weight: 3, dashArray: "4, 8", opacity: 0.9 };
  }
  if (p.kind !== "route_line") return {};
  // Confirmed, below-threshold, and predicted all render the same solid
  // red line now -- see onEachFeature for how predicted stays labeled.
  return { color: REAL_COLOR, weight: 4 };
}

function pointToLayer(feature, latlng) {
  const authors = feature.properties.authors || 1;
  // Fade weakly-supported junctions so they read as secondary to the
  // strongly-corroborated ones on the actual routes -- a single-author,
  // weight-0.3 junction 6 km out shouldn't look as solid as a
  // multi-author junction on a confirmed route.
  const w = feature.properties.weight ?? 1;
  const fillOpacity = Math.max(0.2, Math.min(0.7, 0.2 + w * 0.35));
  return L.circleMarker(latlng, {
    radius: 4 + Math.min(authors, 6),
    fillColor: "#3388ff",
    color: "#3388ff",
    weight: 1,
    fillOpacity,
  });
}

function onEachFeature(feature, layer) {
  const p = feature.properties;
  if (p.kind === "route_gap") {
    layer.bindPopup(
      `<b style="color:#7f8c8d">⚠ Unrouted gap -- not a road</b><br/>` +
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
        `<b style="color:#984ea3">⚠ PREDICTED -- not sourced</b><br/>` +
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
        ? `<b style="color:#2a7">&#10003; Hand-verified from a real DriveTest video</b><br/>`
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
      map.fitBounds(bounds, { padding: [30, 30] });
    }
  }, [geojson, centreLatLng, map]);
  return null;
}

function formatDistance(m) {
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

function sumDuration(rows) {
  const totalSec = rows.reduce((a, r) => a + (r.duration_s || 0), 0);
  const mins = Math.floor(totalSec / 60);
  const secs = Math.round(totalSec % 60);
  return mins > 0 ? `${mins} min ${secs} s` : `${secs} s`;
}

// Real OSM tags (extract_traffic_data.py) -- absent for most segments
// simply because most streets in this extract aren't tagged with
// either (checked directly: ~4.7% of ways have a maxspeed at all).
// Blank cell means "not tagged," never a guessed default.
function formatTrafficControl(kind) {
  if (kind === "traffic_signals") return "Traffic light";
  if (kind === "stop") return "Stop sign";
  return "";
}

function formatSpeedLimit(v) {
  if (!v) return "";
  return /^\d+$/.test(v) ? `${v} km/h` : v; // plain number = km/h in this region; "45 mph" etc. kept as-is
}

// ONE turn-by-turn list for the whole route. lines is a single route's
// pieces (one family); flatten them into one continuous numbered list.
// A mid-route "Arrive at destination" is just where one collected piece
// ended, not the end of the drive, so only the very last one is kept.
function InstructionsTable({ lines }) {
  const rows = [];
  for (const l of lines) {
    const predicted = l.properties.predicted;
    for (const s of l.properties.steps || []) {
      rows.push({ ...s, predicted });
    }
  }
  const steps = rows.filter(
    (r, i) => r.instruction !== "Arrive at destination" || i === rows.length - 1
  );
  if (!steps.length) return null;

  // manual_youtube routes' steps are the owner's own transcript lines
  // (no per-step distance) -- fall back to the real, OSRM-measured
  // per-line distance_m instead of summing to a false "0 m".
  const stepsDistanceM = steps.reduce((a, r) => a + (r.distance_m || 0), 0);
  const totalDistanceM = stepsDistanceM || lines.reduce((a, l) => a + (l.properties.distance_m || 0), 0);
  // duration only exists per real OSRM step -- when every step is the
  // owner's own transcript line (no per-step timing), there's no real
  // number to show. "0 s" would be a fabricated, misleading total for a
  // multi-km route; omit it instead.
  const hasRealDuration = steps.some((r) => r.duration_s);
  const durationText = hasRealDuration ? `, ${sumDuration(steps)}` : "";

  return (
    <div style={{ marginTop: 12 }}>
      <p style={{ fontSize: "0.9em", marginBottom: 4 }}>
        <b>Total: {formatDistance(totalDistanceM)}{durationText}</b> for this
        route.
      </p>
      <p style={{ fontSize: "0.85em", color: "#555", marginBottom: 6 }}>
        Distances and durations are calculated by routing software between the
        collected data points, not measured from a live drive.{" "}
        <span style={{ background: "#f5eaf7", padding: "0 3px" }}>Shaded rows</span> are
        predicted (a road-snapped guess, see banner above), not sourced from a trace.
        Junction and speed-limit data is real OSM tagging where available -- blank means
        untagged, not "none."
      </p>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9em" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "2px solid #333" }}>
            <th style={{ padding: "4px 8px" }}>#</th>
            <th style={{ padding: "4px 8px" }}>Instruction</th>
            <th style={{ padding: "4px 8px" }}>Distance</th>
            <th style={{ padding: "4px 8px" }}>Duration</th>
            <th style={{ padding: "4px 8px" }}>At junction</th>
            <th style={{ padding: "4px 8px" }}>Speed limit</th>
          </tr>
        </thead>
        <tbody>
          {steps.map((r, i) => (
            <tr
              key={i}
              style={{
                background: r.predicted ? "#f5eaf7" : i % 2 ? "#f7f7f7" : "white",
                borderBottom: "1px solid #eee",
              }}
            >
              <td style={{ padding: "4px 8px" }}>{i + 1}</td>
              <td style={{ padding: "4px 8px" }}>{r.instruction}</td>
              <td style={{ padding: "4px 8px" }}>{formatDistance(r.distance_m)}</td>
              <td style={{ padding: "4px 8px" }}>{formatDuration(r.duration_s)}</td>
              <td style={{ padding: "4px 8px" }}>{formatTrafficControl(r.traffic_control)}</td>
              <td style={{ padding: "4px 8px" }}>{formatSpeedLimit(r.speed_limit)}</td>
            </tr>
          ))}
        </tbody>
      </table>
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

function RoutePanel({ centreId, geojson, route, center }) {
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
  const totalDur = steps.reduce((a, s) => a + (s.duration_s || 0), 0);
  // manual_youtube routes show the owner's own transcript lines as
  // instructions (see manual_routes.py's TRANSCRIPT), which don't carry
  // a real per-step distance -- summing steps would silently show "0 m"
  // for a route that's actually several km. The route's own real,
  // OSRM-measured distance_m (always present regardless of step
  // wording) is the honest total here.
  const totalDist = route && route.manualVerified
    ? lineFeatures.reduce((a, f) => a + (f.properties.distance_m || 0), 0)
    : steps.reduce((a, s) => a + (s.distance_m || 0), 0);

  return (
    <div>
      {route && (
        <p style={{ fontSize: "0.9em", marginBottom: 6 }}>
          <b>
            {formatDistance(totalDist)}
            {totalDur ? `, ~${Math.round(totalDur / 60)} min driving` : ""}
          </b>{" "}
          {route.manualVerified ? (
            <span style={{ color: "#2a7" }}>&#10003; hand-verified from a real DriveTest video</span>
          ) : (
            <>from {route.traces} source{route.traces === 1 ? "" : "s"}</>
          )}
          . This is one reconstructed route; it may be partial where sources
          didn't cover every street.
        </p>
      )}
      {gapCount > 0 && (
        <p style={{ background: "#f0f1f2", border: "1px solid #7f8c8d", color: "#4d5656", padding: "6px 10px", borderRadius: 4, fontSize: "0.85em", marginBottom: 8 }}>
          {gapCount} <b>unrouted gap{gapCount === 1 ? "" : "s"}</b> shown{" "}
          <span style={{ color: "#7f8c8d", fontWeight: "bold" }}>dashed gray</span>: the
          data jumps straight where the two ends don't connect on the road map -- not a
          driven road.
        </p>
      )}
      {predictedCount > 0 && (
        <p style={{ background: "#f5eaf7", border: "1px solid #984ea3", color: "#5c1f66", padding: "6px 10px", borderRadius: 4, fontSize: "0.85em", marginBottom: 8 }}>
          Parts of this route are{" "}
          <span style={{ color: "#984ea3", fontWeight: "bold" }}>predicted</span> -- a
          road-snapped guess connecting two real points no single source drove between,
          tagged in the instructions below. Not a confirmed turn-by-turn.
        </p>
      )}
      <MapContainer
        key={`${centreId}-${route ? route.key : "none"}`}
        center={center}
        zoom={13}
        style={{ height: "600px", width: "100%" }}
      >
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution="&copy; OpenStreetMap contributors"
        />
        <Marker position={center} icon={centreIcon}>
          <Tooltip permanent direction="top" offset={[0, -12]}>
            <b>DriveTest Centre</b>
          </Tooltip>
        </Marker>
        <GeoJSON
          data={mapData}
          style={routeLineStyle}
          pointToLayer={pointToLayer}
          onEachFeature={onEachFeature}
        />
        <FitToData geojson={mapData} centreLatLng={center} />
      </MapContainer>
      <InstructionsTable lines={lineFeatures} />
    </div>
  );
}

export default function MapView({ centreId }) {
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

  if (error) return <p style={{ color: "red" }}>Error: {error}</p>;
  if (!geojson) return <p>Loading map…</p>;

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
        <p style={{ fontSize: "0.9em", color: "#555" }}>
          No route reconstructed for this centre yet -- the collected sources cover
          individual junctions (shown below) but not enough of a connected path to
          draw a route. The dots are the real evidence gathered so far.
        </p>
        <RoutePanel centreId={centreId} geojson={geojson} route={null} center={center} />
      </div>
    );
  }

  return (
    <div>
      <div style={{ marginBottom: 8 }}>
        {["G", "G2"].map((c) => {
          const n = routesForClass(routes, c).length;
          return (
            <button
              key={c}
              onClick={() => selectClass(c)}
              disabled={!n}
              title={n ? `${n} route${n === 1 ? "" : "s"}` : "no routes"}
              style={{
                marginRight: 6,
                padding: "4px 12px",
                fontWeight: classFilter === c ? "bold" : "normal",
                border: classFilter === c ? "2px solid #333" : "1px solid #ccc",
                cursor: n ? "pointer" : "not-allowed",
                opacity: n ? 1 : 0.4,
              }}
            >
              {c}
            </button>
          );
        })}
      </div>
      {/* one button per distinct route in the selected class, popular first */}
      <div style={{ marginBottom: 10, display: "flex", flexWrap: "wrap", gap: 6 }}>
        {classRoutes.map((r, i) => (
          <button
            key={r.key}
            onClick={() => setRouteKey(r.key)}
            style={{
              padding: "4px 10px",
              fontSize: "0.9em",
              fontWeight: routeKey === r.key ? "bold" : "normal",
              border: routeKey === r.key ? "2px solid #e41a1c" : "1px solid #ccc",
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            Route {i + 1}
            {i === 0 ? " ★" : ""}{" "}
            <span style={{ color: r.manualVerified ? "#2a7" : "#777", fontWeight: "normal" }}>
              · {r.manualVerified ? "✓ verified" : `${r.traces} src`}
              {r.cls === "unknown" ? " · class ?" : ""}
            </span>
          </button>
        ))}
      </div>
      <RoutePanel centreId={centreId} geojson={geojson} route={selected} center={center} />
    </div>
  );
}