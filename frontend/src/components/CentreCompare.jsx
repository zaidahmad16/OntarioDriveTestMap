import { useState } from "react";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";
import { ChevronDownIcon } from "../Icons.jsx";

function formatKm(m, lang) {
  if (m == null) return "—";
  return `${new Intl.NumberFormat(lang === "fr" ? "fr-CA" : "en-CA", { maximumFractionDigits: 1, minimumFractionDigits: 1 }).format(m / 1000)} km`;
}

// Real distance and step-count averages over confirmed routes only
// (backend compare_centres). No pass-rate column: the app has no
// pass/fail data, so none is invented. A quiet secondary section, not
// the homepage's headline feature (spec §4).
export default function CentreCompare() {
  const { t, lang } = useLang();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);

  function load() {
    setError(null);
    api.compareCentres().then(setRows).catch((err) => setError(err.message));
  }

  return (
    <section className="compare" aria-labelledby="compare-title">
      <details
        className="disclosure compare__details"
        onToggle={(e) => {
          if (e.currentTarget.open && !rows) load();
        }}
      >
        <summary>
          <span id="compare-title">{t("compareCentres")}</span>
          <span className="compare__hint">{t("compareHint")}</span>
          <ChevronDownIcon className="disclosure__chevron" />
        </summary>
        {error && (
          <div className="state-panel state-panel--error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={load}>
              {t("retry")}
            </button>
          </div>
        )}
        {!rows && !error && <p className="muted">{t("loading")}</p>}
        {rows && (
          <div className="compare__table-wrap">
            <table className="data-table compare__table">
              <thead>
                <tr>
                  <th scope="col">{t("centre")}</th>
                  <th scope="col">{t("confirmedRoutes")}</th>
                  <th scope="col">{t("avgDistance")}</th>
                  <th scope="col">{t("avgSteps")}</th>
                </tr>
              </thead>
              <tbody>
                {rows
                  .filter((r) => r.confirmed_route_count > 0)
                  .map((r) => (
                    <tr key={r.id}>
                      <th scope="row">{r.name}</th>
                      <td className="data">{r.confirmed_route_count}</td>
                      <td className="data">{formatKm(r.avg_distance_m, lang)}</td>
                      <td className="data">{r.avg_maneuver_count ?? "—"}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </section>
  );
}
