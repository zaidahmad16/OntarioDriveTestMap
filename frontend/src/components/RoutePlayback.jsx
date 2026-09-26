import { useEffect, useRef } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";
import { useLang } from "../i18n.jsx";

// Route playback: a dot that travels along the selected route's own
// drawn geometry, start to finish. It never goes anywhere the route
// line doesn't -- positions are interpolated between the route's real
// coordinates only. Where the data has an unrouted beeline gap (same
// 400 m rule MapView uses to draw gaps as "not a road"), the dot hops
// across instantly instead of pretending to drive it.

const GAP_THRESHOLD_M = 400;
// Visual pacing only (not a real driving speed): ~1.6 s per km, clamped
// so short G2 loops and long G routes both take a watchable time.
const MIN_PLAY_MS = 12000;
const MAX_PLAY_MS = 45000;
const MS_PER_KM = 1600;

function haversine(a, b) {
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLon = toRad(b[0] - a[0]);
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

// Flatten a route's line features (in route order) into one track of
// segments. `along` is cumulative DRIVEN distance: gap segments add 0,
// so the dot's distance readout only ever counts drawn road.
export function buildTrack(lineFeatures) {
  const segs = [];
  let along = 0;
  let prev = null;
  for (const f of lineFeatures) {
    const coords = f.geometry?.coordinates || [];
    for (const c of coords) {
      if (prev) {
        const d = haversine(prev, c);
        if (d > 0) {
          const jump = d > GAP_THRESHOLD_M;
          segs.push({ from: prev, to: c, start: along, len: jump ? 0 : d, jump });
          if (!jump) along += d;
        }
      }
      prev = c;
    }
  }
  const first = lineFeatures[0]?.geometry?.coordinates?.[0] || null;
  return {
    segs,
    total: along,
    start: first,
    durationMs: Math.min(MAX_PLAY_MS, Math.max(MIN_PLAY_MS, (along / 1000) * MS_PER_KM)),
  };
}

// [lat, lng] at a driven distance along the track.
export function pointAt(track, dist) {
  if (!track.segs.length) return track.start ? [track.start[1], track.start[0]] : null;
  const d = Math.max(0, Math.min(track.total, dist));
  let lo = 0;
  let hi = track.segs.length - 1;
  // last segment whose start <= d (gap segments have len 0 and are skipped over)
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (track.segs[mid].start <= d) lo = mid;
    else hi = mid - 1;
  }
  let i = lo;
  while (i < track.segs.length - 1 && track.segs[i].jump) i++;
  const s = track.segs[i];
  const t = s.len ? Math.min(1, Math.max(0, (d - s.start) / s.len)) : 1;
  const lon = s.from[0] + (s.to[0] - s.from[0]) * t;
  const lat = s.from[1] + (s.to[1] - s.from[1]) * t;
  return [lat, lon];
}

function makeIcon() {
  return L.divIcon({
    className: "route-playhead",
    html: '<span class="route-playhead__halo"></span><span class="route-playhead__dot"></span>',
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

// Lives inside <MapContainer>. Owns the animation loop so the ~60fps
// position updates never re-render React; the UI readout is reported
// back through onProgress at a much lower rate.
export function PlaybackLayer({ track, active, playing, dist, posRef, speed = 1, onProgress, onEnd }) {
  const map = useMap();
  const markerRef = useRef(null);
  // posRef is owned by the parent so Pause/scrub read the exact live
  // position instead of a throttled (up to 120 ms stale) report.
  const distRef = posRef;

  // Create/destroy the marker with the track (i.e. per route).
  useEffect(() => {
    if (!active) return;
    const p = pointAt(track, distRef.current);
    if (!p) return;
    const m = L.marker(p, { icon: makeIcon(), interactive: false, keyboard: false, zIndexOffset: 1000 });
    m.addTo(map);
    markerRef.current = m;
    return () => {
      m.remove();
      markerRef.current = null;
    };
  }, [active, track, map]);

  // External position changes (slider scrub, restart) while paused.
  useEffect(() => {
    distRef.current = dist;
    if (!playing && markerRef.current) {
      const p = pointAt(track, dist);
      if (p) {
        markerRef.current.setLatLng(p);
        // A scrub can land anywhere on the route; bring the dot into
        // view only if it's actually outside it.
        if (!map.getBounds().pad(-0.08).contains(p)) map.panInside(p, { padding: [60, 60], animate: true });
      }
    }
  }, [dist, playing, track, map]);

  useEffect(() => {
    if (!playing || !track.total) return;
    const mPerMs = (track.total / track.durationMs) * speed; // metres per ms
    let raf;
    let last = performance.now();
    let lastReport = 0;
    const frame = (now) => {
      const dt = Math.min(64, now - last); // clamp: a backgrounded tab shouldn't teleport
      last = now;
      distRef.current = Math.min(track.total, distRef.current + dt * mPerMs);
      const p = pointAt(track, distRef.current);
      if (p && markerRef.current) {
        markerRef.current.setLatLng(p);
        // Only move the map when the dot is about to leave the view --
        // never a continuous follow-cam that fights the user's pan/zoom.
        if (!map.getBounds().pad(-0.08).contains(p)) map.panInside(p, { padding: [60, 60], animate: true });
      }
      if (distRef.current >= track.total) {
        onProgress(track.total);
        onEnd();
        return;
      }
      if (now - lastReport > 120) {
        lastReport = now;
        onProgress(distRef.current);
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    // No progress report on cleanup: a scrub that stopped playback has
    // already set the new position, and reporting here would undo it.
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, track, map, speed]);

  return null;
}

function prefersReducedMotion() {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

// Controls strip under the map (not floating over roads).
export const PLAYBACK_SPEEDS = [0.5, 1, 2, 4];

export function PlaybackBar({ track, dist, playing, speed = 1, onSpeed, onPlay, onPause, onScrub, formatDistance }) {
  const { t } = useLang();
  const reduced = prefersReducedMotion();
  const atEnd = track.total > 0 && dist >= track.total - 0.5;
  const step = Math.max(10, Math.round(track.total / 200));
  if (!track.total) return null;

  return (
    <div className="playback-bar no-print">
      {!reduced && (
        <button
          type="button"
          className="playback-bar__play"
          onClick={() => (playing ? onPause() : onPlay(atEnd))}
          aria-label={playing ? t("pausePlayback") : atEnd ? t("replayRoute") : t("playRoute")}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            {playing ? (
              <>
                <rect x="3" y="2" width="3" height="10" rx="1" fill="currentColor" />
                <rect x="8" y="2" width="3" height="10" rx="1" fill="currentColor" />
              </>
            ) : atEnd ? (
              <path d="M11.5 7a4.5 4.5 0 1 1-1.4-3.26M10.5 1.5v3h-3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            ) : (
              <path d="M4 2.2v9.6L11.5 7z" fill="currentColor" />
            )}
          </svg>
          <span>{playing ? t("pausePlayback") : atEnd ? t("replayRoute") : t("playRoute")}</span>
        </button>
      )}
      <label className="playback-bar__scrub">
        <span className="visually-hidden">{t("playbackScrub")}</span>
        <input
          type="range"
          min={0}
          max={Math.round(track.total)}
          step={step}
          value={Math.round(dist)}
          onChange={(e) => {
            // The step can't land exactly on the end; snap the last step to it.
            const v = Number(e.target.value);
            onScrub(v > track.total - step ? track.total : v);
          }}
          aria-valuetext={t("playbackProgress")
            .replace("{done}", formatDistance(dist))
            .replace("{total}", formatDistance(track.total))}
          style={{ "--pct": `${(dist / track.total) * 100}%` }}
        />
      </label>
      {onSpeed && (
        <button
          type="button"
          className="playback-bar__speed"
          aria-label={t("playbackSpeedAria").replace("{x}", speed)}
          title={t("playbackSpeedAria").replace("{x}", speed)}
          onClick={() => onSpeed(PLAYBACK_SPEEDS[(PLAYBACK_SPEEDS.indexOf(speed) + 1) % PLAYBACK_SPEEDS.length])}
        >
          {speed}×
        </button>
      )}
      <span className="playback-bar__readout" aria-hidden="true">
        <span className="data">{formatDistance(dist)}</span>
        <span className="playback-bar__of"> / {formatDistance(track.total)}</span>
      </span>
    </div>
  );
}
