import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useLang } from "./i18n.jsx";
import Login from "./components/Login.jsx";

// Contextual sign-in (guest-landing spec §3-§7). Reading is public;
// identity is asked for only at a protected action, with the reason for
// THAT action, and the action resumes after sign-in. Google sign-in runs
// in-page (no redirect), so "return to the task" is simply: keep the
// page state and run the pending callback -- no returnTo URL, and so no
// open-redirect surface at all.
const SignInContext = createContext({ user: null, requireSignIn: () => {} });

export function useSignIn() {
  return useContext(SignInContext);
}

function SignInDialog({ reasonKey, onLogin, onClose }) {
  const { t } = useLang();
  const headingRef = useRef(null);

  useEffect(() => {
    const trigger = document.activeElement;
    headingRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (trigger && typeof trigger.focus === "function") trigger.focus();
    };
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-panel signin-dialog" role="dialog" aria-modal="true" aria-labelledby="signin-dialog-title">
        <h2 id="signin-dialog-title" ref={headingRef} tabIndex={-1}>
          {t(`signInReason_${reasonKey}_title`)}
        </h2>
        <p>{t(`signInReason_${reasonKey}_body`)}</p>
        <Login onLogin={onLogin} />
        <p className="signin-dialog__fine">{t("signInPrivacyNote")}</p>
        <button type="button" onClick={onClose}>
          {t("notNow")}
        </button>
      </div>
    </div>
  );
}

export function SignInProvider({ user, setUser, children }) {
  const [request, setRequest] = useState(null); // { reasonKey, action }

  // Run the action immediately if already signed in; otherwise ask, and
  // run it once sign-in succeeds. Cancel keeps everything as it was.
  const requireSignIn = useCallback(
    (reasonKey, action) => {
      if (user) {
        action?.();
        return;
      }
      setRequest({ reasonKey, action });
    },
    [user]
  );

  return (
    <SignInContext.Provider value={{ user, requireSignIn }}>
      {children}
      {request && (
        <SignInDialog
          reasonKey={request.reasonKey}
          onClose={() => setRequest(null)}
          onLogin={(u) => {
            setUser(u);
            const action = request.action;
            setRequest(null);
            // Let the signed-in state render before resuming the task.
            if (action) setTimeout(action, 0);
          }}
        />
      )}
    </SignInContext.Provider>
  );
}
