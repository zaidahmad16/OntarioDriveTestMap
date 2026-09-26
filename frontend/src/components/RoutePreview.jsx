import { useEffect, useMemo, useState } from "react";
import { MapContainer, TileLayer, GeoJSON, Marker, useMap } from "react-leaflet";
import L from "leaflet";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";
import { RouteLegend } from "../RouteNotation.jsx";
import {
  CENTRE_COORDS,
  makeCentreIcon,
  buildRoutes,
  routesForClass,
  splitAtGaps,
  isDegenerateRouteLine,
  routeLineStyle,
  casingStyle,
  lineStatus,
  formatDistance,
} from "./MapView.jsx";

// Guest-home product preview (guest spec §4.1): a REAL published route,
// drawn with the same tiles and confidence styles as the app, labelled
// with its true centre / class / route number and linked to it. Picked
// the same way MapView picks its default (most-corroborated route of
// the best-supported centre), so the label here matches what "Open this
// route" shows. Never a schematic, never decoration.

function Fit({ bounds }) {
  const map = useMap();
  useEffect(() => {
    if (bounds?.isValid()) map.fitBounds(bounds, { padding: [24, 24] });
  }, [bounds, map]);
  return null;
}

export default function RoutePreview({ centres }) {
  const { t } = useLang();
  const [state, setState] = useState({ status: "loading" });

  const candidate = useMemo(() => {
    const withRoutes = centres
      .filter((c) => (c.confirmed_route_line_count ?? 0) > 0 && CENTRE_COORDS[c.id])
      .sort((a, b) => b.confirmed_route_line_count - a.confirmed_route_line_count || b.trace_count - a.trace_count);
    return withRoutes[0] || null;
  }, [centres]);

  useEffect(() => {
    if (!candidate) return;
    let stale = false;
    api
      .getMap(candidate.id)
      .then((geojson) => {
        if (stale) return;
        const routes = buildRoutes(geojson);
        const best = routes.slice().sort((a, b) => b.traces - a.traces)[0];
        if (!best) return setState({ status: "empty" });
        const cls = best.cls === "G" || best.cls === "G2" ? best.cls : "G2";
        const forClass = routesForClass(routes, cls);
        const route = forClass[0];
        const idx = 0;
        const lines = route.features.filter((f) => !isDegenerateRouteLine(f));
        const data = { type: "FeatureCollection", features: lines.flatMap(splitAtGaps) };
        const bounds = L.geoJSON(data).getBounds();
        bounds.extend(CENTRE_COORDS[candidate.id]);
        const statuses = ["confirmed", "inferred", "gap"].filter((s) => data.features.some((f) => lineStatus(f.properties) === s));
        const distance = lines.reduce((a, f) => a + (f.properties.distance_m || 0), 0) || null;
        setState({ status: "ready", cls, idx, route, data, bounds, statuses, distance });
      })
      .catch(() => !stale && setState({ status: "error" }));
    return () => {
      stale = true;
    };
  }, [candidate]);

  if (!candidate || state.status === "empty") return null;

  const href =
    state.status === "ready"
      ? `/?centre=${encodeURIComponent(candidate.id)}&class=${state.cls}&route=${state.idx + 1}`
      : `/?centre=${encodeURIComponent(candidate.id)}`;
  const label = state.status === "ready" ? `${state.cls} ${t("route")} ${state.idx + 1}` : "";

  return (
    <figure className="route-preview" aria-labelledby="route-preview-caption">
      <div className="route-preview__map">
        {state.status === "ready" ? (
          <MapContainer
            center={CENTRE_COORDS[candidate.id]}
            zoom={12}
            className="route-preview__leaflet"
            dragging={false}
            scrollWheelZoom={false}
            doubleClickZoom={false}
            touchZoom={false}
            boxZoom={false}
            keyboard={false}
            zoomControl={false}
            attributionControl
          >
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              className="ontario-tiles"
            />
            <GeoJSON data={state.data} style={casingStyle} interactive={false} />
            <GeoJSON data={state.data} style={(f) => routeLineStyle(f, null, false)} interactive={false} />
            <Marker position={CENTRE_COORDS[candidate.id]} icon={makeCentreIcon()} interactive={false} keyboard={false} />
            <Fit bounds={state.bounds} />
          </MapContainer>
        ) : (
          <div className="route-preview__placeholder" aria-hidden={state.status === "loading"}>
            {state.status === "error" ? <p>{t("previewUnavailable")}</p> : null}
          </div>
        )}
        {state.status === "ready" && (
          <div className="route-preview__legend">
            <RouteLegend statuses={state.statuses} showCentre />
          </div>
        )}
      </div>
      <figcaption id="route-preview-caption" className="route-preview__caption">
        <span className="route-preview__label">
          <span className="route-preview__centre">{candidate.name}</span>
          {label && <> · {label}</>}
          {state.distance && <span className="route-preview__dist"> · {formatDistance(state.distance)}</span>}
        </span>
        <a className="btn route-preview__open" href={href}>
          {t("openThisRoute")} →
        </a>
      </figcaption>
    </figure>
  );
}
