import { useEffect, useState } from "react";
import { MapContainer, TileLayer, GeoJSON } from "react-leaflet";
import L from "leaflet";
import { api } from "../api.js";

// Rough starting coordinates per centre, just to point the map somewhere
// sensible before the real geometry loads and Leaflet can fit to it.
const CENTRE_COORDS = {
  walkley: [45.3761, -75.6476],
  canotek: [45.4497, -75.5744],
  smithsfalls: [44.8828, -76.0151],
  winchester: [45.0847, -75.3495],
};

// Functional distinction between test classes -- not a design choice, a
// requirement: a user needs to tell G from G2 (and G2 route variants
// apart) at a glance, since route_lines carry no other visual cue.
const CLASS_COLORS = {
  G: "#e6550d",
  G2: "#3182bd",
};

// NULL and mixed are different findings and must not look the same:
// NULL means "validated geometry, no confirmed G/G2 trace behind it"
// (real route, missing label -- solid, so it doesn't read as "less
// real" than a classed route). Mixed means "confirmed G and confirmed
// G2 traces both feed this family" -- dashed, so a genuine class
// conflict stays visually flagged as different from an absence of
// data. class_vote() currently never returns mixed=true (see
// common/classvote.py), so this style has no live example yet -- kept
// here rather than removed, for whenever real data produces one.
const UNCLASSED_COLOR = "#888888";

// connected_segments is a second, lower-precision layer: real scored
// junctions chained by shared street name (see backend/main.py's
// _connect_segments), not the trace-clustered, OSRM-snapped official
// route_lines. Thin and pale on purpose -- it should read as "here's
// the connected shape the evidence forms," not compete visually with
// an actual published route.
const CONNECTED_SEGMENTS_COLOR = "#2ca25f";

function routeLineStyle(feature) {
  const p = feature.properties;
  if (p.kind === "connected_segments") {
    return { color: CONNECTED_SEGMENTS_COLOR, weight: 2, opacity: 0.7 };
  }
  if (p.kind !== "route_line") return {};
  if (p.mixed_classes) {
    return { color: UNCLASSED_COLOR, weight: 4, dashArray: "6 4" };
  }
  if (!p.test_class) {
    return { color: UNCLASSED_COLOR, weight: 4, dashArray: null };
  }
  return { color: CLASS_COLORS[p.test_class] || "#31a354", weight: 4, dashArray: null };
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
  } else if (p.kind === "connected_segments") {
    layer.bindPopup(
      `<b>Connected evidence path</b><br/>` +
        `${p.segment_count} scored junctions chained by shared street name.<br/>` +
        `Not an official published route -- see individual dots for each ` +
        `junction's own author/video/text support.`
    );
  } else if (p.kind === "route_line") {
    const classLabel = p.mixed_classes
      ? `${p.test_class || "class unknown"} (mixed -- traces disagree)`
      : p.test_class || "class unknown";
    layer.bindPopup(
      `<b>${classLabel} route -- family ${p.family}</b><br/>` +
        `${p.trace_count} traces, ${p.authors} authors<br/>` +
        `${p.distance_m}m`
    );
  }
}

// Route lines only -- consensus_segments (the dots) carry no test_class
// at all, so a class filter has nothing to say about them one way or
// the other. They stay visible regardless of which button is active.
function matchesFilter(feature, filter) {
  if (feature.properties.kind !== "route_line") return true;
  if (filter === "all") return true;
  return feature.properties.test_class === filter;
}

export default function MapView({ centreId }) {
  const [geojson, setGeojson] = useState(null);
  const [error, setError] = useState(null);
  const [classFilter, setClassFilter] = useState("all");

  useEffect(() => {
    setClassFilter("all"); // don't carry a filter across to a different centre
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
  const filtered = {
    ...geojson,
    features: geojson.features.filter((f) => matchesFilter(f, classFilter)),
  };

  return (
    <div>
      <div style={{ marginBottom: 8 }}>
        {["all", "G", "G2"].map((f) => (
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
            {f === "all" ? "All routes" : f}
          </button>
        ))}
      </div>
      <MapContainer
        key={centreId} // force a clean remount per centre, avoids stale-view bugs
        center={center}
        zoom={13}
        style={{ height: "600px", width: "100%" }}
      >
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution="&copy; OpenStreetMap contributors"
        />
        <GeoJSON
          key={classFilter} // react-leaflet's GeoJSON doesn't reliably re-diff
          // an in-place data swap -- remount on filter change instead, same
          // reasoning as the MapContainer's own centreId key above.
          data={filtered}
          style={routeLineStyle}
          pointToLayer={pointToLayer}
          onEachFeature={onEachFeature}
        />
      </MapContainer>
    </div>
  );
}