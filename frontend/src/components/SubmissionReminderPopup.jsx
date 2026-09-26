import { useLang } from "../i18n.jsx";

// Site-wide submission prompt (owner's request, 2026-09-21; hourly
// cadence kept as-is -- changing it is a product decision). Redesigned
// 2026-09-25 from a blocking modal into a non-blocking corner prompt:
// App.jsx only renders it on the home view, never over a route study
// session, the submission wizard, or a practice drive (spec §9).
export default function SubmissionReminderPopup({ onSubmitRoute, onDismiss }) {
  const { t } = useLang();
  return (
    <aside className="nudge" role="complementary" aria-labelledby="nudge-title">
      <p id="nudge-title" className="nudge__title">
        {t("submissionReminderTitle")}
      </p>
      <p className="nudge__body">{t("submissionMotivation")}</p>
      <div className="nudge__actions">
        <button type="button" className="btn-primary" onClick={onSubmitRoute}>
          {t("submitRoute")}
        </button>
        <button type="button" onClick={onDismiss}>
          {t("maybeLater")}
        </button>
      </div>
    </aside>
  );
}
