import { useEffect, useRef } from "react";
import { api } from "../api.js";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;

export default function Login({ onLogin }) {
  const buttonRef = useRef(null);

  useEffect(() => {
    // Google's script loads async via a <script> tag in index.html, so it
    // may not exist yet on first render -- poll briefly rather than assume.
    // `cancelled` stops the poll on unmount -- without it, a slow/blocked
    // Google script (ad-blocker, slow network) left this polling forever,
    // every 100ms, for the rest of the tab's life if the user navigated
    // away before it ever loaded (found in a 2026-09-21 cleanup pass).
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
          try {
            const user = await api.loginWithGoogle(response.credential);
            onLogin(user);
          } catch (err) {
            alert(`Sign-in failed: ${err.message}`);
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

  return <div ref={buttonRef} />;
}