import { useEffect, useState } from "react";
import { MapContainer, TileLayer, GeoJSON, useMap } from "react-leaflet";
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

// Class color-coding (G orange / G2 blue) was dropped by explicit
// request in favour of one uniform color for all real, sourced route
// data -- test_class is still filterable (buttons below) and still
// shown in each line's popup text, just no longer color-coded. Real
// data (confirmed AND below-threshold) is one solid red; nothing about
// this touches the one tier that still MUST stay visually separate.
const REAL_COLOR = "#e41a1c";

// predicted: this is NOT sourced evidence -- it's a road-snapped guess
// bridging two independently real, same-family points that no single
// trace ever connected directly (see predict_family_bridges.py). This
// is the one tier where a real person could drive a turn nobody
// actually confirmed, so it does not get to share a color with
// anything confirmed, at any zoom, ever -- this held even when class
// color-coding for everything else was dropped. Bright, unmistakable,
// never reused elsewhere on this map.
const PREDICTED_COLOR = "#984ea3";

function routeLineStyle(feature) {
  const p = feature.properties;
  if (p.kind !== "route_line") return {};

  if (p.predicted) {
    return { color: PREDICTED_COLOR, weight: 3, opacity: 0.85, dashArray: "1 8" };
  }

  // Confirmed and below-threshold both real, sourced evidence -- both
  // solid now, by request. below_threshold is still exposed in the
  // popup text so the confidence distinction isn't lost, just not
  // carried in the line style anymore.
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
  if (filter === "all") return true;
  return feature.properties.test_class === filter;
}

// Frames the view on whatever real geometry actually exists for this
// centre, instead of a hardcoded guess-coordinate at a fixed zoom.
// Real routes read as "finished" when the map opens already looking at
// them, not when a user has to pan/zoom to find a thin line somewhere
// in a wide default view.
function FitToData({ geojson }) {
  const map = useMap();
  useEffect(() => {
    if (!geojson || !geojson.features.length) return;
    const bounds = L.geoJSON(geojson).getBounds();
    if (bounds.isValid()) {
      map.fitBounds(bounds, { padding: [30, 30] });
    }
  }, [geojson, map]);
  return null;
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
  const hasPredicted = filtered.features.some(
    (f) => f.properties.kind === "route_line" && f.properties.predicted
  );

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
            fontSize: "0.9em",
            marginBottom: 8,
          }}
        >
          <b style={{ color: "#984ea3" }}>⚠ Dotted purple lines are predicted</b>{" "}
          -- road-snapped guesses connecting real, confirmed points that no
          single source actually walked between. Not a confirmed route.
        </p>
      )}
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
        <FitToData geojson={filtered} />
      </MapContainer>
    </div>
  );
}