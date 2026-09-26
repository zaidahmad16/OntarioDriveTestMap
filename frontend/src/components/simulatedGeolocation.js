// DEV-ONLY simulated GPS for practice drive (`?simulateDrive=1`, or
// `?simulateDrive=8` for 8x speed). Lets the owner see the follower work
// without driving. Guarded by import.meta.env.DEV at the call site, so it
// never ships in a production build, and the UI shows a permanent
// "Simulated location" chip while it's active.
//
// It "drives" the selected route's own geometry at ~50 km/h x speed, with
// a little GPS jitter, and deliberately walks through the states the real
// follower has to handle:
//   ~30-38% of the way: accuracy degrades to ~90 m  -> Position uncertain
//   ~60-72% of the way: drifts ~150 m off the line  -> Possibly off route
// then returns to the route and continues to the end.
import { buildTrack, pointAt } from "./RoutePlayback.jsx";

const BASE_SPEED_MPS = 14; // ~50 km/h
const TICK_MS = 1000;

export function simulationSpeed() {
  if (!import.meta.env.DEV || typeof window === "undefined") return 0;
  try {
    const raw = new URLSearchParams(window.location.search).get("simulateDrive");
    if (raw != null) sessionStorage.setItem("simulateDrive", raw || "1");
    const v = Number(sessionStorage.getItem("simulateDrive"));
    if (!v) return 0;
    // Capped at 10x: faster steps exceed the follower's 250 m-in-5 s
    // teleport guard, so fixes would (correctly) be rejected.
    return v === 1 ? 5 : Math.min(10, Math.max(1, v));
  } catch {
    return 0;
  }
}

// Offset a [lat, lon] point by metres east/north.
function offset([lat, lon], east, north) {
  const dLat = north / 111320;
  const dLon = east / (111320 * Math.cos((lat * Math.PI) / 180));
  return [lat + dLat, lon + dLon];
}

function jitter(m) {
  return (Math.random() * 2 - 1) * m;
}

export function makeSimulatedGeolocation(lineFeatures, speedX) {
  const track = buildTrack(lineFeatures);
  const watches = new Map();
  let nextId = 1;
  // Progress survives Pause/Resume and Recheck, like a real car would.
  let dist = 0;

  function sample(dist) {
    const frac = track.total ? dist / track.total : 0;
    let [lat, lon] = pointAt(track, dist) || [0, 0];
    let accuracy = 6 + Math.random() * 6;
    let noise = 4;
    if (frac > 0.3 && frac < 0.38) {
      accuracy = 80 + Math.random() * 20; // weak GPS stretch
      noise = 25;
    }
    if (frac > 0.6 && frac < 0.72) {
      // ramp out to ~150 m and back so there's no teleport-sized jump
      const k = Math.sin(((frac - 0.6) / 0.12) * Math.PI);
      [lat, lon] = offset([lat, lon], 150 * k, 60 * k);
    }
    [lat, lon] = offset([lat, lon], jitter(noise), jitter(noise));
    return { lat, lon, accuracy };
  }

  return {
    watchPosition(success) {
      const id = nextId++;
      const step = BASE_SPEED_MPS * speedX * (TICK_MS / 1000);
      // First fix after a realistic acquisition delay.
      const emit = () => {
        const { lat, lon, accuracy } = sample(dist);
        success({
          coords: { latitude: lat, longitude: lon, accuracy, heading: null, speed: null, altitude: null, altitudeAccuracy: null },
          timestamp: Date.now(),
        });
        dist = Math.min(track.total, dist + step);
      };
      const first = setTimeout(emit, 1200);
      const timer = setInterval(emit, TICK_MS);
      watches.set(id, () => {
        clearTimeout(first);
        clearInterval(timer);
      });
      return id;
    },
    clearWatch(id) {
      const stop = watches.get(id);
      if (stop) stop();
      watches.delete(id);
    },
    getCurrentPosition(success) {
      const { lat, lon, accuracy } = sample(0);
      success({ coords: { latitude: lat, longitude: lon, accuracy }, timestamp: Date.now() });
    },
  };
}
