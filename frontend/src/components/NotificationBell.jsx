import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";
import { BellIcon } from "../Icons.jsx";

// In-app only -- no email, no push. A bell with an unread count; click
// it to see the list, mark things read. Discussion notifications link
// to the discussion page directly. Forum notifications don't deep-link
// to a specific centre's forum (there's no routing for that yet) --
// shown as plain text instead of a broken/misleading link, a known,
// explicit scope limit rather than a bug.
export default function NotificationBell({ signedIn }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [notifications, setNotifications] = useState(null);
  const [unread, setUnread] = useState(0);
  const [error, setError] = useState(null);
  const [justRang, setJustRang] = useState(false);
  const prevUnread = useRef(0);

  useEffect(() => {
    if (!signedIn) return;
    api.getUnreadNotificationCount().then((r) => setUnread(r.count)).catch(() => {});
  }, [signedIn]);

  // A quiet one-shot ring, only when the count genuinely rises (a fresh
  // arrival), never on every poll/render -- see .bell-ring in App.css.
  useEffect(() => {
    if (unread > prevUnread.current) {
      setJustRang(true);
      const t = setTimeout(() => setJustRang(false), 500);
      return () => clearTimeout(t);
    }
    prevUnread.current = unread;
  }, [unread]);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next) {
      api
        .getNotifications()
        .then((rows) => {
          setNotifications(rows);
          setUnread(0);
          prevUnread.current = 0;
        })
        .catch((err) => setError(err.message));
    }
  }

  function markRead(id) {
    api.markNotificationRead(id).then(() => {
      setNotifications((rows) => rows.map((r) => (r.id === id ? { ...r, read: true } : r)));
    });
  }

  function markAllRead() {
    api.markAllNotificationsRead().then(() => {
      setNotifications((rows) => rows.map((r) => ({ ...r, read: true })));
    });
  }

  if (!signedIn) return null;

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <button onClick={toggle} className="icon-btn btn-ghost" aria-label={t("notifications")}>
        <span className={justRang ? "bell-ring" : undefined}>
          <BellIcon />
        </span>
        {unread > 0 && <span className="icon-btn__count">{unread}</span>}
      </button>
      {open && (
        <div className="popover-panel popover">
          <div className="popover-header">
            <span>{t("notifications")}</span>
            <button onClick={markAllRead}>{t("markAllRead")}</button>
          </div>
          {error && <p className="popover-empty" style={{ color: "var(--danger)" }}>{error}</p>}
          {notifications === null && <p className="popover-empty">{t("loading")}</p>}
          {notifications && notifications.length === 0 && (
            <p className="popover-empty">{t("noNotifications")}</p>
          )}
          {notifications &&
            notifications.map((n, i) => {
              const label =
                n.kind === "mention"
                  ? t("notifMentioned").replace("{user}", n.actor_username)
                  : n.kind === "comment"
                  ? t("notifCommented").replace("{user}", n.actor_username)
                  : t("notifBookingReminder");
              const inner = (
                <div
                  key={n.id}
                  onClick={() => !n.read && markRead(n.id)}
                  className={`popover-row rise-in${n.read ? "" : " popover-row--unread"}`}
                  style={{ animationDelay: `${i * 25}ms` }}
                >
                  <div>{label}</div>
                  {n.excerpt && (
                    <div style={{ color: "var(--ink-muted)", margin: "2px 0" }}>&quot;{n.excerpt}&quot;</div>
                  )}
                  <div style={{ color: "var(--ink-muted)", fontSize: "0.8125rem" }}>
                    {n.source === "forum" ? t("forum") : n.source === "discussion" ? t("discussion") : t("reminder")} ·{" "}
                    {new Date(n.created_at).toLocaleString()}
                  </div>
                </div>
              );
              return n.source === "discussion" ? (
                <a key={n.id} href="/discussion.html" style={{ color: "inherit", textDecoration: "none", display: "block" }}>
                  {inner}
                </a>
              ) : (
                inner
              );
            })}
        </div>
      )}
    </div>
  );
}
