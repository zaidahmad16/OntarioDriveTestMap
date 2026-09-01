#!/usr/bin/env python3
"""
apply_corrections.py — mark disputed segments on snapped traces.

A correction never rewrites a trace. Silently editing source data would
destroy the thing this project is for: knowing how well supported each
segment is. A correction marks the dispute so downstream steps can
decide, and so the disagreement stays in the record.

Usage:
    python3 apply_corrections.py ../data/out/snapped.json \\
        --corrections ../data/corrections.json
"""

import argparse
import json
import sys


def matches(trace, corr):
    t = corr["target"]
    if t.get("source_id") and t["source_id"] != trace.get("source_id"):
        return False
    if t.get("centre_id") and t["centre_id"] not in str(trace.get("centre_id", "")):
        return False
    return True


def find_segment(turns, disputed):
    """Index range in `turns` matching the disputed sequence, or None.

    Matches on street only. Direction can differ between accounts of the
    same physical route — one person's right is another's approach from
    the other side — and the segment identity is the streets.
    """
    want = [d["street"] for d in disputed]
    have = [t["street"] for t in turns]
    n = len(want)
    for i in range(len(have) - n + 1):
        if have[i:i + n] == want:
            return (i, i + n)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snapped")
    ap.add_argument("--corrections", default="../data/corrections.json")
    ap.add_argument("--out")
    args = ap.parse_args()

    traces = json.load(open(args.snapped))
    corrs = json.load(open(args.corrections))["corrections"]

    applied = 0
    for c in corrs:
        if c.get("status") == "rejected":
            continue
        for t in traces:
            if not matches(t, c):
                continue
            span = find_segment(t.get("turns", []),
                                c["disputed_segment"]["turns"])
            t.setdefault("disputes", []).append({
                "correction_id": c["id"],
                "status": c["status"],
                "turn_span": span,
                "claim": c["disputed_segment"]["claim"],
                "asserted_instead": c["asserted_instead"]["claim"],
                "reporter": c["reporter"]["kind"],
            })
            applied += 1
            print(f"  {c['id']}  {t['source_id']}  "
                  f"turns {span if span else 'NOT FOUND'}  [{c['status']}]")

    print(f"\n  {applied} correction(s) applied to {len(traces)} traces")
    disputed = [t for t in traces if t.get("disputes")]
    if disputed:
        print(f"  traces carrying a dispute: {len(disputed)}")
        print("  These are still routed and still present. A dispute is a")
        print("  flag for the consensus step to weigh, not a deletion.")

    out = args.out or args.snapped
    json.dump(traces, open(out, "w"), indent=1)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())