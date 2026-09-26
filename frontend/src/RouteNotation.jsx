import { useLang } from "./i18n.jsx";

// The one confidence notation the whole product repeats (spec §2.4):
// the same line sample + the same word, in the legend, the route rail,
// turn rows, the home trust note and About. `status` is a semantic enum,
// never a colour prop, so a caller can't accidentally style an inferred
// section as confirmed.
export const ROUTE_STATUSES = ["confirmed", "inferred", "gap", "unknown"];

const STROKES = {
  confirmed: { color: "var(--brand)", dash: null, width: 4 },
  inferred: { color: "var(--inferred)", dash: "7 6", width: 4 },
  gap: { color: "var(--neutral-line)", dash: "2 6", width: 3 },
  unknown: { color: "var(--neutral-line)", dash: null, width: 2 },
};

export function LineSample({ status, width = 36 }) {
  const s = STROKES[status] || STROKES.unknown;
  return (
    <svg className="line-sample" width={width} height="10" viewBox={`0 0 ${width} 10`} aria-hidden="true">
      {status === "unknown" && (
        <line x1="2" y1="5" x2={width - 2} y2="5" stroke="var(--surface)" strokeWidth="6" strokeLinecap="round" />
      )}
      <line
        x1="2"
        y1="5"
        x2={width - 2}
        y2="5"
        stroke={s.color}
        strokeWidth={s.width}
        strokeLinecap={s.dash ? "butt" : "round"}
        strokeDasharray={s.dash || undefined}
      />
    </svg>
  );
}

const LABEL_KEYS = {
  confirmed: "statusConfirmed",
  inferred: "statusInferred",
  gap: "statusGap",
  unknown: "statusUnknown",
};

export function statusLabel(t, status) {
  return t(LABEL_KEYS[status] || LABEL_KEYS.unknown);
}

// Text + line sample. `size="sm"` for dense rows (turns, route picker).
export function StatusBadge({ status, size, children }) {
  const { t } = useLang();
  const s = ROUTE_STATUSES.includes(status) ? status : "unknown";
  return (
    <span className={`status-badge status-badge--${s}${size === "sm" ? " status-badge--sm" : ""}`}>
      <LineSample status={s} width={size === "sm" ? 18 : 22} />
      {children || statusLabel(t, s)}
    </span>
  );
}

// Legend rows for the map and the About/home explainers. Only statuses
// actually present are passed in by the map; explainers pass the full set.
export function RouteLegend({ statuses, showCentre = false, title }) {
  const { t } = useLang();
  return (
    <div className="route-legend">
      {title && <p className="route-legend__title">{title}</p>}
      <ul>
        {statuses.map((s) => (
          <li key={s}>
            <LineSample status={s} />
            <span>{t(`legend_${s}`)}</span>
          </li>
        ))}
        {showCentre && (
          <li>
            <span className="legend-centre-pin" aria-hidden="true" />
            <span>{t("legend_centre")}</span>
          </li>
        )}
      </ul>
    </div>
  );
}
