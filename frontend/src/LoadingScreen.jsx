import { useLang } from "./i18n.jsx";

// Small inline spinner for a localized loading spot (Suspense fallback,
// inside a card, next to a button). No layout of its own.
export function Spinner({ size = 18 }) {
  return (
    <span
      className="spinner"
      style={{ width: size, height: size }}
      role="status"
      aria-hidden="true"
    />
  );
}

// Full-section loading state -- replaces bare "Loading..." text for the
// two real blocking waits in the app: initial auth check, and map data
// fetch. Centered, on-brand, respects prefers-reduced-motion via the
// .spinner CSS rule itself.
export default function LoadingScreen({ label }) {
  const { t } = useLang();
  return (
    <div className="loading-screen" role="status" aria-live="polite">
      <Spinner size={28} />
      <p className="loading-screen-label">{label || t("loading")}</p>
    </div>
  );
}
