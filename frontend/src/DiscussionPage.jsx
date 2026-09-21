import { useEffect, useState } from "react";
import { api } from "./api.js";
import { useLang } from "./i18n.jsx";
import Login from "./components/Login.jsx";
import UsernamePrompt from "./components/UsernamePrompt.jsx";
import DiscussionSection from "./components/DiscussionSection.jsx";
import NotificationBell from "./components/NotificationBell.jsx";
import { useSeo } from "./seo.js";
import LoadingScreen from "./LoadingScreen.jsx";

// Own page (discussion.html), own JS bundle -- a real separate page,
// not a section of the main SPA. Duplicates the small amount of
// header/auth chrome App.jsx also has (title, sign-in state, language
// toggle, username prompt) rather than importing App.jsx itself, since
// pulling in App.jsx here would pull its whole route/map/forum tree
// into this bundle too -- exactly what a separate page is for avoiding.
export default function DiscussionPage() {
  const { lang, setLang, t } = useLang();
  const [user, setUser] = useState(null);
  const [checkedAuth, setCheckedAuth] = useState(false);
  const [centres, setCentres] = useState([]);
  const [centresError, setCentresError] = useState(null);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 4);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    api
      .getCentres()
      .then(setCentres)
      .catch((err) => setCentresError(err.message));
  }, []);

  // First-party, cookie-free page view (2026-09-21) -- see App.jsx's
  // matching call for what this actually records (path + timestamp,
  // nothing else). Fires once per real navigation to this page.
  useEffect(() => {
    api.recordPageView(window.location.pathname + window.location.search);
  }, []);

  useEffect(() => {
    api
      .me()
      .then((u) => setUser(u))
      .catch(() => setUser(null))
      .finally(() => setCheckedAuth(true));
  }, []);

  useSeo({
    title: "Discussion — OntarioDriveTestMap",
    description: "Ask questions and read recent Ontario DriveTest experiences, route discussions and centre-specific advice from other test-takers.",
    path: "/discussion.html",
  });

  if (!checkedAuth) return <LoadingScreen />;

  return (
    <>
      <header className={`app-header${scrolled ? " app-header--scrolled" : ""}`}>
        <div className="app-header__inner">
          <p className="brand-mark">
            <a href="/" style={{ color: "inherit", textDecoration: "none" }}>
              OntarioDriveTestMap
            </a>
          </p>
          <div className="app-header__actions">
            <NotificationBell signedIn={!!user} />
            <a href="/" className="header-link">{t("backToRoutes")}</a>
            <div className="lang-toggle">
              {["en", "fr"].map((l) => (
                <button key={l} onClick={() => setLang(l)} aria-current={lang === l}>
                  {l.toUpperCase()}
                </button>
              ))}
            </div>
            {user && (
              <div className="app-header__account">
                <span>{user.username || user.email}</span>
                {user.is_admin && <span className="badge-admin">ADMIN</span>}
                <button onClick={() => api.logout().then(() => setUser(null))}>{t("signOut")}</button>
              </div>
            )}
          </div>
        </div>
      </header>

      <main id="main">
      <div className="page">
      {!user ? (
        <div className="card" style={{ maxWidth: 420 }}>
          <p>{t("signInPrompt")}</p>
          <Login onLogin={setUser} />
        </div>
      ) : null}

      {user && !user.username && (
        <UsernamePrompt onSet={(username) => setUser((u) => ({ ...u, username }))} />
      )}

      {centresError && <p className="error-banner">{t("couldntLoadCentres")}: {centresError}</p>}

      {user ? (
        <DiscussionSection centres={centres} username={user.username} isAdmin={!!user.is_admin} />
      ) : (
        <p>{t("signInForCentre")}</p>
      )}
      </div>
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
