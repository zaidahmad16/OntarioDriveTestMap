"""
send_weekly_digest.py — sends the weekly admin summary email.

Lives in backend/scripts/, not the project-root scripts/ directory --
this deliberately deviates from that directory's convention (root-level
pipeline scripts that talk to Postgres directly with their own psycopg2
connection) because this script instead imports digest.py/db.py as real
backend package internals (bare `from db import query`, same style as
main.py itself), so it has to run with backend/ as its working
directory, same as the API server does.

NOT wired into FastAPI's request/response cycle on purpose -- this is
meant to be triggered by an external weekly scheduler (e.g. a Railway
Cron Job, or any host's crontab running this on a weekly cadence), not
by a web request. Configuring that scheduler is a real production
infra decision left to the project owner; this script only does the
compute-and-send part.

Usage:
    cd backend && source ../venv/bin/activate && python scripts/send_weekly_digest.py

Requires RESEND_API_KEY in the environment (.env). Without it, this
prints a clear message and exits 0 -- a missing key is not a failure,
it's the expected state until a real Resend account exists.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from digest import RESEND_API_KEY, compute_weekly_digest, opted_in_admin_emails, send_digest_email


def main():
    recipients = opted_in_admin_emails()
    if not recipients:
        print("[send_weekly_digest] No admin has opted in to the weekly digest. Nothing to send.")
        return

    if not RESEND_API_KEY:
        print(
            "[send_weekly_digest] RESEND_API_KEY is not set -- no email will be sent. "
            "Create a Resend account (resend.com), get an API key, and set "
            "RESEND_API_KEY in the repo root .env to enable real sends. "
            f"Would have sent to: {', '.join(recipients)}"
        )
        return

    stats = compute_weekly_digest()
    print(f"[send_weekly_digest] Computed stats: {stats}")

    for email in recipients:
        ok = send_digest_email(email, stats)
        print(f"[send_weekly_digest] {'Sent' if ok else 'FAILED to send'} to {email}")


if __name__ == "__main__":
    main()
