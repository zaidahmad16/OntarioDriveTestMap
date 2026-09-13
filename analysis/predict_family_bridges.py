#!/usr/bin/env python3
"""
predict_family_bridges.py — road-snap the gaps BETWEEN a family's own
real, disconnected evidence clusters.

DESTINATION: analysis/predict_family_bridges.py

This is a different, riskier operation than recover_low_confidence_routes.py
and deliberately kept separate rather than folded into it -- that script
only ever draws a line where a real trace directly walked from street A
to street B (an actual recorded edge, just below the publish-confidence
threshold). This script does something categorically different: it
predicts the real road path between two points where NO trace recorded
that specific connection, based on the inference that both points
already belong to the same real, trace-clustered family (the same test
route), so a real road almost certainly connects them even though no
single source described that exact stretch.

This is explicit product direction, not a default: connecting arbitrary
points invented a route through buildings once already this project
(see Build Log, reverted commit 63fbec3). The difference here is the
two points being bridged are never invented -- both ends of every
bridge are independently real, confirmed evidence (a scored junction
some trace actually named), and both are already known via
consensus_geometry.py's own trace-level clustering to be part of the
SAME family. This never bridges across two different families, and it
never bridges a point that has no trace evidence behind it at all.

Safety rules, all enforced in code below, not just described here:
  1. Only connects components WITHIN one family (never across families,
     never touching unclustered/-1 traces) -- crossing that line is
     exactly the "welded two unrelated routes together" failure
     snap_traces.py's own docstring documents as a real past incident.
  2. Minimum number of bridges only (a minimum spanning tree over each
     family's disconnected components) -- predicts exactly enough real
     road connections to make the family's own evidence into one shape,
     nothing extra.
  3. Distance-capped (default 2500m -- raised from an initial 1500m
     after the project owner reviewed a specific real case: Canotek
     family 4's `blair x ogilvie` is real, trusted evidence (3 traces)
     but sits ~2.2km from the rest of its own family's cluster. Checked
     the full impact before raising it: going to 2500m adds exactly
     that one bridge and changes nothing else anywhere, in either
     centre -- not a blanket loosening, a specific approved exception
     that happened to want a round-number cap): refuses to bridge two points
     further apart than a single test-route gap plausibly spans. A
     component pair with no candidate under the cap is left unbridged
     and reported, not forced.
  4. Every bridge is marked predicted=true in the database and MUST
     render as visually distinct from confirmed data everywhere it
     appears -- see MapView.jsx's dedicated style for this flag. This
     is a non-negotiable condition of building this feature at all, not
     a nice-to-have.

Usage:
    python3 predict_family_bridges.py --dry-run
    python3 predict_family_bridges.py            # writes to Postgres
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import consensus_geometry as cg

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")

CENTRE_TRACE_FILES = {
    "canotek": ["reddit_traces.json", "ocr_traces.json"],
    "walkley": ["reddit_traces.json", "ocr_traces.json", "ocr_traces_g.json"],
    "smithsfalls": ["reddit_traces.json"],
}

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "out")


def haversine(a, b):
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def load_traces(centre_id, g):
    T = []
    for fname in CENTRE_TRACE_FILES.get(centre_id, []):
        path = os.path.join(DATA_DIR, centre_id, fname)
        if not os.path.exists(path):
            continue
        for t in json.load(open(path)):
            if centre_id not in str(t.get("centre_id", "")):
                continue
            if len(cg.trace_segments(t, g)) >= 2:
                T.append(t)
    return T


def drawn_components(seg, threshold):
    """What's ACTUALLY visible right now, as groups -- not just what's
    topologically related by shared street name.

    A segment sharing a street name with another is not automatically
    connected on the map: order_walk()'s greedy walk only follows one
    unvisited edge at a time, so a real branch off the main spine is
    correctly emitted as its own separate run (by design -- see
    order_walk's own docstring), and a run of exactly ONE edge can never
    become a line at all (a LineString needs 2+ points; one edge
    contributes exactly one junction coordinate). That case was being
    silently dropped everywhere in this pipeline, including the
    original, most-trusted consensus_geometry.py output -- a fully
    confirmed, above-threshold single-edge branch was just as invisible
    as an uncorroborated one. This finds every such gap, from BOTH the
    official (keep) and below-threshold tiers together, so every real
    segment either ends up in a drawn run or is correctly identified as
    needing a bridge -- nothing is silently missing.
    """
    keep = [k for k, v in seg.items() if sum(v["w"].values()) >= threshold]
    below = [k for k, v in seg.items() if sum(v["w"].values()) < threshold]

    drawn_groups = []
    covered = set()
    for edge_set in (keep, below):
        for run in cg.order_walk(seg, edge_set):
            if len(run) >= 2:
                drawn_groups.append(list(run))
                covered.update(run)

    # every real segment not part of any drawn run is its own singleton
    # "component" -- the only way it can ever become visible is a bridge.
    for k in seg:
        if k not in covered:
            drawn_groups.append([k])

    return drawn_groups


def nearest_pair(seg, comp_a, comp_b):
    best = None
    for ka in comp_a:
        na = seg[ka]["node"]
        for kb in comp_b:
            nb = seg[kb]["node"]
            d = haversine((na["lat"], na["lon"]), (nb["lat"], nb["lon"]))
            if best is None or d < best[0]:
                best = (d, na, nb)
    return best


def mst_bridges(seg, components, max_distance):
    """Minimum spanning tree over components, edge weight = nearest real
    distance between any two of their points. Refuses any edge over
    max_distance -- if that leaves components unmerged, they stay
    unmerged and get reported, not forced."""
    n = len(components)
    if n <= 1:
        return [], []

    edges = []
    for i in range(n):
        for j in range(i + 1, n):
            d, na, nb = nearest_pair(seg, components[i], components[j])
            edges.append((d, i, j, na, nb))
    edges.sort(key=lambda e: e[0])

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    bridges, skipped = [], []
    for d, i, j, na, nb in edges:
        ri, rj = find(i), find(j)
        if ri == rj:
            continue
        if d > max_distance:
            skipped.append((d, i, j))
            continue
        parent[ri] = rj
        bridges.append((d, na, nb))
    return bridges, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "osm.db"))
    ap.add_argument("--min-family", type=int, default=3)
    ap.add_argument("--threshold", type=float, default=0.5,
                     help="must match the threshold used elsewhere -- this "
                          "is the line between 'official' and 'below "
                          "threshold', not this script's concern to redefine")
    ap.add_argument("--max-bridge-m", type=float, default=2500.0,
                     help="refuse to bridge two components farther apart "
                          "than this -- see module docstring, rule 3")
    ap.add_argument("--pause", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and not DATABASE_URL:
        sys.exit("Set DATABASE_PUBLIC_URL (or DATABASE_URL) before running this.")

    cg.load_known(args.db)
    g = cg.Graph(args.db)

    conn = cur = None
    if not args.dry_run:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=RealDictCursor)

    total_bridges = 0
    for centre_id in CENTRE_TRACE_FILES:
        T = load_traces(centre_id, g)
        fam = cg.families(T, g, 2)
        real = {k: v for k, v in fam.items()
                if k >= 0 and len(v) >= args.min_family}

        for fid in sorted(real):
            traces = real[fid]
            seg = cg.support(traces, g)
            authors = len({t.get("author_hash") or t["source_id"] for t in traces})
            test_class, mixed_classes, _ = cg.class_vote(
                t.get("test_class") for t in traces)

            components = drawn_components(seg, args.threshold)
            if len(components) <= 1:
                continue

            bridges, skipped = mst_bridges(seg, components, args.max_bridge_m)
            if not bridges:
                continue

            if not args.dry_run:
                cur.execute(
                    "SELECT COALESCE(MAX(run), -1) AS m FROM route_lines "
                    "WHERE centre_id = %s AND family = %s",
                    (centre_id, fid),
                )
                run_start = cur.fetchone()["m"] + 1
            else:
                run_start = 0

            for i, (dist, na, nb) in enumerate(bridges):
                pts = [{"lat": na["lat"], "lon": na["lon"]},
                       {"lat": nb["lat"], "lon": nb["lon"]}]
                geom, real_dist = None, None
                if not args.dry_run:
                    try:
                        r = cg.osrm_route(pts, args.pause)
                        if r.get("code") == "Ok":
                            geom = r["routes"][0]["geometry"]
                            real_dist = r["routes"][0]["distance"]
                    except Exception as e:
                        print(f"     bridge {i}: {str(e)[:80]}")
                if geom is None:
                    geom = {"type": "LineString",
                            "coordinates": [[p["lon"], p["lat"]] for p in pts]}

                run = run_start + i
                print(f"  PREDICT {centre_id} family {fid} run {run}: "
                      f"bridge {dist:.0f}m (straight-line) between two real "
                      f"components")
                total_bridges += 1

                if not args.dry_run:
                    cur.execute(
                        """
                        INSERT INTO route_lines
                            (centre_id, family, run, trace_count, authors,
                             distance_m, geometry, test_class, mixed_classes,
                             below_threshold, predicted)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            centre_id, fid, run, len(traces), authors,
                            round(real_dist) if real_dist else round(dist),
                            json.dumps(geom["coordinates"]),
                            test_class, mixed_classes, False, True,
                        ),
                    )

            for d, i, j in skipped:
                print(f"  SKIP {centre_id} family {fid}: components of size "
                      f"{len(components[i])}/{len(components[j])} are {d:.0f}m "
                      f"apart, over the {args.max_bridge_m:.0f}m cap -- left "
                      f"unbridged")

    if args.dry_run:
        print(f"\n[dry run, nothing written] {total_bridges} predicted bridge(s)")
    else:
        conn.commit()
        cur.close()
        conn.close()
        print(f"\nInserted {total_bridges} predicted bridge(s).")


if __name__ == "__main__":
    main()
