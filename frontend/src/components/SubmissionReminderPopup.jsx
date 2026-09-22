import { useLang } from "../i18n.jsx";

// Site-wide, once-per-session nudge (2026-09-21, owner's own request:
// "a pop up reminder" that submitted route data matters) -- shown to
// any signed-in user once per browser session via sessionStorage, not
// tied to whether they've submitted before (no per-user submission
// count endpoint exists, and building one just for this would be
// scope creep on a simple reminder). Dismissible, never blocks the
// app underneath it.
export default function SubmissionReminderPopup({ onSubmitRoute, onDismiss }) {
  const { t } = useLang();
  return (
    <div className="modal-backdrop" onClick={onDismiss}>
      <div
        className="modal-panel"
        onClick={(e) => e.stopPropagation()}
        style={{ padding: "var(--space-lg)", width: 440, maxWidth: "92vw" }}
      >
        <h3 style={{ marginTop: 0 }}>{t("submissionReminderTitle")}</h3>
        <p style={{ color: "var(--ink-muted)" }}>{t("submissionImportanceNote")}</p>
        <div style={{ display: "flex", gap: "var(--space-sm)", marginTop: "var(--space-md)" }}>
          <button className="btn-primary" onClick={onSubmitRoute}>
            {t("submitRoute")}
          </button>
          <button onClick={onDismiss}>{t("maybeLater")}</button>
        </div>
      </div>
    </div>
  );
}
