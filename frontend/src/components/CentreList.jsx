import { useLang } from "../i18n.jsx";
import { LineSample } from "../RouteNotation.jsx";

// Case- and accent-insensitive, so "montreal" finds "Montréal" and FR
// users typing without accents still match official names (spec §10).
export function normalizeQuery(s) {
  return (s || "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .trim();
}

// Browse only centres that actually have evidence to show (0 traces and
// 0 segments -- e.g. Winchester as of 2026-09 -- stay out of the index
// but remain valid choices in the submission wizard and discussion
// filters, since contributing is exactly how they stop being empty).
export function browsableCentres(centres) {
  return centres.filter((c) => c.trace_count > 0 || c.segment_count > 0);
}

function ClassAvailability({ centre }) {
  const { t } = useLang();
  // Older backends don't send per-class counts; show nothing rather
  // than implying both classes exist.
  if (centre.g_route_count == null && centre.g2_route_count == null) return null;
  const classes = [
    ["G", centre.g_route_count],
    ["G2", centre.g2_route_count],
  ].filter(([, n]) => n > 0);
  if (!classes.length) return null;
  return (
    <span className="centre-row__classes" aria-label={t("classesAvailable")}>
      {classes.map(([c]) => (
        <span key={c} className="class-tag">
          {c}
        </span>
      ))}
    </span>
  );
}

// Centre index (spec §4): aligned rows, not marketing tiles. Confirmed
// and inferred counts are always separate numbers -- never one total.
// Raw traces/junctions are underlying data, shown as a quiet secondary
// line with their real meaning, not as the headline metric.
export default function CentreList({ centres, onSelect }) {
  const { t, tn } = useLang();
  return (
    <ul className="centre-index" aria-label={t("centresWithRoutes")}>
      {centres.map((c) => {
        const confirmed = c.confirmed_route_line_count ?? c.route_line_count ?? 0;
        const inferred = Math.max(0, (c.route_line_count ?? confirmed) - confirmed);
        return (
          <li key={c.id}>
            <a
              className="centre-row"
              href={`/?centre=${encodeURIComponent(c.id)}`}
              onClick={(e) => {
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                e.preventDefault();
                onSelect(c.id);
              }}
            >
              <span className="centre-row__main">
                <span className="centre-row__name">{c.name}</span>
                <ClassAvailability centre={c} />
              </span>
              <span className="centre-row__counts">
                <span className="count-pair">
                  <LineSample status="confirmed" width={22} />
                  <span>
                    <span className="data">{confirmed}</span> {tn("confirmedRoutesN", confirmed)}
                  </span>
                </span>
                <span className="count-pair count-pair--inferred">
                  <LineSample status="inferred" width={22} />
                  <span>
                    <span className="data">{inferred}</span> {tn("inferredSectionsN", inferred)}
                  </span>
                </span>
              </span>
              <span className="centre-row__evidence">
                {tn("sourceRecordsN", c.trace_count).replace("{n}", c.trace_count)} ·{" "}
                {tn("junctionsObservedN", c.segment_count).replace("{n}", c.segment_count)}
              </span>
              <span className="centre-row__cta" aria-hidden="true">
                {t("viewRoutes")} →
              </span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}
