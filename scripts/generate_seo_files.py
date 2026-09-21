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

    # Discussion feed -- always indexable.
    urls.append({"loc": SITE_URL + "/discussion.html", "changefreq": "hourly", "priority": "0.8"})

    # Centres: each is genuinely distinct content, worth indexing on its
    # own query-param URL. Route-class (G/G2) filter states within a
    # centre are NOT separately listed -- they're a subset view of the
    # same centre content, not distinct enough to warrant their own entry
    # (see the audit report's canonical-URL reasoning).
    for c in query("SELECT id FROM centres ORDER BY id"):
        urls.append({
            "loc": f"{SITE_URL}/?centre={quote(c['id'])}",
            "changefreq": "weekly",
            "priority": "0.7",
        })

    # Public discussion posts -- real content, real created_at for
    # lastmod (never fabricated).
    for row in query(
        "SELECT id, created_at FROM discussion_posts ORDER BY created_at DESC LIMIT 2000"
    ):
        entry = {
            "loc": f"{SITE_URL}/discussion.html?post={row['id']}",
            "changefreq": "monthly",
            "priority": "0.5",
        }
        if row.get("created_at"):
            entry["lastmod"] = row["created_at"].date().isoformat()
        urls.append(entry)

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
