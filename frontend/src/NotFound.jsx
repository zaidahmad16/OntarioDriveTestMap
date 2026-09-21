import { useLang } from "./i18n.jsx";
import { useSeo } from "./seo.js";

export default function NotFound() {
  const { t } = useLang();
  useSeo({ title: "Page not found — OntarioDriveTestMap", noindex: true, path: window.location.pathname });

  return (
    <div className="not-found">
      <h1>{t("notFoundTitle")}</h1>
      <p>{t("notFoundBody")}</p>
      <div className="not-found__actions">
        <a href="/" className="btn btn-primary">{t("notFoundHome")}</a>
        <a href="/discussion.html" className="btn">{t("notFoundDiscussion")}</a>
      </div>
    </div>
  );
}
