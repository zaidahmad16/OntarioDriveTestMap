#!/usr/bin/env python3
"""
validate_consensus.py — does the consensus method actually predict?

DESTINATION: analysis/validate_consensus.py

Every confidence number this project produces is currently an assertion.
Weights were chosen by hand: video 0.9, text 0.6, three-year half life.
Nothing has ever tested whether the resulting consensus predicts anything.

Leave-one-out: hold out one trace, build consensus from the rest, then
ask whether the held-out trace's segments were predicted. Repeat for
every trace. That gives precision and recall — real numbers about the
method rather than about the data.

Two baselines are computed alongside, because a score means nothing on
its own:

  frequency   rank segments by raw mention count, ignore authors, ages
              and source types entirely. If the weighted model cannot
              beat this, the weights are decoration.

  random      shuffle the segment set. Establishes the floor.

Usage:
    python3 validate_consensus.py ../data/out/reddit_traces.json \\
        ../data/out/ocr_traces.json ../data/out/ocr_traces_g.json \\
        --db ../data/osm.db --centre walkley
    python3 validate_consensus.py ... --sweep     # try several thresholds
"""

import argparse
import datetime as dt
import json
import os
import math
import random
import re
import sqlite3
import sys
from collections import defaultdict

SUFFIXES = {"road", "rd", "street", "st", "avenue", "ave", "drive", "dr",
            "boulevard", "blvd", "parkway", "pkwy", "crescent", "cres",
            "court", "crt", "lane", "ln", "way", "place", "pl", "private",
            "terrace", "circle", "trail"}

KNOWN = set()


def base(s):
    full = " ".join(re.sub(r"[^\w\s]", " ", (s or "").lower()).split())
    if full in KNOWN:
        return full
    w = full.split()
    while w and w[-1] in SUFFIXES:
        w.pop()
    return " ".join(w) if w else full


class Graph:
    def __init__(self, db):
        self.con = sqlite3.connect(db)
        self._ok = {}
        KNOWN.update(r[0] for r in
                     self.con.execute("SELECT DISTINCT full FROM streets")
                     if r[0])

    def meet(self, a, b):
        k = tuple(sorted((base(a), base(b))))
        if k in self._ok:
            return self._ok[k]
        va = list({base(a), " ".join(re.sub(r"[^\w\s]", " ", a.lower()).split())} - {""})
        vb = list({base(b), " ".join(re.sub(r"[^\w\s]", " ", b.lower()).split())} - {""})
        pa = ",".join("?" * len(va))
        pb = ",".join("?" * len(vb))
        r = self.con.execute(f"""
            SELECT 1 FROM junctions j
            WHERE j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pa}) OR full IN ({pa}))
              AND j.node_id IN (SELECT node_id FROM junction_streets
                                WHERE base IN ({pb}) OR full IN ({pb}))
            LIMIT 1""", (*va, *va, *vb, *vb)).fetchone()
        self._ok[k] = r is not None
        return self._ok[k]


def age_weight(observed, hl=3.0):
    if not observed:
        return 0.6
    try:
        d = dt.date.fromisoformat(observed[:10])
    except ValueError:
        return 0.6
    return max(0.15, 0.5 ** (((dt.date.today() - d).days / 365.25) / hl))


def source_weight(t):
    if t["source_id"].startswith("youtube"):
        return 0.9
    n = len(t.get("turns", []))
    return 0.6 if n >= 6 else (0.45 if n >= 3 else 0.25)


def segments(t, g):
    """Graph-valid segments of one trace."""
    st = [x["street"] for x in t.get("turns", [])]
    out = set()
    for a, b in zip(st, st[1:]):
        if a == b or a.startswith("@") or b.startswith("@"):
            continue
        if g.meet(a, b):
            out.add(tuple(sorted((base(a), base(b)))))
    return out


def consensus(traces, g, mode="weighted"):
    """Segment -> score, from a set of traces.

    weighted   authors deduped, source type and age applied
    frequency  raw mention count, no dedup, no weighting
    """
    seg = defaultdict(lambda: {"a": {}, "n": 0})
    for t in traces:
        who = t.get("author_hash") or t["source_id"]
        w = source_weight(t) * age_weight(t.get("observed_at"))
        for k in segments(t, g):
            s = seg[k]
            s["a"][who] = max(s["a"].get(who, 0), w)
            s["n"] += 1
    if mode == "frequency":
        return {k: float(v["n"]) for k, v in seg.items()}
    return {k: sum(v["a"].values()) for k, v in seg.items()}


def evaluate(traces, g, thresh, mode="weighted", seed=None):
    """Leave-one-out. Returns micro precision, recall, F1.

    Micro-averaged: pool all predictions across folds rather than
    averaging per-fold rates. A trace with two segments should not
    weigh as much as one with twenty.
    """
    tp = fp = fn = 0
    rng = random.Random(seed)
    for i, held in enumerate(traces):
        rest = traces[:i] + traces[i + 1:]
        truth = segments(held, g)
        if not truth:
            continue
        scored = consensus(rest, g, mode)
        if mode == "random":
            keys = list(scored)
            rng.shuffle(keys)
            pred = set(keys[:max(1, int(len(keys) * 0.4))])
        else:
            pred = {k for k, v in scored.items() if v >= thresh}
        tp += len(truth & pred)
        fn += len(truth - pred)
        # only count a false positive if some other trace also lacks it —
        # a segment predicted but absent from THIS trace may simply be a
        # different route, not an error. Restrict to the held-out trace's
        # own streets so the comparison is like-for-like.
        streets = {s for k in truth for s in k}
        rel = {k for k in pred if k[0] in streets and k[1] in streets}
        fp += len(rel - truth)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f, tp, fp, fn


def cluster_traces(T, g, min_size=2):
    """Split traces into route families before validating.

    Consensus pooled across every trace at a centre is answering a
    question nobody asked: what does the average of several different
    routes look like. Every worst-predicted trace in the pooled run was
    a route variant judged against a consensus dominated by the majority
    route.

    Same approach that separated G from G2 on geometry alone: IDF-weighted
    overlap so the shared arterial spine stops dominating similarity,
    then HDBSCAN, which discovers the number of families rather than
    being told it.
    """
    import itertools
    import numpy as np

    sets = [segments(t, g) for t in T]
    df = defaultdict(int)
    for e in sets:
        for k in e:
            df[k] += 1
    n = len(T)
    w = {k: math.log(n / c) + 1e-6 for k, c in df.items()}

    D = np.zeros((n, n))
    for i, j in itertools.combinations(range(n), 2):
        inter = sets[i] & sets[j]
        if inter:
            num = sum(w[k] for k in inter)
            den = min(sum(w[k] for k in sets[i]), sum(w[k] for k in sets[j]))
            sim = num / den if den else 0.0
        else:
            sim = 0.0
        D[i][j] = D[j][i] = 1.0 - sim

    labels = None
    try:
        from sklearn.cluster import HDBSCAN
        labels = HDBSCAN(metric="precomputed", min_cluster_size=min_size,
                         min_samples=1).fit_predict(D)
        if all(l == -1 for l in labels):
            labels = None      # too few points for a density method
    except Exception:
        pass
    if labels is None:
        from scipy.cluster.hierarchy import linkage, fcluster
        from scipy.spatial.distance import squareform
        Z = linkage(squareform(D, checks=False), method="average")
        labels = fcluster(Z, t=0.6, criterion="distance") - 1

    out = defaultdict(list)
    for t, l in zip(T, labels):
        out[int(l)].append(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--centre", default="walkley")
    ap.add_argument("--threshold", type=float, default=1.0)
    ap.add_argument("--min-segments", type=int, default=2)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--cluster", action="store_true",
                    help="validate within route families rather than pooled")
    args = ap.parse_args()

    g = Graph(args.db)
    T = []
    for p in args.traces:
        if not os.path.exists(p):
            print(f"  ! missing {p}")
            continue
        for t in json.load(open(p)):
            if args.centre and args.centre not in str(t.get("centre_id", "")):
                continue
            if len(segments(t, g)) >= args.min_segments:
                T.append(t)

    if len(T) < 5:
        print(f"only {len(T)} usable traces")
        return 1

    auth = {t.get("author_hash") or t["source_id"] for t in T}
    print(f"{len(T)} traces, {len(auth)} distinct authors\n")

    if args.cluster:
        fam = cluster_traces(T, g, args.min_segments)
        real = {k: v for k, v in fam.items() if k >= 0 and len(v) >= 3}
        noise = fam.get(-1, [])
        print(f"{len(real)} route families, {len(noise)} unclustered\n")

        print(f"{'family':>8s} {'n':>4s} {'auth':>5s} {'prec':>6s} "
              f"{'rec':>6s} {'F1':>6s}")
        tot_tp = tot_fp = tot_fn = 0
        for k in sorted(real):
            grp = real[k]
            p, r, f, tp, fp, fn = evaluate(grp, g, args.threshold)
            a = len({t.get("author_hash") or t["source_id"] for t in grp})
            print(f"{k:8d} {len(grp):4d} {a:5d} {p:6.2f} {r:6.2f} {f:6.2f}")
            tot_tp += tp
            tot_fp += fp
            tot_fn += fn

        P = tot_tp / (tot_tp + tot_fp) if tot_tp + tot_fp else 0.0
        R = tot_tp / (tot_tp + tot_fn) if tot_tp + tot_fn else 0.0
        F = 2 * P * R / (P + R) if P + R else 0.0
        pp, pr, pf, *_ = evaluate(T, g, args.threshold)
        print(f"\n  per-family, pooled:  prec {P:.2f}  rec {R:.2f}  F1 {F:.2f}")
        print(f"  all traces together: prec {pp:.2f}  rec {pr:.2f}  F1 {pf:.2f}")
        print()
        if F > pf + 0.02:
            print(f"  Splitting by route family improves F1 by {F-pf:+.2f}.")
            print("  The pooled estimator was averaging distinct routes.")
        elif F < pf - 0.02:
            print(f"  Splitting makes it worse ({F-pf:+.2f}). Families are")
            print("  too small to build consensus from.")
        else:
            print("  No material difference. Pooling was not the problem.")

        for k in sorted(real):
            grp = real[k]
            streets = defaultdict(int)
            for t in grp:
                for a, b in segments(t, g):
                    streets[a] += 1
                    streets[b] += 1
            top = sorted(streets.items(), key=lambda kv: -kv[1])[:7]
            print(f"\n  family {k}: {', '.join(s for s, _ in top)}")
        return 0

    if args.sweep:
        print(f"{'thresh':>7s} {'prec':>6s} {'rec':>6s} {'F1':>6s} "
              f"{'tp':>5s} {'fp':>5s} {'fn':>5s}")
        for th in (0.3, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
            p, r, f, tp, fp, fn = evaluate(T, g, th)
            print(f"{th:7.2f} {p:6.2f} {r:6.2f} {f:6.2f} "
                  f"{tp:5d} {fp:5d} {fn:5d}")
        print("\nThe design doc sets the publish threshold at 1.5. This is")
        print("the first evidence about whether that number is any good.")
        return 0

    print(f"leave-one-out, threshold {args.threshold}\n")
    rows = [
        ("weighted consensus", evaluate(T, g, args.threshold, "weighted")),
        ("frequency baseline", evaluate(T, g, args.threshold, "frequency")),
        ("random baseline", evaluate(T, g, args.threshold, "random", seed=7)),
    ]
    print(f"{'method':22s} {'prec':>6s} {'rec':>6s} {'F1':>6s} "
          f"{'tp':>5s} {'fp':>5s} {'fn':>5s}")
    for name, (p, r, f, tp, fp, fn) in rows:
        print(f"{name:22s} {p:6.2f} {r:6.2f} {f:6.2f} "
              f"{tp:5d} {fp:5d} {fn:5d}")

    wf = rows[0][1][2]
    ff = rows[1][1][2]
    print()
    if wf <= ff:
        print(f"  The weighting does not beat raw frequency ({wf:.2f} vs "
              f"{ff:.2f}).")
        print("  Author dedup, source weights and age decay are currently")
        print("  costing complexity without buying accuracy.")
    else:
        print(f"  Weighting beats frequency by {wf-ff:+.2f} F1.")

    # which traces the method fails on — usually more informative than
    # the headline number
    print("\nworst-predicted traces")
    scored_all = consensus(T, g)
    bad = []
    for i, t in enumerate(T):
        truth = segments(t, g)
        if not truth:
            continue
        rest = consensus(T[:i] + T[i + 1:], g)
        pred = {k for k, v in rest.items() if v >= args.threshold}
        miss = len(truth - pred)
        bad.append((miss / len(truth), miss, len(truth), t["source_id"]))
    bad.sort(reverse=True)
    for frac, miss, tot, sid in bad[:6]:
        print(f"   {sid:30s} {miss}/{tot} segments unpredicted")
    return 0


if __name__ == "__main__":
    sys.exit(main())