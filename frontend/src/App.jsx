import { useEffect, useState, lazy, Suspense } from "react";
import { api } from "./api.js";
import { useLang } from "./i18n.jsx";
import Login from "./components/Login.jsx";
import CentreList from "./components/CentreList.jsx";
import MapView from "./components/MapView.jsx";
import TraceList from "./components/TraceList.jsx";
import ForumSection from "./components/ForumSection.jsx";
import UsernamePrompt from "./components/UsernamePrompt.jsx";
import NotificationBell from "./components/NotificationBell.jsx";
import BookingReminderButton from "./components/BookingReminderButton.jsx";
import { useSeo } from "./seo.js";
import Breadcrumbs from "./Breadcrumbs.jsx";
import LoadingScreen, { Spinner } from "./LoadingScreen.jsx";
import SubmissionReminderPopup from "./components/SubmissionReminderPopup.jsx";

const SUBMISSION_REMINDER_KEY = "odtm_submission_reminder_last_shown";
const SUBMISSION_REMINDER_INTERVAL_MS = 3600 * 1000;

// Lazy: neither renders on first paint (compare is opt-in, the submit
// modal is closed by default) -- keeping them out of the initial bundle
// shrinks what a first-time visitor has to download/parse before seeing
// the centre list.
const CentreCompare = lazy(() => import("./components/CentreCompare.jsx"));
const SubmitRouteModal = lazy(() => import("./components/SubmitRouteModal.jsx"));

export default function App() {
  const { lang, setLang, t } = useLang();
  const [user, setUser] = useState(null);
  const [checkedAuth, setCheckedAuth] = useState(false);
  const [centres, setCentres] = useState([]);
  const [centresError, setCentresError] = useState(null);
  const [selectedCentre, setSelectedCentre] = useState(null);
  const [submitOpen, setSubmitOpen] = useState(false);
  const [reminderOpen, setReminderOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  async function handleDeleteAccount() {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    await api.deleteAccount();
    setUser(null);
    setConfirmDelete(false);
  }

  // Near-invisible header elevation only once the page has actually
  // scrolled -- a heavy floating navbar at rest reads as SaaS chrome,
  // not a civic utility (2026-09-21 spec).
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 4);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Centre list is public, so load it regardless of sign-in state -- it's
  // what a visitor sees before deciding whether to sign in at all. A
  // failure here (backend down, network) must surface, not leave the list
  // silently empty -- an empty list would be indistinguishable from "there
  // are no centres," the same trap TraceList/MapView already guard against.
  useEffect(() => {
    api
      .getCentres()
      .then(setCentres)
      .catch((err) => setCentresError(err.message));
  }, []);

  useEffect(() => {
    api
      .me()
      .then((u) => setUser(u))
      .catch(() => setUser(null))
      .finally(() => setCheckedAuth(true));
  }, []);

  // Owner's own request (2026-09-21): a real pop-up, not buried text,
  // reminding signed-in users that submitted route data matters --
  // recurring every SUBMISSION_REMINDER_INTERVAL_MS (currently 3600s),
  // not just once. Uses localStorage (not sessionStorage) so the
  // countdown survives a page reload instead of restarting every visit
  // -- the timer is real wall-clock time since it last showed, not
  // "once per tab session." Not gated on whether this user has
  // submitted before -- no per-user submission-count endpoint exists,
  // and adding one just for this would be scope creep on a simple
  // reminder. Not reset by dismissing it early ("Maybe later") -- it's
  // a fixed cadence, not a snooze.
  useEffect(() => {
    if (!user) return;
    let timer;
    const showAndScheduleNext = () => {
      setReminderOpen(true);
      localStorage.setItem(SUBMISSION_REMINDER_KEY, String(Date.now()));
      timer = setTimeout(showAndScheduleNext, SUBMISSION_REMINDER_INTERVAL_MS);
    };
    const lastShown = Number(localStorage.getItem(SUBMISSION_REMINDER_KEY) || 0);
    const elapsed = Date.now() - lastShown;
    if (!lastShown || elapsed >= SUBMISSION_REMINDER_INTERVAL_MS) {
      showAndScheduleNext();
    } else {
      timer = setTimeout(showAndScheduleNext, SUBMISSION_REMINDER_INTERVAL_MS - elapsed);
    }
    return () => clearTimeout(timer);
  }, [user]);

  // Lets a Discussion post's "View centre discussion" / "Ask a question"
  // links round-trip back to the right centre (?centre=<id>) instead of
  // dropping the visitor on an unselected home page.
  useEffect(() => {
    const centreId = new URLSearchParams(window.location.search).get("centre");
    if (centreId) setSelectedCentre(centreId);
  }, []);

  // Keep the URL in sync with the selected centre so a centre view is
  // bookmarkable/shareable/crawlable, not just in-memory state. Real
  // path-based URLs (/centres/ottawa-walkley) would need a client router;
  // this query-param approach is the conservative version that doesn't
  // risk breaking the rest of the app's state-driven navigation.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (selectedCentre) params.set("centre", selectedCentre);
    else params.delete("centre");
    const qs = params.toString();
    const next = qs ? `/?${qs}` : "/";
    if (next !== window.location.pathname + window.location.search) {
      window.history.replaceState(null, "", next);
    }
    // First-party, cookie-free page view (2026-09-21) -- fires whenever
    // the visible "page" actually changes (centre selected/cleared),
    // same signal the URL-sync above already uses. Fire-and-forget,
    // never blocks or throws.
    api.recordPageView(next);
  }, [selectedCentre]);

  const selectedCentreObj = centres.find((c) => c.id === selectedCentre);

  useSeo(
    selectedCentreObj
      ? {
          title: `${selectedCentreObj.name} DriveTest Routes — OntarioDriveTestMap`,
          description: `Explore crowdsourced G and G2 DriveTest routes for ${selectedCentreObj.name}, built from GPS traces, community reports and corroborated route evidence.`,
          path: `/?centre=${encodeURIComponent(selectedCentre)}`,
          breadcrumbJsonLd: undefined,
        }
      : {
          title: "OntarioDriveTestMap — Real G/G2 DriveTest Routes",
          description: "Crowdsourced Ontario G/G2 DriveTest routes built from real GPS traces, hand-transcribed test videos and community reports — with honestly labeled confidence, not schematic guesses.",
          path: "/",
        }
  );

  if (!checkedAuth) return <LoadingScreen />;

  // Real, computed totals -- not invented copy. Used in the logged-out
  // hero's stat line so the very first thing a visitor sees is a fact,
  // not marketing language.
  const totalConfirmed = centres.reduce(
    (a, c) => a + (c.confirmed_route_line_count ?? c.route_line_count ?? 0),
    0
  );
  const totalTraces = centres.reduce((a, c) => a + (c.trace_count || 0), 0);

  return (
    <>
      <header className={`app-header${scrolled ? " app-header--scrolled" : ""}`}>
        <div className="app-header__inner">
          <p className="brand-mark">OntarioDriveTestMap</p>
          <div className="app-header__actions">
            <NotificationBell signedIn={!!user} />
            {user && <BookingReminderButton centres={centres} />}
            <a href="/about.html" className="header-link">{t("about")}</a>
            <a href="/discussion.html" className="header-link">{t("discussion")}</a>
            <button className="btn-primary" onClick={() => setSubmitOpen(true)}>{t("submitRoute")}</button>
            <div className="lang-toggle">
              {["en", "fr"].map((l) => (
                <button key={l} onClick={() => setLang(l)} aria-current={lang === l}>
                  {l.toUpperCase()}
                </button>
              ))}
            </div>
            {/* Account status lives in the header's utility row, not the
                page body (2026-09-21 spec). */}
            {user && (
              <div className="app-header__account">
                <span>{user.username || user.email}</span>
                {user.is_admin && <span className="badge-admin">ADMIN</span>}
                {user.is_admin && (
                  <label className="digest-opt-in" title={t("weeklyDigestOptIn")}>
                    <input
                      type="checkbox"
                      checked={!!user.weekly_digest_opt_in}
                      onChange={(e) => {
                        const optIn = e.target.checked;
                        setUser((u) => ({ ...u, weekly_digest_opt_in: optIn }));
                        api.setDigestOptIn(optIn).catch(() =>
                          setUser((u) => ({ ...u, weekly_digest_opt_in: !optIn }))
                        );
                      }}
                    />
                    {t("weeklyDigestOptIn")}
                  </label>
                )}
                <button onClick={() => api.logout().then(() => setUser(null))}>{t("signOut")}</button>
                <button
                  className={`link-btn link-btn--danger${confirmDelete ? " link-btn--confirming" : ""}`}
                  onClick={handleDeleteAccount}
                  onBlur={() => setConfirmDelete(false)}
                >
                  {confirmDelete ? t("deleteAccountConfirm") : t("deleteAccount")}
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <main id="main">
      <div className="page">
      {!user ? (
        <div className="hero anim-rise">
          <p className="hero__headline">{t("heroHeadline")}</p>
          <p className="hero__sub">{t("heroSub")}</p>
          {totalConfirmed > 0 && (
            <p className="hero__stat">
              {t("heroStat")
                .split(/(\{confirmed\}|\{centres\}|\{traces\})/)
                .map((part, i) => {
                  if (part === "{confirmed}") return <span className="data" key={i}>{totalConfirmed}</span>;
                  if (part === "{centres}") return <span className="data" key={i}>{centres.length}</span>;
                  if (part === "{traces}") return <span className="data" key={i}>{totalTraces}</span>;
                  return part;
                })}
            </p>
          )}
          <Login onLogin={setUser} />
        </div>
      ) : null}

      {user && !user.username && (
        <UsernamePrompt onSet={(username) => setUser((u) => ({ ...u, username }))} />
      )}

      {selectedCentreObj && (
        <Breadcrumbs
          items={[
            { name: t("centres"), href: "/", onClick: (e) => { e.preventDefault(); setSelectedCentre(null); } },
            { name: selectedCentreObj.name },
          ]}
        />
      )}

      <h1>{selectedCentreObj ? `${selectedCentreObj.name} DriveTest Routes` : t("findCentre")}</h1>
      {!selectedCentreObj && <p className="page-lede">{t("centresLede")}</p>}

      <h2>{t("centres")}</h2>
      {centresError ? (
        <p className="error-banner">
          {t("couldntLoadCentres")}: {centresError}
        </p>
      ) : (
        <CentreList
          centres={centres}
          selected={selectedCentre}
          onSelect={setSelectedCentre}
        />
      )}

      {user && (
        <Suspense fallback={<div className="suspense-fallback"><Spinner /></div>}>
          <CentreCompare />
        </Suspense>
      )}

      {selectedCentre && !user && <p>{t("signInForCentre")}</p>}
      </div>

      {selectedCentre && user && (
        <div key={selectedCentre} className="map-workspace anim-rise">
          <MapView
            centreId={selectedCentre}
            centreName={centres.find((c) => c.id === selectedCentre)?.name || selectedCentre}
          />
        </div>
      )}

      {selectedCentre && user && (
        <div className="page">
          <TraceList centreId={selectedCentre} isAdmin={!!user.is_admin} />
          <ForumSection
            centreId={selectedCentre}
            centreName={centres.find((c) => c.id === selectedCentre)?.name || selectedCentre}
            username={user.username}
          />
        </div>
      )}

      {submitOpen && (
        <Suspense fallback={<div className="suspense-fallback"><Spinner /></div>}>
          <SubmitRouteModal
            open={submitOpen}
            onClose={() => setSubmitOpen(false)}
            centres={centres}
            signedIn={!!user}
          />
        </Suspense>
      )}

      {reminderOpen && (
        <SubmissionReminderPopup
          onSubmitRoute={() => {
            setReminderOpen(false);
            setSubmitOpen(true);
          }}
          onDismiss={() => setReminderOpen(false)}
        />
      )}
      </main>

      <footer className="app-footer">
        <p className="app-footer__disclaimer">{t("footerDisclaimer")}</p>
        <nav aria-label="Legal">
          <a href="/about.html">About</a>
          <a href="/privacy-policy.html">Privacy</a>
          <a href="/terms-of-service.html">Terms</a>
          <a href="/cookie-policy.html">Cookies</a>
        </nav>
      </footer>
    </>
  );
}