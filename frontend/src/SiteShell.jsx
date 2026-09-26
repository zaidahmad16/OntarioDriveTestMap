import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { useLang } from "./i18n.jsx";
import NotificationBell from "./components/NotificationBell.jsx";
import { ChevronDownIcon } from "./Icons.jsx";
import { useSignIn } from "./SignIn.jsx";

// Small abstract route-path mark: one solid stroke that turns into a
// dash -- the product's confirmed/inferred notation, not a road sign,
// crest or seal (spec §2.1: nothing that could pass for a DriveTest or
// provincial mark).
export function BrandMark() {
  return (
    <svg className="brand__glyph" width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
      <path d="M5 22 C5 13, 12 13, 14 13" fill="none" stroke="var(--brand)" strokeWidth="3.2" strokeLinecap="round" />
      <path d="M14 13 C18 13, 23 12, 23 5" fill="none" stroke="var(--inferred)" strokeWidth="3.2" strokeLinecap="round" strokeDasharray="3.2 3.6" />
      <circle cx="5" cy="22" r="2.6" fill="var(--ink)" />
    </svg>
  );
}

// Click-outside + Escape close for the header's two popovers. Focus
// goes back to the trigger on Escape so keyboard users aren't dropped
// at the top of the document.
function useDismiss(open, setOpen, rootRef, triggerRef) {
  useEffect(() => {
    if (!open) return;
    function onDown(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, setOpen, rootRef, triggerRef]);
}

export function LangSwitch() {
  const { lang, setLang, t } = useLang();
  return (
    <div className="lang-switch" role="group" aria-label={t("languageLabel")}>
      {[
        ["en", "English"],
        ["fr", "Français"],
      ].map(([code, name]) => (
        <button
          key={code}
          type="button"
          lang={code}
          aria-pressed={lang === code}
          aria-label={name}
          onClick={() => setLang(code)}
        >
          {code.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

// Everything account-shaped lives here instead of in the header row
// (spec §3.1, §9): role badge, weekly summary preference, sign out,
// and delete account behind an explicit second confirmation.
function AccountMenu({ user, setUser }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [digestState, setDigestState] = useState(null); // null | "saved" | "failed"
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  useDismiss(open, setOpen, rootRef, triggerRef);

  useEffect(() => {
    if (!open) setConfirmDelete(false);
  }, [open]);

  const name = user.username || user.email;

  return (
    <div className="account-menu" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="account-menu__trigger"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="account-menu__avatar" aria-hidden="true">
          {name.charAt(0).toUpperCase()}
        </span>
        <span className="account-menu__label">{t("account")}</span>
        <ChevronDownIcon />
      </button>
      {open && (
        <div className="popover popover-panel account-menu__panel">
          <div className="account-menu__who">
            <span className="account-menu__name">{name}</span>
            {user.is_admin && <span className="badge-role">{t("adminRole")}</span>}
          </div>
          {user.is_admin && (
            <fieldset className="account-menu__group">
              <legend>{t("emailPreferences")}</legend>
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={!!user.weekly_digest_opt_in}
                  onChange={(e) => {
                    const optIn = e.target.checked;
                    setUser((u) => ({ ...u, weekly_digest_opt_in: optIn }));
                    setDigestState(null);
                    api
                      .setDigestOptIn(optIn)
                      .then(() => setDigestState("saved"))
                      .catch(() => {
                        setUser((u) => ({ ...u, weekly_digest_opt_in: !optIn }));
                        setDigestState("failed");
                      });
                  }}
                />
                <span>{t("weeklyDigestLabel")}</span>
              </label>
              {digestState && (
                <p className={`account-menu__status account-menu__status--${digestState}`} role="status">
                  {digestState === "saved" ? t("preferenceSaved") : t("preferenceFailed")}
                </p>
              )}
            </fieldset>
          )}
          <button type="button" className="account-menu__item" onClick={() => api.logout().then(() => setUser(null))}>
            {t("signOutLabel")}
          </button>
          <div className="account-menu__danger">
            {!confirmDelete ? (
              <button type="button" className="account-menu__item account-menu__item--danger" onClick={() => setConfirmDelete(true)}>
                {t("deleteAccountLabel")}
              </button>
            ) : (
              <div className="account-menu__confirm" role="alertdialog" aria-label={t("deleteAccountLabel")}>
                <p>{t("deleteAccountExplainer")}</p>
                <div className="account-menu__confirm-actions">
                  <button
                    type="button"
                    className="btn-danger"
                    onClick={() =>
                      api.deleteAccount().then(() => {
                        setUser(null);
                        setOpen(false);
                      })
                    }
                  >
                    {t("deleteAccountFinal")}
                  </button>
                  <button type="button" onClick={() => setConfirmDelete(false)}>
                    {t("cancel")}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// One header row (64-72px) for every React entry point. `current` marks
// the active destination; `onSubmit` opens the in-page wizard when this
// page has one, otherwise Submit links to the home page's wizard.
export function SiteHeader({ user, setUser, current, onSubmit, wide = false }) {
  const { t, lang } = useLang();
  const { requireSignIn } = useSignIn();
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const menuRef = useRef(null);
  const menuBtnRef = useRef(null);
  useDismiss(menuOpen, setMenuOpen, menuRef, menuBtnRef);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 4);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Guests get the product's own language (Centres / How it works) and
  // no dark "Submit" CTA before they've seen a route (guest spec §4.1).
  const links = user
    ? [
        { key: "home", href: "/", label: t("navFindCentre") },
        { key: "discussion", href: "/discussion.html", label: t("discussion") },
        { key: "about", href: "/about.html", label: t("about") },
      ]
    : [
        { key: "home", href: "/#centres", label: t("navCentres") },
        { key: "about", href: "/about.html#confidence", label: t("navHowItWorks") },
        { key: "discussion", href: "/discussion.html", label: t("discussion") },
      ];

  const submitLabel = user ? t("submitRoute") : t("shareRoute");
  const submitClass = user ? "btn-primary site-header__submit" : "site-header__submit site-header__submit--quiet";
  const submit = onSubmit ? (
    <button type="button" className={submitClass} onClick={onSubmit}>
      <span className="site-header__submit-long">{submitLabel}</span>
      <span className="site-header__submit-short" aria-hidden="true">{t("submitShort")}</span>
    </button>
  ) : (
    <a className={`btn ${submitClass}`} href="/?submit=1">
      <span className="site-header__submit-long">{submitLabel}</span>
      <span className="site-header__submit-short" aria-hidden="true">{t("submitShort")}</span>
    </a>
  );

  return (
    <header className={`site-header${scrolled ? " site-header--scrolled" : ""}${wide ? " site-header--wide" : ""}`} lang={lang}>
      <div className="site-header__inner">
        <a className="brand" href="/" aria-label="OntarioDriveTestMap — home">
          <BrandMark />
          <span className="brand__word">
            OntarioDriveTest<span>Map</span>
          </span>
        </a>

        <nav className="site-nav" aria-label={t("primaryNav")}>
          {links.map((l) => (
            <a key={l.key} href={l.href} aria-current={current === l.key ? "page" : undefined}>
              {l.label}
            </a>
          ))}
        </nav>

        <div className="site-header__actions">
          {submit}
          <LangSwitch />
          {user && (
            <span className="notif-slot">
              <NotificationBell signedIn />
            </span>
          )}
          {user ? (
            <AccountMenu user={user} setUser={setUser} />
          ) : (
            <button type="button" className="btn-primary site-header__signin" onClick={() => requireSignIn("general")}>
              {t("signIn")}
            </button>
          )}
          <div className="site-menu" ref={menuRef}>
            <button
              ref={menuBtnRef}
              type="button"
              className="site-menu__trigger"
              aria-expanded={menuOpen}
              aria-controls="site-menu-panel"
              onClick={() => setMenuOpen((o) => !o)}
            >
              {t("menu")}
            </button>
            {menuOpen && (
              <div id="site-menu-panel" className="site-menu__panel popover-panel">
                <nav aria-label={t("primaryNav")}>
                  {links.map((l) => (
                    <a key={l.key} href={l.href} aria-current={current === l.key ? "page" : undefined}>
                      {l.label}
                    </a>
                  ))}
                </nav>
                {!user && (
                  <a className="site-menu__share" href={onSubmit ? undefined : "/?submit=1"} onClick={onSubmit ? (e) => { e.preventDefault(); setMenuOpen(false); onSubmit(); } : undefined} role={onSubmit ? "button" : undefined} tabIndex={0}>
                    {t("shareRoute")}
                  </a>
                )}
                {user && (
                  <div className="site-menu__account">
                    <AccountMenu user={user} setUser={setUser} />
                    <NotificationBell signedIn />
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}

export function SiteFooter({ wide = false }) {
  const { t } = useLang();
  return (
    <footer className={`site-footer${wide ? " site-footer--wide" : ""}`}>
      <div className="site-footer__inner">
        <div>
          <p className="site-footer__brand">
            <BrandMark /> OntarioDriveTestMap
          </p>
          <p className="site-footer__disclaimer">{t("footerDisclaimer")}</p>
        </div>
        <nav aria-label={t("legalNav")}>
          <a href="/about.html">{t("about")}</a>
          <a href="/privacy-policy.html">{t("privacy")}</a>
          <a href="/terms-of-service.html">{t("terms")}</a>
          <a href="/cookie-policy.html">{t("cookies")}</a>
        </nav>
      </div>
    </footer>
  );
}
