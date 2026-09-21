import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";

function formatKm(m) {
  if (m == null) return "—";
  return `${(m / 1000).toFixed(1)} km`;
}

// Real distance/duration/maneuver-count stats, confirmed routes only.
// No pass-rate column: this app has no pass/fail data, so it's left out
// rather than invented (matches backend's compare_centres note).
export default function CentreCompare() {
  const { t } = useLang();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open || rows) return;
    api.compareCentres().then(setRows).catch((err) => setError(err.message));
  }, [open, rows]);

  return (
    <div style={{ margin: "var(--space-md) 0" }}>
      <button onClick={() => setOpen((o) => !o)}>
        {t("compareCentres")} {open ? "▲" : "▼"}
      </button>
      {open && error && <p className="error-banner" style={{ marginTop: "var(--space-sm)" }}>{error}</p>}
      {open && rows && (
        <div className="card fade-in" style={{ marginTop: "var(--space-sm)", padding: 0, overflow: "hidden" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("centre")}</th>
                <th>{t("confirmedRoutes")}</th>
                <th>{t("avgDistance")}</th>
                <th>{t("avgManeuvers")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td style={{ fontWeight: 600 }}>{r.name}</td>
                  <td className="data" style={{ color: "var(--confirmed)" }}>
                    {r.confirmed_route_count}
                  </td>
                  <td className="data">{formatKm(r.avg_distance_m)}</td>
                  <td className="data">{r.avg_maneuver_count ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
