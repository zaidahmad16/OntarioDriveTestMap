import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;
const GSI_SRC = "https://accounts.google.com/gsi/client";

// Google's sign-in script is ~100 KB and used to load on every page view.
// Now that reading is public, it loads only when a sign-in button is
// actually rendered (header dialog, wizard, discussion), once per page.
function loadGsi() {
  if (window.google?.accounts?.id) return;
  if (document.querySelector(`script[src="${GSI_SRC}"]`)) return;
  const el = document.createElement("script");
  el.src = GSI_SRC;
  el.async = true;
  el.defer = true;
  document.head.appendChild(el);
}

export default function Login({ onLogin }) {
  const { t } = useLang();
  const buttonRef = useRef(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    // Google's script loads async via a <script> tag in index.html, so it
    // may not exist yet on first render -- poll briefly rather than assume.
    // `cancelled` stops the poll on unmount -- without it, a slow/blocked
    // Google script (ad-blocker, slow network) left this polling forever,
    // every 100ms, for the rest of the tab's life if the user navigated
    // away before it ever loaded (found in a 2026-09-21 cleanup pass).
    loadGsi();
    let cancelled = false;
    let timeoutId;
    const tryInit = () => {
      if (cancelled) return;
      if (!window.google || !buttonRef.current) {
        timeoutId = setTimeout(tryInit, 100);
        return;
      }
      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: async (response) => {
          // Inline, not alert(): a blocking browser dialog on a map
          // page is hostile and unreadable by the rest of the UI.
          try {
            setError(null);
            const user = await api.loginWithGoogle(response.credential);
            onLogin(user);
          } catch (err) {
            setError(err.message);
          }
        },
      });
      window.google.accounts.id.renderButton(buttonRef.current, {
        theme: "outline",
        size: "large",
      });
    };
    tryInit();
    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [onLogin]);

  return (
    <div className="login">
      <div ref={buttonRef} />
      {error && (
        <p className="field-error" role="alert">
          {t("signInFailed")}: {error}
        </p>
      )}
    </div>
  );
}