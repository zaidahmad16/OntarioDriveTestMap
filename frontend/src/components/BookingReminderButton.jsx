import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";
import { ClockIcon } from "../Icons.jsx";

// Notion "Frontend Design" backlog, Hard tier: "Appointment-opening
// notifier." Scoped down from real scraping -- drivetest.ca has no
// public availability data (even "sign in to see your bookings"
// requires a driver's licence number + expiry first), a stated 10
// logins/day cap, and its own Terms of Use as a real government
// service. Automating login as the user is not something this
// assistant will do. This is the honest version: a plain self-set
// reminder, delivered through the existing in-app notification bell --
// not a live watcher.
function inDays(n) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

export default function BookingReminderButton({ centres }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(inDays(14));
  const [centreId, setCentreId] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [sent, setSent] = useState(false);
  const [reminders, setReminders] = useState(null);

  useEffect(() => {
    if (open) {
      api.getBookingReminders().then(setReminders).catch(() => {});
    }
  }, [open]);

  function submit() {
    if (saving || !date) return;
    setSaving(true);
    setError(null);
    api
      .createBookingReminder({ centre_id: centreId || null, remind_at: date, note: note.trim() || null })
      .then(() => {
        setSent(true);
        setNote("");
        api.getBookingReminders().then(setReminders).catch(() => {});
      })
      .catch((err) => setError(err.message))
      .finally(() => setSaving(false));
  }

  function remove(id) {
    api.deleteBookingReminder(id).then(() => {
      setReminders((rows) => rows.filter((r) => r.id !== id));
    });
  }

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <button
        className="btn-ghost"
        onClick={() => setOpen((o) => !o)}
        style={{ display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}
      >
        <ClockIcon /> {t("setReminder")}
      </button>
      {open && (
        <div className="popover-panel popover" style={{ width: 300, padding: "var(--space-md)" }}>
          <p style={{ margin: "0 0 var(--space-sm)", color: "var(--ink-muted)", fontSize: "0.8125rem" }}>
            {t("reminderHint")}
          </p>

          <label style={{ display: "block", marginBottom: "var(--space-sm)", fontSize: "0.8125rem", fontWeight: 600 }}>
            {t("reminderDate")}
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              style={{ display: "block", width: "100%", marginTop: 4 }}
            />
          </label>

          <label style={{ display: "block", marginBottom: "var(--space-sm)", fontSize: "0.8125rem", fontWeight: 600 }}>
            {t("centreOptional")}
            <select
              value={centreId}
              onChange={(e) => setCentreId(e.target.value)}
              style={{ display: "block", width: "100%", marginTop: 4 }}
            >
              <option value="">—</option>
              {(centres || []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>

          <label style={{ display: "block", marginBottom: "var(--space-md)", fontSize: "0.8125rem", fontWeight: 600 }}>
            {t("reminderNoteOptional")}
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder={t("reminderNotePlaceholder")}
              style={{ display: "block", width: "100%", marginTop: 4 }}
            />
          </label>

          <button onClick={submit} disabled={saving || !date} className="btn-primary" style={{ width: "100%" }}>
            {saving ? t("loading") : t("setReminderButton")}
          </button>
          {sent && (
            <p className="fade-in" style={{ color: "var(--success)", margin: "var(--space-sm) 0 0", fontSize: "0.8125rem" }}>
              {t("reminderSet")}
            </p>
          )}
          {error && (
            <p style={{ color: "var(--confirmed)", margin: "var(--space-sm) 0 0", fontSize: "0.8125rem" }}>{error}</p>
          )}

          {reminders && reminders.length > 0 && (
            <>
              <hr style={{ margin: "var(--space-md) 0", border: "none", borderTop: "1px solid var(--border)" }} />
              <p style={{ fontWeight: 600, fontSize: "0.8125rem", margin: "0 0 var(--space-xs)" }}>{t("yourReminders")}</p>
              <ul style={{ margin: 0, padding: 0, listStyle: "none", maxHeight: 140, overflowY: "auto" }}>
                {reminders.map((r) => (
                  <li
                    key={r.id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 6,
                      padding: "6px 0",
                      borderBottom: "1px solid var(--border)",
                      fontSize: "0.8125rem",
                    }}
                  >
                    <span>
                      <span className="data">{r.remind_at}</span> {r.note ? `— ${r.note}` : ""}{" "}
                      <span
                        className={`trust-badge trust-badge--${r.notified ? "success" : "gap"}`}
                        style={{ padding: "1px 6px" }}
                      >
                        {r.notified ? t("reminderDone") : t("reminderPending")}
                      </span>
                    </span>
                    <button onClick={() => remove(r.id)} style={{ padding: "2px 8px", fontSize: "0.75rem" }}>
                      {t("delete")}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
          {reminders && reminders.length === 0 && (
            <p style={{ color: "var(--ink-muted)", margin: "var(--space-sm) 0 0", fontSize: "0.8125rem" }}>
              {t("noReminders")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
