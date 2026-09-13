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
    '<div style="width:22px;height:22px;border-radius:50%;background:#111;' +
    'border:4px solid #ffd400;box-shadow:0 0 0 2px #111,0 1px 6px rgba(0,0,0,.6);"></div>',
  iconSize: [22, 22],
  iconAnchor: [11, 11],
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
// color/weight now matches confirmed data exactly, by request -- the
// distinction moved from the line style to a permanent on-map label
// (see onEachFeature) sitting directly on the segment itself, so it's
// still identifiable at the exact point of use, just not via color.
const PREDICTED_COLOR = "#984ea3";

function routeLineStyle(feature) {
  const p = feature.properties;
  if (p.kind !== "route_line") return {};
  // Confirmed, below-threshold, and predicted all render the same solid
  // red line now -- see onEachFeature for how predicted stays labeled.
  return { color: REAL_COLOR, weight: 4 };
}

function pointToLayer(feature, latlng) {
  const authors = feature.properties.authors || 1;
  return L.circleMarker(latlng, {
    radius: 4 + Math.min(authors, 6),
    fillColor: "#3388ff",
    color: "#3388ff",
    weight: 1,
    fillOpacity: 0.6,
  });
}

function onEachFeature(feature, layer) {
  const p = feature.properties;
  if (p.kind === "segment") {
    layer.bindPopup(
      `<b>${p.streets.join(" × ")}</b><br/>` +
        `${p.authors} author(s), weight ${p.weight.toFixed(2)}<br/>` +
        `${p.video_count} video, ${p.text_count} text<br/>` +
        `last seen: ${p.last_seen || "unknown"}`
    );
  } else if (p.kind === "route_line") {
    if (p.predicted) {
      // Line color/weight matches confirmed data now -- this permanent
      // label is the ONLY thing that still marks this specific segment
      // as predicted at a glance, so it stays visible without a click,
      // right on the segment itself, not just in a general disclaimer.
      layer.bindTooltip(
        `<span style="color:#984ea3;font-weight:bold;">predicted</span>`,
        { permanent: true, direction: "center", className: "predicted-tooltip" }
      );
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
    layer.bindPopup(
      `<b>${classLabel} route -- family ${p.family}</b><br/>` +
        `${p.trace_count} traces, ${p.authors} authors<br/>` +
        `${p.distance_m}m${confidenceNote}`
    );
  }
}

// Route lines only -- consensus_segments (the dots) carry no test_class
// at all, so a class filter has nothing to say about them one way or
// the other. They stay visible regardless of which button is active.
function matchesFilter(feature, filter) {
  if (feature.properties.kind !== "route_line") return true;
  return feature.properties.test_class === filter;
}

// Frames the view on whatever real geometry actually exists for this
// centre, instead of a hardcoded guess-coordinate at a fixed zoom.
// Real routes read as "finished" when the map opens already looking at
// them, not when a user has to pan/zoom to find a thin line somewhere
// in a wide default view.
function FitToData({ geojson, centreLatLng }) {
  const map = useMap();
  useEffect(() => {
    if (!geojson || !geojson.features.length) return;
    const bounds = L.geoJSON(geojson).getBounds();
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
  if (s === 0) return "0 min";
  const mins = Math.max(1, Math.round(s / 60));
  return `${mins} min`;
}

// Turn-by-turn table, concatenated across every route_line in the
// current class -- one running numbered list, in family/run order (the
// API already sorts that way). Each row's instruction/distance/duration
// comes straight from OSRM's own step breakdown for that line's real,
// already-stored geometry -- not re-derived or guessed here.
function InstructionsTable({ lines }) {
  const rows = [];
  for (const line of lines) {
    const p = line.properties;
    for (const step of p.steps || []) {
      rows.push({ ...step, predicted: p.predicted });
    }
  }
  if (!rows.length) return null;

  return (
    <div style={{ marginTop: 12 }}>
      <p style={{ fontSize: "0.85em", color: "#555", marginBottom: 6 }}>
        Distances and durations are calculated by routing software between
        the collected data points, not measured from a live drive.
      </p>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9em" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "2px solid #333" }}>
            <th style={{ padding: "4px 8px" }}>#</th>
            <th style={{ padding: "4px 8px" }}>Instruction</th>
            <th style={{ padding: "4px 8px" }}>Distance</th>
            <th style={{ padding: "4px 8px" }}>Duration</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={i}
              style={{
                background: i % 2 ? "#f7f7f7" : "white",
                borderBottom: "1px solid #eee",
              }}
            >
              <td style={{ padding: "4px 8px" }}>{i + 1}</td>
              <td style={{ padding: "4px 8px" }}>
                {r.instruction}
                {r.predicted && (
                  <span style={{ color: "#984ea3", fontWeight: "bold" }}> (predicted)</span>
                )}
              </td>
              <td style={{ padding: "4px 8px" }}>{formatDistance(r.distance_m)}</td>
              <td style={{ padding: "4px 8px" }}>{formatDuration(r.duration_s)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// One map, one class visible at a time -- switching via the buttons
// below fully remounts the map (key includes classFilter) so there is
// never a moment where both classes' geometry is present together.
// Two side-by-side maps were tried first and explicitly rejected: same
// underlying dots (they carry no class at all) shown twice side by
// side read as duplicated/overlapping, not as a clean comparison.
function RoutePanel({ centreId, geojson, classFilter, center }) {
  const filtered = {
    ...geojson,
    features: geojson.features.filter((f) => matchesFilter(f, classFilter)),
  };
  const routeLines = filtered.features.filter((f) => f.properties.kind === "route_line");
  const predictedCount = routeLines.filter((f) => f.properties.predicted).length;
  const hasPredicted = predictedCount > 0;

  return (
    <div>
      {hasPredicted && (
        <p
          style={{
            background: "#f5eaf7",
            border: "1px solid #984ea3",
            color: "#5c1f66",
            padding: "6px 10px",
            borderRadius: 4,
            fontSize: "0.85em",
            marginBottom: 8,
          }}
        >
          {routeLines.length - predictedCount}/{routeLines.length} of this route is
          confirmed; the rest (labeled{" "}
          <span style={{ color: "#984ea3", fontWeight: "bold" }}>predicted</span> directly
          on the map) is a road-snapped inference, not a confirmed turn-by-turn instruction.
        </p>
      )}
      <MapContainer
        key={`${centreId}-${classFilter}`} // force a clean remount per centre+class
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
          data={filtered}
          style={routeLineStyle}
          pointToLayer={pointToLayer}
          onEachFeature={onEachFeature}
        />
        <FitToData geojson={filtered} centreLatLng={center} />
      </MapContainer>
      <InstructionsTable
        lines={filtered.features.filter((f) => f.properties.kind === "route_line")}
      />
    </div>
  );
}

export default function MapView({ centreId }) {
  const [geojson, setGeojson] = useState(null);
  const [error, setError] = useState(null);
  const [classFilter, setClassFilter] = useState("G");

  useEffect(() => {
    setClassFilter("G"); // don't carry a filter across to a different centre
  }, [centreId]);

  useEffect(() => {
    let stale = false;
    setGeojson(null);
    setError(null);
    api
      .getMap(centreId)
      .then((data) => {
        if (!stale) setGeojson(data);
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

  return (
    <div>
      <div style={{ marginBottom: 8 }}>
        {["G", "G2"].map((f) => (
          <button
            key={f}
            onClick={() => setClassFilter(f)}
            style={{
              marginRight: 6,
              padding: "4px 10px",
              fontWeight: classFilter === f ? "bold" : "normal",
              border: classFilter === f ? "2px solid #333" : "1px solid #ccc",
              cursor: "pointer",
            }}
          >
            {f}
          </button>
        ))}
      </div>
      <RoutePanel
        centreId={centreId}
        geojson={geojson}
        classFilter={classFilter}
        center={center}
      />
    </div>
  );
}