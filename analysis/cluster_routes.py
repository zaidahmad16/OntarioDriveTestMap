#!/usr/bin/env python3
"""
cluster_routes.py — separate traces into distinct routes.

DESTINATION: analysis/cluster_routes.py

The problem this solves: every Walkley trace runs along Walkley Road for
most of its length and differs only in which residential loop it takes.
A plain overlap coefficient therefore scores any two traces at 0.6+
before looking at what actually distinguishes them, and HDBSCAN collapses
everything into one cluster.

Fix: weight each segment by inverse document frequency. A segment present
in every trace carries almost no information; one present in three
carries a lot. Walkley stops dominating without being discarded.

Representation: each trace becomes a set of unordered street pairs — the
edges it traverses. Finer than a bag of street names (order matters),
coarser than OSM way ids (works on unsnapped traces too, so partials and
failures still cluster).

Usage:
    python3 cluster_routes.py ../data/out/reddit_traces.json \\
        ../data/out/ocr_traces.json --class G2 --centre walkley
    python3 cluster_routes.py ... --no-idf     # show the naive result
"""

import argparse
import itertools
import json
import math
import os
import sys
from collections import Counter, defaultdict


def edges(trace):
    """Ordered turns -> set of unordered street pairs actually traversed."""
    st = [t["street"] for t in trace.get("turns", [])]
    out = set()
    for a, b in zip(st, st[1:]):
        if a != b and not a.startswith("@") and not b.startswith("@"):
            out.add(tuple(sorted((a, b))))
    return out


def idf(all_edges, n_traces):
    """Inverse document frequency per edge.

    An edge in every trace scores ~0. An edge in one scores high. This is
    the whole fix: it stops the shared arterial spine from dominating
    similarity between routes that are actually different.
    """
    df = Counter()
    for e in all_edges:
        df.update(e)
    return {e: math.log(n_traces / c) + 1e-6 for e, c in df.items()}


def similarity(a, b, w=None):
    """Weighted overlap coefficient.

    Overlap rather than Jaccard because traces are partial — a short
    trace fully contained in a long one should score high, and Jaccard
    punishes containment.
    """
    inter = a & b
    if not inter:
        return 0.0
    if w is None:
        return len(inter) / min(len(a), len(b))
    num = sum(w[e] for e in inter)
    den = min(sum(w[e] for e in a), sum(w[e] for e in b))
    return num / den if den else 0.0


def load(paths, cls=None, centre=None, min_edges=2):
    traces = []
    for p in paths:
        if not os.path.exists(p):
            print(f"  ! missing {p}")
            continue
        for t in json.load(open(p)):
            if cls and cls.lower() not in str(t.get("test_class", "")).lower():
                continue
            if centre and centre.lower() not in str(t.get("centre_id", "")).lower():
                continue
            e = edges(t)
            if len(e) < min_edges:
                continue
            t["_edges"] = e
            t["_src"] = "youtube" if t["source_id"].startswith("youtube") else "reddit"
            traces.append(t)
    return traces


def components(T, w, thresh=0.8):
    """Which loops exist, and which traces contain them.

    Clustering assumes traces fall into groups. These do not: every route
    is the same arterial spine plus some subset of residential loops, so
    two routes sharing a loop look similar even when they are different
    routes. Identifying the components and which combinations occur is a
    better fit for that structure than partitioning the traces.
    """
    from collections import defaultdict
    # a component is a set of edges that co-occur: present together in
    # the same traces, absent together from the others
    sig = defaultdict(set)
    for i, t in enumerate(T):
        for e in t["_edges"]:
            sig[e].add(i)
    groups = defaultdict(list)
    for e, members in sig.items():
        groups[frozenset(members)].append(e)
    out = []
    for members, es in groups.items():
        if len(members) == len(T):
            kind = "spine"          # in everything, carries no information
        elif len(members) == 1:
            kind = "unique"
        else:
            kind = "loop"
        out.append({"edges": sorted(es), "traces": sorted(members),
                    "kind": kind, "n": len(members)})
    out.sort(key=lambda c: -c["n"])
    return out


def cluster(D, min_size):
    """HDBSCAN on a precomputed distance matrix, with a fallback.

    Route count per centre is unknown, so a method that discovers k is
    required. HDBSCAN also labels outliers as noise, which is free
    bad-source detection.
    """
    try:
        from sklearn.cluster import HDBSCAN
        m = HDBSCAN(metric="precomputed", min_cluster_size=min_size,
                    min_samples=1, allow_single_cluster=False)
        return m.fit_predict(D), "sklearn HDBSCAN"
    except Exception:
        pass
    try:
        import hdbscan
        m = hdbscan.HDBSCAN(metric="precomputed", min_cluster_size=min_size,
                            min_samples=1)
        return m.fit_predict(D), "hdbscan"
    except Exception as e:
        print(f"  HDBSCAN unavailable ({e}); falling back to linkage")

    # Fallback: average-linkage at a fixed cut. Cruder, no noise label,
    # but it runs with no extra dependency and the structure is visible.
    import numpy as np
    from scipy.cluster.hierarchy import linkage, fcluster
    from scipy.spatial.distance import squareform
    Z = linkage(squareform(D, checks=False), method="average")
    return fcluster(Z, t=0.55, criterion="distance") - 1, "average linkage"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="+")
    ap.add_argument("--class", dest="cls", default=None)
    ap.add_argument("--centre", default=None)
    ap.add_argument("--min-edges", type=int, default=2)
    ap.add_argument("--min-size", type=int, default=2)
    ap.add_argument("--no-idf", action="store_true",
                    help="plain overlap coefficient, to show the difference")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    T = load(args.traces, args.cls, args.centre, args.min_edges)
    if len(T) < 3:
        print(f"only {len(T)} traces — not enough to cluster")
        return 1

    print(f"{len(T)} traces "
          f"({sum(1 for t in T if t['_src']=='reddit')} reddit, "
          f"{sum(1 for t in T if t['_src']=='youtube')} youtube)\n")

    w = None if args.no_idf else idf([t["_edges"] for t in T], len(T))

    if w:
        print("segment weights (low = shared by everything, high = distinguishing)")
        for e, v in sorted(w.items(), key=lambda kv: kv[1])[:6]:
            print(f"   {v:5.2f}  {e[0]} × {e[1]}")
        print("   ...")
        for e, v in sorted(w.items(), key=lambda kv: -kv[1])[:4]:
            print(f"   {v:5.2f}  {e[0]} × {e[1]}")
        print()

    import numpy as np
    n = len(T)
    D = np.zeros((n, n))
    for i, j in itertools.combinations(range(n), 2):
        s = similarity(T[i]["_edges"], T[j]["_edges"], w)
        D[i][j] = D[j][i] = 1.0 - s

    off = D[D > 0]
    print(f"distances: min {off.min():.2f}  median "
          f"{float(np.median(off)):.2f}  max {D.max():.2f}\n")

    labels, how = cluster(D, args.min_size)
    # HDBSCAN needs density. At n<20 it routinely labels everything noise,
    # which is a property of the sample size, not of the data.
    if all(l == -1 for l in labels):
        print(f"{how} put every trace in noise — too few points for a "
              f"density method. Falling back.")
        import numpy as _np
        from scipy.cluster.hierarchy import linkage, fcluster
        from scipy.spatial.distance import squareform
        Z = linkage(squareform(D, checks=False), method="average")
        labels = fcluster(Z, t=0.6, criterion="distance") - 1
        how = "average linkage, cut 0.6"
    print(f"clustering: {how}")

    groups = defaultdict(list)
    for t, l in zip(T, labels):
        groups[int(l)].append(t)

    real = {k: v for k, v in groups.items() if k >= 0}
    noise = groups.get(-1, [])
    print(f"clusters found: {len(real)}   noise: {len(noise)}\n")

    for k in sorted(real):
        g = real[k]
        srcs = Counter(t["_src"] for t in g)
        authors = {t.get("author_hash", "") for t in g} - {""}
        # the segments this cluster has that the others do not
        mine = set.union(*[t["_edges"] for t in g])
        others = set()
        for k2, g2 in real.items():
            if k2 != k:
                others |= set.union(*[t["_edges"] for t in g2])
        distinct = mine - others

        print(f"── cluster {k}   {len(g)} traces "
              f"({srcs['reddit']}r/{srcs['youtube']}y, "
              f"{len(authors)} distinct authors)")
        for t in sorted(g, key=lambda x: -len(x["_edges"])):
            seq = " → ".join(x["street"] for x in t["turns"])
            print(f"     {t['source_id']:28s} {seq[:78]}")
        if distinct:
            print(f"     unique to this cluster: "
                  f"{', '.join(f'{a}×{b}' for a, b in sorted(distinct))}")
        print()

    if noise:
        print("── noise (no cluster)")
        for t in noise:
            print(f"     {t['source_id']:28s} "
                  f"{len(t['_edges'])} edges")
        print()

    # independence check: a cluster carried by one author is not
    # corroboration, however many traces it contains
    print("independence")
    for k in sorted(real):
        a = {t.get("author_hash", "") for t in real[k]} - {""}
        flag = "  <-- single source, not corroborated" if len(a) < 2 else ""
        print(f"   cluster {k}: {len(a)} distinct author(s){flag}")

    print("\ncomponents  (what the routes are actually built from)")
    for c in components(T, w):
        if c["kind"] == "spine":
            tag = "in every trace — carries no route information"
        elif c["kind"] == "unique":
            tag = f"only {T[c['traces'][0]]['source_id']}"
        else:
            tag = f"{c['n']}/{len(T)} traces"
        es = ", ".join(f"{a}×{b}" for a, b in c["edges"][:4])
        more = f" +{len(c['edges'])-4}" if len(c["edges"]) > 4 else ""
        print(f"   [{c['kind']:6s}] {tag}")
        print(f"            {es}{more}")

    if args.out:
        json.dump({str(k): [t["source_id"] for t in v]
                   for k, v in groups.items()}, open(args.out, "w"), indent=1)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())