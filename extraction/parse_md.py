#!/usr/bin/env python3
"""
parse_md.py — pull posts and comments out of the saved Reddit pages.

Reads the raw .md pages rather than any derived CSV, because the CSV
truncated 230 comment bodies and hid a same-author collision that
invalidated a corroboration count.

Anchored on the comment permalink, which appears exactly once per comment.
Anchoring on the username line loses every deleted account, which was 28%.

Privacy: usernames are dropped at read time and replaced with a salted
hash, so correlated-source detection stays possible without the project
ever storing a name.

Usage:
    python3 parse_md.py --src ../data/raw/reddit_pages --outdir ../data/out
"""

import argparse
import csv
import glob
import hashlib
import os
import re
import sys
import urllib.parse

PERMA = re.compile(
    r"\[([^\]]*)\]\(https://www\.reddit\.com/r/([^/]+)/comments/([^/]+)/comment/([^/)]+)")
USER = re.compile(r"^\[([A-Za-z0-9_\-]+)\]\(https://www\.reddit\.com/user/[^)]+\)\s*$")
NOISE = re.compile(
    r"^(Reply Share|Share|Reply|More replies|\[More replies\]|Collapse|"
    r"Join the conversation.*|Sort by:|Best|Open comment sort options|"
    r"-\s*$|\[!\[|\[\]\(|!\[|##? |Vote|Award|Report|Follow|Add a Comment|"
    r"Top|New|Controversial|Old|Q&A|Embedded Content|Related Answers|"
    r"More posts you may like|Public|Anyone can view|Created|Members|Online|"
    r"Get a Quote|ad\.doubleclick|Promoted|Advertisement|Repost|Go to |"
    r"Skip to main|Reddit and its partners|Password|Email|Continue|Log In|"
    r"Sign Up)")

FIELDS = ["thread_key", "post_id", "subreddit", "record_type", "title",
          "flair", "post_author", "post_age", "post_body", "num_comments",
          "comment_id", "comment_author", "comment_age", "comment_body",
          "edited", "status", "source_file"]


def anon(u, salt):
    """Stable pseudonym. The username itself is never written out."""
    if not u or u in ("[deleted]", ""):
        return ""
    return "a_" + hashlib.sha256((salt + u.lower()).encode()).hexdigest()[:12]


def clean(lines):
    """Join body lines, dropping Reddit chrome and ad blocks."""
    out = []
    for ln in lines:
        s = ln.strip()
        s = re.sub(r"^\\-\s*", "- ", s)      # unescape list markers
        if s and not NOISE.match(s) and not USER.match(s):
            out.append(s)
    return " ".join(out).strip()


def parse(path, salt):
    raw = open(path, encoding="utf-8", errors="replace").read()
    lines = raw.split("\n")
    title = next((l[2:].strip() for l in lines[1:8] if l.startswith("# ")), "")

    sub = ""
    mm = re.search(r"reddit\.com/r/([^/]+)/comments/", raw)
    if mm:
        sub = mm.group(1)

    # every comment has exactly one permalink line
    idx = [i for i, l in enumerate(lines) if PERMA.search(l)]

    # everything above the comments header belongs to the post
    head = len(lines)
    for i, l in enumerate(lines):
        if l.strip() == "# Comments Section":
            head = i
            break

    post_id = ""
    m0 = re.search(r"reddit\.com/r/[^/]+/comments/([^/]+)/", raw)
    if m0:
        post_id = m0.group(1)

    post_author = ""
    for i in range(min(head, len(lines))):
        u = USER.match(lines[i].strip())
        if u:
            post_author = u.group(1)
            break

    post_age = ""
    ma = re.search(r"•\s*(\d+\s*(?:y|mo|w|d|h|m))\s*ago", "\n".join(lines[:head]))
    if ma:
        post_age = ma.group(1)

    flair = ""
    mf = re.search(r"\?f=flair_name%3A%22([^%\"]+)", raw)
    if mf:
        flair = urllib.parse.unquote(mf.group(1).replace("%20", " "))

    tstart = next((i for i, l in enumerate(lines[:head])
                   if l.startswith("# ") and l[2:].strip() == title), 0)
    post_body = clean(lines[tstart + 1:head])

    rows = [{**{k: "" for k in FIELDS},
             "thread_key": post_id, "post_id": post_id, "subreddit": sub,
             "record_type": "post", "title": title, "flair": flair,
             "post_author": anon(post_author, salt), "post_age": post_age,
             "post_body": post_body, "source_file": os.path.basename(path)}]

    for n, i in enumerate(idx):
        m = PERMA.search(lines[i])
        age, sub2, pid, cid = m.groups()

        # author is the nearest user link above the permalink
        author = ""
        for k in range(i - 1, max(i - 6, -1), -1):
            u = USER.match(lines[k].strip())
            if u:
                author = u.group(1)
                break

        stop = idx[n + 1] if n + 1 < len(idx) else len(lines)
        body_lines = []
        for k in range(i + 1, stop):
            if lines[k].strip() == "## Embedded Content":
                break
            body_lines.append(lines[k])

        body = clean(body_lines)
        if not body:
            continue

        rows.append({**{k: "" for k in FIELDS},
                     "thread_key": pid, "post_id": pid, "subreddit": sub2,
                     "record_type": "comment", "title": title, "flair": flair,
                     "comment_id": cid, "comment_author": anon(author, salt),
                     "comment_age": age.replace(" ago", "").strip(),
                     "comment_body": body,
                     "source_file": os.path.basename(path)})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="../data/raw/reddit_pages")
    ap.add_argument("--outdir", default="../data/out")
    ap.add_argument("--salt", default="ontarioroadtestmap")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.src, "**", "*.md"),
                             recursive=True))
    if not files:
        raise SystemExit(f"no .md files under {args.src}")

    allrows = []
    for f in files:
        allrows.extend(parse(f, args.salt))

    seen, uniq = set(), []
    for r in allrows:
        key = r["comment_id"] or ("post:" + r["post_id"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)

    os.makedirs(args.outdir, exist_ok=True)
    out = os.path.join(args.outdir, "raw_comments.csv")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(uniq)

    posts = sum(1 for r in uniq if r["record_type"] == "post")
    authors = {r["comment_author"] or r["post_author"] for r in uniq} - {""}
    print(f"files:     {len(files)}")
    print(f"posts:     {posts}")
    print(f"comments:  {len(uniq) - posts}")
    print(f"authors:   {len(authors)}")
    print(f"wrote {out}")


if __name__ == "__main__":
    sys.exit(main())