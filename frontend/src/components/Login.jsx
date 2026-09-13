import { useEffect, useRef } from "react";
import { api } from "../api.js";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;

export default function Login({ onLogin }) {
  const buttonRef = useRef(null);

  useEffect(() => {
    // Google's script loads async via a <script> tag in index.html, so it
    // may not exist yet on first render -- poll briefly rather than assume.
    const tryInit = () => {
      if (!window.google || !buttonRef.current) {
        setTimeout(tryInit, 100);
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
  }, [onLogin]);

  return <div ref={buttonRef} />;
}