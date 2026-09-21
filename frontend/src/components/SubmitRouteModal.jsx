import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";

const OTHER = "__other__";

// Same kind of vocabulary the real instructions table shows in its "At
// junction" column (formatTrafficControl in format.js), widened to
// cover what a person actually remembers from driving a route: not
// just signalled junctions, but medians, forks, filter lanes, plain
// intersections. Descriptive, user-recalled -- not fact-checked against
// osm.db (there's no reliable source to verify it against).
const JUNCTION_TYPES = [
  { value: "", label: "—" },
  { value: "traffic_signals", label: "Traffic light" },
  { value: "stop", label: "Stop sign" },
  { value: "yield", label: "Yield" },
  { value: "roundabout", label: "Roundabout" },
  { value: "median", label: "Median" },
  { value: "fork", label: "Fork" },
  { value: "filter", label: "Filter lane" },
  { value: "intersection", label: "Intersection" },
];

function junctionLabel(value) {
  return JUNCTION_TYPES.find((j) => j.value === value)?.label || "—";
}

const STEPS = ["wizardStepCentre", "wizardStepClass", "wizardStepStreets", "wizardStepReview"];

function freshWizard() {
  return {
    step: 1,
    centreChoice: "",
    centreFreeform: "",
    testClass: "G",
    confirmedTurns: [], // [{ street, junctionType }]
    pendingStreet: "",
    pendingJunctionType: "",
    addError: null,
    adding: false,
  };
}

// A real 4-step sequence where the order carries information (each step
// depends on the last) -- exactly the case where a numbered progress
// indicator earns its place rather than being decorative scaffolding.
function StepIndicator({ step }) {
  const { t } = useLang();
  return (
    <div style={{ display: "flex", alignItems: "center", marginBottom: "var(--space-lg)" }}>
      {STEPS.map((key, i) => {
        const n = i + 1;
        const state = n === step ? "current" : n < step ? "done" : "upcoming";
        return (
          <div key={key} style={{ display: "flex", alignItems: "center", flex: i < STEPS.length - 1 ? 1 : "none" }}>
            <div
              className="data"
              style={{
                width: 26,
                height: 26,
                borderRadius: "50%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: "0.8125rem",
                fontWeight: 700,
                flexShrink: 0,
                transition: "background-color var(--duration-base) var(--ease-out-quart), color var(--duration-base) var(--ease-out-quart)",
                background: state === "upcoming" ? "var(--surface-sunken)" : "var(--accent)",
                color: state === "upcoming" ? "var(--ink-faint)" : "#fff",
              }}
            >
              {state === "done" ? "✓" : n}
            </div>
            {i < STEPS.length - 1 && (
              <div
                style={{
                  flex: 1,
                  height: 2,
                  margin: "0 6px",
                  background: n < step ? "var(--accent)" : "var(--border)",
                  transition: "background-color var(--duration-base) var(--ease-out-quart)",
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// Global, header-triggered route submission -- works for any Ontario
// DriveTest centre, including ones this app has no data for yet (a
// freeform name, not tied to the `centres` table).
//
// Broken into steps (centre -> class -> streets -> review) rather than
// one big form, and each street is checked against osm.db the moment
// it's added -- one real network call per addition, plain ok/error --
// instead of validating the whole list in bulk on submit and trying to
// parse which of N streets was the bad one back out of a single error
// string. That parsing approach was fragile (a street name with a
// quote in it, or any mismatch in the message format, would silently
// break the highlighting); this way there's nothing to parse.
export default function SubmitRouteModal({ open, onClose, centres, signedIn }) {
  const { t } = useLang();
  const [w, setW] = useState(freshWizard);
  const [error, setError] = useState(null);
  const [sent, setSent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submissions, setSubmissions] = useState(null);

  useEffect(() => {
    if (!open) return;
    setW(freshWizard());
    setError(null);
    setSent(false);
    if (signedIn) {
      api.getSubmissions().then(setSubmissions).catch(() => {});
    }
  }, [open, signedIn]);

  if (!open) return null;

  const centreName = w.centreChoice === OTHER ? w.centreFreeform.trim() : w.centreChoice;
  const matchedCentre = centres.find((c) => c.id === w.centreChoice);
  const centreDisplayName = matchedCentre ? matchedCentre.name : w.centreFreeform.trim();

  function patch(fields) {
    setW((s) => ({ ...s, ...fields }));
  }

  function addStreet() {
    const value = w.pendingStreet.trim();
    if (!value || w.adding) return;
    patch({ adding: true, addError: null });

    const last = w.confirmedTurns[w.confirmedTurns.length - 1];
    const check = last ? api.validatePair(last.street, value) : api.validateStreet(value);

    check
      .then((res) => {
        if (res.ok) {
          setW((s) => ({
            ...s,
            confirmedTurns: [...s.confirmedTurns, { street: value, junctionType: s.pendingJunctionType }],
            pendingStreet: "",
            pendingJunctionType: "",
            addError: null,
            adding: false,
          }));
        } else {
          patch({ addError: res.error, adding: false });
        }
      })
      .catch((err) => patch({ addError: err.message, adding: false }));
  }

  function removeLastStreet() {
    setW((s) => ({ ...s, confirmedTurns: s.confirmedTurns.slice(0, -1), addError: null }));
  }

  function submit() {
    if (w.confirmedTurns.length < 2 || submitting) return;
    setError(null);
    setSent(false);
    setSubmitting(true);
    const turns = w.confirmedTurns.map((tn) => ({
      street: tn.street,
      junction_type: tn.junctionType || null,
    }));
    api
      .submitRoute(matchedCentre ? matchedCentre.id : null, centreDisplayName, w.testClass, turns)
      .then(() => {
        setSent(true);
        setW(freshWizard());
        api.getSubmissions().then(setSubmissions).catch(() => {});
      })
      .catch((err) => setError(err.message))
      .finally(() => setSubmitting(false));
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()} style={{ padding: "var(--space-lg)", width: 520, maxWidth: "92vw" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "var(--space-md)" }}>
          <h3 style={{ margin: 0 }}>{t("submitRoute")}</h3>
          <button onClick={onClose}>{t("close")}</button>
        </div>

        {!signedIn ? (
          <p>{t("signInForSubmit")}</p>
        ) : (
          <>
            <StepIndicator step={w.step} />

            {w.step === 1 && (
              <div className="fade-in">
                <label style={{ display: "block", fontWeight: 600, fontSize: "0.875rem", marginBottom: 4 }}>
                  {t("wizardStepCentre")}
                </label>
                <select
                  value={w.centreChoice}
                  onChange={(e) => patch({ centreChoice: e.target.value })}
                  style={{ width: "100%" }}
                >
                  <option value="" disabled>
                    {t("centreChoosePrompt")}
                  </option>
                  {centres.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                  <option value={OTHER}>{t("centreNotListed")}</option>
                </select>
                {w.centreChoice === OTHER && (
                  <input
                    value={w.centreFreeform}
                    onChange={(e) => patch({ centreFreeform: e.target.value })}
                    placeholder={t("centreFreeformPlaceholder")}
                    style={{ width: "100%", marginTop: 8 }}
                  />
                )}
                <div style={{ marginTop: "var(--space-lg)" }}>
                  <button className="btn-primary" onClick={() => patch({ step: 2 })} disabled={centreName.length === 0}>
                    {t("wizardNext")}
                  </button>
                </div>
              </div>
            )}

            {w.step === 2 && (
              <div className="fade-in">
                <label style={{ display: "block", fontWeight: 600, fontSize: "0.875rem", marginBottom: 4 }}>
                  {t("wizardStepClass")}
                </label>
                <select value={w.testClass} onChange={(e) => patch({ testClass: e.target.value })}>
                  <option value="G">G</option>
                  <option value="G2">G2</option>
                </select>
                <div style={{ marginTop: "var(--space-lg)", display: "flex", gap: 8 }}>
                  <button onClick={() => patch({ step: 1 })}>{t("wizardBack")}</button>
                  <button className="btn-primary" onClick={() => patch({ step: 3 })}>
                    {t("wizardNext")}
                  </button>
                </div>
              </div>
            )}

            {w.step === 3 && (
              <div className="fade-in">
                <label style={{ display: "block", fontWeight: 600, fontSize: "0.875rem", marginBottom: 4 }}>
                  {t("wizardStepStreets")}
                </label>
                <p style={{ fontSize: "0.8125rem", color: "var(--ink-muted)", margin: "0 0 var(--space-sm)" }}>
                  {w.confirmedTurns.length === 0 ? t("firstStreetHint") : t("nextStreetHint")}
                </p>

                {w.confirmedTurns.length > 0 && (
                  <table style={{ marginBottom: "var(--space-md)", fontSize: "0.875rem" }}>
                    <thead>
                      <tr>
                        <th style={{ textAlign: "left", padding: "4px 6px", borderBottom: `2px solid var(--border-strong)` }}>#</th>
                        <th style={{ textAlign: "left", padding: "4px 6px", borderBottom: `2px solid var(--border-strong)` }}>
                          {t("streetName")}
                        </th>
                        <th style={{ textAlign: "left", padding: "4px 6px", borderBottom: `2px solid var(--border-strong)` }}>
                          {t("atJunction")}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {w.confirmedTurns.map((tn, i) => (
                        <tr key={i} className="rise-in" style={{ animationDelay: `${i * 30}ms` }}>
                          <td style={{ padding: "4px 6px", borderBottom: `1px solid var(--border)` }} className="data">
                            {i + 1}
                          </td>
                          <td style={{ padding: "4px 6px", borderBottom: `1px solid var(--border)`, color: "var(--success)" }}>
                            ✓ {tn.street}
                          </td>
                          <td style={{ padding: "4px 6px", borderBottom: `1px solid var(--border)` }}>{junctionLabel(tn.junctionType)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <input
                    value={w.pendingStreet}
                    onChange={(e) => patch({ pendingStreet: e.target.value, addError: null })}
                    onKeyDown={(e) => e.key === "Enter" && addStreet()}
                    placeholder={t("streetName")}
                    style={{ flex: "1 1 160px", borderColor: w.addError ? "var(--confirmed)" : undefined }}
                  />
                  <select
                    value={w.pendingJunctionType}
                    onChange={(e) => patch({ pendingJunctionType: e.target.value })}
                    title={t("atJunctionHint")}
                  >
                    {JUNCTION_TYPES.map((j) => (
                      <option key={j.value} value={j.value}>
                        {j.label}
                      </option>
                    ))}
                  </select>
                  <button onClick={addStreet} disabled={w.adding || !w.pendingStreet.trim()}>
                    {w.adding ? t("checking") : t("addStreet")}
                  </button>
                </div>
                <p style={{ fontSize: "0.8125rem", color: "var(--ink-muted)", margin: "6px 0 0" }}>{t("atJunctionHint")}</p>
                {w.addError && (
                  <p className="rise-in" style={{ color: "var(--confirmed)", fontSize: "0.875rem", marginTop: 6 }}>
                    {w.addError}
                  </p>
                )}

                {w.confirmedTurns.length > 0 && (
                  <button onClick={removeLastStreet} style={{ marginTop: "var(--space-sm)", fontSize: "0.8125rem" }}>
                    {t("removeLastStreet")}
                  </button>
                )}

                <div style={{ marginTop: "var(--space-lg)", display: "flex", gap: 8, alignItems: "center" }}>
                  <button onClick={() => patch({ step: 2 })}>{t("wizardBack")}</button>
                  <button className="btn-primary" onClick={() => patch({ step: 4 })} disabled={w.confirmedTurns.length < 2}>
                    {t("wizardReview")}
                  </button>
                  {w.confirmedTurns.length < 2 && (
                    <span style={{ fontSize: "0.8125rem", color: "var(--ink-muted)" }}>{t("submissionNeedTwo")}</span>
                  )}
                </div>
              </div>
            )}

            {w.step === 4 && (
              <div className="fade-in">
                <label style={{ display: "block", fontWeight: 600, fontSize: "0.875rem", marginBottom: 8 }}>
                  {t("wizardStepReview")}
                </label>
                <p style={{ margin: "0 0 4px" }}>
                  <b>{t("centreLabel")}:</b> {centreDisplayName}
                </p>
                <p style={{ margin: "0 0 var(--space-sm)" }}>
                  <b>{t("class")}:</b> <span className="trust-badge trust-badge--gap">{w.testClass}</span>
                </p>
                <table style={{ fontSize: "0.875rem" }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: "left", padding: "4px 6px", borderBottom: `2px solid var(--border-strong)` }}>#</th>
                      <th style={{ textAlign: "left", padding: "4px 6px", borderBottom: `2px solid var(--border-strong)` }}>
                        {t("streetName")}
                      </th>
                      <th style={{ textAlign: "left", padding: "4px 6px", borderBottom: `2px solid var(--border-strong)` }}>
                        {t("atJunction")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {w.confirmedTurns.map((tn, i) => (
                      <tr key={i}>
                        <td className="data" style={{ padding: "4px 6px", borderBottom: `1px solid var(--border)` }}>
                          {i + 1}
                        </td>
                        <td style={{ padding: "4px 6px", borderBottom: `1px solid var(--border)` }}>{tn.street}</td>
                        <td style={{ padding: "4px 6px", borderBottom: `1px solid var(--border)` }}>{junctionLabel(tn.junctionType)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div style={{ marginTop: "var(--space-lg)", display: "flex", gap: 8 }}>
                  <button onClick={() => patch({ step: 3 })}>{t("wizardBack")}</button>
                  <button className="btn-primary" onClick={submit} disabled={submitting}>
                    {submitting ? t("loading") : t("submit")}
                  </button>
                </div>
                {error && (
                  <p className="rise-in" style={{ color: "var(--confirmed)", fontSize: "0.875rem", marginTop: 8 }}>
                    {error}
                  </p>
                )}
              </div>
            )}

            {sent && (
              <p className="trust-badge trust-badge--success rise-in" style={{ marginTop: "var(--space-md)" }}>
                ✓ {t("submissionSent")}
              </p>
            )}

            {submissions && (
              <>
                <h4 style={{ marginTop: "var(--space-lg)" }}>
                  {t("communitySubmissions")}{" "}
                  <span style={{ color: "var(--ink-muted)", fontWeight: 400 }}>({submissions.length})</span>
                </h4>
                <ul style={{ fontSize: "0.875rem", maxHeight: 140, overflowY: "auto", margin: 0, padding: 0, listStyle: "none" }}>
                  {submissions.map((sub, i) => (
                    <li
                      key={sub.id}
                      className="rise-in"
                      style={{ padding: "6px 0", borderBottom: `1px solid var(--border)`, animationDelay: `${i * 20}ms` }}
                    >
                      {sub.centre_name} — {sub.test_class} — <span className="data">{new Date(sub.created_at).toLocaleDateString()}</span>{" "}
                      <span className={`trust-badge trust-badge--${sub.status === "promoted" ? "success" : "gap"}`}>
                        {sub.status === "promoted" ? "✓ promoted to official route" : "pending corroboration"}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
