import { useEffect } from "react";

// Single source of truth for the site's public base URL. Falls back to
// the real origin in dev/preview so canonical/OG/sitemap links are never
// wrong just because VITE_SITE_URL wasn't set for a given environment --
// but production deploys should set VITE_SITE_URL explicitly so the value
// is stable even behind a proxy/CDN.
// Production pins the apex domain so www (or the raw Railway hostname)
// never becomes a canonical URL; dev keeps the real local origin.
export const SITE_URL = (
  import.meta.env.VITE_SITE_URL ||
  (import.meta.env.PROD ? "https://ontariodrivetestmap.fyi" : window.location.origin)
).replace(/\/$/, "");

function setMeta(attr, key, content) {
  if (!content) return;
  let el = document.head.querySelector(`meta[${attr}="${key}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);
}

function setLink(rel, href) {
  let el = document.head.querySelector(`link[rel="${rel}"]`);
  if (!href) {
    if (el) el.remove();
    return;
  }
  if (!el) {
    el = document.createElement("link");
    el.setAttribute("rel", rel);
    document.head.appendChild(el);
  }
  el.setAttribute("href", href);
}

function setJsonLd(id, data) {
  let el = document.getElementById(id);
  if (!data) {
    if (el) el.remove();
    return;
  }
  if (!el) {
    el = document.createElement("script");
    el.type = "application/ld+json";
    el.id = id;
    document.head.appendChild(el);
  }
  el.textContent = JSON.stringify(data);
}

/**
 * Updates document title/meta/canonical/OG/JSON-LD for the current view.
 * This app has no SSR, so these updates are only visible to clients that
 * execute JavaScript (real browsers, most modern crawlers) -- a crawler
 * that only reads the initial HTML response will still see the static
 * defaults baked into index.html/discussion.html. Acceptable for a
 * client-rendered SPA at this stage; a real fix would need SSR/prerendering,
 * out of scope for this pass.
 */
export function useSeo({
  title,
  description,
  path = "/",
  ogType = "website",
  ogImage,
  noindex = false,
  jsonLd,
  breadcrumbJsonLd,
} = {}) {
  useEffect(() => {
    if (title) document.title = title;
    setMeta("name", "description", description);
    setMeta("property", "og:title", title);
    setMeta("property", "og:description", description);
    setMeta("property", "og:type", ogType);
    setMeta("property", "og:url", `${SITE_URL}${path}`);
    setMeta("property", "og:image", ogImage || `${SITE_URL}/og-default.png`);
    setMeta("name", "twitter:card", "summary_large_image");
    setMeta("name", "twitter:title", title);
    setMeta("name", "twitter:description", description);
    setMeta("name", "robots", noindex ? "noindex, follow" : "index, follow");
    setLink("canonical", `${SITE_URL}${path}`);
    setJsonLd("ld-page", jsonLd || null);
    setJsonLd("ld-breadcrumb", breadcrumbJsonLd || null);
  }, [title, description, path, ogType, ogImage, noindex, jsonLd, breadcrumbJsonLd]);
}

export function breadcrumbList(items) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: item.name,
      item: item.path ? `${SITE_URL}${item.path}` : undefined,
    })),
  };
}
