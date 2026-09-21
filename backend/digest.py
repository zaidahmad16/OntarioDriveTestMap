"""
digest.py — computes and sends the weekly admin summary email.

Two things live here: compute_weekly_digest() (pure data, queried fresh
from Postgres every call, nothing cached/precomputed) and send_digest_email()
(Resend API call). Kept separate from main.py since neither runs inside a
normal request/response cycle -- compute_weekly_digest() is also used by
scripts/send_weekly_digest.py, which is meant to be triggered by an
external weekly cron, not by FastAPI itself.

Every number here comes from a real query against existing tables --
nothing is estimated or invented. Moderation auto-hide state (forum/
discussion) has no event log (it's computed at query time everywhere
else in this app, see forum.py/discussions.py), so this reports a
current snapshot count of hidden posts, not "N posts crossed the
threshold this week" -- that distinction is called out in the email
itself so it isn't misread as a weekly delta.
"""

import html
import os

from db import query

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RESEND_FROM = os.environ.get("RESEND_FROM", "OntarioDriveTestMap <digest@ontariodrivetestmap.fyi>")

SCORE_HIDE_THRESHOLD = -5
REPORT_HIDE_THRESHOLD = 3


def compute_weekly_digest() -> dict:
    """Real usage stats for the trailing 7 days, plus a page-view
    snapshot from the first-party counter. Every value here is a plain
    COUNT/SUM against existing columns -- see the SQL, not a guess."""

    new_users = query(
        "SELECT count(*) AS n FROM users WHERE created_at >= now() - interval '7 days'",
        one=True,
    )["n"]

    new_submissions = query(
        "SELECT count(*) AS n FROM user_submissions WHERE created_at >= now() - interval '7 days'",
        one=True,
    )["n"]

    promoted_submissions = query(
        """
        SELECT count(*) AS n FROM user_submissions
        WHERE created_at >= now() - interval '7 days' AND status = 'promoted'
        """,
        one=True,
    )["n"]

    new_forum_posts = query(
        "SELECT count(*) AS n FROM forum_posts WHERE created_at >= now() - interval '7 days'",
        one=True,
    )["n"]

    new_forum_comments = query(
        "SELECT count(*) AS n FROM forum_comments WHERE created_at >= now() - interval '7 days'",
        one=True,
    )["n"]

    new_discussion_posts = query(
        "SELECT count(*) AS n FROM discussion_posts WHERE created_at >= now() - interval '7 days'",
        one=True,
    )["n"]

    new_discussion_comments = query(
        "SELECT count(*) AS n FROM discussion_comments WHERE created_at >= now() - interval '7 days'",
        one=True,
    )["n"]

    # Votes/reports tables have no created_at column (see schema.sql --
    # they're simple join rows keyed on post_id+user_id), so a true
    # "cast this week" count isn't derivable without adding a timestamp
    # column, which this pass doesn't do. Reporting all-time totals
    # instead and labeling them as such in the email, rather than
    # silently mislabeling an all-time count as a weekly one.
    forum_votes_total = query("SELECT count(*) AS n FROM forum_votes", one=True)["n"]
    forum_reports_total = query("SELECT count(*) AS n FROM forum_reports", one=True)["n"]
    discussion_votes_total = query("SELECT count(*) AS n FROM discussion_votes", one=True)["n"]
    discussion_reports_total = query("SELECT count(*) AS n FROM discussion_reports", one=True)["n"]

    forum_hidden_now = query(
        """
        SELECT count(*) AS n FROM (
            SELECT fp.id,
                   coalesce(sum(fv.value), 0) AS score,
                   count(DISTINCT fr.user_id) AS reports
            FROM forum_posts fp
            LEFT JOIN forum_votes fv ON fv.post_id = fp.id
            LEFT JOIN forum_reports fr ON fr.post_id = fp.id
            GROUP BY fp.id
        ) s
        WHERE score <= %s OR reports >= %s
        """,
        (SCORE_HIDE_THRESHOLD, REPORT_HIDE_THRESHOLD),
        one=True,
    )["n"]

    discussion_hidden_now = query(
        """
        SELECT count(*) AS n FROM (
            SELECT dp.id,
                   coalesce(sum(dv.value), 0) AS score,
                   count(DISTINCT dr.user_id) AS reports
            FROM discussion_posts dp
            LEFT JOIN discussion_votes dv ON dv.post_id = dp.id
            LEFT JOIN discussion_reports dr ON dr.post_id = dp.id
            GROUP BY dp.id
        ) s
        WHERE score <= %s OR reports >= %s
        """,
        (SCORE_HIDE_THRESHOLD, REPORT_HIDE_THRESHOLD),
        one=True,
    )["n"]

    page_views_total = query(
        "SELECT count(*) AS n FROM page_views WHERE viewed_at >= now() - interval '7 days'",
        one=True,
    )["n"]

    top_paths = query(
        """
        SELECT path, count(*) AS n FROM page_views
        WHERE viewed_at >= now() - interval '7 days'
        GROUP BY path ORDER BY n DESC LIMIT 5
        """,
    )

    return {
        "new_users": new_users,
        "new_submissions": new_submissions,
        "promoted_submissions": promoted_submissions,
        "new_forum_posts": new_forum_posts,
        "new_forum_comments": new_forum_comments,
        "new_discussion_posts": new_discussion_posts,
        "new_discussion_comments": new_discussion_comments,
        "forum_votes_total": forum_votes_total,
        "forum_reports_total": forum_reports_total,
        "discussion_votes_total": discussion_votes_total,
        "discussion_reports_total": discussion_reports_total,
        "forum_hidden_now": forum_hidden_now,
        "discussion_hidden_now": discussion_hidden_now,
        "page_views_total": page_views_total,
        "top_paths": [{"path": r["path"], "views": r["n"]} for r in top_paths],
    }


def render_digest_html(stats: dict) -> str:
    """Plain, table-based HTML -- email clients don't reliably support
    modern CSS (flex/grid/custom properties), so this intentionally
    doesn't try to reuse the site's actual App.css tokens, just its
    palette values as hardcoded hex."""

    def row(label, value):
        # SEC-001: every interpolated value is DB-sourced and must be
        # escaped -- this renders in an admin's email client, not React,
        # so there's no default escaping to rely on here.
        return (
            f'<tr><td style="padding:6px 12px;color:#65635e;font-size:14px;">{html.escape(str(label))}</td>'
            f'<td style="padding:6px 12px;color:#171717;font-size:14px;font-weight:600;text-align:right;">{html.escape(str(value))}</td></tr>'
        )

    top_paths_html = "".join(
        f'<tr><td style="padding:4px 12px;color:#65635e;font-size:13px;">{html.escape(p["path"])}</td>'
        f'<td style="padding:4px 12px;color:#171717;font-size:13px;text-align:right;">{html.escape(str(p["views"]))}</td></tr>'
        for p in stats["top_paths"]
    ) or '<tr><td style="padding:4px 12px;color:#8b8881;font-size:13px;">No page views recorded this week.</td></tr>'

    return f"""
    <div style="background:#f6f4ef;padding:32px 16px;font-family:Arial,Helvetica,sans-serif;">
      <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #dddad3;border-radius:10px;padding:24px;">
        <h1 style="font-size:20px;color:#171717;margin:0 0 4px;">OntarioDriveTestMap — Weekly Summary</h1>
        <p style="font-size:13px;color:#8b8881;margin:0 0 20px;">Trailing 7 days</p>

        <table style="width:100%;border-collapse:collapse;">
          {row("New users", stats["new_users"])}
          {row("New route submissions", stats["new_submissions"])}
          {row("Submissions promoted to official routes", stats["promoted_submissions"])}
          {row("New forum posts", stats["new_forum_posts"])}
          {row("New forum comments", stats["new_forum_comments"])}
          {row("New discussion posts", stats["new_discussion_posts"])}
          {row("New discussion comments", stats["new_discussion_comments"])}
          {row("Page views (this site's own counter)", stats["page_views_total"])}
        </table>

        <p style="font-size:12px;color:#8b8881;margin:20px 0 4px;">All-time totals (no per-week timestamp exists for these)</p>
        <table style="width:100%;border-collapse:collapse;">
          {row("Forum votes", stats["forum_votes_total"])}
          {row("Forum reports", stats["forum_reports_total"])}
          {row("Discussion votes", stats["discussion_votes_total"])}
          {row("Discussion reports", stats["discussion_reports_total"])}
        </table>

        <p style="font-size:12px;color:#8b8881;margin:20px 0 4px;">Currently auto-hidden (snapshot, not a weekly count)</p>
        <table style="width:100%;border-collapse:collapse;">
          {row("Forum posts hidden by moderation", stats["forum_hidden_now"])}
          {row("Discussion posts hidden by moderation", stats["discussion_hidden_now"])}
        </table>

        <p style="font-size:12px;color:#8b8881;margin:20px 0 4px;">Top pages this week</p>
        <table style="width:100%;border-collapse:collapse;">
          {top_paths_html}
        </table>

        <p style="font-size:11px;color:#8b8881;margin-top:24px;">
          You're receiving this because your admin account opted in to weekly summaries.
          Turn it off any time in account settings.
        </p>
      </div>
    </div>
    """


def send_digest_email(to_email: str, stats: dict) -> bool:
    """Sends via Resend's REST API directly (httpx, no extra SDK
    dependency). Returns False and logs, rather than raising, if no API
    key is configured -- this must never crash a caller. No real
    RESEND_API_KEY exists yet as of this build; that's expected until
    the project owner creates a Resend account."""
    if not RESEND_API_KEY:
        print(
            "[digest] RESEND_API_KEY not set -- skipping send to "
            f"{to_email}. Set RESEND_API_KEY in .env to enable real sends."
        )
        return False

    import requests

    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={
            "from": RESEND_FROM,
            "to": [to_email],
            "subject": "OntarioDriveTestMap — Weekly Summary",
            "html": render_digest_html(stats),
        },
        timeout=15,
    )
    if resp.status_code >= 400:
        print(f"[digest] Resend send to {to_email} failed: {resp.status_code} {resp.text}")
        return False
    return True


def opted_in_admin_emails() -> list[str]:
    rows = query(
        """
        SELECT email FROM users
        WHERE is_admin = true AND weekly_digest_opt_in = true AND deleted_at IS NULL
        """
    )
    return [r["email"] for r in rows]
