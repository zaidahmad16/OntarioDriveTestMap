#!/usr/bin/env python3
"""
rebuild_routes.py — regenerate route_lines so each (centre, test_class)
shows the RIGHT number of routes, class-coherent, each as ONE connected
route with its own turn-by-turn.

DESTINATION: rebuild_routes.py (repo root)

Why this exists: the old pipeline clustered routes on shared streets
alone (class-blind), so G and G2 routes that share arterials merged and
rendered as one; and it drew every family + every predicted fragment of a
class at once, which read as "this doesn't look like one route" and summed
a nonsense duration. Overlap-clustering also can't tell "3 distinct short
routes" from "1 long route seen in fragments" -- so the real route count
per (centre, class) is taken from ground truth (ROUTE_COUNTS), not guessed.

What it does, per (centre, class):
  * take that class's traces (confirmed G / G2), enriched with the
    ambiguous/unknown traces that best match, never mixing classes;
  * split into exactly ROUTE_COUNTS[(centre, class)] routes (default 1);
  * for each route, order its real junctions into one travel path
    (nearest-neighbour from the centre) and road-route the whole path
    through OSRM in one call -> one connected LineString + one turn-by-turn
    step list;
  * write ONE route_line (+ its steps) per route.

Safety:
  * --dry-run (default) writes a GeoJSON per centre to data/out/<c>/ for
    inspection and touches no database.
  * --apply writes to Postgres, but ONLY after dumping every route_lines /
    route_line_steps / route_line_segments row to a timestamped JSON backup
    first, and it runs in one transaction.
  * It never invents a street: every junction on every route is a real
    scored junction some trace named. Where OSRM can't connect two real
    junctions it returns a straight beeline; the frontend already renders
    those as dashed "unrouted gap", not confirmed road.

Usage:
    python3 rebuild_routes.py                    # dry run -> geojson only
    python3 rebuild_routes.py --centre walkley   # one centre, dry run
    python3 rebuild_routes.py --apply            # write to the live DB
"""
import argparse
import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis"))
import consensus_geometry as cg  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "osm.db")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "out")

CENTRE_TRACE_FILES = {
    "walkley": ["reddit_traces.json", "ocr_traces.json", "ocr_traces_g.json"],
    "canotek": ["reddit_traces.json", "ocr_traces.json"],
    "smithsfalls": ["reddit_traces.json"],
}

CENTRE_COORDS = {  # (lat, lon) of the DriveTest centre, from osm.db centres
    "walkley": (45.376145807017544, -75.64758859649123),
    "canotek": (45.4528876, -75.5883806),
    "smithsfalls": (44.8827581, -76.0150536),
}

# Ground-truth number of distinct routes per (centre, class). Default 1.
# Edit this as real knowledge firms up -- it's the one place route count
# lives, on purpose.
ROUTE_COUNTS = {
    ("walkley", "G2"): 3,   # three known residential G2 routes
    ("walkley", "G"): 1,    # one airport/highway G route
    ("canotek", "G"): 1,
    ("canotek", "G2"): 1,
    ("smithsfalls", "G2"): 1,
    ("smithsfalls", "G"): 1,
}


def osrm_trip(points, pause=0.3):
    """OSRM trip (TSP) service: shortest round trip visiting all points,
    starting at the first (the centre). Returns geometry + per-leg steps."""
    import time
    import urllib.parse
    import urllib.request
    coords = ";".join(f"{p['lon']},{p['lat']}" for p in points)
    q = urllib.parse.urlencode({"overview": "full", "geometries": "geojson",
                                "steps": "true", "source": "first",
                                "roundtrip": "true"})
    url = f"https://router.project-osrm.org/trip/v1/driving/{coords}?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": cg.UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        d = json.loads(r.read().decode())
    time.sleep(pause)
    return d


def load_traces(centre, g):
    T = []
    for fn in CENTRE_TRACE_FILES.get(centre, []):
        p = os.path.join(DATA_DIR, centre, fn)
        if not os.path.exists(p):
            continue
        for t in json.load(open(p)):
            if centre in str(t.get("centre_id", "")) and len(cg.trace_segments(t, g)) >= 2:
                T.append(t)
    return T


def routes_for_class(sub_traces, g, n_routes):
    """Split a class's traces into n_routes route-families. n_routes==1
    means one route = every trace. n_routes>1 uses the natural clusters,
    keeping the n_routes largest and folding the rest into the nearest."""
    if n_routes <= 1 or len(sub_traces) <= 1:
        return [list(sub_traces)]
    clustered = [v for k, v in cg.families(sub_traces, g, 2).items() if k >= 0]
    clustered.sort(key=len, reverse=True)
    if len(clustered) <= n_routes:
        return clustered
    keep = clustered[:n_routes]
    seg_of = [set().union(*[set(cg.trace_segments(t, g)) for t in fam]) for fam in keep]
    for fam in clustered[n_routes:]:
        fsegs = set().union(*[set(cg.trace_segments(t, g)) for t in fam])
        best = max(range(n_routes),
                   key=lambda i: len(fsegs & seg_of[i]))
        keep[best].extend(fam)
    return keep


def _endpoints(run_nodes):
    return run_nodes[0], run_nodes[-1]


def order_by_street_graph(seg, keep, centre):
    """Order junctions the way the streets actually connect, not by raw
    proximity. order_walk() follows street adjacency to build ordered
    runs (a nearest-neighbour walk over junctions zig-zags across a
    neighbourhood and inflates a 5 km loop to 50 km). Multiple runs (real
    branches) are then chained end-to-end starting from the run nearest
    the centre, flipping each so consecutive runs meet at their closest
    ends -- one continuous junction sequence for OSRM to road-route."""
    runs = cg.order_walk(seg, keep)
    run_nodes = []
    for run in runs:
        ns = []
        seen = set()
        for k in run:
            n = seg[k]["node"]
            if not n:
                continue
            key = (round(n["lat"], 6), round(n["lon"], 6))
            if key not in seen:
                seen.add(key)
                ns.append(n)
        if len(ns) >= 1:
            run_nodes.append(ns)
    if not run_nodes:
        return []

    def d(a, b):
        return cg.haversine((a["lat"], a["lon"]), (b["lat"], b["lon"]))

    # start with the run whose nearest endpoint is closest to the centre
    def near_centre(rn):
        a, b = _endpoints(rn)
        return min(cg.haversine(centre, (a["lat"], a["lon"])),
                   cg.haversine(centre, (b["lat"], b["lon"])))
    run_nodes.sort(key=near_centre)
    first = run_nodes.pop(0)
    a, b = _endpoints(first)
    if cg.haversine(centre, (a["lat"], a["lon"])) > cg.haversine(centre, (b["lat"], b["lon"])):
        first = list(reversed(first))
    ordered = list(first)

    while run_nodes:
        tail = ordered[-1]
        # pick the remaining run + orientation whose start is nearest the tail
        best, best_flip, best_d = None, False, None
        for rn in run_nodes:
            s, e = _endpoints(rn)
            ds, de = d(tail, s), d(tail, e)
            if best_d is None or min(ds, de) < best_d:
                best, best_flip, best_d = rn, de < ds, min(ds, de)
        run_nodes.remove(best)
        ordered.extend(reversed(best) if best_flip else best)
    return ordered


def build_one_route(traces, g, centre, threshold, pause, dry):
    """One family -> one connected route dict, or None if too little to draw."""
    seg = cg.support(traces, g)
    thin = len(traces) < 3
    keep = [k for k, v in seg.items() if thin or sum(v["w"].values()) >= threshold]
    if len(keep) < 2:
        keep = list(seg.keys())  # fall back to everything the traces named
    ordered = order_by_street_graph(seg, keep, centre)
    if len(ordered) < 2:
        return None

    # Put the centre first, then let OSRM's trip (TSP) service find the
    # shortest ROUND TRIP that visits every real junction and returns to the
    # centre -- a driving test is a loop from the centre, and solving the
    # visiting order this way turns a 57 km nearest-neighbour zig-zag into a
    # clean loop. Falls back to a plain in-order route if trip is unavailable.
    pts = [{"lat": centre[0], "lon": centre[1]}] + [{"lat": n["lat"], "lon": n["lon"]} for n in ordered]
    geom, dist, steps = None, None, []
    try:
        r = osrm_trip(pts, pause)
        if r.get("code") == "Ok" and r.get("trips"):
            geom = r["trips"][0]["geometry"]
            dist = r["trips"][0]["distance"]
            steps = cg.extract_steps({"routes": r["trips"]}, g)
        else:
            r = cg.osrm_route(pts, pause)
            if r.get("code") == "Ok":
                geom = r["routes"][0]["geometry"]
                dist = r["routes"][0]["distance"]
                steps = cg.extract_steps(r, g)
    except Exception as e:
        print(f"      OSRM error: {str(e)[:70]}")
    if geom is None:
        geom = {"type": "LineString", "coordinates": [[p["lon"], p["lat"]] for p in pts]}
    authors = len({t.get("author_hash") or t["source_id"] for t in traces})
    return {
        "coordinates": geom["coordinates"],
        "distance_m": round(dist) if dist else None,
        "steps": steps,
        "trace_count": len(traces),
        "authors": authors,
        "n_junctions": len(ordered),
    }


def rebuild(centre, g, threshold, pause, dry):
    T = load_traces(centre, g)
    centre_ll = CENTRE_COORDS[centre]
    by_class = {"G": [], "G2": []}
    unlabeled = []
    for t in T:
        c = t.get("test_class")
        (by_class[c] if c in by_class else unlabeled).append(t)

    routes = []  # (test_class, family_idx, route dict)
    for cls in ("G", "G2"):
        base = by_class[cls]
        if not base:
            continue
        # attach unlabeled traces to the class whose streets they best match
        # (done once, greedily, so ambiguous evidence still enriches a route
        # without ever changing its class).
        fams = routes_for_class(base, g, ROUTE_COUNTS.get((centre, cls), 1))
        # NOTE: ambiguous/unknown traces are deliberately NOT folded in.
        # They share the centre-approach streets with every route, so any
        # loose overlap wrongly attached residential ("ambiguous") evidence
        # to the G (airport) route and blew a 15 km route up to 57 km. A
        # route is built from its own confirmed-class traces only.
        for i, fam in enumerate(fams):
            route = build_one_route(fam, g, centre_ll, threshold, pause, dry)
            if route:
                routes.append((cls, i, route))
                print(f"   {cls} route {i}: {route['trace_count']} traces, "
                      f"{route['n_junctions']} junctions, "
                      f"{(route['distance_m'] or 0)/1000:.1f}km")
    return routes


def write_geojson(centre, routes):
    feats = []
    for cls, idx, r in routes:
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": r["coordinates"]},
            "properties": {
                "test_class": cls, "family": idx, "run": 0,
                "traces": r["trace_count"], "authors": r["authors"],
                "distance_m": r["distance_m"], "steps": r["steps"],
                "predicted": False, "below_threshold": False,
                "source": "rebuild_routes",
            },
        })
    out = os.path.join(DATA_DIR, centre, "rebuilt_routes.geojson")
    json.dump({"type": "FeatureCollection", "features": feats}, open(out, "w"), indent=1)
    print(f"   wrote {out} ({len(feats)} routes)")


def apply_to_db(all_routes):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    if not DATABASE_URL:
        sys.exit("Set DATABASE_PUBLIC_URL (or DATABASE_URL) before --apply.")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # backup everything first
    backup = {}
    for t in ("route_lines", "route_line_steps", "route_line_segments"):
        cur.execute(f"SELECT * FROM {t}")
        backup[t] = [dict(r) for r in cur.fetchall()]
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bpath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         f"route_data_backup_{ts}.json")
    json.dump(backup, open(bpath, "w"), default=str, indent=1)
    print(f"backup written: {bpath} "
          f"({len(backup['route_lines'])} route_lines, "
          f"{len(backup['route_line_steps'])} steps)")

    for centre, routes in all_routes.items():
        cur.execute("DELETE FROM route_lines WHERE centre_id = %s", (centre,))
        for cls, idx, r in routes:
            cur.execute(
                """
                INSERT INTO route_lines
                    (centre_id, family, run, trace_count, authors, distance_m,
                     geometry, test_class, mixed_classes, below_threshold,
                     predicted, source)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (centre, idx, 0, r["trace_count"], r["authors"], r["distance_m"],
                 json.dumps(r["coordinates"]), cls, False, False, False,
                 "rebuild_routes"),
            )
            rid = cur.fetchone()["id"]
            cg.insert_steps(cur, rid, r["steps"])
        print(f"   {centre}: wrote {len(routes)} routes")

    conn.commit()
    cur.close()
    conn.close()
    print("committed.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--centre", help="one centre; default all")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--pause", type=float, default=0.3)
    ap.add_argument("--apply", action="store_true", help="write to the live DB")
    args = ap.parse_args()

    cg.load_known(DB_PATH)
    g = cg.Graph(DB_PATH)
    centres = [args.centre] if args.centre else list(CENTRE_TRACE_FILES)

    all_routes = {}
    for centre in centres:
        print(f"=== {centre} ===")
        routes = rebuild(centre, g, args.threshold, args.pause, dry=not args.apply)
        all_routes[centre] = routes
        if not args.apply:
            write_geojson(centre, routes)

    if args.apply:
        apply_to_db(all_routes)
    else:
        print("\n[dry run] geojson written, DB untouched. Add --apply to write.")


if __name__ == "__main__":
    main()
