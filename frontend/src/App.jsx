import { useEffect, useRef, useState, lazy, Suspense } from "react";
import { api } from "./api.js";
import { useLang } from "./i18n.jsx";
import CentreList, { browsableCentres, normalizeQuery } from "./components/CentreList.jsx";
import TraceList from "./components/TraceList.jsx";
import ForumSection from "./components/ForumSection.jsx";
import UsernamePrompt from "./components/UsernamePrompt.jsx";
import BookingReminderButton from "./components/BookingReminderButton.jsx";
import { useSeo } from "./seo.js";
import Breadcrumbs from "./Breadcrumbs.jsx";
import LoadingScreen, { Spinner } from "./LoadingScreen.jsx";
import SubmissionReminderPopup from "./components/SubmissionReminderPopup.jsx";
import { SiteHeader, SiteFooter } from "./SiteShell.jsx";
import { LineSample } from "./RouteNotation.jsx";
import { SignInProvider, useSignIn } from "./SignIn.jsx";

const SUBMISSION_REMINDER_KEY = "odtm_submission_reminder_last_shown";
const SUBMISSION_REMINDER_INTERVAL_MS = 3600 * 1000;

// Lazy: neither renders on first paint (compare is opt-in, the submit
// modal is closed by default).
const CentreCompare = lazy(() => import("./components/CentreCompare.jsx"));
// Leaflet (~150 KB) lives in these two chunks, so the home page's text,
// search and centre list paint without waiting for the map library.
const MapView = lazy(() => import("./components/MapView.jsx"));
const RoutePreview = lazy(() => import("./components/RoutePreview.jsx"));
const SubmitRouteModal = lazy(() => import("./components/SubmitRouteModal.jsx"));

function readStorage(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode / blocked storage: the reminder just re-shows */
  }
}

function CentreSearch({ query, setQuery, compact }) {
  const { t } = useLang();
  return (
    <form className={`centre-search${compact ? " centre-search--compact" : ""}`} role="search" onSubmit={(e) => e.preventDefault()}>
      <label htmlFor="centre-search">{t("searchCentreLabel")}</label>
      <input
        id="centre-search"
        type="search"
        value={query}
        autoComplete="off"
        placeholder={t("searchCentrePlaceholder")}
        onChange={(e) => setQuery(e.target.value)}
      />
    </form>
  );
}

// Home. Signed-in users get the direct "find your centre" page; guests
// get a deliberate landing (guest spec §4): the product's purpose, a
// REAL published route beside it, and the centres straight away -- no
// sign-in card in front of the map.
function HomeView({ centres, centresError, user, onSelect, onSubmit }) {
  const { t, tn } = useLang();
  const [query, setQuery] = useState(() => new URLSearchParams(window.location.search).get("q") || "");
  const browsable = browsableCentres(centres);
  const q = normalizeQuery(query);
  const results = q ? browsable.filter((c) => normalizeQuery(`${c.name} ${c.id}`).includes(q)) : browsable;

  // Keep the query in the URL so "back" from a centre restores it.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (query) params.set("q", query);
    else params.delete("q");
    const qs = params.toString();
    const next = (qs ? `/?${qs}` : "/") + window.location.hash;
    if (next !== window.location.pathname + window.location.search + window.location.hash) {
      window.history.replaceState(null, "", next);
    }
  }, [query]);

  const loading = !centresError && centres.length === 0;

  const directory = (
    <section className="home-results" id="centres" aria-labelledby="results-title">
      <div className="section-head">
        <h2 id="results-title" className="section-head__title">
          {user ? t("centresWithRoutes") : t("findCentre")}
        </h2>
        <p className="section-head__meta" role="status" aria-live="polite">
          {loading ? t("loadingCentres") : tn("centresResultN", results.length).replace("{n}", results.length)}
        </p>
      </div>
      {!user && <CentreSearch query={query} setQuery={setQuery} compact />}

      {centresError ? (
        <div className="state-panel state-panel--error" role="alert">
          <p>
            {t("couldntLoadCentres")}. {t("checkConnection")}
          </p>
          <button type="button" onClick={() => window.location.reload()}>
            {t("retry")}
          </button>
        </div>
      ) : loading ? (
        <ul className="centre-index centre-index--loading" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <li key={i} className="skeleton-row" />
          ))}
        </ul>
      ) : results.length ? (
        <CentreList centres={results} onSelect={onSelect} />
      ) : (
        <div className="state-panel">
          <p>{t("noCentreMatch").replace("{query}", query)}</p>
          <div className="state-panel__actions">
            <button type="button" onClick={() => setQuery("")}>
              {t("clearSearch")}
            </button>
            <button type="button" onClick={onSubmit}>
              {t("submitEvidenceOtherCentre")}
            </button>
          </div>
        </div>
      )}

      {!centresError && !loading && (
        <p className="home-results__missing">
          {t("dontSeeCentre")}{" "}
          <button type="button" className="link-btn" onClick={onSubmit}>
            {t("submitRouteForCentre")}
          </button>
        </p>
      )}
    </section>
  );

  return (
    <>
      {user ? (
        <>
          <section className="home-hero" aria-labelledby="home-title">
            <div className="home-hero__text">
              <p className="overline">{t("homeOverline")}</p>
              <h1 id="home-title" className="home-hero__title">
                {t("findCentre")}
              </h1>
              <p className="home-hero__lede">{t("homeLede")}</p>
              <CentreSearch query={query} setQuery={setQuery} />
            </div>
          </section>
          {directory}
        </>
      ) : (
        <div className="guest-home">
          <section className="guest-hero" aria-labelledby="home-title">
            <p className="overline">{t("guestOverline")}</p>
            <h1 id="home-title" className="home-hero__title">
              {t("guestHeadline")}
            </h1>
            <p className="home-hero__lede">{t("guestLede")}</p>
            <div className="guest-hero__actions">
              <a className="btn btn-primary" href="#centres">
                {t("exploreCentres")}
              </a>
              <a href="/about.html#confidence">{t("readMethod")}</a>
            </div>
            <ul className="guest-hero__notation" aria-label={t("trustNoteTitle")}>
              <li>
                <LineSample status="confirmed" width={32} /> <b>{t("statusConfirmed")}</b> {t("guestConfirmedShort")}
              </li>
              <li>
                <LineSample status="inferred" width={32} /> <b>{t("statusInferred")}</b> {t("guestInferredShort")}
              </li>
            </ul>
          </section>
          <div className="guest-home__preview">
            {centres.length > 0 && (
              <Suspense fallback={<div className="route-preview route-preview--loading" aria-hidden="true" />}>
                <RoutePreview centres={centres} />
              </Suspense>
            )}
          </div>
          <div className="guest-home__directory">{directory}</div>
        </div>
      )}

      <section className="trust-note" aria-labelledby="trust-title">
        <h2 id="trust-title">{t("trustNoteTitle")}</h2>
        <div className="trust-note__body">
          <dl className="trust-note__samples">
            <div>
              <dt>
                <LineSample status="confirmed" width={56} />
                {t("statusConfirmed")}
              </dt>
              <dd>{t("trustConfirmedDef")}</dd>
            </div>
            <div>
              <dt>
                <LineSample status="inferred" width={56} />
                {t("statusInferred")}
              </dt>
              <dd>{t("trustInferredDef")}</dd>
            </div>
          </dl>
          <p className="trust-note__caveat">
            {t("trustCaveat")} <a href="/about.html#confidence">{t("readMethod")}</a>
          </p>
        </div>
      </section>

      <Suspense fallback={<div className="suspense-fallback"><Spinner /></div>}>
        <CentreCompare />
      </Suspense>
    </>
  );
}

// Centre + route workspace (spec §5). Public read-only for guests (owner
// decision 2026-09-25); contribution/account features ask for sign-in
// in context via useSignIn().
function CentreDetail({ centre, centreId, centres, user, onSelect }) {
  const { t } = useLang();
  const { requireSignIn } = useSignIn();
  const browsable = browsableCentres(centres);
  const name = centre?.name || centreId;

  return (
    <>
      <div className="centre-head">
        <div className="centre-head__id">
          <Breadcrumbs
            items={[
              { name: t("centres"), href: "/", onClick: (e) => { e.preventDefault(); onSelect(null); } },
              { name },
            ]}
          />
          <h1 className="centre-head__title">{name}</h1>
        </div>
        <div className="centre-head__tools">
          <label className="centre-switch">
            <span>{t("changeCentre")}</span>
            <select value={centreId} onChange={(e) => onSelect(e.target.value)}>
              {!browsable.some((c) => c.id === centreId) && <option value={centreId}>{name}</option>}
              {browsable.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          {user && <BookingReminderButton centres={centres} defaultCentreId={centreId} />}
        </div>
      </div>

      <Suspense fallback={<LoadingScreen />}>
        <MapView key={centreId} centreId={centreId} centreName={name} />
      </Suspense>
      <div className="page page--sections">
        <TraceList centreId={centreId} centreName={name} isAdmin={!!user?.is_admin} signedIn={!!user} />
        {user ? (
          <ForumSection centreId={centreId} centreName={name} username={user.username} />
        ) : (
          <section className="page-section" id="centre-tips" aria-labelledby="tips-title">
            <div className="page-section__head">
              <h2 id="tips-title">{t("tipsForCentre").replace("{centre}", name)}</h2>
              <p className="page-section__lede">{t("tipsGuestLede")}</p>
            </div>
            <div className="state-panel">
              <p>{t("tipsGuestBody")}</p>
              <button type="button" onClick={() => requireSignIn("tips")}>
                {t("signInToReadTips")}
              </button>
            </div>
          </section>
        )}
      </div>
    </>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [checkedAuth, setCheckedAuth] = useState(false);
  const [centres, setCentres] = useState([]);
  const [centresError, setCentresError] = useState(null);
  const [selectedCentre, setSelectedCentre] = useState(
    () => new URLSearchParams(window.location.search).get("centre") || null
  );
  const [submitOpen, setSubmitOpen] = useState(
    () => new URLSearchParams(window.location.search).get("submit") === "1"
  );
  const [reminderOpen, setReminderOpen] = useState(false);
  // undefined until the first sync; lets the URL effect tell a real
  // centre switch from page load (and from StrictMode's double-run).
  const prevCentre = useRef(undefined);

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

  // The site-wide submission prompt keeps the owner's hourly cadence
  // (a product decision, not a CSS one) but is now a non-blocking
  // banner that only appears on the home view -- never over a route
  // study session, the wizard, or a practice drive (spec §9).
  useEffect(() => {
    if (!user) return;
    let timer;
    const showAndScheduleNext = () => {
      setReminderOpen(true);
      writeStorage(SUBMISSION_REMINDER_KEY, String(Date.now()));
      timer = setTimeout(showAndScheduleNext, SUBMISSION_REMINDER_INTERVAL_MS);
    };
    const lastShown = Number(readStorage(SUBMISSION_REMINDER_KEY) || 0);
    const elapsed = Date.now() - lastShown;
    if (!lastShown || elapsed >= SUBMISSION_REMINDER_INTERVAL_MS) {
      showAndScheduleNext();
    } else {
      timer = setTimeout(showAndScheduleNext, SUBMISSION_REMINDER_INTERVAL_MS - elapsed);
    }
    return () => clearTimeout(timer);
  }, [user]);

  // Real back/forward between home and a centre: pushState on user
  // navigation, popstate restores. `?centre=` semantics are unchanged.
  useEffect(() => {
    function onPop() {
      setSelectedCentre(new URLSearchParams(window.location.search).get("centre") || null);
    }
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    const isSwitch = prevCentre.current !== undefined && prevCentre.current !== selectedCentre;
    prevCentre.current = selectedCentre;
    const params = new URLSearchParams(window.location.search);
    if (selectedCentre) {
      params.set("centre", selectedCentre);
      params.delete("q");
    } else {
      params.delete("centre");
    }
    // A route deep link belongs to the centre it was made on.
    if (isSwitch || !selectedCentre) {
      params.delete("class");
      params.delete("route");
    }
    params.delete("submit");
    const qs = params.toString();
    const next = qs ? `/?${qs}` : "/";
    if (next !== window.location.pathname + window.location.search) {
      if (isSwitch) window.history.pushState(null, "", next);
      else window.history.replaceState(null, "", next);
    }
    api.recordPageView(next);
    if (isSwitch) window.scrollTo({ top: 0 });
  }, [selectedCentre]);

  const selectedCentreObj = centres.find((c) => c.id === selectedCentre);

  useSeo(
    selectedCentreObj
      ? {
          title: `${selectedCentreObj.name} DriveTest Routes — OntarioDriveTestMap`,
          description: `Study reconstructed G and G2 road-test routes for ${selectedCentreObj.name}, with confirmed and inferred sections labelled and the evidence behind them.`,
          path: `/?centre=${encodeURIComponent(selectedCentre)}`,
          breadcrumbJsonLd: undefined,
        }
      : {
          title: "OntarioDriveTestMap — Ontario G/G2 road-test routes",
          description: "An independent map of reported Ontario G and G2 road-test routes, built from GPS traces, video-transcribed drives and community reports, with confirmed and inferred sections labelled.",
          path: "/",
        }
  );

  const onboardingNeeded = user && !user.username;
  const showReminder = reminderOpen && !selectedCentre && !submitOpen && !onboardingNeeded;

  if (!checkedAuth) return <LoadingScreen />;

  return (
    <SignInProvider user={user} setUser={setUser}>
      <SiteHeader
        user={user}
        setUser={setUser}
        current={selectedCentre ? null : "home"}
        wide={!!selectedCentre}
        onSubmit={() => setSubmitOpen(true)}
      />

      <main id="main" className={selectedCentre ? "main--workspace" : "main--home"}>
        {onboardingNeeded && (
          <div className="page page--notice">
            <UsernamePrompt onSet={(username) => setUser((u) => ({ ...u, username }))} />
          </div>
        )}

        {selectedCentre ? (
          <CentreDetail
            centre={selectedCentreObj}
            centreId={selectedCentre}
            centres={centres}
            user={user}
            onSelect={setSelectedCentre}
          />
        ) : (
          <div className="page">
            <HomeView
              centres={centres}
              centresError={centresError}
              user={user}
              onSelect={setSelectedCentre}
              onSubmit={() => setSubmitOpen(true)}
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
              defaultCentreId={selectedCentre}
              onLogin={setUser}
            />
          </Suspense>
        )}

        {showReminder && (
          <SubmissionReminderPopup
            onSubmitRoute={() => {
              setReminderOpen(false);
              setSubmitOpen(true);
            }}
            onDismiss={() => setReminderOpen(false)}
          />
        )}
      </main>

      <SiteFooter wide={!!selectedCentre} />
    </SignInProvider>
  );
}
