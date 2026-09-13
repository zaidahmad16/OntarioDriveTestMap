#!/usr/bin/env python3
"""
connect_centre_endpoints.py — draw the route_lines segment consensus_geometry.py
throws away: the connection to the DriveTest centre itself.

DESTINATION: analysis/connect_centre_endpoints.py

Every real test route starts and ends at the centre by definition, and
traces regularly say so explicitly -- "...then Shefford, into the test
centre" -- but trace_segments() in consensus_geometry.py hard-skips any
turn starting with "@" (or the other CENTRE_WORDS snap_traces.py already
recognizes), because its junction() lookup only knows how to resolve
STREET x STREET pairs, not STREET x centre. That filter is correct for
its own purpose (a centre is not a street-graph node), but its side
effect is that this specific, real, often-repeated piece of evidence
gets silently discarded everywhere in the pipeline -- not something
this session introduced, a pre-existing gap like the single-edge-orphan
one, just never surfaced before.

This is NOT a predicted/inferred connection like predict_family_bridges.py
-- a trace saying "Shefford, then into the centre" is exactly as real a
piece of evidence as "Shefford x Casey", it just names an endpoint this
pipeline's graph model has no representation for. So this script gives
it one: it finds the REAL centre coordinate (data/osm.db's own centres
table -- an actual surveyed building footprint, not a guess), finds
which of that family's ALREADY-real, already-drawn junction points sits
nearest the centre on the SAME named street a trace actually described
connecting to it, and draws a real, OSRM-snapped road segment between
them. Multiple traces naming the same street add up as authors/weight
exactly like any other segment; a single trace saying it still counts,
same as consensus_geometry.py's own below-threshold segments do.

Safety boundary, same posture as the rest of this session's work: only
ever anchors to a street that ALREADY appears in that specific family's
own real segment set (never invents a location), and only for real,
trusted (min_family) clusters -- never unclustered traces.

Usage:
    python3 connect_centre_endpoints.py --dry-run
    python3 connect_centre_endpoints.py            # writes to Postgres
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

CENTRE_WORDS = ("@centre", "test centre", "test center", "testcentre",
                "drivetest", "drive test", "the centre", "the center",
                "test site")

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


def centre_connections(traces):
    """For each trace, find every real (street, centre) adjacency in its
    RAW turn sequence (not the filtered trace_segments -- that's exactly
    what drops these). Returns {street_key: {"authors": {...}}}."""
    out = {}
    for t in traces:
        who = t.get("author_hash") or t["source_id"]
        sw = cg.source_weight(t) * cg.age_weight(t.get("observed_at"))
        streets = [x["street"] for x in t.get("turns", [])]
        for i, s in enumerate(streets):
            if not any(w in s.lower() for w in CENTRE_WORDS):
                continue
            for j in (i - 1, i + 1):
                if 0 <= j < len(streets):
                    neighbor = streets[j]
                    if any(w in neighbor.lower() for w in CENTRE_WORDS) or neighbor.startswith("@"):
                        continue
                    k = cg.key(neighbor)
                    out.setdefault(k, {"authors": {}, "raw": neighbor})
                    out[k]["authors"][who] = max(out[k]["authors"].get(who, 0), sw)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "osm.db"))
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--min-family", type=int, default=3)
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
    # centre coordinates: read straight from osm.db (same source used to
    # build the frontend's marker), not from Postgres -- Postgres's
    # centres table has no lat/lon column.
    import sqlite3
    ocon = sqlite3.connect(args.db)
    ocon.row_factory = sqlite3.Row
    centre_coords = {r["centre_id"]: (r["lat"], r["lon"])
                      for r in ocon.execute("SELECT centre_id, lat, lon FROM centres")}

    total = 0
    for centre_id in CENTRE_TRACE_FILES:
        if centre_id not in centre_coords:
            continue
        centre_pt = centre_coords[centre_id]

        T = load_traces(centre_id, g)
        fam = cg.families(T, g, 2)
        real = {k: v for k, v in fam.items()
                if k >= 0 and len(v) >= args.min_family}

        for fid in sorted(real):
            traces = real[fid]
            seg = cg.support(traces, g)
            test_class, mixed_classes, _ = cg.class_vote(
                t.get("test_class") for t in traces)

            conns = centre_connections(traces)
            if not conns:
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

            i = 0
            for street_key, info in conns.items():
                # anchor to the nearest real point in THIS family's own
                # segments that touches the named street -- never a
                # location this family's evidence didn't already use.
                candidates = [v["node"] for k, v in seg.items() if street_key in k]
                if not candidates:
                    print(f"  SKIP {centre_id} family {fid}: '{info['raw']}' -> centre, "
                          f"but no existing segment in this family touches that street")
                    continue
                anchor = min(candidates,
                             key=lambda n: haversine((n["lat"], n["lon"]), centre_pt))

                weight = round(sum(info["authors"].values()), 2)
                authors = len(info["authors"])
                below_threshold = weight < args.threshold

                pts = [{"lat": anchor["lat"], "lon": anchor["lon"]},
                       {"lat": centre_pt[0], "lon": centre_pt[1]}]
                geom, dist = None, None
                if not args.dry_run:
                    try:
                        r = cg.osrm_route(pts, args.pause)
                        if r.get("code") == "Ok":
                            geom = r["routes"][0]["geometry"]
                            dist = r["routes"][0]["distance"]
                    except Exception as e:
                        print(f"     centre-link {i}: {str(e)[:80]}")
                if geom is None:
                    geom = {"type": "LineString",
                            "coordinates": [[p["lon"], p["lat"]] for p in pts]}

                run = run_start + i
                conf = "below-threshold" if below_threshold else "confirmed"
                print(f"  CENTRE-LINK {centre_id} family {fid} run {run}: "
                      f"'{info['raw']}' <-> DriveTest centre, {authors} author(s), "
                      f"weight {weight} ({conf})")
                total += 1

                if not args.dry_run:
                    cur.execute(
                        """
                        INSERT INTO route_lines
                            (centre_id, family, run, trace_count, authors,
                             distance_m, geometry, test_class, mixed_classes,
                             below_threshold, predicted, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            centre_id, fid, run, len(traces), authors,
                            round(dist) if dist else None,
                            json.dumps(geom["coordinates"]),
                            test_class, mixed_classes, below_threshold, False,
                            "connect_centre_endpoints",
                        ),
                    )
                i += 1

    if args.dry_run:
        print(f"\n[dry run, nothing written] {total} centre connection(s) found")
    else:
        conn.commit()
        cur.close()
        conn.close()
        print(f"\nInserted {total} centre connection(s).")


if __name__ == "__main__":
    main()
