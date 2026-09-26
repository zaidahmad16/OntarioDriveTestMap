import { useState } from "react";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";

// Shown once, right after Google sign-in, until the user has a
// username set -- forum posts/comments are blocked server-side without
// one (see backend main.py's create_forum_post), since @mentions only
// mean something if the poster is addressable by a real handle.
export default function UsernamePrompt({ onSet }) {
  const { t } = useLang();
  const [value, setValue] = useState("");
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  function save() {
    const name = value.trim();
    if (!name || saving) return;
    setSaving(true);
    setError(null);
    api
      .setUsername(name)
      .then((res) => onSet(res.username)) // echo back what the server actually saved, not the local draft value
      .catch((err) => setError(err.message))
      .finally(() => setSaving(false));
  }

  return (
    <div className="notice-banner anim-rise">
      <p style={{ margin: "0 0 var(--space-sm)" }}>{t("chooseUsernamePrompt")}</p>
      <div style={{ display: "flex", gap: "var(--space-sm)" }}>
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()}
          placeholder={t("usernamePlaceholder")}
          autoComplete="off"
          style={{ flex: "0 1 220px" }}
        />
        <button onClick={save} disabled={saving || !value.trim()} className="btn-primary">
          {saving ? t("loading") : t("save")}
        </button>
      </div>
      {error && <p style={{ color: "var(--danger)", fontSize: "0.8125rem", margin: "var(--space-xs) 0 0" }}>{error}</p>}
    </div>
  );
}
