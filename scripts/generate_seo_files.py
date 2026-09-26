"""
generate_seo_files.py — builds frontend/public/sitemap.xml and robots.txt
from real application data (centres, route classes, public discussion
posts), rather than hand-maintaining them.

Run manually after data changes (new centre, new promoted route class, or
periodically for new discussion posts) and before `npm run build`:

    SITE_URL=https://your-real-domain python scripts/generate_seo_files.py

Defaults to http://localhost:5173 if SITE_URL isn't set, matching the
frontend's own VITE_SITE_URL dev default -- the two should be kept in
sync once a real production domain exists.

URLs reflect this app's REAL current URL architecture (query-param based,
not path-based -- see MapView/DiscussionSection for why: introducing full
path routing was judged too risky to bundle into this same pass, see the
session's SEO audit report). No lastmod timestamps are fabricated; a
lastmod is only emitted where a real created_at/observed date exists.
"""

import os
import sys
from datetime import date
from urllib.parse import quote
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from db import query  # noqa: E402

SITE_URL = os.environ.get("SITE_URL", "http://localhost:5173").rstrip("/")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "public")


def fetch_urls():
    urls = []

    # Homepage -- always indexable.
    urls.append({"loc": SITE_URL + "/", "changefreq": "weekly", "priority": "1.0"})

    # About -- the public explainer of sources, confidence and features.
    urls.append({"loc": SITE_URL + "/about.html", "changefreq": "monthly", "priority": "0.6"})

    # Discussion feed page (its posts need sign-in to read, so individual
    # post URLs are NOT listed -- a crawler would only hit the sign-in
    # screen).
    urls.append({"loc": SITE_URL + "/discussion.html", "changefreq": "daily", "priority": "0.5"})

    # Centres with evidence. Route maps and turns are public (2026-09-25),
    # so each centre page is real indexable content. Centres with no
    # evidence yet (e.g. Winchester) are hidden from browsing and skipped
    # here too. G/G2/route deep links are views of the same page and are
    # not listed separately.
    for c in query(
        """SELECT c.id FROM centres c
           WHERE EXISTS (SELECT 1 FROM traces t WHERE t.centre_id = c.id)
              OR EXISTS (SELECT 1 FROM consensus_segments s WHERE s.centre_id = c.id)
           ORDER BY c.id"""
    ):
        urls.append({
            "loc": f"{SITE_URL}/?centre={quote(c['id'])}",
            "changefreq": "weekly",
            "priority": "0.8",
        })

    return urls


def write_sitemap(urls):
    os.makedirs(OUT_DIR, exist_ok=True)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for u in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(u['loc'])}</loc>")
        if u.get("lastmod"):
            lines.append(f"    <lastmod>{u['lastmod']}</lastmod>")
        lines.append(f"    <changefreq>{u['changefreq']}</changefreq>")
        lines.append(f"    <priority>{u['priority']}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    path = os.path.join(OUT_DIR, "sitemap.xml")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {path} ({len(urls)} URLs)")


def write_robots():
    os.makedirs(OUT_DIR, exist_ok=True)
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /discussion.html\n"
        "\n"
        "Disallow: /admin\n"
        "Disallow: /account\n"
        "Disallow: /notifications\n"
        "\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    path = os.path.join(OUT_DIR, "robots.txt")
    with open(path, "w") as f:
        f.write(content)
    print(f"Wrote {path}")


def write_llms_txt():
    template_path = os.path.join(os.path.dirname(__file__), "llms.txt.template")
    with open(template_path) as f:
        content = f.read()
    content = content.replace("{SITE_URL}", SITE_URL)
    path = os.path.join(OUT_DIR, "llms.txt")
    with open(path, "w") as f:
        f.write(content)
    print(f"Wrote {path}")


if __name__ == "__main__":
    urls = fetch_urls()
    write_sitemap(urls)
    write_robots()
    write_llms_txt()
    print(f"Generated {date.today().isoformat()} for SITE_URL={SITE_URL}")
