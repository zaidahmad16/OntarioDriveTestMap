// Geometry helpers for practice drive (moved from the old
// DriveAlongMode.jsx). Coordinates are [lon, lat] like GeoJSON. Flat-plane
// projection scaled by cos(latitude) is accurate enough at the scale of
// one drive-test route (a few km).

export function haversineM(a, b) {
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
