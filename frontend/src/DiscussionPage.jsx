import { useEffect, useState } from "react";
import { api } from "./api.js";
import { useLang } from "./i18n.jsx";
import Login from "./components/Login.jsx";
import UsernamePrompt from "./components/UsernamePrompt.jsx";
import DiscussionSection from "./components/DiscussionSection.jsx";
import { SiteHeader, SiteFooter } from "./SiteShell.jsx";
import { SignInProvider } from "./SignIn.jsx";
import { useSeo } from "./seo.js";
import LoadingScreen from "./LoadingScreen.jsx";

// Own page (discussion.html), own JS bundle. Shares SiteHeader/
// SiteFooter with index.html rather than importing App.jsx, which would
// pull the whole route/map/forum tree into this bundle.
export default function DiscussionPage() {
  const { t } = useLang();
  const [user, setUser] = useState(null);
  const [checkedAuth, setCheckedAuth] = useState(false);
  const [centres, setCentres] = useState([]);
  const [centresError, setCentresError] = useState(null);

  useEffect(() => {
    api
      .getCentres()
      .then(setCentres)
      .catch((err) => setCentresError(err.message));
  }, []);

  // First-party, cookie-free page view (path + timestamp only).
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
    title: "Road-test discussion — OntarioDriveTestMap",
    description: "Ask questions and read Ontario road-test experiences, route discussions and centre-specific tips from other test-takers.",
    path: "/discussion.html",
  });

  if (!checkedAuth) return <LoadingScreen />;

  return (
    <SignInProvider user={user} setUser={setUser}>
      <SiteHeader
        user={user}
        setUser={setUser}
        current="discussion"
      />

      <main id="main">
        <div className="page">
          {user && !user.username && (
            <UsernamePrompt onSet={(username) => setUser((u) => ({ ...u, username }))} />
          )}
          {centresError && (
            <p className="error-banner" role="alert">
              {t("couldntLoadCentres")}: {centresError}
            </p>
          )}
          {user ? (
            <DiscussionSection centres={centres} username={user.username} isAdmin={!!user.is_admin} />
          ) : (
            <div className="discussion-signedout">
              <div className="discussion-header">
                <h1>{t("discussionTitle")}</h1>
                <p>{t("discussionSub")}</p>
              </div>
              <section className="signin-panel" id="signin" aria-labelledby="signin-title">
                <h2 id="signin-title">{t("signInToDiscussTitle")}</h2>
                <p>{t("signInToDiscussBody")}</p>
                <Login onLogin={setUser} />
              </section>
            </div>
          )}
        </div>
      </main>

      <SiteFooter />
    </SignInProvider>
  );
}
